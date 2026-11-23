"""
Building geometry: height resolution and vectorised extrusion.

Heights come from, in order of trust: an explicit ``height`` tag, the number of
levels, and finally an estimate from the building type and footprint size.
Small, near-rectangular houses get a pitched (gabled) roof, everything else a
flat one. The whole mesh is built with array operations - no per-building
Python loop - so thousands of buildings take a fraction of a second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import geopandas as gpd
import numpy as np
import shapely

from .geo import polygonal, signed_area_2d
from .mesh import Mesh, quad_strip

LEVEL_HEIGHT = 3.1          # metres per storey
MIN_BUILDING_HEIGHT = 2.6
FOUNDATION = 0.4            # walls start this far below the lowest ground point

# eave height (m) used when neither height nor levels are known
KIND_EAVE = {
    "house": 5.5, "detached": 5.5, "semidetached_house": 5.5, "residential": 5.5, "duplex": 5.8, "terrace": 5.8,
    "bungalow": 3.8, "cabin": 3.2, "farm": 5.0, "farm_auxiliary": 4.5, "barn": 5.0, "stable": 4.0,
    "apartments": 12.0, "dormitory": 9.5, "hotel": 12.0, "office": 9.0, "commercial": 7.5, "retail": 6.5,
    "supermarket": 6.5, "industrial": 8.0, "warehouse": 8.0, "factory": 9.0, "manufacture": 9.0, "hangar": 9.0,
    "school": 8.0, "kindergarten": 4.5, "college": 10.0, "university": 12.0, "hospital": 14.0, "civic": 9.0,
    "public": 8.0, "government": 9.0, "church": 9.0, "chapel": 6.0, "cathedral": 20.0, "mosque": 12.0,
    "temple": 10.0, "stadium": 18.0, "silo": 18.0, "tower": 20.0, "garage": 3.0, "garages": 3.0, "carport": 2.8,
    "shed": 2.8, "hut": 2.6, "service": 3.5, "kiosk": 2.8, "greenhouse": 3.5, "roof": 3.0, "shelter": 3.0,
    "parking": 8.0, "static_caravan": 3.0, "container": 2.8, "transformer_tower": 6.0,
}
# types that are drawn as open canopies (a slab on top of nothing)
CANOPY_KINDS = {"roof", "carport", "shelter"}
HOUSE_KINDS = {"house", "detached", "semidetached_house", "residential", "duplex", "bungalow", "cabin", "farm",
               "terrace", "yes", "garage", "garages", "barn", "stable", "shed", "hut", "static_caravan"}
PITCHED = {"gabled", "hipped", "gambrel", "half-hipped", "saltbox", "pitched", "side_hipped", "round", "mansard",
           "hip", "gable"}
FLAT_ROOFS = {"flat", "dome", "skillion", "onion", "cone", "pyramidal", "spire", "cupola"}
MIN_FOOTPRINT_AREA = 6.0


@dataclass
class Heights:
    eave: np.ndarray       # (n,) wall height in metres
    ridge: np.ndarray      # (n,) extra roof height in metres (0 = flat roof)
    estimated: np.ndarray  # (n,) bool: height was guessed rather than mapped


def _hash01(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Deterministic pseudo-random value in [0, 1) from coordinates (stable between runs)."""
    v = np.sin(x * 12.9898 + y * 78.233) * 43758.5453
    return v - np.floor(v)


