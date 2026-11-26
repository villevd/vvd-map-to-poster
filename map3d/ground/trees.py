"""Tree placement and low-poly tree crowns."""

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry.base import BaseGeometry
from .terrain import ZFunc, _boundaries


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
               avoid: BaseGeometry | None, rng: np.random.Generator, half: float,
               on_green=None) -> np.ndarray:
    """
    Positions of all trees: mapped ones plus a procedural scatter in forests and (sparser) in parks.
    The scatter fades out smoothly across each outline (see ``feathered_points``). ``on_green``, when given,
    maps points to a bool array and only the True ones keep a tree (bare ground in the photo gets none).
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
    if len(allp) and on_green is not None:
        allp = allp[np.asarray(on_green(allp), dtype=bool)]
    if len(allp) > max_trees:
        allp = allp[rng.choice(len(allp), max_trees, replace=False)]
    return allp


# Crown archetypes: (share, height scale, width scale, sides, rings).
# Each ring is (height fraction, radius fraction), bottom to top. Blunt, stepped and
# rounded profiles give varied silhouettes instead of one needle-sharp cone.
TREE_VARIANTS = 5    # colour variants a tree can pick from in the viewer

_TREE_KINDS = (
    (0.40, 1.00, 1.05, 10, ((0.10, 1.00), (0.40, 0.84), (0.62, 0.64), (0.82, 0.44), (0.97, 0.24))),  # spruce: tiers, blunt top
    (0.15, 1.10, 0.80, 8, ((0.12, 1.00), (0.50, 0.74), (0.78, 0.48), (0.95, 0.26))),                # fir: narrow and taller
    (0.30, 0.85, 1.15, 12, ((0.28, 0.82), (0.50, 1.00), (0.72, 1.00), (0.90, 0.66), (1.00, 0.30))),  # oak: round crown
    (0.10, 1.10, 0.65, 8, ((0.15, 0.80), (0.45, 0.95), (0.75, 0.85), (0.93, 0.55), (1.00, 0.30))),  # poplar: rounded column
    (0.05, 0.45, 1.50, 9, ((0.20, 1.00), (0.60, 1.05), (0.85, 0.75), (1.00, 0.35))),                # shrub: low and wide
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

    Returns ``(vertices, faces, face_tint, crown_heights, positions, face_variant)``. The last
    two follow the order trees were emitted in (grouped by kind). ``face_variant`` is a per-face
    index in ``0..TREE_VARIANTS-1``: the viewer picks a tree colour from it, so each tree can differ.
    """
    n = len(pos)
    if n == 0:
        return (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), np.zeros(0), np.zeros(0), np.zeros((0, 2)),
                np.zeros(0, dtype=np.uint8))
    ground = zfunc(pos[:, 0], pos[:, 1]).astype(np.float64)
    h_real = rng.uniform(5.0, 11.0, n)
    kind = rng.choice(len(_TREE_KINDS), size=n, p=[k[0] for k in _TREE_KINDS])

    verts, faces, tint, tops, placed, variant = [], [], [], [], [], []
    off = 0
    for idx, (_, hs, ws, sides, profile) in enumerate(_TREE_KINDS):
        sel = kind == idx
        m = int(sel.sum())
        if not m:
            continue
        h = h_real[sel] * hs * exag
        r = 0.20 * h_real[sel] * ws          # crown radius: slim, like real spruce, fir and poplar
        prof = np.asarray(profile, dtype=np.float64)
        # Extra ring sunk below the surface so the crown stays attached on slopes and under the tree's base.
        prof = np.vstack([[-0.08, prof[0, 1]], prof])
        v, f, tris = _crown(pos[sel], ground[sel], h, r, sides, prof, rng)
        verts.append(v)
        faces.append(f + off)
        tint.append(np.repeat(rng.uniform(0.85, 1.12, m), tris))
        variant.append(np.repeat(rng.integers(0, TREE_VARIANTS, m), tris).astype(np.uint8))
        tops.append(h)
        placed.append(pos[sel])
        off += len(v)
    return (np.concatenate(verts), np.concatenate(faces).astype(np.int32), np.concatenate(tint),
            np.concatenate(tops), np.concatenate(placed), np.concatenate(variant))
