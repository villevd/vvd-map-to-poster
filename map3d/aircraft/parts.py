"""Aircraft geometry: fuselage, wings, engines, rotors, built from the specs."""

import math
import numpy as np
from typing import NamedTuple
from .spec import BODY, ENGINE, GEAR, PROP, ROTOR, Spec, WING


# ---------------------------------------------------------------------------------------------------------
# Geometry primitives. Every mesh is in the aircraft frame: x forward, y left, z up (metres).
# ---------------------------------------------------------------------------------------------------------

class Part(NamedTuple):
    name: str
    color: tuple
    verts: np.ndarray       # (N, 3) float64
    faces: np.ndarray       # (M, 3) int
    smooth: bool            # True: curved surface, averaged vertex normals; False: flat faces


def _hex(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _orient(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Turn every triangle so its normal points away from the centre of its shape."""
    if len(f) == 0:
        return f
    f = f.copy()
    c = v.mean(axis=0)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    flip = np.einsum("ij,ij->i", n, tri.mean(axis=1) - c) < 0
    f[flip] = f[flip][:, [0, 2, 1]]
    return f


def _loft(xs, ry, rz, zc, seg: int):
    """Surface of revolution-like tube: one elliptical ring per station along x."""
    n = len(xs)
    th = np.linspace(0.0, 2.0 * np.pi, seg, endpoint=False)
    y = ry[:, None] * np.cos(th)[None, :]
    z = zc[:, None] + rz[:, None] * np.sin(th)[None, :]
    x = np.repeat(np.asarray(xs, float)[:, None], seg, axis=1)
    v = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    i = np.arange(n - 1)[:, None] * seg
    j = np.arange(seg)[None, :]
    a, b = i + j, i + (j + 1) % seg
    c, d = i + seg + j, i + seg + (j + 1) % seg
    f = np.concatenate([np.stack([a, c, d], -1).reshape(-1, 3), np.stack([a, d, b], -1).reshape(-1, 3)])
    return v, f


def _body_profile(length: float, nose: float, tail: float, n: int):
    """Stations along x (tail to nose, denser at both ends), cross-section scale, tail-cone coordinate."""
    t = (1.0 - np.cos(np.pi * np.linspace(0.0, 1.0, n))) / 2.0
    x = -length / 2.0 + length * t
    a = np.ones(n)
    tv = np.zeros(n)
    ln = nose * length
    m = x > length / 2.0 - ln
    u = (x[m] - (length / 2.0 - ln)) / ln
    a[m] = np.sqrt(np.clip(1.0 - u * u, 0.0, 1.0))
    lt = tail * length
    m = x < -length / 2.0 + lt
    v = (-length / 2.0 + lt - x[m]) / lt
    a[m] = np.sqrt(np.clip(1.0 - v * v, 0.0, 1.0))
    tv[m] = v
    return x, a, tv


def _fuselage(length, width, height, zc, nose=0.12, tail=0.25, upsweep=0.0, n=34, seg=20):
    """Fuselage (or nacelle) along x, centred on y = 0, with its axis at height zc."""
    x, a, tv = _body_profile(length, nose, tail, n)
    v, f = _loft(x, a * width / 2.0, a * height / 2.0, zc + upsweep * tv * tv, seg)
    return v, _orient(v, f)


def _nacelle(length, diameter, centre):
    v, f = _fuselage(length, diameter, diameter, 0.0, nose=0.3, tail=0.3, n=18, seg=16)
    return v + np.asarray(centre, float), f


def _cylinder(p0, p1, r, seg: int = 12):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    u = p1 - p0
    u = u / np.linalg.norm(u)
    helper = np.array([0.0, 0.0, 1.0]) if abs(u[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(u, helper)
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    th = 2.0 * np.pi * np.arange(seg) / seg
    ring = np.cos(th)[:, None] * e1 + np.sin(th)[:, None] * e2
    v = np.vstack([p0 + r * ring, p1 + r * ring, p0[None], p1[None]])
    j = np.arange(seg)
    j1 = (j + 1) % seg
    f = np.concatenate([
        np.stack([j, seg + j, seg + j1], -1), np.stack([j, seg + j1, j1], -1),
        np.stack([np.full(seg, 2 * seg), j1, j], -1), np.stack([np.full(seg, 2 * seg + 1), seg + j, seg + j1], -1),
    ])
    return v, _orient(v, f)


def _prism(quad, direction, thickness: float):
    """Convex quad extruded by ``thickness`` along ``direction`` (centred on the quad)."""
    q = np.asarray(quad, float)
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d) * (thickness / 2.0)
    v = np.vstack([q - d, q + d])                      # 0-3 one side, 4-7 the other
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6)]
    for i in range(4):
        j = (i + 1) % 4
        f += [(i, j, j + 4), (i, j + 4, i + 4)]
    f = np.array(f)
    return v, _orient(v, f)


def _box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                  (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)])
    return v, _orient(v, f)


def _blade(r_hub, r_tip, chord, thick, theta, axis, u1, u2):
    """Flat blade in the plane spanned by u1/u2 (normal to ``axis``), at angle theta."""
    d = np.cos(theta) * u1 + np.sin(theta) * u2
    t = -np.sin(theta) * u1 + np.cos(theta) * u2
    c2 = chord / 2.0
    quad = np.array([r_hub * d - c2 * t, r_hub * d + c2 * t, r_tip * d + c2 * t, r_tip * d - c2 * t])
    return _prism(quad, axis, thick)


def _translate(v, offset):
    return v + np.asarray(offset, float)


def _merge(chunks):
    vs, fs, off = [], [], 0
    for v, f in chunks:
        vs.append(v)
        fs.append(f + off)
        off += len(v)
    return np.vstack(vs), np.vstack(fs)


def _fixed_wing_parts(s: Spec) -> list[Part]:
    L, S, H, W = s.length, s.span, s.height, s.width
    FH = s.fuselage_h or W
    half = S / 2.0
    accent = _hex(s.accent)
    zc = s.gear + FH / 2.0                                    # fuselage axis
    sweep = s.sweep if s.sweep is not None else {"airliner": 27.0, "regional": 22.0}.get(s.category, 0.0)
    tan_s = math.tan(math.radians(sweep))
    dih = 5.0 if s.category in ("airliner", "regional") else 3.0
    tan_d = math.tan(math.radians(dih))
    c_root = 0.2 * L if s.category in ("airliner", "regional") else 0.14 * L
    c_tip = 0.045 * S
    x_le = L / 2.0 - s.wing_x * L
    z_root = s.gear + (0.8 if s.wing == "high" else 0.3) * FH
    wing_t = max(0.12, 0.09 * c_root)
    tail_t = max(0.12, 0.018 * L)

    body = _fuselage(L, W, FH, zc, nose=0.1 if s.wing == "high" else 0.12,
                     upsweep=0.3 if s.category in ("airliner", "regional") else 0.1)
    parts = [Part("fuselage", BODY, body[0], body[1], True)]

    # Wings: planform quads, one per side, plus winglets.
    wing_chunks = []
    for sign in (1.0, -1.0):
        yt = sign * half
        x_tip = x_le - half * tan_s
        z_tip = z_root + half * tan_d
        quad = [(x_le, 0.0, z_root), (x_tip, yt, z_tip), (x_tip - c_tip, yt, z_tip), (x_le - c_root, 0.0, z_root)]
        wing_chunks.append(_prism(quad, (0, 0, 1), wing_t))
        if s.winglets:
            wl = 0.07 * S
            wing_chunks.append(_box((x_tip - c_tip * 0.9, yt - 0.06, z_tip - 0.05),
                                    (x_tip - c_tip * 0.1, yt + 0.06, z_tip + wl)))
    parts.append(Part("wings", WING, *_merge(wing_chunks), False))

    # Tail: fin, horizontal stabilizers (conventional on the fuselage or T on top of the fin).
    fin_le = -L / 2.0 + 0.3 * L
    fin_root_z = zc + 0.9 * FH / 2.0
    fin_sweep = 35.0 if s.category in ("airliner", "regional") else 20.0
    fin_root_c = 0.22 * L if s.category in ("airliner", "regional") else 0.16 * L
    fin_tip_c = 0.09 * L if s.category in ("airliner", "regional") else 0.07 * L
    fin_tip_x = fin_le - (H - fin_root_z) * math.tan(math.radians(fin_sweep))
    fin_quad = [(fin_le, 0.0, fin_root_z), (fin_tip_x, 0.0, H), (fin_tip_x - fin_tip_c, 0.0, H),
                (fin_le - fin_root_c, 0.0, fin_root_z)]
    tail_chunks = [_prism(fin_quad, (0, 1, 0), tail_t)]
    stab_span = 0.36 * S if s.category in ("airliner", "regional") else 0.4 * S
    stab_root = 0.12 * L if s.category in ("airliner", "regional") else 0.1 * L
    stab_tip = 0.05 * L
    stab_sweep = 25.0 if s.category in ("airliner", "regional") else 0.0
    if s.tail == "t":
        zs = H - 0.3
        xs = fin_tip_x + 0.25 * fin_tip_c
    else:
        zs = zc + 0.1 * FH
        xs = -L / 2.0 + 0.14 * L
    for sign in (1.0, -1.0):
        tip = sign * stab_span / 2.0
        x_tip = xs - (stab_span / 2.0) * math.tan(math.radians(stab_sweep))
        quad = [(xs, 0.0, zs), (x_tip, tip, zs), (x_tip - stab_tip, tip, zs), (xs - stab_root, 0.0, zs)]
        tail_chunks.append(_prism(quad, (0, 0, 1), 0.12))
    parts.append(Part("tail", accent, *_merge(tail_chunks), False))

    # Engines.
    eng_chunks, eng_props = [], []
    if s.mount == "nose":
        xc = L / 2.0 + 0.02 - s.engine_len / 2.0
        eng_chunks.append(_nacelle(s.engine_len, s.engine_d, (xc, 0.0, zc)))
        if s.blades:
            eng_props.append((L / 2.0 + 0.04, 0.0, zc))
    else:
        for frac in s.engines:
            for sign in (1.0, -1.0):
                y_e = sign * frac * half
                le = x_le - abs(y_e) * tan_s
                z_w = z_root + abs(y_e) * tan_d
                if s.mount == "underwing":
                    xc = le - 0.15 * s.engine_len
                    zc_e = z_w - 0.55 * s.engine_d
                    eng_chunks.append(_nacelle(s.engine_len, s.engine_d, (xc, y_e, zc_e)))
                    eng_chunks.append(_box((xc - 0.3 * s.engine_len, y_e - 0.08 * s.engine_d, zc_e + 0.45 * s.engine_d),
                                           (xc + 0.3 * s.engine_len, y_e + 0.08 * s.engine_d, z_w - 0.02)))
                elif s.mount == "wing_front":
                    xc = le + 0.15 * s.engine_len
                    zc_e = z_w - 0.3 * s.engine_d
                    eng_chunks.append(_nacelle(s.engine_len, s.engine_d, (xc, y_e, zc_e)))
                    eng_props.append((xc + s.engine_len / 2.0 + 0.03 * s.engine_d, y_e, zc_e))
                elif s.mount == "rear":
                    xc = -L / 2.0 + 0.32 * L
                    yc = sign * (W / 2.0 + 0.2 * s.engine_d)
                    zc_e = zc - 0.25 * FH
                    eng_chunks.append(_nacelle(s.engine_len, s.engine_d, (xc, yc, zc_e)))
                    eng_chunks.append(_box((xc - 0.3 * s.engine_len, yc - 0.15 * s.engine_d, zc_e + 0.3 * s.engine_d),
                                           (xc + 0.3 * s.engine_len, yc + 0.15 * s.engine_d, zc_e + 0.5 * s.engine_d)))
    if eng_chunks:
        parts.append(Part("engines", ENGINE, *_merge(eng_chunks), True))

    # Propellers: blades plus a hub.
    if eng_props and s.blades:
        prop_chunks = []
        for (px, py, pz) in eng_props:
            for k in range(s.blades):
                th = np.pi / 2.0 + 2.0 * np.pi * k / s.blades
                bv, bf = _blade(0.12 * s.prop_r, s.prop_r, 0.16 * s.prop_r, 0.05, th, np.array([1.0, 0, 0]),
                                np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
                prop_chunks.append((_translate(bv, (px, py, pz)), bf))
            prop_chunks.append(_cylinder((px - 0.15, py, pz), (px + 0.05, py, pz), 0.12 * s.prop_r))
        parts.append(Part("propellers", PROP, *_merge(prop_chunks), False))

    # Landing gear: tricycle, wheels with axles and struts.
    r_w = s.wheel
    gear_chunks = []
    if r_w > 0:
        strut_r = max(0.03, 0.12 * r_w)
        main_x = (x_le - 0.3 * c_root) if s.wing == "low" else 0.05 * L
        main_y = 0.11 * S
        for sign in (1.0, -1.0):
            yg = sign * main_y
            top = (z_root + abs(yg) * tan_d - 0.05) if s.wing == "low" else (s.gear + 0.25)
            gear_chunks.append(_cylinder((main_x, yg, 2 * r_w - 0.05), (main_x, yg, top), strut_r))
            gear_chunks.append(_cylinder((main_x, yg - 0.2 * r_w, r_w), (main_x, yg + 0.2 * r_w, r_w), r_w, seg=14))
        nose_x = L / 2.0 - 0.15 * L
        gear_chunks.append(_cylinder((nose_x, 0.0, 1.6 * r_w), (nose_x, 0.0, s.gear + 0.2), strut_r))
        gear_chunks.append(_cylinder((nose_x, -0.15 * r_w, 0.8 * r_w), (nose_x, 0.15 * r_w, 0.8 * r_w),
                                     0.8 * r_w, seg=14))
        parts.append(Part("gear", GEAR, *_merge(gear_chunks), True))
    return parts


def _heli_parts(s: Spec) -> list[Part]:
    L, W, H = s.length, s.width, s.height
    R = s.span / 2.0
    FH = s.fuselage_h or W
    zc = s.gear + FH / 2.0
    body_len = 0.72 * L
    x_mid = L / 2.0 - body_len / 2.0
    body_v, body_f = _fuselage(body_len, W, FH, zc, nose=0.22, tail=0.35)
    parts = [Part("cabin", BODY, _translate(body_v, (x_mid, 0, 0)), body_f, True)]

    boom_v, boom_f = _cylinder((-L / 2.0, 0.0, zc + 0.05 * FH), (-L / 2.0 + 0.45 * L, 0.0, zc + 0.05 * FH),
                               0.06 * FH + 0.05, seg=14)
    parts.append(Part("tail_boom", BODY, boom_v, boom_f, True))

    z_r = H - 0.03
    mv, mf = _cylinder((0, 0, zc + 0.45 * FH), (0, 0, z_r), 0.07)
    parts.append(Part("mast", GEAR, mv, mf, True))
    rotor = []
    for th in (np.pi / 2.0, 3.0 * np.pi / 2.0):
        bv, bf = _blade(0.04 * R, R, 0.28, 0.05, th, np.array([0, 0, 1.0]),
                        np.array([1.0, 0, 0]), np.array([0, 1.0, 0]))
        rotor.append((_translate(bv, (0, 0, z_r)), bf))
    x_t = -L / 2.0 - 0.02
    z_t = zc + 0.3 * FH + 0.2
    tr_r = 0.085 * L
    for th in (np.pi / 2.0, 3.0 * np.pi / 2.0):
        bv, bf = _blade(0.0, tr_r, 0.22, 0.05, th, np.array([0, 1.0, 0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))
        rotor.append((_translate(bv, (x_t, 0, z_t)), bf))
    parts.append(Part("rotors", ROTOR, *_merge(rotor), False))

    skids = []
    for sign in (1.0, -1.0):
        y = sign * 0.42 * W
        skids.append(_box((-0.2 * L, y - 0.035, 0.0), (0.35 * L, y + 0.035, 0.09)))
        for xs in (-0.1 * L, 0.2 * L):
            skids.append(_cylinder((xs, y, 0.09), (xs, y, s.gear), 0.035))
    parts.append(Part("skids", GEAR, *_merge(skids), False))
    return parts


def build_parts(spec: Spec) -> list[Part]:
    if spec.category == "helicopter":
        return _heli_parts(spec)
    return _fixed_wing_parts(spec)