def resolve_heights(b: gpd.GeoDataFrame) -> Heights:
    """Wall and roof heights (metres, real-world, before any vertical exaggeration)."""
    geoms = np.asarray(b.geometry.values, dtype=object)
    area = shapely.area(geoms)
    cen = shapely.centroid(geoms)
    jitter = 0.9 + 0.2 * _hash01(shapely.get_x(cen), shapely.get_y(cen))

    height = b["height"].to_numpy(dtype=float)
    levels = b["levels"].to_numpy(dtype=float)
    roof_tag = b["roof_height"].to_numpy(dtype=float)
    kind = b["kind"].to_numpy(dtype=str)
    shape = b["roof_shape"].to_numpy(dtype=str)

    short_side = np.sqrt(np.maximum(area, 1.0))        # rough width, refined by the gable test later
    est_eave = np.array([KIND_EAVE.get(k, np.nan) for k in kind], dtype=float)
    by_area = np.select(
        [area < 25, area < 60, area < 250, area < 1000, area < 5000],
        [2.6, 3.0, 5.5, 6.5, 8.0], default=10.0,
    )
    est_eave = np.where(np.isnan(est_eave), by_area, est_eave) * jitter
    est_eave = np.where(np.isnan(est_eave), 3.0, est_eave)

    pitched = np.array([s in PITCHED for s in shape])
    flat_tagged = np.array([s in FLAT_ROOFS for s in shape])
    house_like = np.array([k in HOUSE_KINDS for k in kind]) & (area >= 25) & (area <= 320) & (shape == "")
    wants_pitch = (pitched | house_like) & ~flat_tagged

    roof_h = np.where(~np.isnan(roof_tag), roof_tag, np.clip(0.28 * short_side, 1.2, 4.5))
    roof_h = np.where(wants_pitch, roof_h, 0.0)

    have_height = ~np.isnan(height)
    have_levels = ~np.isnan(levels) & ~have_height

    # total height above ground
    top = np.where(have_height, height, np.where(have_levels, levels * LEVEL_HEIGHT + roof_h, est_eave + roof_h))
    top = np.maximum(top, MIN_BUILDING_HEIGHT)
    eave = top - roof_h
    # keep walls a believable minimum, shrinking the roof if the mapped height is very low
    too_low = eave < 2.4
    eave = np.where(too_low, np.minimum(top, 2.4), eave)
    roof_h = np.where(too_low, np.maximum(top - eave, 0.0), roof_h)
    return Heights(eave=eave, ridge=roof_h, estimated=~(have_height | have_levels))


# --------------------------------------------------------------------------
# Mesh construction
# --------------------------------------------------------------------------


@dataclass
class BuildingGeometry:
    mesh: Mesh | None
    footprints: np.ndarray        # (P,) Polygons actually built (oriented counter-clockwise)
    heights_above_ground: np.ndarray   # (P,) top of the roof above the lowest ground point (scene metres)
    n_gabled: int = 0


def _ring_edges(parts: np.ndarray):
    rings, ring_part = shapely.get_rings(parts, return_index=True)
    coords, ring_idx = shapely.get_coordinates(rings, return_index=True)
    same = ring_idx[:-1] == ring_idx[1:]
    a = coords[:-1][same]
    b = coords[1:][same]
    pid = ring_part[ring_idx[:-1][same]]
    return a, b, pid


def _triangulate(polys: np.ndarray):
    """Constrained Delaunay triangles of polygons -> ((T, 3, 2) coords, (T,) polygon index)."""
    if len(polys) == 0:
        return np.zeros((0, 3, 2)), np.zeros(0, dtype=int)
    tris = shapely.constrained_delaunay_triangles(polys)
    parts, idx = shapely.get_parts(tris, return_index=True)
    if len(parts) == 0:
        return np.zeros((0, 3, 2)), np.zeros(0, dtype=int)
    coords = shapely.get_coordinates(parts).reshape(-1, 4, 2)[:, :3]
    return coords, idx


