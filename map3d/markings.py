"""
Road markings painted on the road surface, in the North American convention used in Quebec and Ontario:

- two-way motorways, primary and secondary roads: a double solid yellow centre line and white edge lines;
- two-way tertiary roads: a single solid yellow centre line and white edge lines on the main ones only (none);
- one-way roads of those classes: white edge lines only (no centre line);
- residential and service roads, roundabouts: no paint.

Markings are flat polygons clipped to their own road surface, so they stop where the road does (junctions
included). They are meshes with the roles ``marking_yellow`` and ``marking_white``, drawn on top of the road.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from .ground import ROAD_WIDTH

YELLOW = "marking_yellow"
WHITE = "marking_white"
DOUBLE_CENTRE = ("motorway", "primary", "secondary")    # two-way: double solid yellow
SINGLE_CENTRE = ("tertiary",)                            # two-way: single solid yellow
EDGE_LINES = ("motorway", "primary", "secondary")        # white edge lines, one-way or two-way

PAINT = 0.5           # width of one painted line (metres, before the road scale); real paint is ~0.15 m, too thin to see from a model's distance
DOUBLE_GAP = 0.35     # clear gap between the two yellow lines of a double centre line (from the centre, each side)
EDGE_INSET = 1.0      # edge line sits this far inside the road edge (metres)


def _union(parts: list[BaseGeometry]) -> BaseGeometry | None:
    parts = [p for p in parts if p is not None and not p.is_empty]
    return shapely.union_all(parts) if parts else None


def _band(lines: BaseGeometry, inner: float, outer: float) -> BaseGeometry:
    """Strip between ``inner`` and ``outer`` metres from the centre line, on both sides."""
    if inner <= 0:
        return shapely.buffer(lines, outer, cap_style="flat", quad_segs=2)
    return shapely.difference(shapely.buffer(lines, outer, cap_style="flat", quad_segs=2),
                             shapely.buffer(lines, inner, cap_style="flat", quad_segs=2))


def marking_polygons(two_way: dict[str, np.ndarray], one_way: dict[str, np.ndarray], scale: float,
                     tile: BaseGeometry, road_polys: dict[str, BaseGeometry]) -> dict[str, BaseGeometry]:
    """
    Paint polygons per role (``marking_yellow``, ``marking_white``), in the tile's local metres.

    ``two_way`` / ``one_way`` map a road class to its centre lines (roundabouts already left out);
    ``scale`` is the road scale used for the road polygons; ``road_polys`` clips each class to its surface.
    """
    paint = PAINT * scale
    yellow: list[BaseGeometry] = []
    white: list[BaseGeometry] = []

    def keep(role_list: list[BaseGeometry], geom: BaseGeometry | None, cls: str) -> None:
        if geom is None or geom.is_empty:
            return
        clip = road_polys.get(cls)
        if clip is None or clip.is_empty:
            return
        role_list.append(shapely.intersection(shapely.intersection(geom, clip), tile))

    for cls, lines in two_way.items():
        line = _union(list(lines)) if len(lines) else None
        if line is None:
            continue
        if cls in DOUBLE_CENTRE:
            keep(yellow, _band(line, DOUBLE_GAP * scale, DOUBLE_GAP * scale + paint), cls)
        elif cls in SINGLE_CENTRE:
            keep(yellow, _band(line, 0.0, paint / 2), cls)

    # Edge lines on every road of the classes that have them, two-way or one-way
    for lines_by_way in (two_way, one_way):
        for cls, lines in lines_by_way.items():
            line = _union(list(lines)) if cls in EDGE_LINES and len(lines) else None
            if line is None:
                continue
            half = ROAD_WIDTH[cls] * scale / 2.0 - EDGE_INSET * scale
            keep(white, _band(line, half - paint, half), cls)

    out = {}
    if yellow:
        out[YELLOW] = _union(yellow)
    if white:
        out[WHITE] = _union(white)
    return {k: v for k, v in out.items() if v is not None and not v.is_empty}
