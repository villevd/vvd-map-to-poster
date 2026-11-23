"""
Interactive viewer export: one self-contained ``.html`` file.

The mesh data is embedded (zlib-compressed binary, base64) and rendered with
three.js, loaded from a CDN, so the file opens by double-click in any modern
browser: orbit / zoom / pan, real-time sun shadows, layer toggles, live theme
switching, vertical exaggeration, sun position and a PNG snapshot button.
Walking past the model's edge (WASD, mouse) generates the next chunks from real data, when the page is served
by the local GUI (``python -m map3d.gui``); see ``map3d/chunks.py``.

Large multi-tile models stay fast: every tile and detail level is its own
compressed chunk that is decoded and uploaded only when needed (nearest first),
tiles switch between full / medium / far detail with distance, medium-distance
trees become GPU-instanced billboard impostors, distance fog with a draw
distance hides what is too far to matter, an optional fog of war keeps tiles
hidden until you explore towards them, shadows follow the camera, and the
render resolution adapts to the frame rate.
"""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

import numpy as np

from .mesh import Scene
from .style import role_colors

THREE_VERSION = "0.160.0"
TRAFFIC_JS = (Path(__file__).with_name("traffic_sim.js")).read_text(encoding="utf-8")
SEARCH_JS = (Path(__file__).with_name("search.js")).read_text(encoding="utf-8")

GROUPS = {
    "building": "building", "tree": "tree",
    "road_motorway": "roads", "road_primary": "roads", "road_secondary": "roads", "road_tertiary": "roads",
    "road_residential": "roads", "road_service": "roads", "rail": "roads", "bridge": "roads", "bridge_side": "roads",
    "marking_yellow": "markings", "marking_white": "markings",
}
OVERLAY_ROLES = {"road_motorway", "road_primary", "road_secondary", "road_tertiary", "road_residential",
                 "road_service", "rail", "marking_yellow", "marking_white"}


def _y_up(v: np.ndarray) -> np.ndarray:
    out = np.empty_like(v, dtype=np.float32)
    out[:, 0], out[:, 1], out[:, 2] = v[:, 0], v[:, 2], -v[:, 1]
    return out


def _pack(scene: Scene, theme: dict) -> tuple[list[dict], dict[str, bytes]]:
    """Meshes grouped by (tile, detail level); each group is one independently compressed chunk."""
    colors = role_colors(theme)
    buckets: dict[tuple, list] = {}
    for m in scene.meshes:
        if m.role not in colors or (m.n_faces == 0 and "impostors" not in m.tags):
            continue
        buckets.setdefault((tuple(m.tags.get("chunk", (0, 0))), m.lod), []).append(m)

    groups, blobs = [], {}
    for k, ((chunk, lod), meshes) in enumerate(sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0]))):
        blob = bytearray()

        def put(arr: np.ndarray) -> int:
            while len(blob) % 4:
                blob.append(0)
            off = len(blob)
            blob.extend(arr.tobytes())
            return off

        entries, imp = [], None
        for m in meshes:
            if "impostors" in m.tags:
                a = m.tags["impostors"]
                cards = np.column_stack([a[:, 0], a[:, 2], -a[:, 1], a[:, 3], a[:, 4], a[:, 5]]).astype(np.float32)
                imp = {"off": put(cards), "n": int(len(cards))}
            if m.n_faces == 0:
                continue
            e = {"role": m.role, "group": GROUPS.get(m.role, "terrain"), "flat": bool(m.flat),
                 "cast": bool(m.casts_shadow), "receive": bool(m.receives_shadow),
                 "overlay": m.role in OVERLAY_ROLES, "nv": m.n_vertices, "nf": m.n_faces}
            e["pos"] = put(_y_up(m.vertices))
            small = m.n_vertices < 65536
            e["idx16"] = small
            e["idx"] = put(m.faces.astype(np.uint16 if small else np.uint32).ravel())
            if m.tint is not None:
                vt = np.ones(m.n_vertices, dtype=np.float64)
                vt[m.faces.ravel()] = np.repeat(m.tint.astype(np.float64), 3)
                e["tint"] = put(np.clip(vt * 127.5 + 0.5, 0, 255).astype(np.uint8))
            if m.variant is not None:
                vv = np.zeros(m.n_vertices, dtype=np.uint8)
                vv[m.faces.ravel()] = np.repeat(m.variant.astype(np.uint8), 3)
                e["variant"] = put(vv)
            if m.base is not None and len(m.base) == m.n_vertices:
                e["base"] = put(np.asarray(m.base, dtype=np.float32))
            if m.normals is not None and not m.flat:
                e["nrm"] = put(np.round(_y_up(m.normals) * 127).astype(np.int8))
            entries.append(e)
        cx, cy = meshes[0].tags.get("center", (0.0, 0.0))
        bid = f"b{k}"
        blobs[bid] = bytes(blob)
        groups.append({"id": bid, "chunk": list(chunk), "lod": lod, "center": [float(cx), float(-cy)],
                       "meshes": entries, "imp": imp})
    return groups, blobs


def _road_cfg(paths) -> list[list[float]]:
    """Road centre lines as flat [x, y, z, ...] lists in the viewer's frame (x east, y up, z = -north)."""
    out = []
    for p in paths:
        p = np.asarray(p, dtype=np.float64)
        if len(p) < 2:
            continue
        q = np.column_stack([p[:, 0], p[:, 2], -p[:, 1]])
        out.append(np.round(q, 1).ravel().tolist())
    return out


