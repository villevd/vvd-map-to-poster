"""Model frame and base meshes."""

import numpy as np
from ..mesh import Mesh, concat, quad_strip
from .terrain import ZFunc


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
