"""
Static renderer: draws a Scene as a poster-ready image with matplotlib.

A small software 3-D pipeline - perspective (or orthographic) projection,
back-face culling, Lambert lighting with baked ground shadows, painter's-
algorithm depth sorting - feeding one ``PolyCollection``. It needs nothing
beyond matplotlib/numpy, so it works anywhere the 2-D poster generator does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from PIL import Image, ImageDraw
from scipy import ndimage

from .mesh import Scene
from .style import role_colors, to_rgb

SHADOW_DARKEN = 0.66


@dataclass
class Camera:
    azimuth: float = 225.0      # compass bearing of the camera position seen from the model centre (deg)
    elevation: float = 45.0     # deg above the horizon
    fov: float = 24.0           # vertical field of view for perspective (deg); 0 = orthographic
    zoom: float = 1.0           # > 1 crops in on the model


def _basis(cam: Camera):
    az, el = math.radians(cam.azimuth), math.radians(cam.elevation)
    d = np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])
    f = -d
    right = np.cross(f, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, f)
    return d, right, up


class Projector:
    """World (x, y, z) -> screen (sx, sy) plus a view-space distance used for depth sorting."""

    def __init__(self, cam: Camera, center: np.ndarray, radius: float):
        self.cam = cam
        self.d, self.right, self.up = _basis(cam)
        self.center = np.asarray(center, dtype=np.float64)
        self.perspective = cam.fov > 0.5
        self.dist = radius / math.tan(math.radians(cam.fov) / 2.0) if self.perspective else radius * 4.0
        self.eye = self.center + self.d * self.dist

    def __call__(self, pts: np.ndarray):
        p = pts.astype(np.float64) - self.eye
        along = -(p @ self.d)                      # distance in front of the camera
        sx = p @ self.right
        sy = p @ self.up
        if self.perspective:
            return sx / along, sy / along, along
        return sx, sy, along


def _fit(sx: np.ndarray, sy: np.ndarray, x0: float, x1: float, y0: float, y1: float):
    """Uniform scale + offset that centres the bounds (sx, sy) in the rectangle."""
    bx0, bx1, by0, by1 = sx.min(), sx.max(), sy.min(), sy.max()
    s = min((x1 - x0) / (bx1 - bx0), (y1 - y0) / (by1 - by0))
    ox = (x0 + x1) / 2 - s * (bx0 + bx1) / 2
    oy = (y0 + y1) / 2 - s * (by0 + by1) / 2
    return s, ox, oy


def render_scene(scene: Scene, theme: dict, width: float, height: float, cam: Camera | None = None,
                 background: str | None = None, log=print):
    """
    Draw ``scene`` on a new figure of ``width`` x ``height`` inches.

    Returns ``(fig, ax)``; the axes cover the whole figure with ``transAxes``
    free for the caller's typography. The model occupies the upper ~75 %.
    """
    cam = cam or Camera()
    colors = role_colors(theme)
    bg = background or theme["bg"]
    ledge = float(scene.info.get("ledge", 0.0))
    OUT = scene.half + ledge
    zlo, zhi = scene.z_range

    # ---- projector + framing ----------------------------------------------------------------
    centre = np.array([0.0, 0.0, 0.35 * zhi])
    radius = math.sqrt(2.0) * OUT * 1.15
    proj = Projector(cam, centre, radius)
    corners = np.array([[x, y, z] for x in (-OUT, OUT) for y in (-OUT, OUT) for z in (zlo, zhi)])
    csx, csy, _ = proj(corners)
    aspect = height / width
    s, ox, oy = _fit(csx, csy, 0.05, 0.95, 0.22 * aspect, 0.96 * aspect)
    if cam.zoom != 1.0:                       # zoom about the middle of the available area
        mx, my = 0.5, 0.59 * aspect
        s, ox, oy = s * cam.zoom, mx + (ox - mx) * cam.zoom, my + (oy - my) * cam.zoom

    # ---- gather triangles -----------------------------------------------------------------------
    light_b, light_e = math.radians(scene.light_bearing), math.radians(scene.light_elevation)
    light = np.array([math.sin(light_b) * math.cos(light_e), math.cos(light_b) * math.cos(light_e), math.sin(light_e)])

    xy_parts, col_parts, key_parts = [], [], []
    n_total = 0
    for mesh in scene.meshes:
        if mesh.n_faces == 0 or mesh.role not in colors:
            continue
        tri = mesh.vertices[mesh.faces].astype(np.float64)               # (M, 3, 3)
        fn = mesh.face_normals()
        cen = tri.mean(axis=1)
        sx, sy, along = proj(tri.reshape(-1, 3))
        sx, sy, along = sx.reshape(-1, 3), sy.reshape(-1, 3), along.reshape(-1, 3)

        # back-face culling against the direction to the eye
        to_eye = proj.eye - cen if proj.perspective else np.broadcast_to(proj.d, cen.shape)
        facing = np.einsum("ij,ij->i", fn, to_eye) > 0
        in_front = (along > 1e-3).all(axis=1)
        keep = facing & in_front
        if not keep.any():
            continue

        # lighting: smooth normals where the mesh has them, face normals otherwise
        if mesh.normals is not None:
            nrm = mesh.normals[mesh.faces].mean(axis=1).astype(np.float64)
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
        else:
            nrm = fn
        diffuse = np.clip(nrm @ light, 0.0, 1.0)
        shade = 0.50 + 0.11 * np.clip(nrm[:, 2], 0, 1) + 0.50 * diffuse
        base = np.asarray(colors[mesh.role], dtype=np.float64)
        rgb = base[None, :] * shade[:, None]
        if mesh.tint is not None:
            rgb = rgb * mesh.tint[:, None]
        if mesh.shadowed is not None:
            rgb = np.where(mesh.shadowed[:, None], rgb * SHADOW_DARKEN, rgb)
        rgb = np.clip(rgb, 0.0, 1.0)

        scr = np.stack([sx * s + ox, sy * s + oy], axis=2)                # (M, 3, 2)
        cdist = along.mean(axis=1) - mesh.depth_bias
        xy_parts.append(scr[keep])
        col_parts.append(rgb[keep])
        key_parts.append(cdist[keep])
        n_total += int(keep.sum())

    xy = np.concatenate(xy_parts)
    rgb = np.concatenate(col_parts)
    key = np.concatenate(key_parts)

    # drop triangles entirely off-canvas
    on = ~((xy[..., 0].max(axis=1) < 0) | (xy[..., 0].min(axis=1) > 1)
           | (xy[..., 1].max(axis=1) < 0) | (xy[..., 1].min(axis=1) > aspect))
    xy, rgb, key = xy[on], rgb[on], key[on]
    order = np.argsort(-key, kind="stable")                                # far to near
    log(f"Rendering {len(order):,} triangles...")

    # ---- draw ------------------------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(width, height), facecolor=bg)
    ax.set_facecolor(bg)
    ax.set_position((0.0, 0.0, 1.0, 1.0))
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, aspect)
    ax.axis("off")

    _contact_shadow(ax, proj, s, ox, oy, OUT, zlo, bg, aspect)
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, aspect)
    rgba = np.concatenate([rgb[order], np.ones((len(order), 1))], axis=1)
    coll = PolyCollection(xy[order], facecolors=rgba, edgecolors=rgba, linewidths=0.3, antialiaseds=True)
    coll.set_rasterized(True)
    ax.add_collection(coll)
    return fig, ax


def _contact_shadow(ax, proj: Projector, s: float, ox: float, oy: float, OUT: float, zlo: float, bg: str,
                    aspect: float) -> None:
    """Soft drop shadow beneath the model so it sits on the page instead of floating."""
    floor = np.array([[-OUT, -OUT, zlo], [OUT, -OUT, zlo], [OUT, OUT, zlo], [-OUT, OUT, zlo]])
    sx, sy, _ = proj(floor)
    pts = np.column_stack([sx * s + ox, sy * s + oy])
    pts = pts + np.array([0.012, -0.022])                 # nudge the shadow down and to the side
    res = 360                                              # raster resolution along x; blurred, so coarse is fine
    nx, ny = res, max(int(res * aspect), 1)
    mask = Image.new("L", (nx, ny), 0)
    px = pts[:, 0] * nx
    py = (1.0 - pts[:, 1] / aspect) * ny
    ImageDraw.Draw(mask).polygon(list(zip(px, py)), fill=255)
    soft = ndimage.gaussian_filter(np.asarray(mask, dtype=np.float32) / 255.0, sigma=nx * 0.018)
    dark = np.array(to_rgb(bg)) * 0.3
    rgba = np.zeros((ny, nx, 4), dtype=np.float32)
    rgba[..., :3] = dark
    rgba[..., 3] = np.clip(soft * 0.55, 0.0, 1.0)
    ax.imshow(rgba, extent=(0.0, 1.0, 0.0, aspect), origin="upper", interpolation="bilinear", zorder=0.5,
              aspect="auto")
