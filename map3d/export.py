"""
Model exporters (no third-party dependency):

* **GLB** - glTF 2.0 binary with the theme colours baked in. Opens in Blender,
  macOS Quick Look / Preview, Windows 3D Viewer, three.js, Unity, ...
* **STL** - geometry only, scaled to a physical size in millimetres for 3-D
  printing. Flat road/rail overlays are skipped (they are paint, not shape).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from .mesh import Mesh, Scene
from .style import RGB, role_colors


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _vertex_colors(mesh: Mesh, base: RGB) -> np.ndarray | None:
    """Per-vertex sRGB colours from per-face tints (meshes whose vertices are not shared between objects)."""
    if mesh.tint is None:
        return None
    vt = np.ones(mesh.n_vertices, dtype=np.float64)
    vt[mesh.faces.ravel()] = np.repeat(mesh.tint.astype(np.float64), 3)
    return np.clip(np.asarray(base)[None, :] * vt[:, None], 0.0, 1.0)


def _y_up(v: np.ndarray) -> np.ndarray:
    """Z-up (x east, y north) -> glTF Y-up, rotation about X so winding is preserved."""
    out = np.empty_like(v, dtype=np.float32)
    out[:, 0], out[:, 1], out[:, 2] = v[:, 0], v[:, 2], -v[:, 1]
    return out


def export_glb(scene: Scene, theme: dict, path: str | Path, credits: str = "") -> Path:
    """Write the scene as a glTF 2.0 binary (``.glb``) in metres, Y-up."""
    colors = role_colors(theme)
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    materials: list[dict] = []
    mat_index: dict[tuple, int] = {}
    meshes_json: list[dict] = []
    nodes: list[dict] = []

    def add_view(data: bytes, target: int | None = None) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data),
                      **({"target": target} if target else {})})
        blob.extend(data)
        return len(views) - 1

    def add_accessor(view: int, ctype: int, count: int, atype: str, **extra) -> int:
        accessors.append({"bufferView": view, "componentType": ctype, "count": count, "type": atype, **extra})
        return len(accessors) - 1

    for mesh in scene.meshes:
        if mesh.n_faces == 0 or mesh.role not in colors:
            continue
        pos = _y_up(mesh.vertices)
        base = colors[mesh.role]
        attrs = {"POSITION": add_accessor(add_view(pos.tobytes(), 34962), 5126, len(pos), "VEC3",
                                          min=pos.min(axis=0).tolist(), max=pos.max(axis=0).tolist())}
        if mesh.normals is not None and not mesh.flat:
            nrm = _y_up(mesh.normals)
            attrs["NORMAL"] = add_accessor(add_view(nrm.tobytes(), 34962), 5126, len(nrm), "VEC3")
        vcol = _vertex_colors(mesh, base)
        if vcol is not None:
            lin = _srgb_to_linear(vcol).astype(np.float32)
            attrs["COLOR_0"] = add_accessor(add_view(lin.tobytes(), 34962), 5126, len(lin), "VEC3")
            key: tuple = ("white",)
            factor = [1.0, 1.0, 1.0, 1.0]
        else:
            factor = [*_srgb_to_linear(np.asarray(base)).tolist(), 1.0]
            key = tuple(round(x, 4) for x in factor)
        if key not in mat_index:
            mat_index[key] = len(materials)
            materials.append({"pbrMetallicRoughness": {"baseColorFactor": factor, "metallicFactor": 0.0,
                                                       "roughnessFactor": 1.0},
                              "doubleSided": mesh.role in ("bridge_side",), "name": mesh.role})
        idx = mesh.faces.astype(np.uint32).ravel()
        iacc = add_accessor(add_view(idx.tobytes(), 34963), 5125, len(idx), "SCALAR")
        meshes_json.append({"name": mesh.name, "primitives": [{"attributes": attrs, "indices": iacc,
                                                               "material": mat_index[key], "mode": 4}]})
        nodes.append({"name": mesh.name, "mesh": len(meshes_json) - 1})

    doc = {
        "asset": {"version": "2.0", "generator": "maptoposter map3d", "copyright": credits or "OpenStreetMap contributors"},
        "scene": 0,
        "scenes": [{"name": scene.title or "city", "nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": meshes_json, "materials": materials,
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
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


STL_SKIP_ROLES = {"rail"} | {f"road_{c}" for c in ("motorway", "primary", "secondary", "tertiary", "residential",
                                                   "service")}


def export_stl(scene: Scene, path: str | Path, size_mm: float = 200.0) -> Path:
    """
    Binary STL, Z-up, scaled so the tile is ``size_mm`` wide and sitting on z = 0.
    All meshes share a consistent outward winding, which slicers use to tell inside from outside.
    """
    side = 2.0 * (scene.half + float(scene.info.get("ledge", 0.0)))
    k = size_mm / side
    zmin = scene.z_range[0]
    tris = []
    for mesh in scene.meshes:
        if mesh.n_faces == 0 or mesh.role in STL_SKIP_ROLES:
            continue
        v = mesh.vertices.astype(np.float64).copy()
        v[:, 2] -= zmin
        tris.append(v[mesh.faces] * k)
    t = np.concatenate(tris).astype(np.float32)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    n = (n / ln).astype(np.float32)
    dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec = np.zeros(len(t), dtype=dt)
    rec["n"], rec["v"] = n, t
    path = Path(path)
    with open(path, "wb") as f:
        f.write(b"maptoposter map3d - millimetres".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())
    return path
