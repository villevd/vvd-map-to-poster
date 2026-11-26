"""Green-ground test from the satellite photo the viewer draws, so trees only stand on green terrain."""

import io
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

from ..geo import LocalFrame

# Same picture service the viewer uses for the ground texture (see viewer/js/05_imagery.js).
ARCGIS = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_PX = 256
ZOOM = 17            # about 1.2 m per picture pixel at the equator: fine enough for single trees
MAX_PICTURES = 400   # wider areas drop to zoom 16 (about 2.4 m per pixel), so the download stays bounded
# Excess green 2g - r - b (0..510): grass, woodland and shrubs clear it; bare earth, sand, tracks and pavement do not.
EXG_MIN = 20


def _merc_tiles(lon, lat, z: int):
    """lon/lat (degrees) -> fractional Web Mercator tile coordinates at zoom ``z``."""
    n = 2.0 ** z
    x = (np.asarray(lon, dtype=float) + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(np.asarray(lat, dtype=float)))) / np.pi) / 2.0 * n
    return x, y


def _fetch_picture(z: int, tx: int, ty: int) -> np.ndarray | None:
    try:
        r = requests.get(ARCGIS.format(z=z, x=tx, y=ty), timeout=10)
        if not r.ok:
            return None
        return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    except Exception:
        return None


def photo_green_test(frame: LocalFrame, log=print):
    """
    Returns ``test(xy) -> bool array`` for local-metre points: True where the satellite photo under the point
    is green. Pixels the photo does not cover count as green, so the map data still decides there.
    Returns None when no picture could be fetched; the caller then places trees from the map data alone.
    """
    w, s, e, n = frame.bbox_lonlat(margin=20.0)

    def span(z: int):
        x0, y1 = _merc_tiles(w, s, z)
        x1, y0 = _merc_tiles(e, n, z)
        return int(math.floor(x0)), int(math.floor(y0)), int(math.floor(x1)), int(math.floor(y1))

    z = ZOOM
    x0, y0, x1, y1 = span(z)
    if (x1 - x0 + 1) * (y1 - y0 + 1) > MAX_PICTURES:
        z = ZOOM - 1
        x0, y0, x1, y1 = span(z)
    cells = [(tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)]
    if len(cells) > 4 * MAX_PICTURES:
        log("  ! Area too large for the satellite photo check: trees placed from the map data alone")
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        pictures = list(pool.map(lambda c: _fetch_picture(z, *c), cells))
    if not any(p is not None for p in pictures):
        log("  ! Satellite photo unavailable: trees placed from the map data alone")
        return None

    cols, rows = (x1 - x0 + 1) * TILE_PX, (y1 - y0 + 1) * TILE_PX
    rgb = np.zeros((rows, cols, 3), dtype=np.int16)
    known = np.zeros((rows, cols), dtype=bool)
    for (tx, ty), pic in zip(cells, pictures):
        if pic is None or pic.shape[:2] != (TILE_PX, TILE_PX):
            continue
        r0, c0 = (ty - y0) * TILE_PX, (tx - x0) * TILE_PX
        rgb[r0:r0 + TILE_PX, c0:c0 + TILE_PX] = pic
        known[r0:r0 + TILE_PX, c0:c0 + TILE_PX] = True

    exg = 2 * rgb[..., 1] - rgb[..., 0] - rgb[..., 2]
    green = (exg >= EXG_MIN) | ~known
    log(f"  · Satellite photo check: {int((green & known).sum()):,} of {int(known.sum()):,} pixels green")

    def test(xy):
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        lon, lat = frame.to_lonlat(xy[:, 0], xy[:, 1])
        tx, ty = _merc_tiles(lon, lat, z)
        col = np.clip(np.floor((np.asarray(tx) - x0) * TILE_PX).astype(int), 0, cols - 1)
        row = np.clip(np.floor((np.asarray(ty) - y0) * TILE_PX).astype(int), 0, rows - 1)
        return green[row, col]

    return test
