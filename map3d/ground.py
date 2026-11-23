"""
Everything that sits on the ground: the terrain mesh itself, land-cover
patches, roads, rails, bridges, trees and the model's base frame.

The terrain is *one* conforming Delaunay triangulation. Land-cover polygon
edges are densified and inserted as vertices, interior grid points are kept
away from them, so triangle edges follow the polygon outlines. Each triangle
is then classified by its centroid. The result has crisp water / park / forest
shores, no cracks between layers, and smooth shading across the whole surface.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import shapely
from scipy import ndimage
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .geo import polygonal, signed_area_2d
from .mesh import Mesh, concat, quad_strip, vertex_normals
from .terrain import Dem

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


# --------------------------------------------------------------------------
# Roads, rails, bridges
# --------------------------------------------------------------------------


def road_polygons(lines_by_class: dict[str, np.ndarray], scale: float, tile: BaseGeometry,
                  water: BaseGeometry | None) -> dict[str, BaseGeometry]:
    """Buffer centre-lines into one polygon per road class, without overlap between classes."""
    out: dict[str, BaseGeometry] = {}
    above: BaseGeometry | None = None
    for cls in ROAD_ORDER:
        lines = lines_by_class.get(cls)
        if lines is None or len(lines) == 0:
            continue
        buf = shapely.buffer(lines, ROAD_WIDTH[cls] * scale / 2.0, cap_style="round", quad_segs=2)
        poly = shapely.intersection(shapely.union_all(buf), tile)
        if water is not None and not water.is_empty:
            poly = shapely.difference(poly, water)
        if above is not None and not above.is_empty:
            poly = shapely.difference(poly, above)
        poly = polygonal([poly])[0]
        if poly is None:
            continue
        out[cls] = poly
        above = poly if above is None else shapely.union(above, poly)
    return out


def drape_polygons(polys: np.ndarray, step: float, zfunc: ZFunc, lift: float):
    """
    Triangulate polygons and drape them on the terrain (vertices sampled from
    ``zfunc`` and raised by ``lift``). Returns (vertices, faces).
    """
    polys = polys[[p is not None and not p.is_empty for p in polys]] if len(polys) else polys
    if not len(polys):
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    parts = shapely.get_parts(polys)
    parts = parts[shapely.area(parts) > 0.5]
    if not len(parts):
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    parts = shapely.segmentize(parts, step)
    tris = shapely.constrained_delaunay_triangles(parts)
    tp = shapely.get_parts(tris)
    if not len(tp):
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    xy = shapely.get_coordinates(tp).reshape(-1, 4, 2)[:, :3]
    flip = signed_area_2d(xy) < 0
    xy = np.where(flip[:, None, None], xy[:, ::-1], xy)
    z = zfunc(xy[..., 0].ravel(), xy[..., 1].ravel()).reshape(-1, 3) + lift
    v = np.concatenate([xy, z[..., None]], axis=2).reshape(-1, 3)
    f = np.arange(len(v), dtype=np.int32).reshape(-1, 3)
    return v, f


def split_shadow(poly: BaseGeometry, shadow: BaseGeometry | None):
    """(lit, shadowed) parts of a polygon; shadowed is None without shadows."""
    if shadow is None or shadow.is_empty:
        return poly, None
    lit = polygonal([shapely.difference(poly, shadow)])[0]
    dark = polygonal([shapely.intersection(poly, shadow)])[0]
    return lit, dark


def bridge_meshes(lines: np.ndarray, widths: np.ndarray, step: float, zfunc: ZFunc, exag: float,
                  lift: float) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """
    Raised decks for bridge segments: a ramp up from the terrain at each end,
    a flat span in the middle, thin side walls so they read as slabs.
    Returns [(vertices, faces, part)] with part in {"deck", "side"}.
    """
    decks_v, decks_f, side_v, side_f = [], [], [], []
    for line, w in zip(lines, widths):
        if line is None or line.is_empty or line.geom_type != "LineString" or line.length < 4.0:
            continue
        length = line.length
        poly = polygonal([shapely.buffer(line, w / 2.0, cap_style="flat", join_style="round", quad_segs=2)])[0]
        if poly is None:
            continue
        poly = shapely.get_parts(poly)
        poly = poly[np.argmax(shapely.area(poly))]
        poly = shapely.segmentize(poly, max(step, 3.0))
        poly = shapely.orient_polygons(poly, exterior_cw=False)

        z_a = float(zfunc(np.array([line.coords[0][0]]), np.array([line.coords[0][1]]))[0])
        z_b = float(zfunc(np.array([line.coords[-1][0]]), np.array([line.coords[-1][1]]))[0])
        clearance = min(6.0 * max(exag, 1.0), 0.18 * length * max(exag, 1.0))
        ramp = max(8.0, 0.2 * length)

        def deck_z(x, y, _line=line, _za=z_a, _zb=z_b, _len=length, _c=clearance, _ramp=ramp):
            pts = shapely.points(x, y)
            s = shapely.line_locate_point(_line, pts)
            u = np.clip(np.minimum(s, _len - s) / _ramp, 0.0, 1.0)
            ease = u * u * (3.0 - 2.0 * u)
            return _za + (_zb - _za) * (s / _len) + _c * ease + lift

        tris = shapely.constrained_delaunay_triangles(poly)
        tp = shapely.get_parts(tris)
        if not len(tp):
            continue
        xy = shapely.get_coordinates(tp).reshape(-1, 4, 2)[:, :3]
        flip = signed_area_2d(xy) < 0
        xy = np.where(flip[:, None, None], xy[:, ::-1], xy)
        z = deck_z(xy[..., 0].ravel(), xy[..., 1].ravel()).reshape(-1, 3)
        v = np.concatenate([xy, z[..., None]], axis=2).reshape(-1, 3)
        decks_v.append(v)
        decks_f.append(np.arange(len(v), dtype=np.int32).reshape(-1, 3))

        ring = shapely.get_coordinates(shapely.get_exterior_ring(poly))
        a, b = ring[:-1], ring[1:]
        za_top = deck_z(a[:, 0], a[:, 1])
        zb_top = deck_z(b[:, 0], b[:, 1])
        thick = 1.6 * max(exag, 1.0)
        sv, sf = quad_strip(a, b, za_top - thick, zb_top - thick, za_top, zb_top)
        side_v.append(sv)
        side_f.append(sf)

    out: list[tuple[np.ndarray, np.ndarray, str]] = []
    for part, vs, fs in (("deck", decks_v, decks_f), ("side", side_v, side_f)):
        if vs:
            offs = np.cumsum([0] + [len(v) for v in vs[:-1]])
            out.append((np.concatenate(vs), np.concatenate([f + o for f, o in zip(fs, offs)]), part))
    return out


# --------------------------------------------------------------------------
# Trees
# --------------------------------------------------------------------------


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _outline_tree(geom: BaseGeometry, half: float, step: float) -> cKDTree | None:
    """
    KD-tree over the densified outline of ``geom``. Stretches that run along the
    tile edge are left out: the forest continues past the tile, so it should not fade there.
    """
    bd = _boundaries(geom)
    if bd is None:
        return None
    pts = shapely.get_coordinates(shapely.segmentize(bd, step))
    pts = pts[~(np.abs(np.abs(pts) - half) < 1e-3).any(axis=1)]
    return cKDTree(pts) if len(pts) else None


def feathered_points(geom: BaseGeometry | None, spacing: float, feather: float, half: float,
                     rng: np.random.Generator) -> np.ndarray:
    """
    Jittered-grid tree candidates around a (multi)polygon, thinned by a smooth ramp
    across its outline instead of stopping on it: every candidate ``feather / 2``
    inside is kept, half of those on the outline are, and none ``feather / 2``
    outside are. Trees thin out gradually across the edge rather than being cut off.
    """
    if geom is None or geom.is_empty or spacing <= 0:
        return np.zeros((0, 2))
    reach = 0.5 * feather
    minx, miny, maxx, maxy = geom.bounds
    minx, miny = max(minx - reach, -half), max(miny - reach, -half)
    maxx, maxy = min(maxx + reach, half), min(maxy + reach, half)
    if maxx <= minx or maxy <= miny:
        return np.zeros((0, 2))
    gx, gy = np.meshgrid(np.arange(minx, maxx, spacing), np.arange(miny, maxy, spacing))
    pts = np.column_stack([gx.ravel(), gy.ravel()]) + rng.uniform(-0.45, 0.45, size=(gx.size, 2)) * spacing
    on_tile = (np.abs(pts) <= half).all(axis=1)
    pts = pts[on_tile]

    tree = _outline_tree(geom, half, max(1.0, 0.5 * spacing))
    dist = np.full(len(pts), np.inf) if tree is None else tree.query(pts)[0]
    inside = shapely.contains_xy(geom, pts[:, 0], pts[:, 1])
    signed = np.where(inside, dist, -dist)                 # > 0 inside, < 0 outside
    keep = rng.random(len(pts)) < _smoothstep((signed + reach) / feather)
    return pts[keep]


def plan_trees(forest: BaseGeometry | None, parks: BaseGeometry | None, mapped: np.ndarray, max_trees: int,
               avoid: BaseGeometry | None, rng: np.random.Generator, half: float) -> np.ndarray:
    """
    Positions of all trees: mapped ones plus a procedural scatter in forests and (sparser) in parks.
    The scatter fades out smoothly across each outline (see ``feathered_points``).
    """
    f_area = forest.area if forest is not None and not forest.is_empty else 0.0
    p_area = parks.area if parks is not None and not parks.is_empty else 0.0
    budget = max(max_trees - len(mapped), 0)
    sp_f = max(7.0, float(np.sqrt(f_area / max(budget * 0.8, 1)))) if f_area else 0.0
    sp_p = max(15.0, float(np.sqrt(p_area / max(budget * 0.2, 1)))) if p_area else 0.0
    # The fade spans about six tree spacings, so it reads as a thinning stand rather than a single row.
    f_pts = feathered_points(forest, sp_f, 6.0 * sp_f, half, rng)
    p_pts = feathered_points(parks, sp_p, 6.0 * sp_p, half, rng)
    if len(p_pts) and forest is not None and not forest.is_empty:
        # Park candidates that drift into a forest would add a bump of extra trees inside it.
        p_pts = p_pts[~shapely.contains_xy(forest, p_pts[:, 0], p_pts[:, 1])]
    allp = np.concatenate([f_pts, p_pts, np.asarray(mapped).reshape(-1, 2)])
    if len(allp) and avoid is not None and not avoid.is_empty:
        allp = allp[~shapely.contains_xy(avoid, allp[:, 0], allp[:, 1])]
    if len(allp) > max_trees:
        allp = allp[rng.choice(len(allp), max_trees, replace=False)]
    return allp


# Crown archetypes: (share, height scale, width scale, sides, rings).
# Each ring is (height fraction, radius fraction), bottom to top. Blunt, stepped and
# rounded profiles give varied silhouettes instead of one needle-sharp cone.
_TREE_KINDS = (
    (0.40, 1.00, 1.05, 7, ((0.10, 1.00), (0.40, 0.84), (0.62, 0.64), (0.82, 0.44), (0.97, 0.24))),  # spruce: tiers, blunt top
    (0.15, 1.10, 0.80, 6, ((0.12, 1.00), (0.50, 0.74), (0.78, 0.48), (0.95, 0.26))),                # fir: narrow and taller
    (0.30, 0.85, 1.15, 8, ((0.28, 0.82), (0.50, 1.00), (0.72, 1.00), (0.90, 0.66), (1.00, 0.30))),  # oak: round crown
    (0.10, 1.10, 0.65, 6, ((0.15, 0.80), (0.45, 0.95), (0.75, 0.85), (0.93, 0.55), (1.00, 0.30))),  # poplar: rounded column
    (0.05, 0.45, 1.50, 7, ((0.20, 1.00), (0.60, 1.05), (0.85, 0.75), (1.00, 0.35))),                # shrub: low and wide
)


def _crown(xy: np.ndarray, ground: np.ndarray, h: np.ndarray, r: np.ndarray, sides: int,
           profile: np.ndarray, rng: np.random.Generator):
    """
    One crown per row of ``xy``: stacked rings of ``sides`` vertices following ``profile``,
    closed by a flat top cap. Each tree gets its own stretch, lean and rotation, and every
    vertex a small radial jitter, so no two crowns are identical. Returns (vertices, faces, tris per tree).
    """
    m = len(xy)
    zf, rf = profile[:, 0], profile[:, 1]
    R = len(zf)
    ang = rng.uniform(0, 2 * np.pi, m)[:, None] + np.arange(sides)[None, :] * (2 * np.pi / sides)
    jit = rng.uniform(0.85, 1.12, (m, R, sides))
    stretch = rng.uniform(0.85, 1.15, (m, 2))
    lean = rng.uniform(-0.12, 0.12, (m, 2)) * r[:, None]
    rad = r[:, None, None] * rf[None, :, None] * jit                       # (m, R, S)
    x = xy[:, 0, None, None] + lean[:, 0, None, None] * zf[None, :, None] + stretch[:, 0, None, None] * rad * np.cos(ang)[:, None, :]
    y = xy[:, 1, None, None] + lean[:, 1, None, None] * zf[None, :, None] + stretch[:, 1, None, None] * rad * np.sin(ang)[:, None, :]
    z = np.broadcast_to(ground[:, None, None] + h[:, None, None] * zf[None, :, None], x.shape)
    ring = np.stack([x, y, z], axis=-1).reshape(m, R * sides, 3)
    top = np.stack([xy[:, 0] + lean[:, 0], xy[:, 1] + lean[:, 1], ground + h * zf[-1]], axis=-1)[:, None, :]
    verts = np.concatenate([ring, top], axis=1)                            # (m, R*S + 1, 3)
    per = R * sides + 1

    tpl = []
    for k in range(R - 1):
        for s in range(sides):
            a, b = k * sides + s, k * sides + (s + 1) % sides
            c, d = (k + 1) * sides + s, (k + 1) * sides + (s + 1) % sides
            tpl += [(a, b, d), (a, d, c)]
    last = (R - 1) * sides
    for s in range(sides):
        tpl.append((last + s, last + (s + 1) % sides, R * sides))          # top cap around the centre vertex
    tpl = np.asarray(tpl)
    faces = (np.arange(m)[:, None, None] * per + tpl[None]).reshape(-1, 3)
    return verts.reshape(-1, 3), faces, len(tpl)


def tree_geometry(pos: np.ndarray, zfunc: ZFunc, exag: float, rng: np.random.Generator):
    """
    Low-poly trees of several kinds (see ``_TREE_KINDS``), each crown a little different.

    Returns ``(vertices, faces, face_tint, crown_heights, positions)``; the last
    two follow the order trees were emitted in (grouped by kind).
    """
    n = len(pos)
    if n == 0:
        return (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), np.zeros(0), np.zeros(0), np.zeros((0, 2)))
    ground = zfunc(pos[:, 0], pos[:, 1]).astype(np.float64)
    h_real = rng.uniform(6.0, 13.0, n)
    kind = rng.choice(len(_TREE_KINDS), size=n, p=[k[0] for k in _TREE_KINDS])

    verts, faces, tint, tops, placed = [], [], [], [], []
    off = 0
    for idx, (_, hs, ws, sides, profile) in enumerate(_TREE_KINDS):
        sel = kind == idx
        m = int(sel.sum())
        if not m:
            continue
        h = h_real[sel] * hs * exag
        r = 0.26 * h_real[sel] * ws
        prof = np.asarray(profile, dtype=np.float64)
        # Extra ring sunk below the surface so the crown stays attached on slopes and under the tree's base.
        prof = np.vstack([[-0.08, prof[0, 1]], prof])
        v, f, tris = _crown(pos[sel], ground[sel], h, r, sides, prof, rng)
        verts.append(v)
        faces.append(f + off)
        tint.append(np.repeat(rng.uniform(0.85, 1.12, m), tris))
        tops.append(h)
        placed.append(pos[sel])
        off += len(v)
    return (np.concatenate(verts), np.concatenate(faces).astype(np.int32), np.concatenate(tint),
            np.concatenate(tops), np.concatenate(placed))


# --------------------------------------------------------------------------
# Shadows (baked into the PNG renderer)
# --------------------------------------------------------------------------


def shadow_union(footprints: np.ndarray, heights: np.ndarray, light_bearing: float, light_elevation: float,
                 simplify: float = 1.0, min_area: float = 12.0) -> BaseGeometry | None:
    """
    Ground shadows of buildings for a sun at ``light_bearing`` (compass degrees, the
    direction the light comes *from*) and ``light_elevation``. Each shadow is the convex
    hull of the footprint and the footprint shifted along the shadow direction.
    """
    if not len(footprints):
        return None
    az = np.radians(light_bearing)
    el = np.radians(max(light_elevation, 8.0))
    # horizontal shadow length per metre of height
    dx, dy = -np.sin(az) / np.tan(el), -np.cos(az) / np.tan(el)
    coords, idx = shapely.get_coordinates(footprints, return_index=True)
    off = np.column_stack([dx * heights[idx], dy * heights[idx]])
    both = np.concatenate([coords, coords + off])
    both_idx = np.concatenate([idx, idx])
    order = np.argsort(both_idx, kind="stable")      # multipoints() wants increasing indices
    hulls = shapely.convex_hull(shapely.multipoints(both[order], indices=both_idx[order]))
    union = shapely.simplify(shapely.union_all(hulls), simplify)
    parts = shapely.get_parts(union)
    parts = parts[shapely.area(parts) >= min_area]
    if not len(parts):
        return None
    return polygonal([shapely.union_all(parts)])[0]


# --------------------------------------------------------------------------
# Base frame (plinth + skirt)
# --------------------------------------------------------------------------


def frame_meshes(half: float, boundary_pts: np.ndarray, ground_z: ZFunc, ledge: float, plinth: float,
                 sides_on: tuple = (True, True, True, True)) -> list[Mesh]:
    """
    The model's body: vertical skirt under the terrain edge (role ``skirt``),
    and a plinth - ledge ring, outer walls and underside - (role ``frame``).
    """
    tol = 0.08
    skirt_v, skirt_f = [], []
    sides = (
        (np.abs(boundary_pts[:, 1] + half) < tol, 0, False),   # south, walking east
        (np.abs(boundary_pts[:, 0] - half) < tol, 1, False),   # east, walking north
        (np.abs(boundary_pts[:, 1] - half) < tol, 0, True),    # north, walking west
        (np.abs(boundary_pts[:, 0] + half) < tol, 1, True),    # west, walking south
    )
    for (mask, axis, reverse), on in zip(sides, sides_on):
        if not on:
            continue
        p = boundary_pts[mask]
        p = p[np.argsort(p[:, axis])]
        if reverse:
            p = p[::-1]
        a, b = p[:-1], p[1:]
        za, zb = ground_z(a[:, 0], a[:, 1]), ground_z(b[:, 0], b[:, 1])
        zeros = np.zeros(len(a))
        v, f = quad_strip(a, b, zeros, zeros, za, zb)
        skirt_v.append(v)
        skirt_f.append(f)
    skirt = concat("skirt", "skirt", list(zip(skirt_v, skirt_f)), flat=True, receives_shadow=False)
    if not any(sides_on):
        return [m for m in (skirt,) if m is not None]

    H = half
    OUT = half + ledge
    t = plinth
    corners_in = np.array([[-H, -H], [H, -H], [H, H], [-H, H]])
    corners_out = np.array([[-OUT, -OUT], [OUT, -OUT], [OUT, OUT], [-OUT, OUT]])
    # The long frame pieces are cut into short segments: the static renderer sorts triangles by
    # their centre, which only works when a triangle is small compared with the model.
    n_seg = 64
    ts = np.linspace(0.0, 1.0, n_seg + 1)[:, None]
    parts = []
    for k in range(4):
        if not sides_on[k]:
            continue
        i, j = k, (k + 1) % 4
        out = corners_out[i] + (corners_out[j] - corners_out[i]) * ts      # (n+1, 2) along the outer edge
        inn = corners_in[i] + (corners_in[j] - corners_in[i]) * ts
        # ledge: top surface at z = 0, normals up
        quad_v = np.stack([out[:-1], out[1:], inn[1:], inn[:-1]], axis=1)  # (n, 4, 2)
        v = np.concatenate([quad_v, np.zeros((n_seg, 4, 1))], axis=2).reshape(-1, 3)
        base = (np.arange(n_seg) * 4)[:, None]
        f = np.concatenate([base + [0, 1, 2], base + [0, 2, 3]]).reshape(-1, 3)
        parts.append((v, f))
        # outer wall from -t up to 0, facing outwards
        zeros = np.zeros(n_seg)
        parts.append(quad_strip(out[:-1], out[1:], zeros - t, zeros - t, zeros, zeros))
    if all(sides_on):      # underside (normals down)
        v = np.column_stack([corners_out, np.full(4, -t)])
        parts.append((v, np.array([[0, 2, 1], [0, 3, 2]])))
    frame = concat("frame", "frame", parts, flat=True, receives_shadow=False)
    return [m for m in (skirt, frame) if m is not None]
