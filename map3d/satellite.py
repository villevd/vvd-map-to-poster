"""Satellite picture tiles for the 3-D viewer.

The viewer drapes Esri World Imagery over the ground and roofs and asks for picture tiles by z/x/y. Two places keep them:

* this computer: every tile that is fetched is kept on disk under ``CACHE_DIR/satellite``, so it is never downloaded twice;
* the poster's baked map (.db): when a map is baked, the tiles its default picture needs are stored inside it, so the map
  opens with its picture and no network at all.

``cells_for_tile`` reproduces, in Python, the tile choice made by ``buildSatCanvas`` in the viewer's 05_imagery.js. Keep
the two in step: a change to one needs the same change to the other.
"""

from __future__ import annotations

import math
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UPSTREAM = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MIN_BYTES = 32          # anything smaller is not a picture (the server's "no data" answer)
CELL_CAP = 144          # most picture tiles one model tile uses for its base picture (as in the viewer)
BAKE_LIMIT = 20000      # a bake that would need more pictures than this is skipped rather than run for hours
_HEADERS = {"User-Agent": "map3d-viewer"}


def cache_root(cache_dir: str | os.PathLike | None = None) -> Path:
    return Path(cache_dir or os.environ.get("CACHE_DIR", "cache")) / "satellite"


def key_of(z: int, x: int, y: int) -> str:
    return f"{z}/{x}/{y}"


def fetch_tile(z: int, x: int, y: int, cache_dir=None) -> bytes | None:
    """One picture tile: from this computer's disk cache, else from the network (then kept on disk).
    None when the tile cannot be had."""
    path = cache_root(cache_dir) / str(z) / str(x) / f"{y}.jpg"
    try:
        data = path.read_bytes()
        if len(data) >= MIN_BYTES:
            return data
    except OSError:
        pass
    try:
        req = urllib.request.Request(UPSTREAM.format(z=z, x=x, y=y), headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
    except Exception:
        return None
    if len(data) < MIN_BYTES:
        return None
    try:   # a failed write only costs a download next time
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{id(data)}.part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------- which tiles a model needs (mirrors 05_imagery.js)

def _lonlat(x: float, y: float, lat: float, lon: float) -> tuple[float, float]:
    """Model-local metres (east, north) -> lon/lat, as metresToLonLat in the viewer."""
    f = lat * math.pi / 180
    m_lat = 111132.92 - 559.82 * math.cos(2 * f) + 1.175 * math.cos(4 * f)
    m_lon = 111412.84 * math.cos(f) - 93.5 * math.cos(3 * f)
    return lon + x / m_lon, lat + y / m_lat


def _merc(lon: float, lat: float, z: int) -> tuple[float, float]:
    return ((lon + 180) / 360 * 2 ** z,
            (1 - math.asinh(math.tan(lat * math.pi / 180)) / math.pi) / 2 * 2 ** z)


def cells_for_tile(center_x: float, center_z: float, tile_size: float, tiles: int, lat: float, lon: float,
                   high: bool = False, cap: int = CELL_CAP) -> list[tuple[int, int, int]]:
    """The (z, x, y) picture tiles of one model tile's base ground picture.

    ``center_x`` / ``center_z`` are the model tile's centre as the viewer has it (the group's ``center``);
    ``tile_size`` is the tile's full width in metres (``CFG.tileSize``), ``tiles`` the tiles per side (``CFG.tiles``),
    ``high`` the Quality "High" setting (a sharper base picture).
    """
    half = tile_size / 2
    north = -center_z                         # the viewer's tile.center.z is minus the north coordinate
    w, s = _lonlat(center_x - half, north - half, lat, lon)
    e, n = _lonlat(center_x + half, north + half, lat, lon)
    if high:
        side = 2048 if tiles > 1 else 4096
    else:
        side = 1024 if tiles > 1 else 2048
    z = min(18, max(10, math.floor(math.log2(156543.03 * math.cos(lat * math.pi / 180) / (tile_size / side)) + 0.5)))
    while True:
        ax, ay = _merc(w, n, z)
        bx, by = _merc(e, s, z)
        x0, x1, y0, y1 = math.floor(ax), math.floor(bx), math.floor(ay), math.floor(by)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= cap or z <= 10:
            break
        z -= 1
    return [(z, x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def needed_tiles(cfg: dict) -> set[tuple[int, int, int]]:
    """Every picture tile the default ground picture of a baked model's tiles uses (see ``cells_for_tile``)."""
    geo = cfg.get("geo") or {}
    if "lat" not in geo or "lon" not in geo or not cfg.get("groups"):
        return set()
    centers = {(float(g["center"][0]), float(g["center"][1])) for g in cfg["groups"]}
    wanted: set[tuple[int, int, int]] = set()
    for cx, cz in centers:
        wanted.update(cells_for_tile(cx, cz, float(cfg["tileSize"]), int(cfg["tiles"]), float(geo["lat"]), float(geo["lon"])))
    return wanted


def prefetch(cfg: dict, cache_dir=None, log=print) -> dict[str, bytes]:
    """Download (or read from the disk cache) every picture a baked model's default ground needs.

    Returns ``{"z/x/y": jpeg bytes}`` for storing in the map. Tiles the source does not have are left out, and a
    network failure leaves the map without pictures rather than failing the bake."""
    wanted = sorted(needed_tiles(cfg))
    if not wanted:
        return {}
    if len(wanted) > BAKE_LIMIT:
        log(f"⚠ The satellite picture would need {len(wanted)} tiles: too many to store, the map will fetch them when shown.")
        return {}
    log(f"Storing the satellite picture in the map: {len(wanted)} tiles (kept on this computer for next time)...")
    out: dict[str, bytes] = {}
    done = 0

    def one(t):
        return key_of(*t), fetch_tile(*t, cache_dir=cache_dir)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, data in pool.map(one, wanted):
            done += 1
            if data:
                out[key] = data
            if done % 200 == 0:
                log(f"  {done}/{len(wanted)} picture tiles")
    log(f"  {len(out)} of {len(wanted)} picture tiles stored" + ("" if len(out) == len(wanted) else " (the rest were not available)"))
    return out
