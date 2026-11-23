"""
Local aircraft library for the kiosk API.

Each entry is a low-poly GLB built from the type's dimensions and configuration
(engine count and placement, wing position, tail type, gear), so a 737 looks like
a 737 and a Dash 8 like a Dash 8 without any photogrammetry. The figures are
approximate, written from published general specifications: verify them before
anything public depends on them. A real model can replace a generated one by
dropping a GLB into the library folder and editing its catalog entry.

Axes (metres, glTF convention): nose along +X, up +Y, left wing towards -Z.
Origin: on the ground, under the middle of the fuselage (lowest point of the gear).

    python -m map3d.aircraft planes      # (re)build planes/*.glb and planes/catalog.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np

FORMAT = "maptoposter-aircraft-library"
FORMAT_VERSION = 1
AXES = "glTF: nose +X, up +Y, left wing -Z; metres; origin on the ground under the fuselage middle"

BODY = (0.95, 0.95, 0.94)
WING = (0.80, 0.82, 0.85)
ENGINE = (0.55, 0.58, 0.62)
GEAR = (0.20, 0.20, 0.22)
PROP = (0.12, 0.12, 0.13)
ROTOR = (0.14, 0.14, 0.15)


class LibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Spec:
    id: str
    codes: tuple[str, ...]          # ICAO type designators this model stands for
    name: str
    maker: str
    category: str                   # airliner | regional | turboprop | light | helicopter
    length: float                   # overall length (helicopters: body + boom)
    span: float                     # wingspan (helicopters: main rotor diameter)
    height: float                   # overall height on the ground
    width: float                    # fuselage width
    gear: float                     # height of the fuselage belly above the ground (helicopters: skid body gap)
    wing: str                       # "low" or "high"
    engines: tuple[float, ...]      # spanwise engine positions per side, fraction of the half span
    mount: str                      # underwing | wing_front | rear | nose | heli
    engine_d: float                 # nacelle / engine diameter
    engine_len: float               # nacelle length
    tail: str                       # "conventional" or "t"
    blades: int = 0                 # propeller blades (0 for jets)
    prop_r: float = 0.0             # propeller radius
    winglets: bool = False
    wheel: float = 0.5              # wheel radius
    fuselage_h: float | None = None  # fuselage height when not round
    sweep: float | None = None      # leading-edge sweep, degrees
    accent: str = "#1F3C88"         # livery accent on fins and stabilizers
    wing_x: float = 0.36            # wing root leading edge, fraction of the length aft of the nose
    approx: bool = True
    generic: bool = False           # a stand-in for types the library does not list


def _spec(**kw) -> Spec:
    return Spec(**kw)


SPECS: tuple[Spec, ...] = (
    # --- narrowbody jets -------------------------------------------------------------------------
    _spec(id="a319", codes=("A319",), name="Airbus A319", maker="Airbus", category="airliner",
          length=33.8, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.4, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a320", codes=("A320",), name="Airbus A320", maker="Airbus", category="airliner",
          length=37.6, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.5, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a321", codes=("A321",), name="Airbus A321", maker="Airbus", category="airliner",
          length=44.5, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.5, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a20n", codes=("A20N",), name="Airbus A320neo", maker="Airbus", category="airliner",
          length=37.6, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.0, engine_len=3.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a21n", codes=("A21N",), name="Airbus A321neo", maker="Airbus", category="airliner",
          length=44.5, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.0, engine_len=3.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a220", codes=("BCS3", "BCS1"), name="Airbus A220-300", maker="Airbus", category="airliner",
          length=38.7, span=35.1, height=11.5, width=3.7, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.8, engine_len=3.3, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b738", codes=("B737", "B738"), name="Boeing 737-800", maker="Boeing", category="airliner",
          length=39.5, span=35.8, height=12.5, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.55, engine_len=2.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b38m", codes=("B38M",), name="Boeing 737 MAX 8", maker="Boeing", category="airliner",
          length=39.5, span=35.9, height=12.3, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.1, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b39m", codes=("B39M",), name="Boeing 737 MAX 9", maker="Boeing", category="airliner",
          length=42.2, span=35.9, height=12.3, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.1, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b739", codes=("B739",), name="Boeing 737-900", maker="Boeing", category="airliner",
          length=42.1, span=34.3, height=12.5, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.55, engine_len=2.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b752", codes=("B752",), name="Boeing 757-200", maker="Boeing", category="airliner",
          length=47.3, span=38.1, height=13.6, width=3.8, gear=1.9, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.9, engine_len=3.6, tail="conventional", wheel=0.6),
    # --- widebody jets ----------------------------------------------------------------------------------
    _spec(id="b763", codes=("B763",), name="Boeing 767-300", maker="Boeing", category="airliner",
          length=54.9, span=47.6, height=15.8, width=5.03, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.7, engine_len=4.6, tail="conventional", wheel=0.7, sweep=31.5),
    _spec(id="b788", codes=("B788",), name="Boeing 787-8", maker="Boeing", category="airliner",
          length=56.7, span=60.1, height=17.0, width=5.77, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.8, engine_len=4.7, tail="conventional", wheel=0.7, sweep=32),
    _spec(id="b789", codes=("B789",), name="Boeing 787-9", maker="Boeing", category="airliner",
          length=62.8, span=60.1, height=17.0, width=5.77, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.8, engine_len=4.7, tail="conventional", wheel=0.7, sweep=32),
    _spec(id="b77w", codes=("B77W",), name="Boeing 777-300ER", maker="Boeing", category="airliner",
          length=73.9, span=64.8, height=18.5, width=6.2, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=3.3, engine_len=5.5, tail="conventional", winglets=True, wheel=0.7,
          sweep=31.6),
    _spec(id="b744", codes=("B744",), name="Boeing 747-400", maker="Boeing", category="airliner",
          length=70.7, span=64.4, height=19.4, width=6.5, gear=2.8, wing="low", engines=(0.36, 0.7),
          mount="underwing", engine_d=2.5, engine_len=4.6, tail="conventional", winglets=True, wheel=0.7,
          sweep=37.5),
    _spec(id="a333", codes=("A333",), name="Airbus A330-300", maker="Airbus", category="airliner",
          length=63.7, span=60.3, height=16.8, width=5.64, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.85, engine_len=4.8, tail="conventional", winglets=True, wheel=0.7,
          sweep=30),
    _spec(id="a359", codes=("A359",), name="Airbus A350-900", maker="Airbus", category="airliner",
          length=66.8, span=64.75, height=17.1, width=5.96, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.96, engine_len=5.2, tail="conventional", winglets=True, wheel=0.7,
          sweep=33),
    _spec(id="a388", codes=("A388",), name="Airbus A380-800", maker="Airbus", category="airliner",
          length=72.7, span=79.8, height=24.1, width=7.14, fuselage_h=8.41, gear=2.8, wing="low",
          engines=(0.28, 0.6), mount="underwing", engine_d=2.95, engine_len=5.0, tail="conventional",
          wheel=0.7, sweep=33.5),
    # --- regional jets -----------------------------------------------------------------------------------
    _spec(id="e170", codes=("E170",), name="Embraer E170", maker="Embraer", category="regional",
          length=29.9, span=26.0, height=8.5, width=2.8, gear=1.2, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.2, engine_len=2.5, tail="t", wheel=0.45, sweep=25),
    _spec(id="e75l", codes=("E75L", "E175"), name="Embraer E175", maker="Embraer", category="regional",
          length=31.7, span=28.7, height=9.7, width=2.8, gear=1.2, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.25, engine_len=2.6, tail="t", wheel=0.45, sweep=25),
    _spec(id="e190", codes=("E190",), name="Embraer E190", maker="Embraer", category="regional",
          length=36.2, span=28.7, height=10.7, width=3.0, gear=1.3, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.5, engine_len=2.9, tail="t", wheel=0.5, sweep=25),
    _spec(id="e195", codes=("E195",), name="Embraer E195", maker="Embraer", category="regional",
          length=38.7, span=28.7, height=10.7, width=3.0, gear=1.3, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.5, engine_len=2.9, tail="t", wheel=0.5, sweep=25),
    _spec(id="crj2", codes=("CRJ2",), name="Bombardier CRJ200", maker="Bombardier", category="regional",
          length=26.8, span=21.2, height=6.2, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.0, engine_len=2.4, tail="t", wheel=0.45, sweep=25),
    _spec(id="crj7", codes=("CRJ7",), name="Bombardier CRJ700", maker="Bombardier", category="regional",
          length=32.5, span=23.2, height=7.5, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.2, engine_len=2.4, tail="t", wheel=0.45, sweep=25),
    _spec(id="crj9", codes=("CRJ9",), name="Bombardier CRJ900", maker="Bombardier", category="regional",
          length=36.2, span=24.9, height=7.5, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.25, engine_len=2.5, tail="t", wheel=0.45, sweep=25),
    # --- turboprops ---------------------------------------------------------------------------------------
    _spec(id="dh8d", codes=("DH8D",), name="De Havilland Dash 8-400", maker="De Havilland Canada",
          category="turboprop", length=32.8, span=28.4, height=8.4, width=2.8, gear=1.5, wing="high",
          engines=(0.3,), mount="wing_front", engine_d=1.2, engine_len=2.6, tail="conventional",
          blades=6, prop_r=1.95, wheel=0.5, sweep=3, accent="#0B6E4F"),
    _spec(id="dh8c", codes=("DH8C",), name="De Havilland Dash 8-300", maker="De Havilland Canada",
          category="turboprop", length=25.7, span=27.4, height=7.5, width=2.7, gear=1.4, wing="high",
          engines=(0.3,), mount="wing_front", engine_d=1.0, engine_len=2.2, tail="conventional",
          blades=4, prop_r=1.7, wheel=0.5, sweep=3, accent="#0B6E4F"),
    _spec(id="at76", codes=("AT76", "AT72", "AT75"), name="ATR 72", maker="ATR", category="turboprop",
          length=27.2, span=27.05, height=7.65, width=2.9, gear=1.3, wing="high", engines=(0.42,),
          mount="wing_front", engine_d=1.0, engine_len=2.6, tail="conventional", blades=6, prop_r=1.95,
          wheel=0.5, sweep=0, wing_x=0.42, accent="#0B6E4F"),
    # --- light aircraft --------------------------------------------------------------------------------------
    _spec(id="c208", codes=("C208",), name="Cessna 208 Caravan", maker="Textron Aviation", category="light",
          length=11.5, span=15.9, height=3.8, width=1.6, fuselage_h=1.9, gear=0.8, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.8, engine_len=1.8, tail="conventional", blades=3, prop_r=1.5,
          wheel=0.25, sweep=0, wing_x=0.42, accent="#1F3C88"),
    _spec(id="c172", codes=("C172",), name="Cessna 172", maker="Textron Aviation", category="light",
          length=8.3, span=11.0, height=2.7, width=1.25, gear=0.45, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.45, engine_len=0.9, tail="conventional", blades=2, prop_r=0.97,
          wheel=0.2, sweep=0, accent="#C62828"),
    _spec(id="pc12", codes=("PC12",), name="Pilatus PC-12", maker="Pilatus", category="light",
          length=14.4, span=16.3, height=4.3, width=1.6, fuselage_h=2.2, gear=0.6, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.6, engine_len=1.9, tail="t", blades=4, prop_r=1.45, wheel=0.25,
          sweep=0, accent="#37474F"),
    # --- helicopters ----------------------------------------------------------------------------------------------
    _spec(id="b06", codes=("B06", "B206"), name="Bell 206 JetRanger", maker="Bell", category="helicopter",
          length=9.5, span=10.2, height=2.9, width=1.5, gear=0.6, wing="high", engines=(0.0,), mount="heli",
          engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.3, accent="#C62828"),
    _spec(id="ec35", codes=("EC35", "EC30"), name="Airbus H135", maker="Airbus Helicopters",
          category="helicopter", length=10.2, span=10.2, height=3.4, width=1.6, gear=0.6, wing="high",
          engines=(0.0,), mount="heli", engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.6,
          accent="#1F3C88"),
    _spec(id="r44", codes=("R44",), name="Robinson R44", maker="Robinson", category="helicopter",
          length=9.0, span=10.1, height=2.9, width=1.2, gear=0.5, wing="high", engines=(0.0,), mount="heli",
          engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.3, accent="#F9A825"),
)

# Fallbacks for types the library does not list: same geometry, no ICAO codes of their own.
GENERIC_SPECS: tuple[Spec, ...] = (
    dataclasses.replace(next(s for s in SPECS if s.id == "a320"), id="generic-jet", codes=(),
                        name="Generic airliner", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "at76"), id="generic-turboprop", codes=(),
                        name="Generic turboprop", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "c172"), id="generic-light", codes=(),
                        name="Generic light aircraft", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "b06"), id="generic-heli", codes=(),
                        name="Generic helicopter", maker="", generic=True),
)

ALL_SPECS: tuple[Spec, ...] = SPECS + GENERIC_SPECS


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


# ---------------------------------------------------------------------------------------------------------
# GLB writer (glTF 2.0, one node per part, double-sided materials with flat or smooth normals).
# ---------------------------------------------------------------------------------------------------------

def _srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_gltf(v: np.ndarray) -> np.ndarray:
    """Aircraft frame (x fwd, y left, z up) -> glTF (X fwd, Y up, Z = -left): a proper rotation."""
    out = np.empty_like(v, dtype=np.float64)
    out[:, 0], out[:, 1], out[:, 2] = v[:, 0], v[:, 2], -v[:, 1]
    return out


def _pack(v: np.ndarray, f: np.ndarray, smooth: bool):
    tri = v[f]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])        # area-weighted face normals
    if smooth:
        vn = np.zeros_like(v)
        for k in range(3):
            np.add.at(vn, f[:, k], fn)
        pos, nrm, idx = v, vn, f.reshape(-1)
    else:
        pos = tri.reshape(-1, 3)
        nrm = np.repeat(fn, 3, axis=0)
        idx = np.arange(len(pos))
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.where(ln > 1e-12, nrm / np.maximum(ln, 1e-12), np.array([0.0, 0.0, 1.0]))
    return pos.astype(np.float32), nrm.astype(np.float32), idx.astype(np.uint32)


def write_glb(path: str | Path, parts: list[Part], extras: dict | None = None) -> Path:
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    materials: list[dict] = []
    mat_index: dict[tuple, int] = {}
    meshes: list[dict] = []
    nodes: list[dict] = []

    def add_view(data: bytes, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        blob.extend(data)
        return len(views) - 1

    def add_accessor(view: int, ctype: int, count: int, atype: str, **extra) -> int:
        accessors.append({"bufferView": view, "componentType": ctype, "count": count, "type": atype, **extra})
        return len(accessors) - 1

    for p in parts:
        if len(p.faces) == 0:
            continue
        pos, nrm, idx = _pack(p.verts, p.faces, p.smooth)
        pos = _to_gltf(pos.astype(np.float64)).astype(np.float32)
        nrm = _to_gltf(nrm.astype(np.float64)).astype(np.float32)
        a_pos = add_accessor(add_view(pos.tobytes(), 34962), 5126, len(pos), "VEC3",
                             min=pos.min(axis=0).tolist(), max=pos.max(axis=0).tolist())
        a_nrm = add_accessor(add_view(nrm.tobytes(), 34962), 5126, len(nrm), "VEC3")
        a_idx = add_accessor(add_view(idx.tobytes(), 34963), 5125, len(idx), "SCALAR")
        key = tuple(round(float(c), 4) for c in p.color)
        if key not in mat_index:
            lin = _srgb_to_linear(p.color).tolist()
            mat_index[key] = len(materials)
            materials.append({"name": p.name, "doubleSided": True,
                              "pbrMetallicRoughness": {"baseColorFactor": [*lin, 1.0],
                                                       "metallicFactor": 0.0, "roughnessFactor": 0.6}})
        meshes.append({"name": p.name, "primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nrm},
                                                       "indices": a_idx, "material": mat_index[key], "mode": 4}]})
        nodes.append({"name": p.name, "mesh": len(meshes) - 1})

    asset = {"version": "2.0", "generator": "maptoposter map3d.aircraft"}
    if extras:
        asset["extras"] = extras
    doc = {
        "asset": asset, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": meshes, "materials": materials, "accessors": accessors,
        "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    blob.extend(b"\0" * (-len(blob) % 4))
    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
    out += struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob)
    path = Path(path)
    path.write_bytes(bytes(out))
    return path


# ---------------------------------------------------------------------------------------------------------
# Library: build once, serve from disk, resolve ICAO type codes.
# ---------------------------------------------------------------------------------------------------------

def _engine_count(spec: Spec) -> int:
    if spec.mount == "heli":
        return 0
    if spec.mount == "nose":
        return 1
    return 2 * len(spec.engines)


def _entry(spec: Spec, path: Path) -> dict:
    data = path.read_bytes()
    return {
        "id": spec.id,
        "codes": list(spec.codes),
        "name": spec.name,
        "manufacturer": spec.maker,
        "category": spec.category,
        "generic": spec.generic,
        "dimensions_m": {"length": spec.length, "span": spec.span, "height": spec.height},
        "configuration": {"wing": spec.wing, "engines": _engine_count(spec), "mount": spec.mount,
                          "tail": spec.tail, "winglets": spec.winglets, "propeller_blades": spec.blades},
        "livery_accent": spec.accent,
        "source": "procedural",
        "approximate": spec.approx,
        "file": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_library(out_dir: str | Path, specs: tuple[Spec, ...] = ALL_SPECS, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    wanted = set()
    for spec in specs:
        path = out / f"{spec.id}.glb"
        parts = build_parts(spec)
        extras = {"id": spec.id, "name": spec.name, "codes": list(spec.codes), "axes": AXES,
                  "dimensions_m": {"length": spec.length, "span": spec.span, "height": spec.height}}
        write_glb(path, parts, extras)
        entries.append(_entry(spec, path))
        wanted.add(path.name)
        log(f"  {spec.id:<16} {spec.name:<26} {len(parts)} parts  {path.stat().st_size / 1024:6.1f} KB")
    for stale in out.glob("*.glb"):
        if stale.name not in wanted:
            stale.unlink()
    catalog = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "axes": AXES,
        "note": "Procedural models from approximate published dimensions; see map3d/aircraft.py.",
        "entries": entries,
    }
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


class Library:
    """A built library folder: catalog.json plus one GLB per entry."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        try:
            data = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise LibraryError(f"no aircraft catalog in {self.root}: {e}") from e
        if data.get("format") != FORMAT:
            raise LibraryError(f"{self.root}/catalog.json is not an aircraft library")
        self.generated = data.get("generated")
        self.entries: list[dict] = data["entries"]
        self._by_id = {e["id"]: e for e in self.entries}
        self._by_code: dict[str, dict] = {}
        for e in self.entries:
            for code in e["codes"]:
                self._by_code.setdefault(code.upper(), e)

    def get(self, entry_id: str) -> dict | None:
        return self._by_id.get(entry_id)

    def path(self, entry: dict) -> Path:
        return self.root / entry["file"]

    def resolve(self, type_code: str | None, helicopter: bool = False) -> tuple[dict, str]:
        """The model for an ICAO type designator: ("exact", entry) or ("category", generic stand-in)."""
        code = (type_code or "").strip().upper()
        if code and code in self._by_code:
            return self._by_code[code], "exact"
        fallback = self._by_id.get("generic-heli" if helicopter else "generic-jet")
        if fallback is None:
            raise LibraryError("catalog has no generic fallback models")
        return fallback, "category"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build the procedural aircraft library.")
    ap.add_argument("out", nargs="?", default="planes", help="output folder (default: planes)")
    args = ap.parse_args(argv)
    print(f"Building aircraft library in {args.out}")
    catalog = build_library(args.out)
    print(f"{len(catalog['entries'])} models written, catalog at {Path(args.out) / 'catalog.json'}")


if __name__ == "__main__":
    main()
