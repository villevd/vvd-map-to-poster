"""Building footprints from OpenStreetMap and Overture, merged into one set."""

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from ..geo import LocalFrame
from shapely.strtree import STRtree
from .model import BUILDING_COLUMNS
from .parse import _col, _first, _is_yes, parse_length, parse_number


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
