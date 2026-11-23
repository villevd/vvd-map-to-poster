"""Road and rail surfaces, draped over the terrain, plus bridges."""

import geopandas as gpd
import numpy as np
from ..geo import LocalFrame
from shapely.geometry import LineString
from shapely.ops import substring
from .parse import _col, _first, _is_yes


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
        {"cls": [r[0] for r in rows], "bridge": [r[1] for r in rows],
         "ref": [r[3] if len(r) > 3 else "" for r in rows], "roundabout": [bool(r[4]) if len(r) > 4 else False for r in rows],
         "oneway": [bool(r[5]) if len(r) > 5 else False for r in rows]},   # for the road markings (two-way lines only)
        geometry=[r[2] for r in rows],
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
    refs = _col(edges, "ref").map(lambda v: ";".join(map(str, v)) if isinstance(v, (list, tuple)) else ("" if v is None or v != v else str(v)))
    ring = _col(edges, "junction").map(_first).astype(str).str.lower().isin(["roundabout", "circular"]).to_numpy()
    oneway = _col(edges, "oneway").map(lambda v: str(_first(v)).lower() in ("yes", "true", "1", "-1")).to_numpy()
    for g, c, b, t, ref, rb, ow in zip(edges.geometry.values, cls.to_numpy(), bridge, tunnel, refs.to_numpy(), ring, oneway):
        if c is None or (isinstance(c, float) and np.isnan(c)) or t:
            continue
        rows.append((c, bool(b), g, ref, bool(rb), bool(ow)))
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

    def route_refs(routes) -> str:       # Overture lists the numbered routes a segment belongs to, e.g. Quebec R 117
        try:
            return ";".join(str(r.get("ref")) for r in routes if r.get("ref"))
        except (TypeError, AttributeError):
            return ""

    refs = _col(seg, "routes").map(route_refs).to_numpy()
    for geom, s, k, fl, ref in zip(seg.geometry.values, sub, klass, flags, refs):
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
            rows.append((c, False, geom, ref, False))
            continue
        cuts = sorted(bridges + tunnels)
        for a, b in _complement(cuts):
            rows.append((c, False, substring(geom, a, b, normalized=True), ref, False))
        for a, b in bridges:
            rows.append((c, True, substring(geom, a, b, normalized=True), ref, False))
    rows = [r for r in rows if isinstance(r[2], LineString) and r[2].length > 1.0]
    return _road_frame(rows).set_crs(frame.crs), np.asarray(rails, dtype=object)
