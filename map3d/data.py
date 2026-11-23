"""
Provider aggregation: turn OpenStreetMap + Overture Maps into one clean,
source-independent description of the city (``CityData``).

* OpenStreetMap (through OSMnx / Overpass) is the primary, freshest source and
  carries rich tags: ``height``, ``building:levels``, ``roof:shape``,
  ``building:part``, bridges, tunnels...
* Overture Maps adds footprints OSM is missing (Microsoft / Google ML
  buildings), heights from other datasets, ESA land cover, water, bridges and a
  complete road network - and keeps working when Overpass is down.

Both are merged by geometric overlap, never by id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring
from shapely.strtree import STRtree

from . import overture
from .geo import LocalFrame, clean_polygons, polygonal

# --------------------------------------------------------------------------
# Provider wiring
# --------------------------------------------------------------------------


@dataclass
class Providers:
    """
    Where data comes from. The OSM callables are injected so this package does
    not depend on the CLI module (and so tests can feed canned data).
    """

    osm_features: Callable[[str, dict], gpd.GeoDataFrame | None] | None = None
    osm_graph: Callable[[], object | None] | None = None
    overture: bool = True
    cache_dir: str = "cache"


@dataclass
class CityData:
    buildings: gpd.GeoDataFrame                 # Polygons, local metres (see BUILDING_COLUMNS)
    water: np.ndarray                           # (multi)polygons
    forest: np.ndarray
    parks: np.ndarray
    roads: gpd.GeoDataFrame                     # LineStrings: cls, bridge
    rails: np.ndarray                           # LineStrings
    trees: np.ndarray                           # (n, 2) positions of individually mapped trees
    sources: dict = field(default_factory=dict)  # human-readable provenance, for logs and the viewer
    stats: dict = field(default_factory=dict)    # building merge statistics


BUILDING_COLUMNS = ["height", "levels", "min_height", "roof_shape", "roof_height", "kind", "part", "source", "h_src"]

OSM_LANDCOVER_TAGS = {
    "natural": ["water", "bay", "strait", "wood", "scrub", "grassland", "heath", "wetland", "tree"],
    "waterway": ["riverbank", "river", "stream", "canal", "drain"],
    "landuse": ["forest", "grass", "meadow", "recreation_ground", "cemetery", "village_green", "orchard"],
    "leisure": ["park", "garden", "playground", "pitch", "golf_course", "common"],
}
OSM_BUILDING_TAGS = {"building": True, "building:part": True}
OSM_RAIL_TAGS = {"railway": "rail"}

# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------

_LEN_RE = re.compile(
    r"^\s*(?P<v>-?\d+(?:[.,]\d+)?)\s*(?P<u>m|meters?|metres?|ft|feet|foot|')?\s*(?:(?P<i>\d+(?:\.\d+)?)\s*(?:\"|in))?\s*$",
    re.IGNORECASE,
)


def parse_length(value) -> float:
    """OSM length tag ("12", "12.5 m", "40 ft", "40'6\"") -> metres, NaN when unusable."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return v if np.isfinite(v) and 0 < v < 900 else float("nan")
    m = _LEN_RE.match(str(value).split(";")[0])
    if not m:
        return float("nan")
    v = float(m.group("v").replace(",", "."))
    unit = (m.group("u") or "m").lower()
    if unit in ("ft", "feet", "foot", "'"):
        v *= 0.3048
        if m.group("i"):
            v += float(m.group("i")) * 0.0254
    return v if 0 < v < 900 else float("nan")