def build_buildings(b: gpd.GeoDataFrame, heights: Heights, ground_z: Callable[[np.ndarray, np.ndarray], np.ndarray],
                    exag: float, tint: np.ndarray | None = None) -> BuildingGeometry:
    """
    Extrude building footprints into a single triangle mesh.

    ``ground_z(x, y)`` gives the scene height of the terrain; roof and wall
    heights are multiplied by ``exag`` (the vertical exaggeration).
    """
    empty = BuildingGeometry(None, np.asarray([], dtype=object), np.zeros(0))
    if len(b) == 0:
        return empty

    geoms = np.asarray(b.geometry.values, dtype=object)
    row_of_part_src = np.arange(len(geoms))
    parts, src = shapely.get_parts(geoms, return_index=True)
    row = row_of_part_src[src]
    area = shapely.area(parts)
    keep = area >= MIN_FOOTPRINT_AREA
    parts, row = parts[keep], row[keep]
    if len(parts) == 0:
        return empty
    parts = shapely.orient_polygons(parts, exterior_cw=False)

    eave = heights.eave[row]
    ridge = heights.ridge[row]
    min_h = b["min_height"].to_numpy(dtype=float)[row]
    kind = b["kind"].to_numpy(dtype=str)[row]
    P = len(parts)

    # ---- ground level under each footprint (lowest sampled point) ----------------
    coords, cidx = shapely.get_coordinates(parts, return_index=True)
    zg = ground_z(coords[:, 0], coords[:, 1]).astype(np.float64)
    z0 = np.full(P, np.inf)
    np.minimum.at(z0, cidx, zg)
    cen = shapely.centroid(parts)
    z0 = np.minimum(z0, ground_z(shapely.get_x(cen), shapely.get_y(cen)))

    # ---- gabled roof candidates: simple, near-rectangular footprints ----------------
    gable = ridge > 0
    n_holes = shapely.get_num_interior_rings(parts)
    mrr = shapely.minimum_rotated_rectangle(parts)
    mrr_ok = shapely.get_type_id(mrr) == 3
    mrr_area = np.where(mrr_ok, shapely.area(mrr), 1.0)
    fill = shapely.area(parts) / np.maximum(mrr_area, 1e-6)
    gc = np.zeros((P, 2))
    gu = np.zeros((P, 2))
    gn = np.zeros((P, 2))
    gw = np.ones(P)
    gl = np.ones(P)
    cand = np.where(gable & mrr_ok & (n_holes == 0) & (fill >= 0.78))[0]
    if len(cand):
        mc = shapely.get_coordinates(mrr[cand]).reshape(-1, 5, 2)[:, :4]
        e01 = mc[:, 1] - mc[:, 0]
        e12 = mc[:, 2] - mc[:, 1]
        l01 = np.linalg.norm(e01, axis=1)
        l12 = np.linalg.norm(e12, axis=1)
        long_is_01 = l01 >= l12
        long_len = np.where(long_is_01, l01, l12)
        short_len = np.where(long_is_01, l12, l01)
        u = np.where(long_is_01[:, None], e01, e12) / np.maximum(long_len[:, None], 1e-9)
        ok = (short_len >= 3.0) & (long_len / np.maximum(short_len, 1e-9) >= 1.12)
        idx = cand[ok]
        gc[idx] = mc[ok].mean(axis=1)
        gu[idx] = u[ok]
        gn[idx] = np.column_stack([-u[ok][:, 1], u[ok][:, 0]])
        gw[idx] = short_len[ok] / 2.0
        gl[idx] = long_len[ok]
        gable_ok = np.zeros(P, dtype=bool)
        gable_ok[idx] = True
        # ridge scales with the real width so wide buildings keep a sensible pitch
        ridge = np.where(gable_ok, np.minimum(ridge, 0.6 * gw), 0.0)
        gable = gable_ok
    else:
        gable = np.zeros(P, dtype=bool)
        ridge = np.zeros(P)
    # A building that wanted a pitched roof but failed the shape test gets a flat one at half the roof height
    top_flat = eave + 0.5 * heights.ridge[row] * (~gable)
    wall_top_z = z0 + np.where(gable, eave, top_flat) * exag
    ridge_s = ridge * exag
    floor_z = np.where(min_h > 0, z0 + min_h * exag, z0 - FOUNDATION)
    canopy = np.array([k in CANOPY_KINDS for k in kind])
    floor_z = np.where(canopy, wall_top_z - 0.4 * max(exag, 1.0), floor_z)

    def roof_z(x, y, pid):
        t = (x - gc[pid, 0]) * gn[pid, 0] + (y - gc[pid, 1]) * gn[pid, 1]
        frac = np.clip(1.0 - np.abs(t) / gw[pid], 0.0, 1.0)
        return wall_top_z[pid] + ridge_s[pid] * frac * gable[pid]

    # ---- walls -------------------------------------------------------------------
    a, b_, pid = _ring_edges(parts)
    if gable.any():
        ta = (a[:, 0] - gc[pid, 0]) * gn[pid, 0] + (a[:, 1] - gc[pid, 1]) * gn[pid, 1]
        tb = (b_[:, 0] - gc[pid, 0]) * gn[pid, 0] + (b_[:, 1] - gc[pid, 1]) * gn[pid, 1]
        cross = gable[pid] & (ta * tb < 0)
        denom = np.where(cross, ta - tb, 1.0)
        m = a + (b_ - a) * (ta / denom)[:, None]
        A = np.concatenate([a, m[cross]])
        B = np.concatenate([np.where(cross[:, None], m, b_), b_[cross]])
        PID = np.concatenate([pid, pid[cross]])
    else:
        A, B, PID = a, b_, pid
    z_bot = floor_z[PID]
    zt_a = roof_z(A[:, 0], A[:, 1], PID)
    zt_b = roof_z(B[:, 0], B[:, 1], PID)
    ok = (np.maximum(zt_a, zt_b) - z_bot > 0.05) & (np.linalg.norm(B - A, axis=1) > 1e-4)
    A, B, PID, z_bot, zt_a, zt_b = A[ok], B[ok], PID[ok], z_bot[ok], zt_a[ok], zt_b[ok]
    E = len(A)
    wall_v, wall_f = quad_strip(A, B, z_bot, z_bot, zt_a, zt_b)
    wall_pid = np.concatenate([PID, PID])

    # ---- roofs --------------------------------------------------------------------
    flat_idx = np.where(~gable)[0]
    tri_c, tri_p = _triangulate(parts[flat_idx])
    roof_pid_flat = flat_idx[tri_p]

    roof_tris, roof_pids = [tri_c], [roof_pid_flat]
    n_gabled = int(gable.sum())
    if n_gabled:
        gi = np.where(gable)[0]
        L = gl[gi] + 4.0
        c, u, n = gc[gi], gu[gi], gn[gi]
        p1, p2 = c - u * L[:, None], c + u * L[:, None]
        left = np.stack([p1, p2, p2 + n * L[:, None], p1 + n * L[:, None], p1], axis=1)
        right = np.stack([p1, p2, p2 - n * L[:, None], p1 - n * L[:, None], p1], axis=1)
        for rect in (left, right):
            half = polygonal(shapely.intersection(parts[gi], shapely.polygons(rect)))
            ok_h = np.array([h is not None for h in half], dtype=bool)
            hp, hsrc = shapely.get_parts(half[ok_h], return_index=True)
            hp_pid = gi[np.where(ok_h)[0][hsrc]]
            sel = shapely.area(hp) > 1e-3
            tc, tp = _triangulate(hp[sel])
            roof_tris.append(tc)
            roof_pids.append(hp_pid[sel][tp])
    tri_xy = np.concatenate(roof_tris) if roof_tris else np.zeros((0, 3, 2))
    tri_pid = np.concatenate(roof_pids) if roof_pids else np.zeros(0, dtype=int)
    if len(tri_xy):
        flip = signed_area_2d(tri_xy) < 0
        tri_xy = np.where(flip[:, None, None], tri_xy[:, ::-1], tri_xy)
        roof_v = np.empty((len(tri_xy), 3, 3))
        roof_v[..., :2] = tri_xy
        roof_v[..., 2] = roof_z(tri_xy[..., 0], tri_xy[..., 1], tri_pid[:, None].repeat(3, axis=1))
        roof_f = np.arange(len(tri_xy) * 3).reshape(-1, 3)
    else:
        roof_v = np.zeros((0, 3, 3))
        roof_f = np.zeros((0, 3), dtype=int)

    verts = np.concatenate([wall_v, roof_v.reshape(-1, 3)]).astype(np.float32)
    faces = np.concatenate([wall_f, roof_f + E * 4]).astype(np.int32)
    face_pid = np.concatenate([wall_pid, tri_pid])
    if tint is None:
        tint_f = np.ones(len(faces), dtype=np.float32)
    else:
        tint_f = tint[row][face_pid].astype(np.float32)
    mesh = Mesh(name="buildings", role="building", vertices=verts, faces=faces, tint=tint_f, flat=True,
                casts_shadow=True, receives_shadow=True)
    above = wall_top_z + ridge_s - z0
    return BuildingGeometry(mesh=mesh, footprints=parts, heights_above_ground=above, n_gabled=n_gabled)


def building_tint(b: gpd.GeoDataFrame) -> np.ndarray:
    """Subtle deterministic brightness variation per building, so blocks do not read as one blob."""
    cen = shapely.centroid(np.asarray(b.geometry.values, dtype=object))
    return (0.93 + 0.13 * _hash01(shapely.get_x(cen) * 0.37, shapely.get_y(cen) * 0.61)).astype(np.float32)
