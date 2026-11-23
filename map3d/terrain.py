"""
Terrain elevation provider.

Elevation comes from the AWS Open Data "Terrain Tiles" (Terrarium encoding).
They are a global, key-free blend of the best public DEMs - SRTM, NRCan CDEM
in Canada, USGS 3DEP in the USA, EU-DEM and others - so the same code path
gives good relief anywhere on Earth. Tiles are cached on disk.
"""

from __future__ import annotations

import io
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

from .geo import LocalFrame

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT = "city_map_poster-3d (terrain tiles)"
TILE_PX = 256
MAX_ZOOM = 14          # beyond this the source DEMs add no real detail
MAX_TILES = 64


class Dem:
    """
    Regular elevation grid in the local metric frame.

    ``z[j, i]`` is the elevation (metres above sea level) at
    ``(x0 + i * step, y0 + j * step)``.
    """

    def __init__(self, x0: float, y0: float, step: float, z: np.ndarray, source: str):
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.step = float(step)
        self.z = np.asarray(z, dtype=np.float32)
        self.source = source

    @classmethod
    def flat(cls, frame: LocalFrame, level: float = 0.0, source: str = "flat (no terrain)") -> "Dem":
        step = frame.side / 8.0
        n = 11
        x0 = -frame.half - step
        return cls(x0, x0, step, np.full((n, n), level, dtype=np.float32), source)

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape  # type: ignore[return-value]

    def sample(self, x, y) -> np.ndarray:
        """Bilinear elevation at local coordinates (arrays broadcast together)."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        fx = (x - self.x0) / self.step
        fy = (y - self.y0) / self.step
        out = ndimage.map_coordinates(self.z, [fy.ravel(), fx.ravel()], order=1, mode="nearest")
        return out.reshape(x.shape).astype(np.float32)

    def coordinates(self) -> tuple[np.ndarray, np.ndarray]:
        """2-D arrays (X, Y) of the grid node positions."""
        ny, nx = self.z.shape
        xs = self.x0 + np.arange(nx) * self.step
        ys = self.y0 + np.arange(ny) * self.step
        return np.meshgrid(xs, ys)

    def stats(self, half: float) -> tuple[float, float]:
        """(min, max) elevation inside the tile."""
        X, Y = self.coordinates()
        inside = (np.abs(X) <= half) & (np.abs(Y) <= half)
        if not inside.any():
            return float(self.z.min()), float(self.z.max())
        return float(self.z[inside].min()), float(self.z[inside].max())


def _pixel_xy(lon, lat, zoom: int):
    """Global web-mercator pixel coordinates (float) at a zoom level."""
    n = TILE_PX * 2 ** zoom
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.clip(np.asarray(lat, dtype=np.float64), -85.0511, 85.0511)
    px = (lon + 180.0) / 360.0 * n
    py = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * n
    return px, py


def choose_zoom(lat: float, target_res: float) -> int:
    """Smallest zoom whose ground resolution is at least ``target_res`` metres/pixel."""
    res0 = 156543.03392 * math.cos(math.radians(lat))
    z = math.ceil(math.log2(res0 / max(target_res, 0.1)))
    return int(min(MAX_ZOOM, max(6, z)))


def decode_terrarium(png_bytes: bytes) -> np.ndarray:
    """Terrarium PNG -> elevation in metres (float32, 256x256)."""
    rgb = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"), dtype=np.float32)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def _download_tile(z: int, x: int, y: int, cache_dir: Path, session: requests.Session) -> np.ndarray | None:
    path = cache_dir / f"{z}_{x}_{y}.png"
    absent = path.with_suffix(".missing")          # outside the dataset: asked for once, never again
    if absent.exists():
        return None
    if path.exists():
        try:
            return decode_terrarium(path.read_bytes())
        except Exception:  # corrupt cache entry: fall through and re-download
            path.unlink(missing_ok=True)
    url = TERRARIUM_URL.format(z=z, x=x, y=y)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                data = decode_terrarium(r.content)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.content)
                os.replace(tmp, path)
                return data
            if r.status_code in (403, 404):      # outside the dataset
                absent.touch()
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def grid_step_for(side: float) -> float:
    """Elevation-grid spacing (m): fine enough for relief, coarse enough to stay light."""
    return float(min(25.0, max(3.0, side / 500.0)))


def fetch_dem(frame: LocalFrame, cache_dir: str | os.PathLike = "cache", log=print, step: float | None = None) -> Dem:
    """
    Download (or load from cache) Terrarium tiles covering the tile and
    resample them onto a regular local grid. Raises RuntimeError when no tile
    could be fetched at all.
    """
    cache = Path(cache_dir) / "terrain"
    cache.mkdir(parents=True, exist_ok=True)

    step = grid_step_for(frame.side) if step is None else float(step)
    margin = 4 * step
    zoom = choose_zoom(frame.lat, target_res=max(4.0, step * 0.8))

    west, south, east, north = frame.bbox_lonlat(margin=margin + step)
    px0, py1 = _pixel_xy(west, south, zoom)
    px1, py0 = _pixel_xy(east, north, zoom)
    tx0, tx1 = int(px0 // TILE_PX), int(px1 // TILE_PX)
    ty0, ty1 = int(py0 // TILE_PX), int(py1 // TILE_PX)
    # Pathologically large requests (huge tiles at high zoom): drop the zoom
    while (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TILES and zoom > 6:
        zoom -= 1
        px0, py1 = _pixel_xy(west, south, zoom)
        px1, py0 = _pixel_xy(east, north, zoom)
        tx0, tx1 = int(px0 // TILE_PX), int(px1 // TILE_PX)
        ty0, ty1 = int(py0 // TILE_PX), int(py1 // TILE_PX)

    coords = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda c: _download_tile(zoom, c[0], c[1], cache, session), coords))
    got = sum(r is not None for r in results)
    if got == 0:
        raise RuntimeError("no elevation tiles could be downloaded")

    nx_t, ny_t = tx1 - tx0 + 1, ty1 - ty0 + 1
    mosaic = np.full((ny_t * TILE_PX, nx_t * TILE_PX), np.nan, dtype=np.float32)
    for (tx, ty), tile in zip(coords, results):
        if tile is not None:
            r0, c0 = (ty - ty0) * TILE_PX, (tx - tx0) * TILE_PX
            mosaic[r0:r0 + TILE_PX, c0:c0 + TILE_PX] = tile
    if np.isnan(mosaic).any():
        # Fill holes (missing tiles) with the nearest valid elevation
        idx = ndimage.distance_transform_edt(np.isnan(mosaic), return_distances=False, return_indices=True)
        mosaic = mosaic[tuple(idx)]

    # Resample the mosaic onto the local metric grid
    n = int(math.ceil((frame.side + 2 * margin) / step)) + 1
    x0 = -frame.half - margin
    xs = x0 + np.arange(n) * step
    X, Y = np.meshgrid(xs, xs)
    lon, lat = frame.to_lonlat(X, Y)
    px, py = _pixel_xy(lon, lat, zoom)
    cols = px - tx0 * TILE_PX - 0.5
    rows = py - ty0 * TILE_PX - 0.5
    z = ndimage.map_coordinates(mosaic, [rows.ravel(), cols.ravel()], order=1, mode="nearest").reshape(X.shape)
    # The 8-bit packed source is stair-steppy on gentle slopes; a light blur removes the terraces
    z = ndimage.gaussian_filter(z, sigma=0.8)

    res = 156543.03392 * math.cos(math.radians(frame.lat)) / 2 ** zoom
    source = f"AWS Terrain Tiles (z{zoom}, ~{res:.1f} m/px, {got}/{len(coords)} tiles)"
    log(f"✓ Terrain: {source}")
    return Dem(x0, x0, step, z, source)