def _facts(info: dict) -> dict:
    """Build facts shown in the GUI's Insights tab (plain numbers, JSON-safe)."""
    out: dict = {}
    b = info.get("buildings")
    if isinstance(b, dict):
        out["buildings"] = {k: int(v) for k, v in b.items()}
    for k in ("trees", "bridges"):
        if k in info:
            out[k] = int(info[k])
    for k in ("relief_m", "dem_min_m", "base_m"):
        if k in info:
            out[k] = round(float(info[k]), 1)
    return out


def export_html(scene: Scene, theme: dict, path: str | Path, title: str = "", subtitle: str = "",
                azimuth: float = 225.0, elevation: float = 45.0, credits: str = "",
                themes: dict | None = None, theme_id: str = "") -> Path:
    """``themes`` maps id -> theme dict; when given, the viewer can switch between all of them live."""
    groups, blobs = _pack(scene, theme)
    palette = dict(themes or {})
    if not theme_id or theme_id not in palette:
        theme_id = theme_id or "current"
        palette[theme_id] = theme
    theme_cfg = {}
    for tid, t in palette.items():
        cols = role_colors(t)
        theme_cfg[tid] = {"name": t.get("name", tid), "bg": t["bg"], "text": t.get("text", "#ffffff"),
                          "colors": {r: [round(c, 4) for c in rgb] for r, rgb in cols.items()}}
    zlo, zhi = scene.z_range
    tiles = int(scene.info.get("tiles", 1))
    cfg = {
        "groups": groups, "half": scene.half, "tiles": tiles,
        "tileSize": 2.0 * float(scene.info.get("tile_half", scene.half)),
        "maxLod": max((g["lod"] for g in groups), default=0),
        "zrange": [zlo, zhi], "bg": theme["bg"], "text": theme.get("text", "#ffffff"),
        "sun": {"bearing": scene.light_bearing, "elevation": scene.light_elevation},
        "camera": {"azimuth": azimuth, "elevation": elevation},
        "title": title, "subtitle": subtitle, "credits": credits,
        "themes": theme_cfg, "theme": theme_id,
        "traffic": scene.info.get("traffic"),
        "airport": scene.info.get("airport"),
        "facts": _facts(scene.info),
        "roads": _road_cfg(scene.info.get("road_paths") or []),
        "exag": {"terrain": float(scene.info.get("exaggeration", 1.0)),
                 "building": float(scene.info.get("building_exaggeration", scene.info.get("exaggeration", 1.0)))},
        "geo": {"lat": scene.center_lonlat[1], "lon": scene.center_lonlat[0], "half": scene.half,
                "place": ", ".join(x for x in (title, subtitle) if x)},
    }
    cfg["chunks"] = _chunk_spec(scene, theme_id, cfg["maxLod"])
    cfg_json = json.dumps(cfg, separators=(",", ":")).replace("<", "\\u003c")
    blob_tags = "\n".join(
        f'<script type="text/plain" id="{bid}">{base64.b64encode(zlib.compress(b, 6)).decode("ascii")}</script>'
        for bid, b in blobs.items())
    html = (TEMPLATE
            .replace("__TITLE__", (title or "3D city model").replace("<", "&lt;"))
            .replace("__THREE__", THREE_VERSION)
            .replace("__BG__", theme["bg"]).replace("__TEXT__", theme.get("text", "#ffffff"))
            .replace("__CFG__", cfg_json)
            .replace("__TRAFFIC_JS__", TRAFFIC_JS)
            .replace("__SEARCH_JS__", SEARCH_JS)
            .replace("__BLOBS__", blob_tags))
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path


def _chunk_spec(scene: Scene, theme_id: str, max_lod: int) -> dict | None:
    """What the page needs to ask the local server for the chunks beyond the model's edge (map3d/chunks.py).

    None when the model cannot be extended: no ground datum recorded, or no named theme to build with.
    """
    info = scene.info
    if "dem_min_m" not in info or not theme_id or theme_id == "current":
        return None
    exag = float(info.get("exaggeration", 1.0))
    return {"lat": float(scene.center_lonlat[1]), "lon": float(scene.center_lonlat[0]), "half": float(scene.half),
            "tiles": int(info.get("tiles", 1)), "tileHalf": float(info.get("tile_half", scene.half)),
            "zmin": float(info["dem_min_m"]), "exag": exag,
            "bexag": float(info.get("building_exaggeration", exag)), "maxLod": int(max_lod), "theme": theme_id}


WEB_DIR = Path(__file__).with_name("web") / "viewer"


def _assemble_template() -> str:
    """
    The viewer page: the HTML shell, the stylesheet and the browser code, read from ``web/viewer``.
    The JavaScript is split by feature into ``js/NN_name.js`` and joined in name order into one module
    script, so the page is still one file and all parts share one scope.
    """
    shell = (WEB_DIR / "shell.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    js = "".join((WEB_DIR / "js" / name).read_text(encoding="utf-8") for name in sorted(p.name for p in (WEB_DIR / "js").glob("*.js")))
    return shell.replace("__VIEWER_CSS__", css).replace("__VIEWER_JS__", js)


TEMPLATE = _assemble_template()
