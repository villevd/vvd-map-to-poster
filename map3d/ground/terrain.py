"""Terrain meshes and the water carve, from the elevation grid."""

import numpy as np
import shapely
from ..geo import signed_area_2d
from ..mesh import Mesh, vertex_normals
from ..terrain import Dem
from scipy import ndimage
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from typing import Callable


ZFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]

# Real-world road widths (metres); scaled up on large tiles so they stay legible.
ROAD_WIDTH = {"motorway": 18.0, "primary": 15.0, "secondary": 12.0, "tertiary": 10.0, "residential": 8.5, "service": 5.5}
ROAD_ORDER = ["motorway", "primary", "secondary", "tertiary", "residential", "service"]   # drawn on top first


# --------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------


def burn_water(dem: Dem, water: BaseGeometry | None, recess: float) -> Dem:
    """
    Flatten the elevation grid inside water bodies (public DEMs are noisy over
    water) and sink it slightly so shores read as banks.
    """
    if water is None or water.is_empty:
        return dem
    X, Y = dem.coordinates()
    inside = shapely.contains_xy(water, X.ravel(), Y.ravel()).reshape(X.shape)
    if not inside.any():
        return dem
    z = dem.z.astype(np.float64)
    sigma = max(2.0, 40.0 / dem.step)
    num = ndimage.gaussian_filter(np.where(inside, z, 0.0), sigma)
    den = ndimage.gaussian_filter(inside.astype(np.float64), sigma)
    smooth = np.where(den > 1e-3, num / np.maximum(den, 1e-3), z)
    out = np.where(inside, np.minimum(z, smooth) - recess, z)
    return Dem(dem.x0, dem.y0, dem.step, out.astype(np.float32), dem.source)


def _boundaries(geom: BaseGeometry):
    b = shapely.boundary(geom)
    return b if b is not None and not b.is_empty else None


def conforming_triangulation(half: float, step: float, classes: list[tuple[str, BaseGeometry]],
                             shadow: BaseGeometry | None):
    """
    Triangulate the square tile so that triangle edges follow the outlines of
    ``classes`` (and ``shadow``). Returns ``(points (N,2), faces (T,3),
    class_index (T,) -1 = plain ground, in_shadow (T,) bool)``.
    """
    tile = box(-half, -half, half, half)
    lines = [tile.exterior]
    for _, g in classes:
        bd = _boundaries(g)
        if bd is not None:
            lines.append(bd)
    if shadow is not None and not shadow.is_empty:
        bd = _boundaries(shadow)
        if bd is not None:
            lines.append(bd)
    dense = shapely.segmentize(np.asarray(lines, dtype=object), step)
    pts = shapely.get_coordinates(dense)
    pts = np.clip(pts, -half, half)
    key = np.round(pts / 0.05).astype(np.int64)
    _, uniq = np.unique(key, axis=0, return_index=True)
    pts = pts[np.sort(uniq)]

    # Interior points on a jittered grid, kept clear of the outlines so those stay Delaunay edges
    g = 2.0 * step
    ax = np.arange(-half + g, half - g * 0.5, g)
    gx, gy = np.meshgrid(ax, ax)
    rng = np.random.default_rng(7)
    jit = rng.uniform(-0.18, 0.18, size=(gx.size, 2)) * g
    grid = np.column_stack([gx.ravel(), gy.ravel()]) + jit
    d, _ = cKDTree(pts).query(grid)
    grid = grid[d > 0.9 * step]
    allp = np.concatenate([pts, grid])

    tri = Delaunay(allp).simplices.astype(np.int32)
    area = signed_area_2d(allp[tri])
    tri = np.where((area < 0)[:, None], tri[:, ::-1], tri)
    tri = tri[np.abs(area) > 1e-9]

    cen = allp[tri].mean(axis=1)
    cls = np.full(len(tri), -1, dtype=np.int32)
    for k, (_, g_) in enumerate(classes):
        todo = np.where(cls < 0)[0]
        if not len(todo):
            break
        inside = shapely.contains_xy(g_, cen[todo, 0], cen[todo, 1])
        cls[todo[inside]] = k
    in_shadow = np.zeros(len(tri), dtype=bool)
    if shadow is not None and not shadow.is_empty:
        in_shadow = shapely.contains_xy(shadow, cen[:, 0], cen[:, 1])
    return allp, tri, cls, in_shadow, pts


def terrain_meshes(half: float, step: float, classes: list[tuple[str, BaseGeometry]], shadow: BaseGeometry | None,
                   ground_z: ZFunc):
    """Terrain split into one mesh per land-cover role. Also returns the tile-edge points for the skirt."""
    pts, tri, cls, shd, boundary_pts = conforming_triangulation(half, step, classes, shadow)
    verts = np.column_stack([pts, ground_z(pts[:, 0], pts[:, 1])]).astype(np.float32)
    normals = vertex_normals(verts, tri)
    roles = [r for r, _ in classes]
    meshes = []
    for k, role in enumerate(["ground"] + roles):
        mask = cls == (k - 1)
        if not mask.any():
            continue
        m = Mesh(name=f"terrain:{role}", role=role, vertices=verts, faces=tri[mask], normals=normals,
                 shadowed=shd[mask], flat=False, casts_shadow=False, receives_shadow=True)
        meshes.append(m.compact())
    return meshes, boundary_pts
