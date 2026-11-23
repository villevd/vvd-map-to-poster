"""Collects every provider for one area and tiles it for the viewer."""

import numpy as np
import shapely
from .. import overture
from ..geo import LocalFrame, polygonal
from .buildings import _empty_buildings, drop_parents_covered_by_parts, merge_buildings, normalize_osm_buildings, normalize_overture_buildings
from .landcover import classify_osm_landcover, classify_overture_landcover
from .model import CityData, OSM_AIRPORT_TAGS, OSM_BUILDING_TAGS, OSM_LANDCOVER_TAGS, OSM_RAIL_TAGS, OSM_TRAFFIC_TAGS, Providers
from .parse import _col, _first
from .roads import _road_frame, roads_from_osm_graph, roads_from_overture


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

_OV_BUILDING_COLS = ["id", "geometry", "height", "min_height", "num_floors", "class", "subtype", "roof_shape",
                     "roof_height", "is_underground", "sources"]


def _try(fn, what: str, log):
    try:
        return fn()
    except Exception as e:  # providers are best-effort; the 3D pipeline continues with whatever answered
        log(f"⚠ {what} unavailable: {e}")
        return None


def collect(frame: LocalFrame, providers: Providers, log=print, want_trees: bool = True,
            want_rails: bool = True, want_traffic: bool = True, want_airport: bool = True) -> CityData:
    """Fetch everything from every enabled provider and merge it."""
    tile = frame.tile_polygon()
    sources: dict[str, list[str]] = {"buildings": [], "landcover": [], "roads": [], "terrain": []}

    # ---- OpenStreetMap --------------------------------------------------
    osm_b, osm_lc, osm_rail, osm_traffic, osm_air, graph = None, None, None, None, None, None
    log("Step 1/3 - Downloading map data. Each layer is cached on disk, so a repeat run of the same area is fast.")
    if providers.osm_features is not None:
        log("  * OpenStreetMap: building outlines, heights and roof shapes...")
        osm_b = _try(lambda: providers.osm_features("buildings3d", OSM_BUILDING_TAGS), "OSM buildings", log)
        log("  * OpenStreetMap: water, parks, forests and other land cover...")
        osm_lc = _try(lambda: providers.osm_features("landcover3d", OSM_LANDCOVER_TAGS), "OSM land cover", log)
        if want_rails:
            log("  * OpenStreetMap: railway lines...")
            osm_rail = _try(lambda: providers.osm_features("railways", OSM_RAIL_TAGS), "OSM railways", log)
        if want_traffic:
            log("  * OpenStreetMap: traffic lights and stop signs (the cars obey these)...")
            osm_traffic = _try(lambda: providers.osm_features("traffic", OSM_TRAFFIC_TAGS), "OSM traffic signals", log)
        if want_airport:
            osm_air = _try(lambda: providers.osm_features("airport", OSM_AIRPORT_TAGS), "OSM runways", log)
    if providers.osm_graph is not None:
        log("  * OpenStreetMap: the street network (lanes, one-ways, speeds)...")
        graph = _try(providers.osm_graph, "OSM street network", log)
    log("  * Converting everything to metres on a flat local grid centred on the city...")

    buildings_osm = _try(lambda: normalize_osm_buildings(osm_b, frame), "OSM buildings", log)
    buildings_osm = buildings_osm if buildings_osm is not None else _empty_buildings()
    if len(buildings_osm):
        sources["buildings"].append("OpenStreetMap")
    lc = classify_osm_landcover(osm_lc, frame) if osm_lc is not None else classify_osm_landcover(None, frame)
    if any(len(lc[k]) for k in ("water", "forest", "parks")):
        sources["landcover"].append("OpenStreetMap")

    roads = _road_frame([]).set_crs(frame.crs)
    if graph is not None:
        roads = _try(lambda: roads_from_osm_graph(graph, frame), "OSM roads", log)
        roads = roads if roads is not None else _road_frame([]).set_crs(frame.crs)
        if len(roads):
            sources["roads"].append("OpenStreetMap")

    rails = np.asarray([], dtype=object)
    if osm_rail is not None and not osm_rail.empty:
        r = frame.project(osm_rail)
        rails = np.asarray(r.geometry.values[r.geometry.geom_type.isin(["LineString", "MultiLineString"]).to_numpy()],
                           dtype=object)

    runways = []
    if osm_air is not None and not osm_air.empty:
        ra = frame.project(osm_air)
        for g in ra.geometry.values:
            if g is not None and g.geom_type in ("LineString", "MultiLineString"):
                runways.append(g)
    signals, stop_signs = np.zeros((0, 2)), np.zeros((0, 2))
    if osm_traffic is not None and not osm_traffic.empty:
        t = frame.project(osm_traffic)
        pts = t.geometry.centroid                      # nodes are points; a mapped area counts as its centre
        kind = _col(t, "highway").map(_first).astype(str).str.lower().to_numpy()
        xy = np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])
        signals = xy[kind == "traffic_signals"]
        stop_signs = xy[kind == "stop"]

    got = len(buildings_osm) + len(roads) + len(signals) + len(stop_signs)
    if got:
        log(f"  OK OpenStreetMap gave {len(buildings_osm):,} buildings, {len(roads):,} road segments, "
            f"{len(signals):,} traffic lights, {len(stop_signs):,} stop signs.")
    else:
        log("  ⚠ OpenStreetMap returned nothing for this area (the server may have failed silently).")
    # ---- Overture Maps --------------------------------------------------
    buildings = buildings_osm
    merge_stats: dict = {}
    if providers.overture:
        bbox = frame.bbox_lonlat(margin=20.0)
        log("Step 2/3 - Overture Maps: filling gaps (extra buildings and heights, water, land cover)...")
        fl = lambda otype, cols: overture.fetch_layer(otype, bbox, cols, providers.cache_dir, log)  # noqa: E731
        ov_b = fl("building", _OV_BUILDING_COLS)
        if ov_b is not None:
            ovn = normalize_overture_buildings(ov_b, frame)
            buildings, merge_stats = merge_buildings(buildings_osm, ovn)
            if merge_stats.get("overture_added") or merge_stats.get("heights_borrowed") or not len(buildings_osm):
                sources["buildings"].append(f"Overture Maps {ov_b.attrs.get('release', '')}".strip())

        layers = {
            "water": fl("water", ["id", "geometry", "subtype", "class"]),
            "land": fl("land", ["id", "geometry", "subtype", "class"]),
            "land_cover": fl("land_cover", ["id", "geometry", "subtype"]),
            "land_use": fl("land_use", ["id", "geometry", "subtype", "class"]),
        }
        if any(v is not None for v in layers.values()):
            # Satellite land cover is coarse: only lean on it where OSM mapped little vegetation
            osm_green = sum(float(shapely.area(lc[k]).sum()) for k in ("forest", "parks")) / tile.area
            ovlc = classify_overture_landcover(layers, frame, use_cover=osm_green < 0.03)
            for k in ("water", "forest", "parks"):
                lc[k] = np.concatenate([lc[k], ovlc[k]]) if len(ovlc[k]) else lc[k]
            sources["landcover"].append("Overture Maps")

        if not len(roads):
            seg = fl("segment", ["id", "geometry", "subtype", "class", "road_flags", "routes"])
            if seg is not None and not seg.empty:
                roads, ov_rails = roads_from_overture(seg, frame)
                if len(roads):
                    sources["roads"].append("Overture Maps")
                if len(ov_rails) and not len(rails):
                    rails = ov_rails

    buildings = buildings[buildings.geometry.notna() & ~buildings.geometry.is_empty].reset_index(drop=True)
    if len(buildings):
        # Cut buildings that straddle the tile edge, like the physical model would be
        valid = shapely.make_valid(np.asarray(buildings.geometry.values, dtype=object))
        clipped = polygonal(shapely.intersection(valid, tile))
        keep = np.array([g is not None for g in clipped], dtype=bool)
        buildings = buildings[keep].reset_index(drop=True)
        buildings["geometry"] = clipped[keep]
    buildings = drop_parents_covered_by_parts(buildings)

    if len(rails):
        rails = shapely.intersection(rails, tile)
        rails = rails[~shapely.is_empty(rails)]

    return CityData(
        buildings=buildings,
        water=lc["water"], forest=lc["forest"], parks=lc["parks"],
        roads=roads, rails=rails, trees=lc["trees"] if want_trees else np.zeros((0, 2)),
        sources={k: v for k, v in sources.items() if v},
        stats=merge_stats, signals=signals, stop_signs=stop_signs, runways=runways,
    )