def parse_number(value) -> float:
    """First number found in a value such as ``3`` or ``"3;4"``; NaN otherwise."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return v if np.isfinite(v) else float("nan")
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(m.group(0).replace(",", ".")) if m else float("nan")


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def _first(value):
    """OSMnx sometimes returns lists for merged tags; take the first item."""
    if isinstance(value, (list, tuple, np.ndarray)):
        return value[0] if len(value) else None
    return value


def _is_yes(series: pd.Series) -> np.ndarray:
    """Tag present and not an explicit 'no'."""
    s = series.map(_first)
    return (s.notna() & (s.astype(str).str.lower() != "no")).to_numpy()


# --------------------------------------------------------------------------
# Buildings
# --------------------------------------------------------------------------


def _empty_buildings() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {c: [] for c in BUILDING_COLUMNS}, geometry=gpd.GeoSeries([], dtype="geometry"),
    )


def normalize_osm_buildings(gdf: gpd.GeoDataFrame, frame: LocalFrame) -> gpd.GeoDataFrame:
    """OSM building + building:part features -> the common building table (local metres)."""
    if gdf is None or gdf.empty:
        return _empty_buildings()
    g = frame.project(gdf)
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if g.empty:
        return _empty_buildings()

    building = _col(g, "building").map(_first)
    part_tag = _col(g, "building:part").map(_first)
    is_part = _is_yes(part_tag)
    has_building = (building.notna() & (building.astype(str).str.lower() != "no")).to_numpy()
    keep = has_building | is_part
    underground = _col(g, "location").map(_first).astype(str).str.lower().isin(["underground", "underwater"]).to_numpy()
    keep &= ~underground
    g = g[keep]
    if g.empty:
        return _empty_buildings()
    building, part_tag, is_part = building[keep], part_tag[keep], is_part[keep]

    height = np.array([parse_length(_first(v)) for v in _col(g, "height")])
    levels = np.array([parse_number(_first(v)) for v in _col(g, "building:levels")])
    roof_levels = np.array([parse_number(_first(v)) for v in _col(g, "roof:levels")])
    min_level = np.array([parse_number(_first(v)) for v in _col(g, "building:min_level")])
    min_height = np.array([parse_length(_first(v)) for v in _col(g, "min_height")])
    min_height = np.where(np.isnan(min_height) & ~np.isnan(min_level), min_level * 3.0, min_height)
    roof_height = np.array([parse_length(_first(v)) for v in _col(g, "roof:height")])
    roof_height = np.where(np.isnan(roof_height) & ~np.isnan(roof_levels), roof_levels * 3.0, roof_height)
    roof_shape = _col(g, "roof:shape").map(_first).fillna("").astype(str).str.lower().to_numpy()

    kind = np.where(is_part, part_tag.astype(str).str.lower(), building.astype(str).str.lower())
    h_src = np.where(~np.isnan(height), "height", np.where(~np.isnan(levels), "levels", ""))

    return gpd.GeoDataFrame(
        {
            "height": height, "levels": levels, "min_height": np.nan_to_num(min_height, nan=0.0),
            "roof_shape": roof_shape, "roof_height": roof_height, "kind": kind, "part": is_part,
            "source": "osm", "h_src": h_src,
        },
        geometry=g.geometry.values, crs=frame.crs,
    )


def normalize_overture_buildings(gdf: gpd.GeoDataFrame, frame: LocalFrame) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return _empty_buildings()
    g = frame.project(gdf)
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if "is_underground" in g.columns:
        g = g[~g["is_underground"].fillna(False).astype(bool)]
    if g.empty:
        return _empty_buildings()

    def num(name):
        return pd.to_numeric(_col(g, name), errors="coerce").to_numpy(dtype=float)

    height = num("height")
    levels = num("num_floors")
    roof_height = num("roof_height")
    min_height = np.nan_to_num(num("min_height"), nan=0.0)
    roof_shape = _col(g, "roof_shape").fillna("").astype(str).str.lower().to_numpy()
    klass = _col(g, "class").where(_col(g, "class").notna(), _col(g, "subtype")).fillna("yes").astype(str).str.lower()

    def src_name(s):
        try:
            return str(s[0]["dataset"])
        except Exception:
            return "overture"

    ds_name = _col(g, "sources").map(src_name).to_numpy()
    h_src = np.where(~np.isnan(height), "overture", np.where(~np.isnan(levels), "overture", ""))
    return gpd.GeoDataFrame(
        {
            "height": height, "levels": levels, "min_height": min_height, "roof_shape": roof_shape,
            "roof_height": roof_height, "kind": klass.to_numpy(), "part": False,
            "source": np.where(np.char.find(ds_name.astype(str), "OpenStreetMap") >= 0, "overture/osm", "overture"),
            "h_src": h_src,
        },
        geometry=g.geometry.values, crs=frame.crs,
    )


def merge_buildings(osm: gpd.GeoDataFrame, ov: gpd.GeoDataFrame, covered: float = 0.4) -> tuple[gpd.GeoDataFrame, dict]:
    """
    Add Overture footprints that OSM does not already cover, and borrow Overture
    heights for OSM buildings that have none.
    """
    stats = {"osm": int(len(osm)), "overture_added": 0, "heights_borrowed": 0}
    if ov.empty:
        return osm, stats
    if osm.empty:
        stats["overture_added"] = int(len(ov))
        return ov.reset_index(drop=True), stats

    osm_geoms = np.asarray(osm.geometry.values, dtype=object)
    ov_geoms = np.asarray(ov.geometry.values, dtype=object)
    tree = STRtree(osm_geoms)
    ov_i, osm_i = tree.query(ov_geoms, predicate="intersects")
    inter = shapely.area(shapely.intersection(ov_geoms[ov_i], osm_geoms[osm_i])) if len(ov_i) else np.array([])
    ov_area = np.maximum(shapely.area(ov_geoms), 1e-6)
    cover = np.bincount(ov_i, weights=inter, minlength=len(ov_geoms)) / ov_area
    new = cover < covered

    # Borrow heights: for each OSM building without any height/levels, best-overlapping Overture match
    needs = osm["h_src"].to_numpy() == ""
    if len(ov_i) and needs.any():
        ov_h = ov["height"].to_numpy(dtype=float)
        ov_l = ov["levels"].to_numpy(dtype=float)
        ok = (needs[osm_i]) & (~np.isnan(ov_h[ov_i]) | ~np.isnan(ov_l[ov_i]))
        if ok.any():
            pair = pd.DataFrame({"osm": osm_i[ok], "ov": ov_i[ok], "a": inter[ok]})
            best = pair.sort_values("a", ascending=False).drop_duplicates("osm")
            best = best[best["a"] > 0.3 * shapely.area(osm_geoms[best["osm"].to_numpy()])]
            osm = osm.copy()
            rows = best["osm"].to_numpy()
            h_new = ov_h[best["ov"].to_numpy()]
            l_new = ov_l[best["ov"].to_numpy()]
            cur_h = osm["height"].to_numpy(dtype=float)
            cur_l = osm["levels"].to_numpy(dtype=float)
            cur_s = osm["h_src"].to_numpy(dtype=object)
            cur_h[rows] = h_new
            cur_l[rows] = np.where(np.isnan(h_new), l_new, np.nan)
            cur_s[rows] = "overture"
            osm["height"], osm["levels"], osm["h_src"] = cur_h, cur_l, cur_s
            stats["heights_borrowed"] = int(len(best))

    added = ov[new]
    stats["overture_added"] = int(len(added))
    merged = pd.concat([osm, added], ignore_index=True)
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=osm.crs), stats


def drop_parents_covered_by_parts(b: gpd.GeoDataFrame, ratio: float = 0.5) -> gpd.GeoDataFrame:
    """A ``building`` outline whose ``building:part`` pieces cover it would be extruded twice; drop the outline."""
    if b.empty or not b["part"].any():
        return b
    geoms = np.asarray(b.geometry.values, dtype=object)
    is_part = b["part"].to_numpy(dtype=bool)
    parents = np.where(~is_part)[0]
    parts = np.where(is_part)[0]
    tree = STRtree(geoms[parts])
    p_i, q_i = tree.query(geoms[parents], predicate="intersects")
    if not len(p_i):
        return b
    inter = shapely.area(shapely.intersection(geoms[parents][p_i], geoms[parts][q_i]))
    cover = np.bincount(p_i, weights=inter, minlength=len(parents)) / np.maximum(shapely.area(geoms[parents]), 1e-6)
    drop = parents[cover >= ratio]
    return b.drop(b.index[drop]).reset_index(drop=True)


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


# --------------------------------------------------------------------------
# Roads and rails
# --------------------------------------------------------------------------

ROAD_CLASS = {
    "motorway": "motorway", "motorway_link": "motorway",
    "trunk": "primary", "trunk_link": "primary", "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "tertiary", "tertiary_link": "tertiary",
    "residential": "residential", "living_street": "residential", "unclassified": "residential", "road": "residential",
    "service": "service",
}


def _road_frame(rows: list) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame({"cls": [], "bridge": []}, geometry=gpd.GeoSeries([], dtype="geometry"))
    return gpd.GeoDataFrame(
        {"cls": [r[0] for r in rows], "bridge": [r[1] for r in rows]}, geometry=[r[2] for r in rows],
    )


def roads_from_osm_graph(graph, frame: LocalFrame) -> gpd.GeoDataFrame:
    import osmnx as ox
    edges = ox.graph_to_gdfs(graph, nodes=False)
    edges = frame.project(edges)
    rows = []
    hw = _col(edges, "highway").map(_first).astype(str).str.lower()
    cls = hw.map(ROAD_CLASS)
    bridge = _is_yes(_col(edges, "bridge"))
    tunnel = _is_yes(_col(edges, "tunnel"))
    for g, c, b, t in zip(edges.geometry.values, cls.to_numpy(), bridge, tunnel):
        if c is None or (isinstance(c, float) and np.isnan(c)) or t:
            continue
        rows.append((c, bool(b), g))
    out = _road_frame(rows)
    return out.set_crs(frame.crs)


def _flag_intervals(flags, name: str) -> list[tuple[float, float]]:
    """Linear-referenced [a, b] intervals where an Overture road flag applies (whole segment -> [0, 1])."""
    out: list[tuple[float, float]] = []
    if flags is None:
        return out
    try:
        items = list(flags)
    except TypeError:
        return out
    for it in items:
        try:
            vals = list(it["values"]) if it["values"] is not None else []
            if name not in vals:
                continue
            between = it["between"]
            out.append((0.0, 1.0) if between is None else (float(between[0]), float(between[1])))
        except Exception:
            continue
    return out


def _complement(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pos, out = 0.0, []
    for a, b in sorted(intervals):
        if a > pos + 1e-6:
            out.append((pos, a))
        pos = max(pos, b)
    if pos < 1.0 - 1e-6:
        out.append((pos, 1.0))
    return out


def roads_from_overture(segments: gpd.GeoDataFrame, frame: LocalFrame) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """Overture transportation segments -> road lines (bridge portions split out) and rail lines."""
    seg = frame.project(segments)
    rows, rails = [], []
    sub = _col(seg, "subtype").astype(str).str.lower().to_numpy()
    klass = _col(seg, "class").astype(str).str.lower().to_numpy()
    flags = _col(seg, "road_flags").to_numpy()
    for geom, s, k, fl in zip(seg.geometry.values, sub, klass, flags):
        if geom is None or geom.is_empty:
            continue
        if s == "rail":
            if k in ("standard_gauge", "narrow_gauge", "light_rail", "funicular", "monorail", "unknown"):
                rails.append(geom)
            continue
        if s != "road":
            continue
        c = ROAD_CLASS.get(k)
        if c is None or geom.geom_type != "LineString":
            continue
        bridges = _flag_intervals(fl, "is_bridge")
        tunnels = _flag_intervals(fl, "is_tunnel")
        if not bridges and not tunnels:
            rows.append((c, False, geom))
            continue
        cuts = sorted(bridges + tunnels)
        for a, b in _complement(cuts):
            rows.append((c, False, substring(geom, a, b, normalized=True)))
        for a, b in bridges:
            rows.append((c, True, substring(geom, a, b, normalized=True)))
    rows = [r for r in rows if isinstance(r[2], LineString) and r[2].length > 1.0]
    return _road_frame(rows).set_crs(frame.crs), np.asarray(rails, dtype=object)


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
            want_rails: bool = True) -> CityData:
    """Fetch everything from every enabled provider and merge it."""
    tile = frame.tile_polygon()
    sources: dict[str, list[str]] = {"buildings": [], "landcover": [], "roads": [], "terrain": []}

    # ---- OpenStreetMap --------------------------------------------------
    osm_b, osm_lc, osm_rail, graph = None, None, None, None
    if providers.osm_features is not None:
        osm_b = _try(lambda: providers.osm_features("buildings3d", OSM_BUILDING_TAGS), "OSM buildings", log)
        osm_lc = _try(lambda: providers.osm_features("landcover3d", OSM_LANDCOVER_TAGS), "OSM land cover", log)
        if want_rails:
            osm_rail = _try(lambda: providers.osm_features("railways", OSM_RAIL_TAGS), "OSM railways", log)
    if providers.osm_graph is not None:
        graph = _try(providers.osm_graph, "OSM street network", log)

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

    # ---- Overture Maps --------------------------------------------------
    buildings = buildings_osm
    merge_stats: dict = {}
    if providers.overture:
        bbox = frame.bbox_lonlat(margin=20.0)
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
            seg = fl("segment", ["id", "geometry", "subtype", "class", "road_flags"])
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
        stats=merge_stats,
    )
