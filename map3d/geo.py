"""
Local metric frame used by every 3D layer.

All 3D geometry lives in a flat, metric, north-up frame centred on the map
point: +x is east, +y is north, +z is up. The frame is a transverse Mercator
projection whose origin is exactly the requested centre, so distortion is
negligible for the few-kilometre tiles a 3D model covers.
"""

from __future__ import annotations

import math

import numpy as np
import pyproj
import shapely
from geopandas import GeoDataFrame
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry


class LocalFrame:
    """Square tile of ``2 * half`` metres centred on (lat, lon)."""

    def __init__(self, lat: float, lon: float, half: float):
        self.lat = float(lat)
        self.lon = float(lon)
        self.half = float(half)
        self.crs = pyproj.CRS.from_proj4(
            f"+proj=tmerc +lat_0={self.lat} +lon_0={self.lon} +k=1 +x_0=0 +y_0=0 "
            "+datum=WGS84 +units=m +no_defs"
        )
        self._fwd = pyproj.Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self._inv = pyproj.Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    @property
    def side(self) -> float:
        return 2.0 * self.half

    def to_xy(self, lon, lat):
        """lon/lat (degrees) -> local metres."""
        return self._fwd.transform(lon, lat)

    def to_lonlat(self, x, y):
        """Local metres -> lon/lat (degrees)."""
        return self._inv.transform(x, y)

    def bbox_lonlat(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        """(west, south, east, north) covering the tile plus ``margin`` metres."""
        h = self.half + margin
        xs = np.array([-h, 0, h, h, h, 0, -h, -h], dtype=float)
        ys = np.array([-h, -h, -h, 0, h, h, h, 0], dtype=float)
        lon, lat = self.to_lonlat(xs, ys)
        return float(np.min(lon)), float(np.min(lat)), float(np.max(lon)), float(np.max(lat))

    def tile_polygon(self, margin: float = 0.0) -> BaseGeometry:
        h = self.half + margin
        return box(-h, -h, h, h)

    def project(self, gdf: GeoDataFrame) -> GeoDataFrame:
        """Reproject a lon/lat GeoDataFrame into the local frame (a copy)."""
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf.to_crs(self.crs)


def polygonal(geoms) -> np.ndarray:
    """
    Element-wise: keep (multi)polygons as they are, reduce geometry collections
    to the union of their polygonal members, and turn everything else
    (points, lines, empties) into ``None``. The output has the input's length.
    """
    arr = np.asarray(list(geoms), dtype=object)
    out = np.empty(len(arr), dtype=object)
    for i, g in enumerate(arr):
        res = None
        if g is not None and not g.is_empty:
            if g.geom_type in ("Polygon", "MultiPolygon"):
                res = g
            elif g.geom_type == "GeometryCollection":
                polys = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
                if polys:
                    res = shapely.union_all(polys)
        out[i] = res
    return out


def clean_polygons(geoms, clip: BaseGeometry | None = None) -> np.ndarray:
    """
    Return only valid, non-empty (multi)polygons, optionally clipped to ``clip``.

    Invalid rings are repaired with ``make_valid``; anything that is not
    polygonal afterwards (points, lines left over from a repair) is dropped.
    """
    arr = np.asarray(list(geoms), dtype=object)
    if arr.size == 0:
        return arr
    arr = shapely.make_valid(arr)
    if clip is not None:
        arr = shapely.intersection(arr, clip)
    res = polygonal(arr)
    return res[np.array([g is not None for g in res], dtype=bool)]


def explode_polygons(geoms) -> np.ndarray:
    """Flatten MultiPolygons into single Polygons."""
    arr = np.asarray(list(geoms), dtype=object)
    if arr.size == 0:
        return arr
    return shapely.get_parts(arr)


def signed_area_2d(tri: np.ndarray) -> np.ndarray:
    """Signed area of triangles shaped (T, 3, 2); positive for counter-clockwise."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))


def meters_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of longitude, metres per degree of latitude)."""
    lat_r = math.radians(lat)
    return 111_320.0 * math.cos(lat_r), 110_574.0
