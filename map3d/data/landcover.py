"""Water, forest and park polygons from land cover data."""

import geopandas as gpd
import numpy as np
import shapely
from ..geo import LocalFrame, clean_polygons
from shapely.geometry.base import BaseGeometry
from .parse import _col, _first, _is_yes


# --------------------------------------------------------------------------
# Land cover (water / forest / parks / trees)
# --------------------------------------------------------------------------

WATERWAY_WIDTH = {"river": 14.0, "canal": 9.0, "stream": 4.0, "drain": 3.0, "ditch": 2.5}


def _polys(gdf: gpd.GeoDataFrame | None, mask: np.ndarray, tile: BaseGeometry) -> np.ndarray:
    if gdf is None or not mask.any():
        return np.asarray([], dtype=object)
    return clean_polygons(gdf.geometry.values[mask], clip=tile)


def _buffer_lines(lines: np.ndarray, widths: np.ndarray, tile: BaseGeometry) -> np.ndarray:
    if len(lines) == 0:
        return np.asarray([], dtype=object)
    buf = shapely.buffer(lines, widths / 2.0, cap_style="round", quad_segs=3)
    return clean_polygons(buf, clip=tile)


def classify_osm_landcover(gdf: gpd.GeoDataFrame | None, frame: LocalFrame) -> dict:
    """Split OSM landcover features into water / forest / park polygons and tree points."""
    out = {k: np.asarray([], dtype=object) for k in ("water", "forest", "parks")}
    out["trees"] = np.zeros((0, 2))
    if gdf is None or gdf.empty:
        return out
    g = frame.project(gdf)
    tile = frame.tile_polygon()
    gt = g.geometry.geom_type
    is_poly = gt.isin(["Polygon", "MultiPolygon"]).to_numpy()
    is_line = gt.isin(["LineString", "MultiLineString"]).to_numpy()
    is_pt = (gt == "Point").to_numpy()

    nat = _col(g, "natural").map(_first).astype(str).str.lower()
    ww = _col(g, "waterway").map(_first).astype(str).str.lower()
    lu = _col(g, "landuse").map(_first).astype(str).str.lower()
    le = _col(g, "leisure").map(_first).astype(str).str.lower()
    tunnel = _is_yes(_col(g, "tunnel")) | _is_yes(_col(g, "culvert"))

    water_poly = is_poly & (nat.isin(["water", "bay", "strait"]) | (ww == "riverbank")).to_numpy()
    forest = is_poly & ((nat == "wood") | (lu == "forest")).to_numpy()
    park = is_poly & (
        nat.isin(["scrub", "grassland", "heath", "wetland"])
        | lu.isin(["grass", "meadow", "recreation_ground", "cemetery", "village_green", "orchard"])
        | le.isin(["park", "garden", "playground", "pitch", "golf_course", "common"])
    ).to_numpy()
    park &= ~forest & ~water_poly

    river_mask = is_line & ww.isin(list(WATERWAY_WIDTH)).to_numpy() & ~tunnel
    widths = ww[river_mask].map(WATERWAY_WIDTH).to_numpy(dtype=float)
    wlines = np.asarray(g.geometry.values[river_mask], dtype=object)

    out["water"] = np.concatenate([_polys(g, water_poly, tile), _buffer_lines(wlines, widths, tile)])
    out["forest"] = _polys(g, forest, tile)
    out["parks"] = _polys(g, park, tile)
    tree_pts = is_pt & (nat == "tree").to_numpy()
    if tree_pts.any():
        pts = g.geometry.values[tree_pts]
        out["trees"] = np.column_stack([shapely.get_x(pts), shapely.get_y(pts)])
    return out


OV_FOREST = {"forest", "wood"}
OV_GREEN = {"grass", "shrub", "meadow", "moss", "wetland", "park", "garden", "cemetery", "recreation", "golf_course",
            "fairway", "green", "playground", "pitch", "recreation_ground", "village_green", "scrub", "heath"}
