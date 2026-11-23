"""Writes the aircraft models as binary glTF."""

import json
import numpy as np
import struct
from pathlib import Path
from .parts import Part


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
