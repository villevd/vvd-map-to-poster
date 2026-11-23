"""Road and rail surfaces, draped over the terrain, plus bridges."""

import numpy as np
import shapely
from ..geo import polygonal, signed_area_2d
from ..mesh import quad_strip
from shapely.geometry.base import BaseGeometry
from .terrain import ROAD_ORDER, ROAD_WIDTH, ZFunc


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