OV_WATER_LINE_WIDTH = {"river": 14.0, "canal": 9.0, "stream": 4.0, "drain": 3.0, "ditch": 2.5}


def _cat(parts) -> np.ndarray:
    parts = [p for p in parts if len(p)]
    return np.concatenate(parts) if parts else np.asarray([], dtype=object)


# Overture `land_cover` is a *layered* product: a coarse forest/grass background polygon is covered by finer
# polygons (urban, barren, crop ...) drawn on top. Those must be cut out of the green classes.
OV_COVER_ERASE = {"urban", "barren", "crop", "snow"}


def classify_overture_landcover(layers: dict, frame: LocalFrame, use_cover: bool = True) -> dict:
    """
    Overture ``water`` / ``land`` / ``land_use`` / ``land_cover`` -> water / forest / park polygons.

    ``land`` and ``land_use`` derive from OSM-quality data and are used as is. ``land_cover`` (satellite
    derived, coarse) is only used when ``use_cover`` is set - i.e. when OSM mapped little vegetation.
    """
    tile = frame.tile_polygon()
    water, forest, parks, erasers = [], [], [], []

    w = layers.get("water")
    if w is not None and not w.empty:
        w = frame.project(w)
        sub = _col(w, "subtype").astype(str).str.lower()
        klass = _col(w, "class").astype(str).str.lower()
        gt = w.geometry.geom_type
        poly = gt.isin(["Polygon", "MultiPolygon"]).to_numpy() & (klass != "swimming_pool").to_numpy()
        water.append(_polys(w, poly, tile))
        line = gt.isin(["LineString", "MultiLineString"]).to_numpy()
        if line.any():
            widths = klass[line].map(OV_WATER_LINE_WIDTH).fillna(sub[line].map(OV_WATER_LINE_WIDTH)).fillna(3.0)
            water.append(_buffer_lines(np.asarray(w.geometry.values[line], dtype=object), widths.to_numpy(float), tile))
        erasers.append(water[0])

    cover_forest, cover_green = [], []
    for key in ("land", "land_use", "land_cover"):
        d = layers.get(key)
        if d is None or d.empty:
            continue
        d = frame.project(d)
        poly = d.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).to_numpy()
        sub = _col(d, "subtype").astype(str).str.lower()
        klass = _col(d, "class").astype(str).str.lower()
        is_forest = (sub.isin(OV_FOREST) | klass.isin(OV_FOREST)).to_numpy()
        bunker = klass.isin(["bunker", "tee"]).to_numpy()
        is_green = (sub.isin(OV_GREEN) | klass.isin(OV_GREEN) | ((sub == "golf").to_numpy() & ~bunker)).to_numpy()
        is_green &= ~is_forest
        if key == "land_cover":
            if use_cover:
                cover_forest.append(_polys(d, poly & is_forest, tile))
                cover_green.append(_polys(d, poly & is_green, tile))
                erasers.append(_polys(d, poly & sub.isin(OV_COVER_ERASE).to_numpy(), tile))
            continue
        forest.append(_polys(d, poly & is_forest, tile))
        parks.append(_polys(d, poly & is_green, tile))
        if key == "land_use":     # built-up land keeps satellite "forest" out of towns and industrial sites
            built = (sub == "developed").to_numpy()
            erasers.append(_polys(d, poly & built, tile))

    if cover_forest or cover_green:
        all_erasers = _cat(erasers)
        erase = shapely.union_all(all_erasers) if len(all_erasers) else None
        for src, dst in ((cover_forest, forest), (cover_green, parks)):
            polys = _cat(src)
            if len(polys) and erase is not None:
                polys = clean_polygons(shapely.difference(polys, erase), clip=tile)
            dst.append(polys)

    return {"water": _cat(water), "forest": _cat(forest), "parks": _cat(parks)}
