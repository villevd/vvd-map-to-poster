"""Shadow footprints for the baked poster renderer."""

import numpy as np
import shapely
from ..geo import polygonal
from shapely.geometry.base import BaseGeometry


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
