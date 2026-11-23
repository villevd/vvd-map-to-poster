"""
Mesh containers shared by the geometry builders, renderers and exporters.

Meshes are theme independent: each carries a semantic ``role`` (``"ground"``,
``"water"``, ``"building"`` ...) and colours are resolved from the active theme
only when rendering or exporting, so one scene can be restyled for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Mesh:
    name: str
    role: str
    vertices: np.ndarray                      # (N, 3) float32, local metres, z up
    faces: np.ndarray                         # (M, 3) int32, counter-clockwise seen from outside
    normals: np.ndarray | None = None         # (N, 3) smooth vertex normals (terrain-like meshes)
    tint: np.ndarray | None = None            # (M,) per-face brightness multiplier
    shadowed: np.ndarray | None = None        # (M,) bool, face lies in a cast shadow (PNG renderer only)
    flat: bool = True                         # flat (faceted) shading vs smooth
    casts_shadow: bool = False                # interactive viewer: casts real-time shadows
    receives_shadow: bool = True              # interactive viewer: receives real-time shadows
    depth_bias: float = 0.0                   # painter's-algorithm bias in metres (towards the camera)
    base: np.ndarray | None = None            # (N,) float32 ground height under each vertex, so the viewer can rescale heights above it
    lod: int = 0                              # level of detail: 0 full, 1 medium, 2 far (static outputs use 0 only)
    tags: dict = field(default_factory=dict)

    @property
    def n_faces(self) -> int:
        return int(self.faces.shape[0])

    @property
    def n_vertices(self) -> int:
        return int(self.vertices.shape[0])

    def face_vertices(self) -> np.ndarray:
        """Triangle corner positions, shape (M, 3, 3)."""
        return self.vertices[self.faces]

    def face_normals(self) -> np.ndarray:
        """Unit face normals, shape (M, 3)."""
        tri = self.vertices[self.faces].astype(np.float64)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        return n / ln

    def compact(self) -> "Mesh":
        """Drop vertices no face refers to and renumber the faces."""
        if self.n_faces == 0:
            return self
        used, inverse = np.unique(self.faces.ravel(), return_inverse=True)
        return Mesh(
            name=self.name, role=self.role,
            vertices=self.vertices[used],
            faces=inverse.reshape(-1, 3).astype(np.int32),
            normals=None if self.normals is None else self.normals[used],
            tint=self.tint, shadowed=self.shadowed, flat=self.flat,
            casts_shadow=self.casts_shadow, receives_shadow=self.receives_shadow,
            depth_bias=self.depth_bias, lod=self.lod, tags=dict(self.tags),
            base=None if self.base is None else self.base[used],
        )

    def subset(self, face_mask: np.ndarray, **overrides) -> "Mesh":
        """New compacted mesh holding only the faces selected by ``face_mask``."""
        m = Mesh(
            name=self.name, role=self.role, vertices=self.vertices, faces=self.faces[face_mask],
            normals=self.normals,
            tint=None if self.tint is None else self.tint[face_mask],
            shadowed=None if self.shadowed is None else self.shadowed[face_mask],
            flat=self.flat, casts_shadow=self.casts_shadow, receives_shadow=self.receives_shadow,
            depth_bias=self.depth_bias, lod=self.lod, tags=dict(self.tags), base=self.base,
        )
        for key, value in overrides.items():
            setattr(m, key, value)
        return m.compact()


def vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Area-weighted smooth vertex normals."""
    v = vertices.astype(np.float64)
    tri = v[faces]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])   # length = 2 * area
    out = np.zeros_like(v)
    for k in range(3):
        np.add.at(out, faces[:, k], fn)
    ln = np.linalg.norm(out, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return (out / ln).astype(np.float32)


def quad_strip(a: np.ndarray, b: np.ndarray, za_bot, zb_bot, za_top, zb_top) -> tuple[np.ndarray, np.ndarray]:
    """
    Vertical quads along the 2-D edges a -> b, as (vertices (4E, 3), faces (2E, 3)).

    The solid must lie to the *left* of a -> b (counter-clockwise rings): the
    quads then face outwards.
    """
    e = len(a)
    v = np.empty((e, 4, 3))
    v[:, 0, :2], v[:, 0, 2] = a, za_bot
    v[:, 1, :2], v[:, 1, 2] = b, zb_bot
    v[:, 2, :2], v[:, 2, 2] = b, zb_top
    v[:, 3, :2], v[:, 3, 2] = a, za_top
    base = (np.arange(e) * 4)[:, None]
    f = np.concatenate([base + np.array([0, 1, 2]), base + np.array([0, 2, 3])]).reshape(-1, 3)
    return v.reshape(-1, 3), f


def concat(name: str, role: str, parts: list[tuple[np.ndarray, np.ndarray]], **kwargs) -> Mesh | None:
    """Concatenate (vertices, faces) pairs into one mesh. Returns None when empty."""
    parts = [(v, f) for v, f in parts if len(f)]
    if not parts:
        return None
    verts, faces, offset = [], [], 0
    for v, f in parts:
        verts.append(v.astype(np.float32))
        faces.append(f.astype(np.int32) + offset)
        offset += len(v)
    return Mesh(name=name, role=role, vertices=np.concatenate(verts), faces=np.concatenate(faces), **kwargs)


@dataclass
class Scene:
    """Everything needed to draw or export one 3D city tile."""

    meshes: list[Mesh]
    half: float                    # half side of the tile (metres)
    z_range: tuple[float, float]   # lowest / highest point of the model (metres)
    center_lonlat: tuple[float, float]
    title: str = ""
    info: dict = field(default_factory=dict)       # provenance + statistics, for logging / viewer
    light_bearing: float = 0.0     # compass bearing (deg) the light comes from
    light_elevation: float = 45.0  # deg above the horizon

    def by_role(self, role: str) -> list[Mesh]:
        return [m for m in self.meshes if m.role == role]

    @property
    def n_faces(self) -> int:
        return sum(m.n_faces for m in self.meshes)
