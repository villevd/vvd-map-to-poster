"""
Chunks beyond the edge of an exported 3D city model.

The viewer holds the model's own grid of tiles. When the view moves past their edge it asks the local
GUI server for the next tile (``GET /api/chunk``). Each chunk is built from real data (OpenStreetMap,
Overture Maps, elevation) around its own centre, on the model's grid, with the model's ground datum and
exaggeration, so its seams line up with the model. The result is returned in the viewer's chunk format.

Standard library plus the 3D pipeline only; the heavy OSM stack is imported when a chunk is first built.
"""

from __future__ import annotations

import base64
import math
import re
import threading
import zlib

import numpy as np

from .geo import LocalFrame
from .mesh import Scene

MAX_CELLS = 40                 # how far beyond the model's grid a chunk may be, in cells, per axis
OSM_MARGIN = 1.06              # data is fetched a little beyond the chunk so that its edges are whole
THEME_RE = re.compile(r"[A-Za-z0-9_-]{1,60}")
_BUILD_LOCK = threading.Lock()  # one build at a time: each one downloads and holds a whole area in memory


class ChunkError(ValueError):
    """A request the chunk endpoint cannot serve (bad or missing parameter)."""


def _num(q: dict, key: str, lo: float, hi: float) -> float:
    if key not in q:
        raise ChunkError(f"'{key}' is required")
    try:
        v = float(q[key])
    except ValueError:
        raise ChunkError(f"'{key}' must be a number") from None
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ChunkError(f"'{key}' must be between {lo:g} and {hi:g}")
    return v


def _int(q: dict, key: str, lo: float, hi: float) -> int:
    v = _num(q, key, lo, hi)
    if not v.is_integer():
        raise ChunkError(f"'{key}' must be a whole number")
    return int(v)


def parse_request(q: dict) -> tuple[dict, int, int]:
    """Validate the query of ``/api/chunk``. Returns (model spec, i, j); i and j index the chunk's cell."""
    spec = {
        "lat": _num(q, "lat", -85, 85), "lon": _num(q, "lon", -180, 180),
        "half": _num(q, "half", 50, 200_000), "tiles": _int(q, "tiles", 1, 15),
        "tileHalf": _num(q, "tileHalf", 20, 200_000), "zmin": _num(q, "zmin", -1_000, 9_000),
        "exag": _num(q, "exag", 0.01, 20), "bexag": _num(q, "bexag", 0.01, 20),
        "maxLod": _int(q, "maxLod", 0, 2),
    }
    theme = q.get("theme", "")
    if not THEME_RE.fullmatch(theme):
        raise ChunkError("'theme' is required")
    spec["theme"] = theme
    i = _int(q, "i", -MAX_CELLS, spec["tiles"] - 1 + MAX_CELLS)
    j = _int(q, "j", -MAX_CELLS, spec["tiles"] - 1 + MAX_CELLS)
    return spec, i, j


def cell_center(spec: dict, i: int, j: int) -> tuple[float, float]:
    """Centre of cell (i, j) in the model's local metres (x east, y north). Same grid as the model's tiles."""
    step = 2.0 * spec["tileHalf"]
    return (i - (spec["tiles"] - 1) / 2) * step, (j - (spec["tiles"] - 1) / 2) * step


def cell_latlon(spec: dict, i: int, j: int) -> tuple[float, float]:
    """(lat, lon) of the cell centre, through the model's own projection (exact, not a flat-earth guess)."""
    cx, cy = cell_center(spec, i, j)
    lon, lat = LocalFrame(spec["lat"], spec["lon"], spec["half"]).to_lonlat(cx, cy)
    return float(lat), float(lon)


def _place(meshes, cx: float, cy: float, i: int, j: int) -> None:
    """Move a cell's meshes from cell-local to model-local metres, as build_tiled_scene does for its tiles."""
    off = np.array([cx, cy, 0.0], dtype=np.float32)
    for m in meshes:
        if m.n_vertices:
            m.vertices = m.vertices + off
        if "impostors" in m.tags:
            m.tags["impostors"][:, 0] += cx
            m.tags["impostors"][:, 1] += cy
        m.tags["chunk"] = (i, j)
        m.tags["center"] = (cx, cy)


