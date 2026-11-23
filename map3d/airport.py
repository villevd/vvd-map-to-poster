"""
Airport for the 3D viewer: the runway from OpenStreetMap and the final approach a fake plane flies
down to land on it.

The runway is a real OpenStreetMap feature (``aeroway=runway``). The approach is a straight 3 degree
glide path along the runway centre line, ending in a rollout to the far end. The path is built here,
in the scene frame, so the viewer only has to move a plane along it.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import LineString

GLIDE_DEG = 3.0          # standard glide slope
APPROACH_M = 1200.0      # how far out the approach starts: about 60 m high at the 3 degree glide, so the descent stays in the model
CRUISE_MS = 70.0         # approach speed, about 135 knots
MIN_RUNWAY_M = 400.0     # shorter pieces are taxiway stubs or helipads, not the runway
STEP_M = 20.0            # spacing of the path points


def _longest_runway(runways) -> LineString | None:
    best = None
    for geom in runways or []:
        if geom is None:
            continue
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type != "LineString" or part.length < MIN_RUNWAY_M:
                continue
            if best is None or part.length > best.length:
                best = part
    return best


def build_airport(runways, ground_z, lift: float, exaggeration: float, log=print) -> dict | None:
    """
    Runway and landing path for the viewer, in three.js coordinates (x east, y up, z = -north).

    Args:
        runways: LineStrings of ``aeroway=runway`` features, scene metres
        ground_z: function (x, y) -> scene height of the ground (arrays in, arrays out)
        lift: height of the runway surface above the ground (scene units)
        exaggeration: vertical exaggeration; heights above the ground are scaled with it, as the buildings are
    Returns:
        A JSON-serialisable dict, or None when no runway is in the model.
    """
    run = _longest_runway(runways)
    if run is None:
        return None
    xy = np.asarray(run.coords)[:, :2]
    a, b = xy[0], xy[-1]
    # The threshold is the end farther from the model centre, so the plane comes in from outside the model
    if np.hypot(*a) < np.hypot(*b):
        a, b = b, a
    length = float(np.hypot(*(b - a)))
    u = (b - a) / length                  # landing direction, threshold -> far end
    o = -u                                # outward, along the approach

    d_app = np.arange(APPROACH_M, 0.0, -STEP_M)
    d_app = np.append(d_app, 0.0)
    t_run = np.linspace(0.0, length, max(2, int(length / STEP_M) + 1))
    d_all = np.concatenate([d_app, t_run[1:]])
    agl = np.concatenate([np.maximum(d_app, 0.0) * np.tan(np.radians(GLIDE_DEG)),
                          np.zeros(len(t_run) - 1)])
    px = a[0] + np.concatenate([o[0] * d_app, u[0] * t_run[1:]])
    py = a[1] + np.concatenate([o[1] * d_app, u[1] * t_run[1:]])
    base = np.asarray(ground_z(px, py), dtype=np.float64) + lift
    height = base + agl * exaggeration
    pts = np.column_stack([px, height, -py])
    # the runway itself, threshold to far end, for drawing the strip
    rx, ry = a[0] + u[0] * t_run, a[1] + u[1] * t_run
    rz = np.asarray(ground_z(rx, ry), dtype=np.float64) + lift
    runway = np.column_stack([rx, rz, -ry])
    log(f"✓ Airport: runway {length:.0f} m, glide path from {APPROACH_M:.0f} m out")
    return {
        "pts": np.round(pts, 2).ravel().tolist(),
        "runway": np.round(runway, 2).ravel().tolist(),
        "touchdown_m": round(APPROACH_M, 1),   # distance along the path where the wheels touch the runway
        "roll_m": round(length, 1),
        "cruise_ms": CRUISE_MS,
    }