def tile_data(data: CityData, cx: float, cy: float, half: float) -> CityData:
    """
    The part of ``data`` that belongs to the square tile centred on (cx, cy), moved so the tile is
    centred on the origin. Buildings and trees go to the tile holding their centre (never cut in two);
    areas and lines are only pre-filtered, the scene builder clips them to the tile.
    """
    off = np.array([cx, cy])
    tile = shapely.box(cx - half, cy - half, cx + half, cy + half)
    near = shapely.buffer(tile, 40.0)

    def shift(geoms):
        return shapely.transform(geoms, lambda c: c - off) if len(geoms) else geoms

    def pick(geoms):
        geoms = np.asarray(geoms, dtype=object)
        return shift(geoms[shapely.intersects(geoms, near)]) if len(geoms) else geoms

    b = data.buildings
    if len(b):
        cen = shapely.centroid(np.asarray(b.geometry.values, dtype=object))
        gx, gy = shapely.get_x(cen), shapely.get_y(cen)
        inside = (gx >= cx - half) & (gx < cx + half) & (gy >= cy - half) & (gy < cy + half)
        b = b[inside].copy()
        b["geometry"] = shift(np.asarray(b.geometry.values, dtype=object))
    roads = data.roads
    if roads is not None and len(roads):
        roads = roads[shapely.intersects(np.asarray(roads.geometry.values, dtype=object), near)].copy()
        roads["geometry"] = shift(np.asarray(roads.geometry.values, dtype=object))
    trees = data.trees
    if len(trees):
        t_in = (np.abs(trees[:, 0] - cx) < half) & (np.abs(trees[:, 1] - cy) < half)
        trees = trees[t_in] - off
    def points(xy):
        if not len(xy):
            return xy
        xy = xy[(np.abs(xy[:, 0] - cx) < half + 40.0) & (np.abs(xy[:, 1] - cy) < half + 40.0)]
        return xy - off

    return CityData(buildings=b, water=pick(data.water), forest=pick(data.forest), parks=pick(data.parks),
                    roads=roads, rails=pick(data.rails), trees=trees, sources=data.sources, stats=data.stats,
                    signals=points(data.signals), stop_signs=points(data.stop_signs))