def pack_payload(meshes, i: int, j: int, theme: dict) -> dict:
    """Viewer chunk payload: the same groups the exported model uses, with their data inline (base64, zlib)."""
    from .viewer import _pack   # the viewer's own packing, so chunks and model stay in one format

    groups, blobs = _pack(Scene(meshes=meshes, half=0.0, z_range=(0.0, 0.0), center_lonlat=(0.0, 0.0)), theme)
    out_groups, out_blobs = [], {}
    for k, g in enumerate(groups):
        old = g["id"]                                  # _pack's blob id for this group
        g["id"] = f"c{i}_{j}_{k}"                      # unique among the chunks the viewer already holds
        out_blobs[g["id"]] = base64.b64encode(zlib.compress(blobs[old], 6)).decode("ascii")
        out_groups.append(g)
    return {"i": i, "j": j, "groups": out_groups, "blobs": out_blobs}


def build_chunk(spec: dict, i: int, j: int, log=print) -> dict:
    """Fetch the data for cell (i, j), build its detail levels and return the viewer payload."""
    import create_map_poster as cmp   # heavy (OpenStreetMap stack): only when a chunk is really built
    from .data import Providers, collect
    from .scene import SceneOptions, build_scene
    from .terrain import Dem, fetch_dem

    th, total = spec["tileHalf"], spec["half"]
    cx, cy = cell_center(spec, i, j)
    clat, clon = cell_latlon(spec, i, j)
    cell = LocalFrame(clat, clon, th)                       # the chunk: its own centre is its origin
    wide = LocalFrame(clat, clon, th * OSM_MARGIN)          # data a little beyond it, so no edge is cut short
    point = (clat, clon)
    dist = th * OSM_MARGIN
    state = {"down": False}

    def fetch_cached(key, fetch_once, what):
        try:
            cached = cmp.cache_get(key)
        except cmp.CacheError:
            cached = None
        if cached is not None:
            return cached
        return cmp._fetch_with_retries(fetch_once, what, state)

    providers = Providers(
        osm_features=lambda name, tags: fetch_cached(
            cmp._features_cache_key(point, dist, tags, name),
            lambda: cmp.fetch_features(point, dist, tags, name), f"OpenStreetMap {name}"),
        osm_graph=lambda: fetch_cached(
            cmp._graph_cache_key(point, dist), lambda: cmp.fetch_graph(point, dist), "OpenStreetMap streets"),
        overture=True, cache_dir=cmp.CACHE_DIR_PATH,
    )
    log(f"Chunk {i},{j}: collecting map data around {clat:.4f}, {clon:.4f}...")
    data = collect(wide, providers, log=log, want_traffic=False)

    # The elevation grid covers the whole model-sized area around the chunk, so its step (and so its
    # heights at the seams) matches the model's own terrain.
    try:
        dem = fetch_dem(LocalFrame(clat, clon, total), cmp.CACHE_DIR_PATH, log=log)
    except Exception as e:                                  # no terrain: flat, at the model's ground level
        log(f"⚠ Terrain unavailable for chunk {i},{j} ({e}); it will be flat.")
        dem = Dem.flat(cell, level=spec["zmin"])

    theme = cmp.load_theme(spec["theme"])
    levels = (0, 1, 2) if spec["maxLod"] > 0 else (0,)
    meshes = []
    for detail in levels:
        opts = SceneOptions(exaggeration=spec["exag"], building_exaggeration=spec["bexag"], traffic=False,
                            zmin=spec["zmin"], detail=detail, frame_sides=(False, False, False, False),
                            shadows=detail == 0, light_bearing=270.0, light_elevation=40.0)
        sc = build_scene(cell, data, dem, opts, title="", log=lambda *_: None)
        _place(sc.meshes, cx, cy, i, j)
        meshes.extend(sc.meshes)
    return pack_payload(meshes, i, j, theme)


def chunk_payload(spec: dict, i: int, j: int, log=print) -> dict:
    """The payload for cell (i, j), from the cache when this exact chunk was built before."""
    import create_map_poster as cmp

    key = "chunk3d_" + cmp._cache_digest(
        cmp._map3d_stamp(), round(spec["lat"], 6), round(spec["lon"], 6), spec["half"], spec["tiles"],
        spec["tileHalf"], i, j, round(spec["zmin"], 3), spec["exag"], spec["bexag"], spec["maxLod"], spec["theme"])
    hit = cmp._cache_try_get(key)
    if hit is not None:
        return hit
    with _BUILD_LOCK:
        payload = build_chunk(spec, i, j, log=log)
    cmp._cache_try_set(key, payload)
    return payload
