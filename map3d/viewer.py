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
}
OVERLAY_ROLES = {"road_motorway", "road_primary", "road_secondary", "road_tertiary", "road_residential",
                 "road_service", "rail"}


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


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg: __BG__; --text: __TEXT__; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; overflow: hidden; }
  canvas { display: block; }
  #ui { position: fixed; top: 16px; left: 16px; width: 270px; max-height: calc(100vh - 32px); overflow-y: auto; padding: 16px;
    border-radius: 14px; background: color-mix(in srgb, var(--bg) 78%, transparent); backdrop-filter: blur(10px);
    border: 1px solid color-mix(in srgb, var(--text) 22%, transparent); }
  #ui h1 { margin: 0 0 2px; font-size: 20px; letter-spacing: .12em; text-transform: uppercase; }
  #ui .sub { margin: 0 0 12px; opacity: .75; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
  #ui label { display: flex; align-items: center; gap: 8px; margin: 5px 0; cursor: pointer; }
  #ui .row { display: block; margin: 10px 0 2px; font-size: 12px; opacity: .85; }
  #ui select { width: 100%; padding: 6px 8px; border-radius: 8px; color: var(--text); font: inherit;
    background: color-mix(in srgb, var(--bg) 70%, transparent); border: 1px solid color-mix(in srgb, var(--text) 35%, transparent); }
  #ui input[type=range] { width: 100%; accent-color: var(--text); }
  #ui input[type=checkbox] { accent-color: var(--text); }
  #ui .btns { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  #ui button { flex: 1; padding: 7px 10px; border-radius: 8px; cursor: pointer; color: var(--text);
    background: transparent; border: 1px solid color-mix(in srgb, var(--text) 45%, transparent); font: inherit; }
  #ui button:hover { background: color-mix(in srgb, var(--text) 14%, transparent); }
  #ui details { margin-top: 10px; } #ui summary { cursor: pointer; font-size: 12px; opacity: .85; }
  #stats { margin-top: 8px; font: 11px/1.4 ui-monospace, Consolas, monospace; opacity: .7; white-space: pre; }
  #findBox { position: fixed; top: 16px; left: 50%; transform: translateX(-50%); width: min(460px, 90vw); z-index: 4;
    border-radius: 12px; padding: 8px; background: color-mix(in srgb, var(--bg) 85%, transparent); backdrop-filter: blur(10px);
    border: 1px solid color-mix(in srgb, var(--text) 30%, transparent); }
  #find { width: 100%; padding: 8px 10px; border-radius: 8px; font: inherit; color: var(--text); background: transparent;
    border: 1px solid color-mix(in srgb, var(--text) 40%, transparent); outline: none; }
  #findList { list-style: none; margin: 6px 0 0; padding: 0; font-size: 13px; }
  #findList li { padding: 6px 8px; border-radius: 6px; cursor: pointer; }
  #findList li.sel, #findList li:hover { background: color-mix(in srgb, var(--text) 16%, transparent); }
  #findList li.msg { opacity: .7; cursor: default; }
  #credits { position: fixed; right: 12px; bottom: 8px; font-size: 11px; opacity: .6; max-width: 60vw; text-align: right; }
  #loading { position: fixed; inset: 0; display: grid; place-items: center; background: var(--bg);
    letter-spacing: .2em; text-transform: uppercase; font-size: 13px; transition: opacity .4s; z-index: 5; }
  #lbox { max-width: min(520px, 90vw); text-transform: none; letter-spacing: 0; font-size: 14px; }
  #ltitle { text-transform: uppercase; letter-spacing: .2em; font-size: 13px; margin-bottom: 10px; }
  #lsteps { margin: 0; padding: 0; list-style: none; }
  #lsteps li { padding: 2px 0 2px 22px; position: relative; opacity: .45; }
  #lsteps li.now { opacity: 1; } #lsteps li.done { opacity: .85; }
  #lsteps li::before { content: '\25CB'; position: absolute; left: 0; }
  #lsteps li.now::before { content: '\25D0'; } #lsteps li.done::before { content: '\2713'; }
  #lnote { margin-top: 10px; font-size: 12px; opacity: .65; }
  #loading.done { opacity: 0; pointer-events: none; }
  #hint { position: fixed; left: 16px; bottom: 8px; font-size: 11px; opacity: .55; }
  @media (max-width: 640px) {
    #ui { width: auto; right: 12px; left: 12px; top: auto; bottom: 12px; padding: 10px 12px; max-height: 45vh; }
    #ui .sub, #hint, #credits { display: none; }
  }
</style>
</head>
<body>
<div id="findBox" hidden><input id="find" type="search" placeholder="Find a place…  (Enter to go, ↑↓ for other matches, Esc to close)" autocomplete="off"><ul id="findList"></ul></div>
<div id="loading"><div id="lbox"><div id="ltitle">Opening the 3D model</div><ol id="lsteps"></ol><div id="lnote"></div></div></div>
<div id="ui">
  <h1 id="t"></h1>
  <p class="sub" id="s"></p>
  <span class="row">Theme</span>
  <select id="themeSel"></select>
  <label><input type="checkbox" data-group="building" checked> Buildings</label>
  <label><input type="checkbox" data-group="tree" checked> Trees</label>
  <label><input type="checkbox" data-group="roads" checked> Roads &amp; bridges</label>
  <label id="trafficRow"><input type="checkbox" data-group="traffic" checked> Traffic (stops at real lights &amp; signs)</label>
  <label><input type="checkbox" id="satOn"> Satellite ground (map overlay)</label>
  <label><input type="checkbox" id="texOn"> Real building textures</label>
  <span class="row" id="trafficSpeedRow">Traffic speed multiplier <output id="tspeedO"></output></span>
  <input type="range" id="tspeed" min="0" max="4" step="0.25" value="1">
  <div class="btns"><button id="landBtn">Land a plane</button></div>
  <label><input type="checkbox" id="followBox" checked> Follow the plane</label>
  <span class="row">Terrain exaggeration <output id="vExagO"></output></span>
  <input type="range" id="exag" min="0.2" max="6" step="0.1" value="1">
  <span class="row">Building height <output id="vBexagO"></output></span>
  <input type="range" id="bexag" min="0.2" max="6" step="0.1" value="1">
  <span class="row">Sun direction</span>
  <input type="range" id="az" min="0" max="360" step="1">
  <span class="row">Sun height</span>
  <input type="range" id="el" min="8" max="85" step="1">
  <label><input type="checkbox" id="real"> Realistic view (now)</label>
  <div id="realBox" hidden>
    <span class="row">Time of day</span>
    <input type="range" id="tod" min="0" max="24" step="0.25" value="12">
    <div class="btns"><button id="nowBtn">Now</button></div>
    <p class="sub" id="realInfo"></p>
  </div>
  <details id="perf"><summary>Performance</summary>
    <span class="row">Quality</span>
    <select id="quality"><option value="auto">Auto (adapts to your GPU)</option><option value="high">High</option><option value="low">Low</option></select>
    <label><input type="checkbox" id="shadowsOn" checked> Real-time shadows</label>
    <label><input type="checkbox" id="fogOn"> Fog &amp; draw distance</label>
    <span class="row">View distance <output id="ddO"></output></span>
    <input type="range" id="dd" min="1.5" max="14" step="0.5">
    <label id="fowRow"><input type="checkbox" id="fowOn"> Fog of war (reveal by exploring)</label>
    <div id="stats"></div>
  </details>
  <span class="row">Rotation speed</span>
  <input type="range" id="rotSpeed" min="0.1" max="8" step="0.1" value="0.8">
  <div class="btns">
    <button id="rot">Auto-rotate</button><button id="reset">Reset view</button><button id="shot">Save PNG</button>
  </div>
</div>
<div id="hint">Drag to orbit · scroll to zoom · right-drag to pan</div>
<div id="credits"></div>

<script type="application/json" id="cfg">__CFG__</script>
__BLOBS__
<script>__TRAFFIC_JS__</script>
<script>__SEARCH_JS__</script>
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@__THREE__/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@__THREE__/examples/jsm/" } }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const CFG = JSON.parse(document.getElementById('cfg').textContent);
// When the GUI shows this page in a frame it moves the settings panel into its own sidebar; lookups then follow it there
let PARENT_DOC = null;
try { if (window.parent !== window) PARENT_DOC = window.parent.document; } catch (e) {}
const $ = id => document.getElementById(id) || (PARENT_DOC && PARENT_DOC.getElementById(id));
$('t').textContent = CFG.title || '3D city';
$('s').textContent = CFG.subtitle || '';
$('credits').textContent = CFG.credits || '';

async function inflate(b64) {
  // Decode base64 directly (fetching a huge data: URL can stall or fail in some browsers / frames)
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
  return await new Response(stream).arrayBuffer();
}

// ---------------------------------------------------------------- renderer / scene
const dpr = Math.min(window.devicePixelRatio || 1, 2);
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(dpr);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
const N = CFG.tiles, T = CFG.tileSize, H = CFG.half;
const zc = (CFG.zrange[0] + CFG.zrange[1]) * 0.25;
const camera = new THREE.PerspectiveCamera(32, window.innerWidth / window.innerHeight, T * 0.01, H * 60);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, maxPolarAngle: Math.PI / 2 - 0.03,
  minDistance: Math.min(T * 0.08, 25), maxDistance: H * 14 });
controls.target.set(0, zc, 0);

function dirFromCompass(bearingDeg, elevDeg) {
  const b = bearingDeg * Math.PI / 180, e = elevDeg * Math.PI / 180;
  return new THREE.Vector3(Math.sin(b) * Math.cos(e), Math.sin(e), -Math.cos(b) * Math.cos(e));
}
const startDist = H * 4.6;
function resetView() {
  controls.target.set(0, zc, 0);
  camera.position.copy(controls.target).add(dirFromCompass(CFG.camera.azimuth, CFG.camera.elevation).multiplyScalar(startDist));
  controls.update();
}
resetView();

const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 1.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.5);
sun.castShadow = true;
sun.shadow.mapSize.set(4096, 4096);
const shadowRange = N > 1 ? T * 0.95 : H * 1.45;
Object.assign(sun.shadow.camera, { left: -shadowRange, right: shadowRange, top: shadowRange, bottom: -shadowRange, near: 1, far: H * 12 });
// Keep the biases tiny (scaled to one shadow-map texel): large values detach shadows from the building base.
sun.shadow.bias = -0.00005;
sun.shadow.normalBias = Math.min(0.15, 2 * shadowRange / 4096 * 0.5);
scene.add(sun, sun.target);
let sunAz = CFG.sun.bearing, sunEl = CFG.sun.elevation;
function placeSun() {   // the shadow window follows what you are looking at, so shadows stay sharp on big models
  sun.target.position.set(controls.target.x, zc, controls.target.z);
  sun.position.copy(sun.target.position).add(dirFromCompass(sunAz, sunEl).multiplyScalar(H * 5));
}
placeSun();
$('az').value = ((sunAz % 360) + 360) % 360;
$('el').value = sunEl;
$('az').oninput = $('el').oninput = () => { sunAz = +$('az').value; sunEl = +$('el').value; placeSun(); };

// ---------------------------------------------------------------- themes
const root = new THREE.Group();
scene.add(root);
let theme = CFG.themes[CFG.theme];
const impostorUniforms = [];
const placeholderMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.28, depthWrite: false });
function applyThemeTo(obj) {
  obj.traverse(o => {
    if (!o.material || !o.material.color || o.userData.impostor) return;
    if (o.userData.textured) { o.material.color.setRGB(1, 1, 1); return; }   // the photo carries the colour
    const c = o.userData.role && theme.colors[o.userData.role];
    if (c) o.material.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
  });
}
function setThemeColors() {
  const c = theme.colors.tree;
  impostorUniforms.forEach(u => u.uColor.value.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace));
  const bg = new THREE.Color().setStyle(theme.bg, THREE.SRGBColorSpace);
  if (!REAL.on) { scene.background = bg; if (scene.fog) scene.fog.color.copy(bg); }   // the realistic sky takes over otherwise
  placeholderMat.color.setStyle(theme.text, THREE.SRGBColorSpace);
  document.documentElement.style.setProperty('--bg', theme.bg);
  document.documentElement.style.setProperty('--text', theme.text);
  if (REAL.on) paintSeason();
}
function applyTheme(id) {
  theme = CFG.themes[id]; if (!theme) return;
  applyThemeTo(root); setThemeColors(); $('themeSel').value = id;
}
for (const [id, th] of Object.entries(CFG.themes)) {
  const o = document.createElement('option'); o.value = id; o.textContent = th.name; $('themeSel').appendChild(o);
}
$('themeSel').onchange = () => applyTheme($('themeSel').value);
window.setViewerTheme = id => { if (!CFG.themes[id]) return false; applyTheme(id); return true; };   // used by the GUI shell

// ---------------------------------------------------------------- billboard impostors (instanced, GPU-side facing)
function makeTreeTexture() {
  const c = document.createElement('canvas'); c.width = 64; c.height = 128;
  const g = c.getContext('2d');
  g.fillStyle = '#fff';
  for (let k = 0; k < 3; k++) {            // three stacked tiers read as a conifer from far away
    const top = 4 + k * 30, bot = 56 + k * 30, half = 14 + k * 8;
    g.beginPath(); g.moveTo(32, top); g.lineTo(32 + half, bot); g.lineTo(32 - half, bot); g.closePath(); g.fill();
  }
  g.globalCompositeOperation = 'source-atop';
  const gr = g.createLinearGradient(0, 0, 0, 128); gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,0.45)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 128);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.NoColorSpace; return tex;
}
let treeTex = null;
function makeImpostors(arr, n) {
  treeTex = treeTex || makeTreeTexture();
  const geo = new THREE.InstancedBufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute([-0.5, 0, 0, 0.5, 0, 0, 0.5, 1, 0, -0.5, 1, 0], 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  geo.setIndex([0, 1, 2, 0, 2, 3]);
  const ib = new THREE.InstancedInterleavedBuffer(arr, 6);
  geo.setAttribute('iPos', new THREE.InterleavedBufferAttribute(ib, 3, 0));
  geo.setAttribute('iSize', new THREE.InterleavedBufferAttribute(ib, 2, 3));
  geo.setAttribute('iTint', new THREE.InterleavedBufferAttribute(ib, 1, 5));
  geo.instanceCount = n;
  const uniforms = THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uTex: { value: treeTex }, uColor: { value: new THREE.Color() } }]);
  uniforms.uBF = heightU.uBF;
  impostorUniforms.push(uniforms);
  const mat = new THREE.ShaderMaterial({
    uniforms, fog: true, side: THREE.DoubleSide,
    vertexShader: `
      attribute vec3 iPos; attribute vec2 iSize; attribute float iTint; uniform float uBF;
      varying vec2 vUv; varying float vTint;
      #include <fog_pars_vertex>
      void main() {
        vec3 right = normalize(vec3(viewMatrix[0][0], 0.0, viewMatrix[2][0]));
        vec3 wp = iPos + right * position.x * iSize.x + vec3(0.0, 1.0, 0.0) * position.y * iSize.y * uBF;
        vec4 mvPosition = viewMatrix * modelMatrix * vec4(wp, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        vUv = uv; vTint = iTint;
        #include <fog_vertex>
      }`,
    fragmentShader: `
      uniform sampler2D uTex; uniform vec3 uColor; varying vec2 vUv; varying float vTint;
      #include <fog_pars_fragment>
      void main() {
        vec4 t = texture2D(uTex, vUv);
        if (t.a < 0.5) discard;
        gl_FragColor = vec4(uColor * vTint * t.rgb * 0.85, 1.0);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
  });
  const m = new THREE.Mesh(geo, mat);
  m.frustumCulled = false; m.userData.impostor = true; m.userData.role = 'tree'; m.userData.group = 'tree';
  return m;
}

// ---------------------------------------------------------------- tiles, lazily loaded detail levels
const layers = { building: true, tree: true, roads: true, traffic: true };
const tiles = new Map();
for (const g of CFG.groups) {
  const key = g.chunk.join(',');
  if (!tiles.has(key)) tiles.set(key, { key, center: new THREE.Vector3(g.center[0], zc, g.center[1]), levels: {}, shown: -1, explored: false });
  tiles.get(key).levels[g.lod] = { cfg: g, obj: null, state: 'idle', tris: 0 };
}
const tileList = [...tiles.values()];
const maxLod = CFG.maxLod;
let loadedGroups = 0;

function setLayers(obj) {
  obj.traverse(o => { const g = o.userData.group; if (g && g in layers) o.visible = layers[g]; });
}

// ---------------------------------------------------------------- real imagery (Esri World Imagery, as in vvd-kiosk)
// Ground, forest and parks take the satellite picture as a map overlay; buildings take it too, so roofs show
// real roofs and walls show the real surroundings. Tiles are fetched in the browser, one square per model tile,
// and cached. The model's own planar UVs (top-down) place the picture on the geometry.
const IMG_KIND = { ground: 'sat', forest: 'sat', park: 'sat', building: 'bld' };
const imgOn = { sat: false, bld: false };
const SAT_CREDIT = 'Imagerie Esri, Maxar, Earthstar Geographics';
const GEO0 = CFG.geo || { lat: 0, lon: 0, half: H };
function metresToLonLat(x, y) {   // model-local metres (east, north) -> lon/lat; linear is exact to well under a metre over a tile
  const f = GEO0.lat * Math.PI / 180;
  const mLat = 111132.92 - 559.82 * Math.cos(2 * f) + 1.175 * Math.cos(4 * f);
  const mLon = 111412.84 * Math.cos(f) - 93.5 * Math.cos(3 * f);
  return [GEO0.lon + x / mLon, GEO0.lat + y / mLat];
}
const tileSlots = { free: 8, queue: [] };
function withTileSlot(fn) {
  return new Promise((resolve, reject) => {
    const run = () => {
      tileSlots.free--;
      fn().then(resolve, reject).finally(() => { tileSlots.free++; const next = tileSlots.queue.shift(); if (next) next(); });
    };
    if (tileSlots.free > 0) run(); else tileSlots.queue.push(run);
  });
}
const satBitmaps = new Map();   // "z/x/y" -> Promise<ImageBitmap | null>
function satBitmap(z, x, y) {
  const k = z + '/' + x + '/' + y;
  if (!satBitmaps.has(k)) {
    if (satBitmaps.size > 400) satBitmaps.delete(satBitmaps.keys().next().value);
    satBitmaps.set(k, withTileSlot(async () => {
      const r = await fetch(`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`);
      if (!r.ok) return null;
      const blob = await r.blob();
      return blob.size < 32 ? null : createImageBitmap(blob);
    }).catch(() => null));
  }
  return satBitmaps.get(k);
}
/** One canvas texture covering the tile square: north is up, so v = 1 at the north edge. Null when offline. */
async function buildSatTexture(tile) {
  const th = T / 2, cx = tile.center.x, my = -tile.center.z;
  const [w, s] = metresToLonLat(cx - th, my - th), [e, n] = metresToLonLat(cx + th, my + th);
  const S = N > 1 ? 1024 : 2048;
  const merc = (lon, lat, z) => [(lon + 180) / 360 * 2 ** z,
    (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * 2 ** z];
  const tile2lon = (x, z) => x / 2 ** z * 360 - 180;
  const tile2lat = (y, z) => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / 2 ** z))) * 180 / Math.PI;
  // Zoom that gives about one picture pixel per canvas pixel, lowered until the tile needs a sane number of fetches
  let z = Math.min(18, Math.max(10, Math.round(Math.log2(156543.03 * Math.cos(GEO0.lat * Math.PI / 180) / (T / S)))));
  let x0, x1, y0, y1;
  for (;;) {
    const a = merc(w, n, z), b = merc(e, s, z);
    x0 = Math.floor(a[0]); x1 = Math.floor(b[0]); y0 = Math.floor(a[1]); y1 = Math.floor(b[1]);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) <= 144 || z <= 10) break;
    z -= 1;
  }
  const cells = [];
  for (let ty = y0; ty <= y1; ty++) for (let tx = x0; tx <= x1; tx++) cells.push([tx, ty]);
  const bitmaps = await Promise.all(cells.map(([tx, ty]) => satBitmap(z, tx, ty)));
  if (!bitmaps.some(Boolean)) return null;
  const c = document.createElement('canvas'); c.width = c.height = S;
  const g = c.getContext('2d');
  g.fillStyle = '#1a1c16'; g.fillRect(0, 0, S, S);
  const px = lon => (lon - w) / (e - w) * S, py = lat => (n - lat) / (n - s) * S;
  cells.forEach(([tx, ty], i) => {
    const bmp = bitmaps[i]; if (!bmp) return;
    const l = px(tile2lon(tx, z)), r = px(tile2lon(tx + 1, z)), t = py(tile2lat(ty, z)), b = py(tile2lat(ty + 1, z));
    g.drawImage(bmp, l, t, r - l, b - t);
  });
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}
function satTexture(tile) {
  if (!tile.sat) tile.sat = buildSatTexture(tile).catch(() => null);
  return tile.sat;
}
/** Planar UVs from the top-down position: the tile square maps onto [0, 1] (u east, v north). */
function tileUV(pos, tile) {
  const th = T / 2, x0 = tile.center.x - th, zN = tile.center.z - th, n = pos.length / 3;
  const uv = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    uv[i * 2] = (pos[i * 3] - x0) / T;                 // viewer x = east
    uv[i * 2 + 1] = (zN + T - pos[i * 3 + 2]) / T;     // viewer z = -north: the north edge (zN) gives v = 1
  }
  return uv;
}
/** Put the picture on (or take it off) every loaded mesh of ``tile`` that the imagery toggles cover. */
async function syncImagery(tile) {
  const meshes = [];
  for (const lv of Object.values(tile.levels)) if (lv.obj) lv.obj.traverse(o => { if (o.userData.img && o.material) meshes.push(o); });
  if (!meshes.length) return;
  const tex = meshes.some(m => imgOn[m.userData.img]) ? await satTexture(tile) : null;
  for (const m of meshes) {
    const on = !!tex && imgOn[m.userData.img];
    m.userData.textured = on;
    if (m.material.map !== (on ? tex : null)) { m.material.map = on ? tex : null; m.material.needsUpdate = true; }
    applyThemeTo(m);
  }
  updateCredits();
}
function updateCredits() {
  const parts = [CFG.credits, imgOn.sat || imgOn.bld ? SAT_CREDIT : ''].filter(Boolean);
  $('credits').textContent = parts.join(' · ');
}
async function loadGroup(tile, lod) {
  const lv = tile.levels[lod]; if (!lv || lv.state !== 'idle') return;
  lv.state = 'loading';
  const el = lv.cfg.id ? $(lv.cfg.id) : null;  // model tiles keep their data in a <script>; chunks carry it inline
  const buf = await inflate(lv.cfg.b64 || el.textContent.trim());
  lv.cfg.b64 = null; if (el) el.textContent = '';   // free the base64 text once decoded
  const group = new THREE.Group(); group.visible = false;
  for (const m of lv.cfg.meshes) {
    const g = new THREE.BufferGeometry();
    const posArr = new Float32Array(buf, m.pos, m.nv * 3);
    g.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
    if (IMG_KIND[m.role]) g.setAttribute('uv', new THREE.BufferAttribute(tileUV(posArr, tile), 2));
    g.setIndex(new THREE.BufferAttribute(m.idx16 ? new Uint16Array(buf, m.idx, m.nf * 3) : new Uint32Array(buf, m.idx, m.nf * 3), 1));
    if (m.tint !== undefined) {
      const bytes = new Uint8Array(buf, m.tint, m.nv), t = new Float32Array(m.nv * 3);
      for (let i = 0; i < m.nv; i++) { const v = bytes[i] / 127.5; t[i * 3] = t[i * 3 + 1] = t[i * 3 + 2] = v; }
      g.setAttribute('color', new THREE.BufferAttribute(t, 3));
    }
    if (m.nrm !== undefined) g.setAttribute('normal', new THREE.BufferAttribute(new Int8Array(buf, m.nrm, m.nv * 3), 3, true));
    else if (!m.flat) g.computeVertexNormals();
    g.computeBoundingSphere();
    const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: m.tint !== undefined, flatShading: m.flat,
      side: m.role === 'bridge_side' ? THREE.DoubleSide : THREE.FrontSide });
    if (m.overlay) { mat.polygonOffset = true; mat.polygonOffsetFactor = -2; mat.polygonOffsetUnits = -2; }
    if (m.role === 'building') addWindows(mat);
    if (m.base !== undefined) g.setAttribute('aBase', new THREE.BufferAttribute(new Float32Array(buf, m.base, m.nv), 1));
    if (m.base !== undefined) heightScale(mat);
    const mesh = new THREE.Mesh(g, mat);
    if (m.base !== undefined && m.cast) {
      mesh.customDepthMaterial = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }); heightScale(mesh.customDepthMaterial);
    }
    Object.assign(mesh.userData, { role: m.role, group: m.group, cast: m.cast, receive: m.receive, img: IMG_KIND[m.role] });
    group.add(mesh); lv.tris += m.nf;
  }
  if (lv.cfg.imp) {
    group.add(makeImpostors(new Float32Array(buf, lv.cfg.imp.off, lv.cfg.imp.n * 6), lv.cfg.imp.n));
    lv.tris += lv.cfg.imp.n * 2;
  }
  applyThemeTo(group); setThemeColors();
  root.add(group); lv.obj = group; lv.state = 'ready'; loadedGroups++;
  setLayers(group);
  if (imgOn.sat || imgOn.bld) syncImagery(tile);
}
for (const [id, kind] of [['satOn', 'sat'], ['texOn', 'bld']]) {
  $(id).onchange = () => { imgOn[kind] = $(id).checked; tileList.forEach(t => syncImagery(t)); updateCredits(); };
}
document.querySelectorAll('input[data-group]').forEach(cb => {
  cb.onchange = () => { layers[cb.dataset.group] = cb.checked; tileList.forEach(t => Object.values(t.levels).forEach(l => l.obj && setLayers(l.obj))); };
});
// Terrain exaggeration scales the whole model vertically; building height then rescales heights above the ground
// (per-vertex 'aBase') so the two are independent. Both start at the values the model was built with.
const EXC = CFG.exag || { terrain: 1, building: 1 };   // models made before this setting existed
const EX = { t: Math.max(EXC.terrain, 0.01), b: Math.max(EXC.building, 0.01) };
const heightU = { uBF: { value: 1 } };
function heightScale(mat) {
  const prev = mat.onBeforeCompile;
  mat.onBeforeCompile = (sh, r) => {
    if (prev) prev(sh, r);
    sh.uniforms.uBF = heightU.uBF;
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nattribute float aBase; uniform float uBF;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed.y = aBase + (transformed.y - aBase) * uBF;');
  };
  mat.customProgramCacheKey = () => 'hs' + (prev ? 'w' : '');
}
function applyExaggeration() {
  const T = +$('exag').value, B = +$('bexag').value;
  $('vExagO').textContent = T.toFixed(1) + '×'; $('vBexagO').textContent = B.toFixed(1) + '×';
  root.scale.y = T / EX.t;
  heightU.uBF.value = (B / EX.b) / (T / EX.t);
}
$('exag').oninput = $('bexag').oninput = applyExaggeration;

// ---------------------------------------------------------------- traffic: cars that obey the real signals and stop signs
const trafficRoot = new THREE.Group();
root.add(trafficRoot);
let trafficUpdate = null, trafficLights = null;
if (CFG.traffic && CFG.traffic.edges.length) {
  const tc = CFG.traffic, sim = TrafficSim.createSim(tc, { seed: tc.seed });
  const L = tc.car_len, LANE = Math.min(1.7, L * 0.34);   // cars keep to the right-hand lane
  const SPEC = TrafficSim.TYPES;
  // Side profiles in units of L (x = length, y = height, front at +x), extruded across the width. Real proportions.
  const prof = (pts, depth) => {
    const sh = new THREE.Shape(); pts.forEach(([x, y], i) => i ? sh.lineTo(x * L, y * L) : sh.moveTo(x * L, y * L));
    const g = new THREE.ExtrudeGeometry(sh, { depth: depth, bevelEnabled: false }); g.translate(0, 0, -depth / 2); return g;
  };
  const scaled = (pts, xs, ys) => pts.map(([x, y]) => [x * xs, y * ys]);
  const HATCH = [[-.5, .09], [.5, .09], [.5, .27], [.42, .31], [.2, .34], [.1, .5], [-.2, .5], [-.38, .36], [-.5, .33]];
  const HATCH_GLASS = [[.17, .345], [.09, .47], [-.19, .47], [-.34, .355]];
  const MODELS = [
    { body: scaled(HATCH, 0.82, 0.92), glass: scaled(HATCH_GLASS, 0.82, 0.92), axles: [0.255, -0.255], wheel: 0.08,
      colors: [0xe9ecef, 0xb8bec6, 0x8a2527, 0x1f3350, 0xc9b99a, 0xf1f1ee, 0x2f4a3a, 0xd9a21b] },                    // small car
    { body: HATCH, glass: HATCH_GLASS, axles: [0.31, -0.31], wheel: 0.085,
      colors: [0xe9ecef, 0xb8bec6, 0x6b7178, 0x23262b, 0x1f3350, 0x8a2527, 0x2f4a3a, 0xc9b99a, 0xf1f1ee, 0x3d4650] },  // car
    { body: [[-.65, .10], [.65, .10], [.65, .27], [.56, .32], [.34, .35], [.27, .37], [.12, .58], [-.12, .58], [-.2, .44], [-.65, .44]],
      glass: [[.235, .375], [.115, .55], [-.105, .55], [-.175, .445]], axles: [0.42, -0.42], wheel: 0.095,
      colors: [0x23262b, 0x8a2527, 0xe9ecef, 0x2f4a3a, 0x6b7178, 0x1f3350, 0xb8bec6] },                               // pickup truck
    { body: [[-1.15, .12], [1.15, .12], [1.15, .36], [1.08, .41], [.94, .44], [.88, .74], [.62, .76], [.58, .54], [.46, .54], [.46, .86], [-1.15, .86]],
      glass: [[.90, .47], [.865, .70], [.65, .71], [.63, .49]], axles: [0.78, -0.55, -0.9], wheel: 0.1,
      colors: [0xf1f1ee, 0xe9ecef, 0xb8bec6, 0x1f3350, 0x8a2527, 0x2f4a3a, 0xd9a21b] },                               // lorry
  ];
  const LIGHT = { g: new THREE.Color(0x2ecc71), a: new THREE.Color(0xffb300), r: new THREE.Color(0xe53935) };
  const count = SPEC.map(() => 0), loc = [];
  sim.cars.forEach(c => { loc.push(count[c.type]++); });
  const col = new THREE.Color(), parts = [];
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.55, metalness: 0.05 });
  const glassMat = new THREE.MeshStandardMaterial({ color: 0x141a22, roughness: 0.15, metalness: 0.3 });
  const wheelMat = new THREE.MeshLambertMaterial({ color: 0x111111 });
  const kinds = MODELS.map((m, t) => {
    const W = L * 0.4 * SPEC[t].w, n = Math.max(1, count[t]);
    const wheelGeo = new THREE.CylinderGeometry(L * m.wheel, L * m.wheel, W * 1.02, 10).rotateX(Math.PI / 2);
    const bodies = new THREE.InstancedMesh(prof(m.body, W), bodyMat, n);
    const glass = new THREE.InstancedMesh(prof(m.glass, W * 1.03), glassMat, n);
    const wheels = new THREE.InstancedMesh(wheelGeo, wheelMat, n * m.axles.length);
    bodies.count = glass.count = count[t]; wheels.count = count[t] * m.axles.length;
    bodies.castShadow = glass.castShadow = true;
    bodies.frustumCulled = glass.frustumCulled = wheels.frustumCulled = false;
    parts.push(bodies, glass, wheels);
    return { m, W, bodies, glass, wheels, half: SPEC[t].len * L / 2 };
  });
  sim.cars.forEach((c, i) => {
    const k = kinds[c.type], pal = k.m.colors;
    k.bodies.setColorAt(loc[i], col.setHex(pal[(i * 7 + c.type) % pal.length]));
  });
  kinds.forEach(k => { if (k.bodies.instanceColor) k.bodies.instanceColor.needsUpdate = true; });
  // head / tail lights, lit at night in the realistic view
  const carLightPos = new Float32Array(tc.cars * 12), carLightCol = new Float32Array(tc.cars * 12);
  for (let i = 0; i < tc.cars; i++) for (let j = 0; j < 4; j++) carLightCol.set(j < 2 ? [1, 0.93, 0.75] : [1, 0.1, 0.05], i * 12 + j * 3);
  const carLightGeo = new THREE.BufferGeometry();
  carLightGeo.setAttribute('position', new THREE.BufferAttribute(carLightPos, 3));
  carLightGeo.setAttribute('color', new THREE.BufferAttribute(carLightCol, 3));
  trafficLights = new THREE.Points(carLightGeo, new THREE.PointsMaterial({ size: L * 0.22, sizeAttenuation: true, vertexColors: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  trafficLights.frustumCulled = false;
  const ap = sim.approaches;
  const lights = new THREE.InstancedMesh(new THREE.SphereGeometry(L * 0.2, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }), Math.max(1, ap.length));
  lights.frustumCulled = false;
  const lightShown = new Array(ap.length).fill('');
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), qp = new THREE.Quaternion(), p = new THREE.Vector3(), one = new THREE.Vector3(1, 1, 1);
  const up = new THREE.Vector3(0, 1, 0), zAxis = new THREE.Vector3(0, 0, 1), off = new THREE.Vector3();
  ap.forEach((a, i) => { m4.compose(p.set(a.x, a.y + L * 1.3, a.z), q.identity(), one); lights.setMatrixAt(i, m4); });
  lights.instanceMatrix.needsUpdate = true;
  trafficRoot.add(...parts, lights, trafficLights);
  // Rendered pose per car: low-pass filtered so corners, junction hand-overs and stop-and-go never make a car twitch
  const view = [];
  for (let i = 0; i < tc.cars; i++) view.push(null);
  const rearCar = { e: 0, dir: 0, s: 0 };
  trafficUpdate = (dt, real) => {
    sim.step(dt);
    const k = 1 - Math.exp(-Math.min(dt, 0.1) * 12);
    sim.cars.forEach((car, i) => {
      const kind = kinds[car.type], len = car.len, W = kind.W;
      const f = sim.pose(car);                                   // the sim tracks the front bumper
      rearCar.e = car.e; rearCar.dir = car.dir; rearCar.s = Math.max(0, car.s - len * 0.8);
      const r = sim.pose(rearCar);
      let hx = f.x - r.x, hz = f.z - r.z, hl = Math.hypot(hx, hz);
      if (hl < len * 0.3) { hx = f.dx; hz = f.dz; hl = 1; }      // too close to the start of a road: use its direction
      hx /= hl; hz /= hl;
      const rx = -hz, rz = hx;                                   // right of travel
      const tx = (f.x + r.x) / 2 + rx * LANE, tz = (f.z + r.z) / 2 + rz * LANE, ty = (f.y + r.y) / 2;
      let v = view[i];
      const ang = Math.atan2(hz, hx);
      if (!v || Math.hypot(tx - v.x, tz - v.z) > len * 3) v = view[i] = { x: tx, y: ty, z: tz, ang, pitch: 0 };
      else {
        const kp = 1 - Math.exp(-Math.min(dt, 0.1) * 30);       // position tracks closely, heading is damped
        v.x += (tx - v.x) * kp; v.y += (ty - v.y) * kp; v.z += (tz - v.z) * kp;
        let da = ang - v.ang; da -= Math.PI * 2 * Math.round(da / (Math.PI * 2));
        const maxTurn = 2.2 * Math.min(dt, 0.1);                 // rad/s: a car cannot spin on the spot
        v.ang += Math.max(-maxTurn, Math.min(maxTurn, da * (1 - Math.exp(-Math.min(dt, 0.1) * 8))));
      }
      v.hx = Math.cos(v.ang); v.hz = Math.sin(v.ang);
      const pitchT = Math.atan2(f.y - r.y, Math.max(hl, 0.001)); v.pitch += (pitchT - v.pitch) * k;
      q.setFromAxisAngle(up, -v.ang).multiply(qp.setFromAxisAngle(zAxis, v.pitch));
      m4.compose(p.set(v.x, v.y + 0.03, v.z), q, one);
      kind.bodies.setMatrixAt(loc[i], m4); kind.glass.setMatrixAt(loc[i], m4);
      const axles = kind.m.axles;
      for (let w = 0; w < axles.length; w++) {                   // one cylinder across each axle
        off.set(axles[w] * L, L * kind.m.wheel, 0).applyQuaternion(q);
        m4.compose(p.set(v.x + off.x, v.y + 0.03 + off.y, v.z + off.z), q, one); kind.wheels.setMatrixAt(loc[i] * axles.length + w, m4);
      }
      if (real) {
        const P = carLightPos, o = i * 12, sx = -v.hz * W * 0.35, sz = v.hx * W * 0.35, hy = v.y + L * 0.2;
        const fx = v.hx * kind.half, fz = v.hz * kind.half;
        P[o] = v.x + fx + sx; P[o + 1] = hy; P[o + 2] = v.z + fz + sz;
        P[o + 3] = v.x + fx - sx; P[o + 4] = hy; P[o + 5] = v.z + fz - sz;
        P[o + 6] = v.x - fx + sx; P[o + 7] = hy; P[o + 8] = v.z - fz + sz;
        P[o + 9] = v.x - fx - sx; P[o + 10] = hy; P[o + 11] = v.z - fz - sz;
      }
    });
    kinds.forEach(k => { k.bodies.instanceMatrix.needsUpdate = k.glass.instanceMatrix.needsUpdate = k.wheels.instanceMatrix.needsUpdate = true; });
    if (real) carLightGeo.attributes.position.needsUpdate = true;
    carLightGeo.setDrawRange(0, real ? tc.cars * 4 : 0);
    let changed = false;
    ap.forEach((a, i) => {
      const st = sim.lightAt(a);
      if (st !== lightShown[i]) { lightShown[i] = st; lights.setColorAt(i, LIGHT[st]); changed = true; }
    });
    if (changed) lights.instanceColor.needsUpdate = true;
  };
} else {
  $('trafficRow').style.display = 'none';
  $('trafficSpeedRow').style.display = 'none';
  $('tspeed').style.display = 'none';
  $('tspeedO').style.display = 'none';
}

const tsp = $('tspeed'), tspO = $('tspeedO');
const showTsp = () => { tspO.textContent = (+tsp.value).toFixed(2) + '×'; };
tsp.oninput = showTsp; showTsp();

// load queue: coarsest level of every tile first (instant whole-model coverage), then what the camera needs, nearest first
const wanted = new Map();
let pumping = false;
function want(tile, lod, prio) {
  const lv = tile.levels[lod]; if (!lv || lv.state !== 'idle') return;
  const k = tile.key + '/' + lod; const cur = wanted.get(k);
  if (!cur || prio < cur.prio) wanted.set(k, { tile, lod, prio });
  if (!pumping) pump();
}
async function pump() {
  pumping = true;
  while (wanted.size) {
    const next = [...wanted.values()].sort((a, b) => a.prio - b.prio)[0];
    wanted.delete(next.tile.key + '/' + next.lod);
    try { await loadGroup(next.tile, next.lod); }
    catch (e) {                                // one bad chunk must not stall the whole queue
      console.error('chunk failed', next.tile.key, next.lod, e);
      const lv = next.tile.levels[next.lod]; if (lv) lv.state = 'failed';
      loadedGroups++;
    }
    await new Promise(r => setTimeout(r, 0));
  }
  pumping = false;
}

// ---------------------------------------------------------------- distance fog, draw distance, fog of war
const fogOn = $('fogOn'), dd = $('dd'), fowOn = $('fowOn'), shadowsOn = $('shadowsOn'), quality = $('quality');
fogOn.checked = N > 1;
dd.value = N > 1 ? Math.min(14, startDist / T + N * 0.9) : 14;
if (N <= 1) { $('fowRow').style.display = 'none'; }
const plane = new THREE.PlaneGeometry(T * 0.98, T * 0.98); plane.rotateX(-Math.PI / 2);
tileList.forEach(t => {
  t.placeholder = new THREE.Mesh(plane, placeholderMat);
  t.placeholder.position.set(t.center.x, CFG.zrange[0] + T * 0.002, t.center.z); t.placeholder.visible = false; scene.add(t.placeholder);
});
function applyFog() {
  $('ddO').textContent = (+dd.value).toFixed(1) + ' tiles';
  if (REAL.on) { applyRealistic(); return; }
  if (fogOn.checked) { const far = +dd.value * T; scene.fog = new THREE.Fog(scene.background, far * 0.55, far); }
  else scene.fog = null;
  setThemeColors();
  tileList.forEach(t => Object.values(t.levels).forEach(l => l.obj && l.obj.traverse(o => { if (o.material) o.material.needsUpdate = true; })));
}
fogOn.onchange = dd.oninput = applyFog;

// ---------------------------------------------------------------- per-frame tile management
let qualityScale = 1, ratio = dpr, lastFpsT = performance.now(), frames = 0, fps = 60;
function lodFor(d) {
  if (maxLod === 0) return 0;
  const s = T * qualityScale;
  return d < 3.5 * s ? 0 : (d < 7 * s ? Math.min(1, maxLod) : maxLod);
}
function nearestReady(tile, wantLod) {
  const order = [];
  for (let l = 0; l <= maxLod; l++) order.push([Math.abs(l - wantLod) + (l < wantLod ? 0.1 : 0), l]);
  order.sort((a, b) => a[0] - b[0]);
  for (const [, l] of order) if (tile.levels[l] && tile.levels[l].state === 'ready') return l;
  return -1;
}
const cam = new THREE.Vector3();
function updateTiles() {
  cam.copy(camera.position);
  const far = +dd.value * T;
  const wantShadow = shadowsOn.checked && quality.value !== 'low';
  for (const t of tileList) {
    const d = cam.distanceTo(t.center);
    if (controls.target.distanceTo(t.center) < 1.2 * T) t.explored = true;
    // Hidden tiles (past the draw distance, or unexplored under the fog of war) cost nothing: they are not even loaded
    const hidden = (fogOn.checked && d > far * 1.15) || (fowOn.checked && !t.explored);
    const wantLod = lodFor(d);
    if (!hidden) {
      want(t, maxLod, 0);
      if (wantLod !== maxLod) want(t, wantLod, 1 + d / T);
    }
    const show = hidden ? -1 : nearestReady(t, wantLod);
    for (const [l, lv] of Object.entries(t.levels)) if (lv.obj) lv.obj.visible = (+l === show);
    t.shown = show;
    t.placeholder.visible = fowOn.checked && !t.explored;
    if (show >= 0) {
      const casts = wantShadow && show === 0 && d < 2.2 * T;
      t.levels[show].obj.traverse(o => {
        if (o.isMesh && !o.userData.impostor) { o.castShadow = casts && o.userData.cast; o.receiveShadow = wantShadow && o.userData.receive && show === 0; }
      });
    }
  }
  renderer.shadowMap.enabled = wantShadow; sun.castShadow = wantShadow;
  requestChunks();
}
quality.onchange = () => {
  qualityScale = { auto: 1, high: 1.8, low: 0.5 }[quality.value];
  ratio = quality.value === 'low' ? Math.min(dpr, 0.75) : dpr; renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight);
};
shadowsOn.onchange = updateTiles;

// ---------------------------------------------------------------- chunks beyond the model's edge (generated as you go)
// Walking past the outer tiles asks the local server for the next ones (/api/chunk, map3d/chunks.py). A chunk is built
// from real data on the model's own grid, datum and exaggeration. Chunks the view has left far behind are unloaded.
const CH = CFG.chunks || null;
const CHUNK_KEEP = 3, CHUNK_RETRY_MS = 20000;
const chunkFailed = new Map(), chunkBusy = new Set();
let chunkInflight = 0, chunkOff = !CH, chunkNote = '';
const chunkQuery = CH && new URLSearchParams({ lat: CH.lat, lon: CH.lon, half: CH.half, tiles: CH.tiles, tileHalf: CH.tileHalf,
  zmin: CH.zmin, exag: CH.exag, bexag: CH.bexag, maxLod: CH.maxLod, theme: CH.theme });
const HINT_TEXT = $('hint').textContent;
function setChunkNote(msg) { chunkNote = msg; $('hint').textContent = msg ? msg : HINT_TEXT; }
if (CH && !/^https?:$/.test(location.protocol)) { chunkOff = true; setChunkNote('Edge chunks only work when this page is opened from the GUI: run python -m map3d.gui and open the model there.'); }
const cellOf = p => [Math.round(p.x / T + (N - 1) / 2), Math.round(-p.z / T + (N - 1) / 2)];

function addChunk(c, data) {
  if (tiles.has(c.k)) return;
  const cx = (c.i - (N - 1) / 2) * T, cz = -(c.j - (N - 1) / 2) * T;
  const t = { key: c.k, ij: [c.i, c.j], extra: true, center: new THREE.Vector3(cx, zc, cz), levels: {}, shown: -1, explored: false };
  t.placeholder = new THREE.Mesh(plane, placeholderMat);
  t.placeholder.position.set(cx, CFG.zrange[0] + T * 0.002, cz); t.placeholder.visible = false; scene.add(t.placeholder);
  for (const g of data.groups) {
    t.levels[g.lod] = { cfg: Object.assign(g, { b64: data.blobs[g.id] }), obj: null, state: 'idle', tris: 0 };
    CFG.groups.push(g);                      // so the stats count chunk groups too
  }
  tiles.set(c.k, t); tileList.push(t);
}
function unloadTile(t) {
  if (Object.values(t.levels).some(l => l.state === 'loading')) return;   // finish the download first
  for (let l = 0; l <= maxLod; l++) wanted.delete(t.key + '/' + l);
  for (const lv of Object.values(t.levels)) if (lv.obj) {
    root.remove(lv.obj);
    lv.obj.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  }
  scene.remove(t.placeholder);
  if (t.sat) t.sat.then(tex => tex && tex.dispose());
  tiles.delete(t.key); tileList.splice(tileList.indexOf(t), 1);
}
// Loading skeleton: a pulsing tile outline where a chunk is being generated; removed when the chunk arrives or fails.
const skelMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.2, depthWrite: false, side: THREE.DoubleSide });
const skelLineMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.6 });
const skeletons = new Map();
function showSkeleton(c) {
  const cx = (c.i - (N - 1) / 2) * T, cz = -(c.j - (N - 1) / 2) * T;
  const g = new THREE.Group();
  g.add(new THREE.Mesh(plane, skelMat), new THREE.LineSegments(new THREE.EdgesGeometry(plane), skelLineMat));
  g.position.set(cx, CFG.zrange[0] + T * 0.002, cz);
  scene.add(g); skeletons.set(c.k, g);
}
function hideSkeleton(k) {
  const g = skeletons.get(k); if (!g) return;
  scene.remove(g); g.children[1].geometry.dispose(); skeletons.delete(k);
}
async function loadChunk(c) {
  chunkBusy.add(c.k); chunkInflight++; showSkeleton(c);
  try {
    const r = await fetch('/api/chunk?' + chunkQuery + `&i=${c.i}&j=${c.j}`, { cache: 'no-store' });
    if (r.status === 404) throw new TypeError('no chunk route');   // an older GUI process without the route
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
    addChunk(c, await r.json());
    if (chunkNote) setChunkNote('');
  } catch (e) {
    if (e instanceof TypeError) {            // no server, or no chunk route: stop asking, the model itself keeps working
      chunkOff = true;
      setChunkNote('Edge chunks are not available: restart the GUI (python -m map3d.gui) and reopen the model.');
    } else {
      chunkFailed.set(c.k, performance.now()); console.warn('chunk failed', c.k, e);
      setChunkNote('Could not build the next chunk (see the terminal running the GUI). Retrying.');
    }
  } finally { hideSkeleton(c.k); chunkBusy.delete(c.k); chunkInflight--; }
}
// Runs with the frame updates: the cells beside the view target (within one tile of it) are requested nearest first,
// two at a time; cells far behind the target are unloaded. The model's own tiles are never unloaded.
function requestChunks() {
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 350);
  skelMat.opacity = 0.12 + 0.12 * pulse; skelLineMat.opacity = 0.35 + 0.35 * pulse;
  if (chunkOff) return;
  const [ci, cj] = cellOf(controls.target);
  for (const t of [...tileList]) {
    if (!t.extra) continue;
    if (Math.max(Math.abs(t.ij[0] - ci), Math.abs(t.ij[1] - cj)) > CHUNK_KEEP) unloadTile(t);
  }
  if (chunkInflight >= 2) return;
  const now = performance.now();
  let best = null;
  for (let j = cj - 1; j <= cj + 1; j++) for (let i = ci - 1; i <= ci + 1; i++) {
    const k = i + ',' + j;
    if (tiles.has(k) || chunkBusy.has(k)) continue;
    const f = chunkFailed.get(k); if (f !== undefined && now - f < CHUNK_RETRY_MS) continue;
    const cx = (i - (N - 1) / 2) * T, cz = -(j - (N - 1) / 2) * T;
    const d = Math.hypot(cx - controls.target.x, cz - controls.target.z);
    if (d < T && (!best || d < best.d)) best = { i, j, k, d };
  }
  if (best) loadChunk(best);
}

function adapt(now) {
  frames++;
  if (now - lastFpsT < 1000) return;
  fps = frames * 1000 / (now - lastFpsT); frames = 0; lastFpsT = now;
  if (quality.value === 'auto') {            // adaptive resolution: trade sharpness for smoothness, and back
    const next = fps < 26 ? Math.max(0.6, ratio - 0.2) : (fps > 56 ? Math.min(dpr, ratio + 0.1) : ratio);
    if (Math.abs(next - ratio) > 1e-3) { ratio = next; renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight); }
  }
  const info = renderer.info.render;
  $('stats').textContent = `${fps.toFixed(0)} fps · ${(info.triangles / 1000).toFixed(0)}k tris · ${info.calls} calls\n` +
    `res ${(ratio * 100).toFixed(0)}% · ${loadedGroups}/${CFG.groups.length} chunks · ${tileList.filter(t => t.shown >= 0).length}/${tileList.length} tiles`;
}

$('rot').onclick = () => { controls.autoRotate = !controls.autoRotate; controls.autoRotateSpeed = +$('rotSpeed').value; $('rot').style.opacity = controls.autoRotate ? 1 : .7; };
$('rotSpeed').oninput = () => { controls.autoRotateSpeed = +$('rotSpeed').value; if (!controls.autoRotate) { controls.autoRotate = true; $('rot').style.opacity = 1; } };
$('reset').onclick = () => {
  controls.autoRotate = false; resetView();
  tileList.forEach(t => { t.explored = t.center.distanceTo(controls.target) < 0.5 * T; });
};
$('shot').onclick = () => {
  renderer.render(scene, camera);
  renderer.domElement.toBlob(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b);
    a.download = (CFG.title || 'city') + '-3d.png'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); });
};
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);
});
$('exag').value = EX.t; $('bexag').value = EX.b; applyExaggeration();

// ---------------------------------------------------------------- Ctrl+F place search
const placeSearch = PlaceSearch.create({ THREE, scene, root, camera, controls, CFG, input: $('find'), list: $('findList'),
  onFlying: () => { controls.autoRotate = false; } });
addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); placeSearch.open(); }
});
// WASD moves the view over the ground (relative to where the camera looks), Q lowers it, E raises it; Shift is faster.
const held = new Set(), fwd = new THREE.Vector3(), rgt = new THREE.Vector3(), mv = new THREE.Vector3();
const typing = e => /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
addEventListener('keydown', e => {
  if (!typing(e) && !e.ctrlKey && !e.metaKey && !e.altKey && 'wasdqe'.includes(e.key.toLowerCase()) && e.key.length === 1) {
    held.add(e.key.toLowerCase()); placeSearch.cancelFly && placeSearch.cancelFly();
  }
});
addEventListener('keyup', e => held.delete(e.key.toLowerCase()));
addEventListener('blur', () => held.clear());
function keyMove(dt) {
  if (!held.size) return;
  camera.getWorldDirection(fwd); fwd.y = 0;
  if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1);
  fwd.normalize(); rgt.set(-fwd.z, 0, fwd.x);
  mv.set(0, 0, 0);
  if (held.has('w')) mv.add(fwd);
  if (held.has('s')) mv.sub(fwd);
  if (held.has('d')) mv.add(rgt);
  if (held.has('a')) mv.sub(rgt);
  if (held.has('e')) mv.y += 1;
  if (held.has('q')) mv.y -= 1;
  if (mv.lengthSq() === 0) return;
  const speed = Math.max(controls.getDistance() * 0.8, T * 0.03) * (shiftDown ? 3 : 1);
  mv.normalize().multiplyScalar(speed * dt);
  if (controls.target.y + mv.y < 0 && mv.y < 0) mv.y = Math.max(mv.y, -controls.target.y);
  camera.position.add(mv); controls.target.add(mv);
  controls.autoRotate = false;
}
let shiftDown = false;
addEventListener('keydown', e => { if (e.key === 'Shift') shiftDown = true; });
addEventListener('keyup', e => { if (e.key === 'Shift') shiftDown = false; });
addEventListener('message', e => { if (e.data && e.data.type === 'find') placeSearch.open(); });   // from the GUI shell

// ---------------------------------------------------------------- realistic view
// Sun, sky, weather, season, night lights and traffic, from the model's location and the current date and time.
const WHITE = new THREE.Color(1, 1, 1);
const SNOW = new THREE.Color(0.93, 0.95, 0.97), WINTER_TREE = new THREE.Color(0.5, 0.42, 0.33), WINTER_GROUND = new THREE.Color(0.72, 0.66, 0.52);
const SKY_DAY = new THREE.Color(0.5, 0.7, 0.92), SKY_CLOUD = new THREE.Color(0.62, 0.66, 0.7);
const SKY_DUSK = new THREE.Color(0.9, 0.5, 0.32), SKY_NIGHT = new THREE.Color(0.015, 0.025, 0.06);
const SUN_LOW = new THREE.Color(1, 0.6, 0.35), SUN_HIGH = new THREE.Color(1, 0.97, 0.92);
const HEMI_DAY = new THREE.Color(0.78, 0.86, 1), HEMI_NIGHT = new THREE.Color(0.1, 0.13, 0.26);
const TRAFFIC_HOURS = [[0, 0.06], [5, 0.2], [6.5, 0.8], [8, 1], [9.5, 0.6], [12, 0.5], [15, 0.65], [17, 0.95], [18.5, 0.75], [20, 0.4], [22, 0.2], [24, 0.06]];
const MAX_CARS = 900, RAIN_DROPS = 2400, SNOW_FLAKES = 2400, STARS = 1400, LAMP_SPACING = 22;
const REAL = { on: false, weather: null, override: null, roads: null, cars: null, lamps: null, stars: null,
  rain: null, snow: null, night: 0, traffic: 0, season: 1, snowCover: 0, cloud: 0, cond: {}, saved: null, t: 0 };
const winU = { uNight: { value: 0 }, uLit: { value: 0.5 }, uSnow: { value: 0 } };
const clamp01 = v => Math.min(1, Math.max(0, v));
const rnd = (a, b) => a + Math.random() * (b - a);

// Building walls get lit windows and snowy roofs in the shader: a window grid in world space, lit per cell.
function addWindows(mat) {
  mat.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, winU);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWp;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvWp = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
varying vec3 vWp;
uniform float uNight; uniform float uLit; uniform float uSnow;
float hash21(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }`)
      .replace('#include <color_fragment>', `#include <color_fragment>
  vec3 fN = normalize(cross(dFdx(vWp), dFdy(vWp)));
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.94, 0.96, 0.98), uSnow * smoothstep(0.6, 0.85, fN.y));`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
  {
    vec3 tN = normalize(cross(fN, vec3(0.0, 1.0, 0.0)));
    float wall = 1.0 - smoothstep(0.45, 0.75, abs(fN.y));
    vec2 cell = vec2(dot(vWp, tN) / 3.2, vWp.y / 3.4);
    vec2 f = fract(cell);
    float inWin = step(0.28, f.x) * step(f.x, 0.72) * step(0.3, f.y) * step(f.y, 0.75);
    float lit = step(hash21(floor(cell)), uLit);
    totalEmissiveRadiance += vec3(1.0, 0.8, 0.5) * 1.1 * wall * inWin * lit * uNight;
  }`);
  };
  mat.customProgramCacheKey = () => 'windows';
}

// Sun altitude and compass bearing (degrees) for a UTC instant at a place: the standard low-precision solar formulas.
function sunPos(ms, latDeg, lonDeg) {
  const rad = Math.PI / 180, d = ms / 86400000 - 0.5 + 2440588 - 2451545;
  const M = rad * (357.5291 + 0.98560028 * d);
  const C = rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + rad * 102.9372 + Math.PI, e = rad * 23.4397;
  const ra = Math.atan2(Math.sin(L) * Math.cos(e), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const H = rad * (280.16 + 360.9856235 * d + lonDeg) - ra;
  const lat = rad * latDeg;
  const alt = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(lat) - Math.tan(dec) * Math.cos(lat));
  return { alt: alt / rad, bearing: (az / rad + 180) % 360 };
}

// 0 = deep winter, 1 = midsummer, from the day of the year and the hemisphere.
function seasonK(now) {
  const doy = (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(now.getFullYear(), 0, 0)) / 86400000;
  const s = CFG.geo.lat >= 0 ? 1 : -1;
  return (s * Math.cos(2 * Math.PI * (doy - 172) / 365) + 1) / 2;
}

// Relative traffic by hour: morning and evening peaks, quiet nights; weekends are about 40% quieter.
function trafficAt(h, weekend) {
  let v = TRAFFIC_HOURS[TRAFFIC_HOURS.length - 1][1];
  for (let i = 0; i < TRAFFIC_HOURS.length - 1; i++) {
    const [h0, v0] = TRAFFIC_HOURS[i], [h1, v1] = TRAFFIC_HOURS[i + 1];
    if (h >= h0 && h <= h1) { v = v0 + (v1 - v0) * (h - h0) / (h1 - h0); break; }
  }
  return weekend ? v * 0.6 : v;
}

function conditions(w) {   // WMO weather codes from Open-Meteo, backed by the precipitation amounts
  const c = w ? w.code : 0;
  const snowing = !!w && (w.snow > 0 || (c >= 71 && c <= 77) || c === 85 || c === 86);
  const raining = !!w && !snowing && (w.rain > 0 || (c >= 51 && c <= 67) || (c >= 80 && c <= 82) || c >= 95);
  return { snowing, raining, foggy: !!w && (c === 45 || c === 48), storm: !!w && c >= 95 };
}

function describeWeather(w) {
  if (!w) return 'weather unavailable (offline): clear sky assumed';
  const cd = conditions(w);
  const sky = cd.foggy ? 'fog' : cd.storm ? 'thunderstorm' : cd.snowing ? 'snow' : cd.raining ? 'rain'
    : w.cloud > 0.8 ? 'overcast' : w.cloud > 0.4 ? 'partly cloudy' : 'clear';
  return (w.temp == null ? '' : Math.round(w.temp) + ' °C · ') + sky;
}

async function fetchWeather() {   // current conditions at the model's centre; only the coordinates are sent
  REAL.weather = null;
  try {
    const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + CFG.geo.lat.toFixed(3) + '&longitude=' + CFG.geo.lon.toFixed(3)
      + '&current=temperature_2m,cloud_cover,rain,snowfall,weather_code&timezone=auto';
    const c = (await (await fetch(url)).json()).current;
    REAL.weather = { temp: c.temperature_2m, cloud: c.cloud_cover / 100, rain: c.rain, snow: c.snowfall, code: c.weather_code };
  } catch (e) { REAL.weather = null; }
  if (REAL.on) applyRealistic();
}

function realDate() {
  const d = new Date();
  if (REAL.override !== null) d.setHours(Math.floor(REAL.override), Math.round((REAL.override % 1) * 60), 0, 0);
  return d;
}

// Road centre lines as arrays of points with cumulative lengths, for cars.
function buildRoads() {
  if (REAL.roads) return REAL.roads;
  REAL.roads = [];
  for (const flat of CFG.roads || []) {
    const pts = new Float32Array(flat), n = pts.length / 3, cum = new Float64Array(n);
    for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + Math.hypot(pts[i * 3] - pts[(i - 1) * 3], pts[i * 3 + 2] - pts[(i - 1) * 3 + 2]);
    if (cum[n - 1] > 1) REAL.roads.push({ pts, cum, len: cum[n - 1] });
  }
  return REAL.roads;
}
const P_AT = { x: 0, y: 0, z: 0, tx: 0, tz: 1 };
function pointAt(road, s) {   // position and unit horizontal tangent at distance s along a road
  const cum = road.cum, pts = road.pts;
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
  const t = clamp01((s - cum[lo]) / Math.max(1e-6, cum[hi] - cum[lo])), a = lo * 3, b = hi * 3;
  P_AT.x = pts[a] + (pts[b] - pts[a]) * t; P_AT.y = pts[a + 1] + (pts[b + 1] - pts[a + 1]) * t;
  P_AT.z = pts[a + 2] + (pts[b + 2] - pts[a + 2]) * t;
  const dx = pts[b] - pts[a], dz = pts[b + 2] - pts[a + 2], L = Math.hypot(dx, dz) || 1;
  P_AT.tx = dx / L; P_AT.tz = dz / L;
  return P_AT;
}
function pickRoad(roads, total) {   // a road, chosen in proportion to its length
  let r = Math.random() * total;
  for (const road of roads) { r -= road.len; if (r <= 0) return road; }
  return roads[roads.length - 1];
}

// Created on first use: cars, street lights, rain, snow and stars. Everything on the road network is in the root group,
// so it follows the vertical exaggeration like the buildings do.
function ensureRealistic() {
  if (REAL.cars) return;
  const roads = buildRoads(), H = CFG.half, yLo = CFG.zrange[0], yHi = CFG.zrange[1] + H * 0.08;
  const total = roads.reduce((a, r) => a + r.len, 0);
  const body = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshLambertMaterial({ color: 0xffffff }), MAX_CARS);
  body.frustumCulled = false; body.count = 0;
  const palette = [0xf2f2f2, 0x25282d, 0x9aa1aa, 0x8c1c1c, 0x1f3a5f, 0xd9d4c7, 0x4a4f55, 0xe0b040];
  const lightPos = new Float32Array(MAX_CARS * 12), lightCol = new Float32Array(MAX_CARS * 12), state = [];
  const col = new THREE.Color();
  for (let i = 0; roads.length && i < MAX_CARS; i++) {
    col.setHex(palette[Math.floor(Math.random() * palette.length)]);
    body.setColorAt(i, col);
    const road = pickRoad(roads, total);
    state.push({ road, s: Math.random() * road.len, dir: Math.random() < 0.5 ? 1 : -1, v: 8 + Math.random() * 6 });
    for (let j = 0; j < 4; j++) lightCol.set(j < 2 ? [1, 0.95, 0.8] : [1, 0.12, 0.06], i * 12 + j * 3);
  }
  const lightGeo = new THREE.BufferGeometry();
  lightGeo.setAttribute('position', new THREE.BufferAttribute(lightPos, 3));
  lightGeo.setAttribute('color', new THREE.BufferAttribute(lightCol, 3));
  lightGeo.setDrawRange(0, 0);
  const lights = new THREE.Points(lightGeo, new THREE.PointsMaterial({ size: H * 0.005, sizeAttenuation: true, vertexColors: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  lights.frustumCulled = false;
  REAL.cars = { body, lights, lightGeo, lightPos, state, dummy: new THREE.Object3D() };
  root.add(body, lights);

  const lampPos = [];
  for (const r of roads) for (let s = LAMP_SPACING / 2; s < r.len && lampPos.length < 90000; s += LAMP_SPACING) {
    const p = pointAt(r, s); lampPos.push(p.x, p.y + 6.5, p.z);
  }
  const lampGeo = new THREE.BufferGeometry();
  lampGeo.setAttribute('position', new THREE.Float32BufferAttribute(lampPos, 3));
  REAL.lamps = new THREE.Points(lampGeo, new THREE.PointsMaterial({ color: 0xffd08a, size: H * 0.006, sizeAttenuation: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  REAL.lamps.frustumCulled = false;
  root.add(REAL.lamps);

  const rainPos = new Float32Array(RAIN_DROPS * 6);
  for (let i = 0; i < RAIN_DROPS; i++) { const x = rnd(-H, H), z = rnd(-H, H), y = rnd(yLo, yHi); rainPos.set([x, y, z, x, y - 2.5, z], i * 6); }
  const rainGeo = new THREE.BufferGeometry();
  rainGeo.setAttribute('position', new THREE.BufferAttribute(rainPos, 3));
  const rainObj = new THREE.LineSegments(rainGeo, new THREE.LineBasicMaterial({ color: 0xbfd6ee, transparent: true, opacity: 0.45, depthWrite: false }));
  rainObj.frustumCulled = false; rainObj.visible = false;
  root.add(rainObj);
  REAL.rain = { obj: rainObj, geo: rainGeo, pos: rainPos, rate: 0 };

  const snowPos = new Float32Array(SNOW_FLAKES * 3);
  for (let i = 0; i < SNOW_FLAKES; i++) snowPos.set([rnd(-H, H), rnd(yLo, yHi), rnd(-H, H)], i * 3);
  const snowGeo = new THREE.BufferGeometry();
  snowGeo.setAttribute('position', new THREE.BufferAttribute(snowPos, 3));
  const snowObj = new THREE.Points(snowGeo, new THREE.PointsMaterial({ color: 0xffffff, size: H * 0.006, sizeAttenuation: true,
    transparent: true, opacity: 0.9, depthWrite: false }));
  snowObj.frustumCulled = false; snowObj.visible = false;
  root.add(snowObj);
  REAL.snow = { obj: snowObj, geo: snowGeo, pos: snowPos, rate: 0, yLo, yHi };
  REAL.rain.yLo = yLo; REAL.rain.yHi = yHi;

  const starPos = new Float32Array(STARS * 3), R = H * 30;
  for (let i = 0; i < STARS; i++) {   // sky dome, upper hemisphere
    const u = rnd(-1, 1), a = rnd(0, Math.PI * 2), q = Math.sqrt(1 - u * u);
    starPos.set([R * q * Math.cos(a), R * Math.abs(u), R * q * Math.sin(a)], i * 3);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  REAL.stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false,
    transparent: true, opacity: 0, depthWrite: false, fog: false }));
  REAL.stars.visible = false;
  scene.add(REAL.stars);
}

function realisticObjects() {
  if (!REAL.cars) return [];
  return [REAL.cars.body, REAL.cars.lights, REAL.lamps, REAL.stars, REAL.rain.obj, REAL.snow.obj];
}

// Recomputes the whole environment from the date, time, place and weather. Cheap; called on changes and every 30 s.
function applyRealistic() {
  if (!REAL.on) return;
  ensureRealistic();
  const now = realDate(), hour = now.getHours() + now.getMinutes() / 60, w = REAL.weather, cd = conditions(w);
  const sp = sunPos(now.getTime(), CFG.geo.lat, CFG.geo.lon), elev = sp.alt;
  const cloud = w ? clamp01(w.cloud) : 0.1;
  const up = clamp01((elev + 2) / 8), day = clamp01((elev + 6) / 12), night = clamp01((-elev - 1) / 5);
  const warm = 1 - clamp01(elev / 30), dusk = clamp01(1 - Math.abs(elev) / 10);
  const season = seasonK(now), winterness = 1 - season;
  const weekend = now.getDay() === 0 || now.getDay() === 6;
  const traffic = trafficAt(hour, weekend);
  const snowCover = cd.snowing ? 0.9 : (w && w.temp != null && w.temp < -2 && winterness > 0.5 ? 0.6 : 0);
  const fogAmt = cd.foggy ? 1 : cd.storm ? 0.5 : cd.raining ? 0.4 : cd.snowing ? 0.3 : 0;
  Object.assign(REAL, { night, traffic, season, snowCover, cloud, cond: cd, elev });

  // sun and sky
  sunAz = sp.bearing; sunEl = Math.max(elev, 1);
  sun.visible = up > 0.01;
  sun.intensity = 2.6 * up * (1 - 0.65 * cloud);
  sun.color.copy(SUN_HIGH).lerp(SUN_LOW, warm);
  hemi.intensity = (0.3 + 1.25 * day) * (1 - 0.3 * cloud);
  hemi.color.copy(HEMI_NIGHT).lerp(HEMI_DAY, day);
  placeSun();
  $('az').value = ((sunAz % 360) + 360) % 360; $('el').value = Math.max(8, sunEl);
  const sky = new THREE.Color().copy(SKY_NIGHT).lerp(SKY_DAY, day).lerp(SKY_CLOUD, cloud * 0.8 * day).lerp(SKY_DUSK, 0.75 * dusk);
  scene.background = sky;
  const far = (+dd.value) * T * (1 - 0.7 * fogAmt);
  scene.fog = new THREE.Fog(sky, far * 0.55, far);

  // weather
  REAL.rain.rate = cd.raining ? clamp01(0.25 + (w.rain || 0) / 2) : 0;
  REAL.snow.rate = cd.snowing ? clamp01(0.3 + (w.snow || 0)) : 0;
  REAL.rain.obj.visible = REAL.rain.rate > 0; REAL.snow.obj.visible = REAL.snow.rate > 0;

  // night: lit windows, street lights, stars and car lights
  winU.uNight.value = night;
  winU.uLit.value = hour >= 17 && hour < 23 ? 0.7 : (hour >= 23 || hour < 5 ? 0.3 : 0.5);
  winU.uSnow.value = snowCover * 0.9;
  REAL.stars.visible = night > 0.02; REAL.stars.material.opacity = night;
  REAL.lamps.visible = night > 0.02; REAL.lamps.material.opacity = 0.9 * night;
  REAL.cars.lights.material.opacity = night;
  if (trafficLights) trafficLights.material.opacity = night;

  setThemeColors();       // paints season and snow on the terrain and trees
  if (REAL.override === null) $('tod').value = hour;
  const tr = traffic < 0.3 ? 'quiet' : traffic < 0.7 ? 'moderate' : 'busy';
  $('realInfo').textContent = [now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }),
    'sun ' + Math.round(elev) + '°', describeWeather(w), 'traffic ' + tr].join(' · ');
}

// Season and snow colours on top of the theme: multiplied per role, so switching the theme or chunks is always consistent.
function paintSeason() {
  const k = REAL.season, snowC = winU.uSnow.value;
  const treeMul = new THREE.Color().lerpColors(WINTER_TREE, WHITE, k), groundMul = new THREE.Color().lerpColors(WINTER_GROUND, WHITE, k);
  const tc = theme.colors.tree;
  impostorUniforms.forEach(u => u.uColor.value.setRGB(tc[0], tc[1], tc[2], THREE.SRGBColorSpace).multiply(treeMul));
  root.traverse(o => {
    const role = o.userData.role, c = role && theme.colors[role];
    if (!c || !o.isMesh || o.userData.impostor || !o.material || !o.material.color) return;
    const col = o.material.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
    if (role === 'tree') col.multiply(treeMul);
    else if (role === 'ground' || role === 'park' || role === 'forest') { col.multiply(groundMul); col.lerp(SNOW, snowC * 0.85); }
  });
}

// Cars drive along the road centre lines (ping-pong at the ends), with headlights and tail lights.
function stepRealistic(now) {
  const dt = Math.min(0.1, Math.max(0, (now - REAL.t) / 1000)); REAL.t = now;
  if (!REAL.on || !REAL.cars) return;
  const C = REAL.cars, n = trafficUpdate ? 0 : Math.min(Math.round(MAX_CARS * REAL.traffic), C.state.length), dummy = C.dummy, P = C.lightPos;
  for (let i = 0; i < n; i++) {
    const st = C.state[i];
    st.s += st.dir * st.v * dt;
    const L = st.road.len;
    if (st.s > L) { st.s = 2 * L - st.s; st.dir = -1; }
    if (st.s < 0) { st.s = -st.s; st.dir = 1; }
    const p = pointAt(st.road, st.s);
    const x = p.x, y = p.y, z = p.z, tx = p.tx * st.dir, tz = p.tz * st.dir;
    dummy.position.set(x, y + 0.75, z); dummy.rotation.set(0, Math.atan2(tx, tz), 0); dummy.scale.set(1.8, 1.5, 4.4);
    dummy.updateMatrix(); C.body.setMatrixAt(i, dummy.matrix);
    const px = -tz * 0.7, pz = tx * 0.7, hy = y + 0.6;
    P.set([x + tx * 2.3 + px, hy, z + tz * 2.3 + pz, x + tx * 2.3 - px, hy, z + tz * 2.3 - pz,
           x - tx * 2.3 + px, hy, z - tz * 2.3 + pz, x - tx * 2.3 - px, hy, z - tz * 2.3 - pz], i * 12);
  }
  C.body.count = n; C.body.instanceMatrix.needsUpdate = true;
  C.lightGeo.setDrawRange(0, n * 4); C.lightGeo.attributes.position.needsUpdate = true;
  // rain falls past the tile, snow drifts down slowly
  const R = REAL.rain;
  if (R.rate > 0) {
    const Q = R.pos, count = Math.round(RAIN_DROPS * R.rate);
    for (let i = 0; i < count; i++) {
      let y = Q[i * 6 + 1] - 20 * dt;
      if (y < R.yLo) {
        const x = rnd(-CFG.half, CFG.half), z = rnd(-CFG.half, CFG.half);
        Q[i * 6] = Q[i * 6 + 3] = x; Q[i * 6 + 2] = Q[i * 6 + 5] = z; y = R.yHi;
      }
      Q[i * 6 + 1] = y; Q[i * 6 + 4] = y - 2.5;
    }
    R.geo.setDrawRange(0, count * 2); R.geo.attributes.position.needsUpdate = true;
  }
  const S = REAL.snow;
  if (S.rate > 0) {
    const Q = S.pos, count = Math.round(SNOW_FLAKES * S.rate);
    for (let i = 0; i < count; i++) {
      let y = Q[i * 3 + 1] - 1.2 * dt, x = Q[i * 3] + Math.sin(now * 0.0007 + i) * 0.4 * dt;
      if (y < S.yLo) { x = rnd(-CFG.half, CFG.half); Q[i * 3 + 2] = rnd(-CFG.half, CFG.half); y = S.yHi; }
      Q[i * 3] = x; Q[i * 3 + 1] = y;
    }
    S.geo.setDrawRange(0, count); S.geo.attributes.position.needsUpdate = true;
  }
}

function setRealistic(on) {
  REAL.on = on;
  $('realBox').hidden = !on;
  renderer.toneMapping = on ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
  if (on) {
    REAL.saved = { az: sunAz, el: sunEl };
    ensureRealistic(); fetchWeather(); applyRealistic();
    return;
  }
  for (const o of realisticObjects()) o.visible = false;
  sunAz = REAL.saved ? REAL.saved.az : CFG.sun.bearing; sunEl = REAL.saved ? REAL.saved.el : CFG.sun.elevation;
  sun.visible = true; sun.intensity = 2.5; sun.color.set(0xffffff);
  hemi.intensity = 1.55; hemi.color.set(0xffffff);
  winU.uNight.value = 0; winU.uSnow.value = 0;
  scene.fog = null;
  $('az').value = ((sunAz % 360) + 360) % 360; $('el').value = sunEl;
  placeSun();
  applyTheme($('themeSel').value);
  applyFog();
}
$('real').onchange = () => setRealistic($('real').checked);
$('tod').oninput = () => { REAL.override = +$('tod').value; applyRealistic(); };
$('nowBtn').onclick = () => { REAL.override = null; applyRealistic(); };
setInterval(() => { if (REAL.on && REAL.override === null) applyRealistic(); }, 30000);
setInterval(() => { if (REAL.on) fetchWeather(); }, 10 * 60000);

applyTheme(CFG.theme); applyFog();
tileList.forEach(t => { if (t.center.distanceTo(controls.target) < 0.5 * T) t.explored = true; });
let tick = 0;
controls.addEventListener('change', placeSun);
window.__map3d = { updateTiles, tileList, explore: k => { const t = tiles.get(k); if (t) t.explored = true; } };
// ---------------------------------------------------------------- fake plane: lands on the model's runway
const planeRoot = new THREE.Group();
root.add(planeRoot);
const landBtn = $('landBtn'), followBox = $('followBox');
let planeUpdate = null;
if (CFG.airport && CFG.airport.pts.length >= 6) {
  const ap = CFG.airport;
  const PATH = Float32Array.from(ap.pts), N = PATH.length / 3;
  const cum = new Float64Array(N);
  for (let i = 1; i < N; i++) {
    cum[i] = cum[i - 1] + Math.hypot(PATH[3 * i] - PATH[3 * i - 3], PATH[3 * i + 1] - PATH[3 * i - 2], PATH[3 * i + 2] - PATH[3 * i - 1]);
  }
  const TOTAL = cum[N - 1], TD = ap.touchdown_m, ROLL = ap.roll_m, CRUISE = ap.cruise_ms;
  // a plane about 60 m long on a 3 km model: small, but readable from the default view
  const L = Math.min(90, Math.max(30, CFG.half * 2 * 0.02));
  // runway strip, drawn 45 m wide on top of the ground
  const RW = Float32Array.from(ap.runway), NR = RW.length / 3;
  const rxz = [RW[3 * NR - 3] - RW[0], RW[3 * NR - 1] - RW[2]], rl = Math.hypot(rxz[0], rxz[1]) || 1;
  const px = -rxz[1] / rl * 22.5, pz = rxz[0] / rl * 22.5;
  const strip = new Float32Array(NR * 6), sidx = [];
  for (let i = 0; i < NR; i++) {
    const x = RW[3 * i], y = RW[3 * i + 1] + 0.05, z = RW[3 * i + 2];
    strip.set([x + px, y, z + pz, x - px, y, z - pz], i * 6);
    if (i) sidx.push(2 * i - 2, 2 * i - 1, 2 * i, 2 * i - 1, 2 * i + 1, 2 * i);
  }
  const stripGeo = new THREE.BufferGeometry();
  stripGeo.setAttribute('position', new THREE.BufferAttribute(strip, 3));
  stripGeo.setIndex(sidx);
  const runwayMesh = new THREE.Mesh(stripGeo, new THREE.MeshLambertMaterial({ color: 0x3b3f44, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4, side: THREE.DoubleSide }));
  runwayMesh.receiveShadow = true;
  planeRoot.add(runwayMesh);

  // the plane: nose +X, up +Y, left wing -Z (the same axes as the aircraft library)
  const white = new THREE.MeshStandardMaterial({ color: 0xf2f4f6, roughness: 0.45 });
  const grey = new THREE.MeshStandardMaterial({ color: 0x9aa3ad, roughness: 0.4 });
  const plane = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(L * 0.025, L * 0.05, L, 14).rotateZ(Math.PI / 2), white);
  const wing = new THREE.Mesh(new THREE.BoxGeometry(L * 0.2, L * 0.02, L * 0.9).translate(L * 0.02, -L * 0.02, 0), grey);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(L * 0.1, L * 0.012, L * 0.3).translate(-L * 0.42, L * 0.02, 0), grey);
  const fin = new THREE.Mesh(new THREE.BoxGeometry(L * 0.12, L * 0.13, L * 0.012).translate(-L * 0.42, L * 0.09, 0), white);
  plane.add(body, wing, tail, fin);
  for (const z of [-0.22, 0.22]) {
    const eng = new THREE.Mesh(new THREE.CylinderGeometry(L * 0.05, L * 0.04, L * 0.14, 12).rotateZ(Math.PI / 2).translate(L * 0.06, -L * 0.07, z * L), grey);
    plane.add(eng);
  }
  body.castShadow = wing.castShadow = fin.castShadow = true;
  const nav = [[0xff2d2d, [0, 0, -0.45]], [0x2ecc71, [0, 0, 0.45]], [0xffffff, [-0.5, 0.1, 0]]];
  for (const [c, [x, y, z]] of nav) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(L * 0.012, 8, 6), new THREE.MeshBasicMaterial({ color: c }));
    dot.position.set(x * L, y * L, z * L); plane.add(dot);
  }
  const planeGroup = new THREE.Group();
  planeGroup.add(plane);
  planeGroup.visible = false;
  planeRoot.add(planeGroup);

  // position and heading on the path at distance s (metres from the start of the approach)
  const sample = s => {
    s = Math.max(0, Math.min(TOTAL, s));
    let lo = 0, hi = N - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
    const seg = cum[hi] - cum[lo] || 1, f = Math.min(1, Math.max(0, (s - cum[lo]) / seg));
    const dx = PATH[3 * hi] - PATH[3 * lo], dy = PATH[3 * hi + 1] - PATH[3 * lo + 1], dz = PATH[3 * hi + 2] - PATH[3 * lo + 2];
    const hl = Math.hypot(dx, dz) || 1;
    return { x: PATH[3 * lo] + dx * f, y: PATH[3 * lo + 1] + dy * f, z: PATH[3 * lo + 2] + dz * f,
             yaw: Math.atan2(-dz, dx), pitch: Math.atan2(dy, hl) };
  };
  // speed: cruise on the approach, then braking from touchdown to a stop at the far end of the runway
  // constant braking (v^2 falls linearly with distance) stops the plane after exactly one runway length
  const speedAt = s => s < TD ? CRUISE : CRUISE * Math.sqrt(Math.max(0, 1 - (s - TD) / ROLL));

  const st = { active: false, s: 0, yaw: 0, follow: followBox.checked, locked: false, done: false };
  const pWorld = new THREE.Vector3(), lastP = new THREE.Vector3(), fwd = new THREE.Vector3(), goal = new THREE.Vector3();
  const q1 = new THREE.Quaternion(), q2 = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), zA = new THREE.Vector3(0, 0, 1);
  const follow = dt => {
    planeGroup.getWorldPosition(pWorld);
    if (!st.locked) {
      // chase: swing the camera behind and above the plane, then hold that distance while it flies
      fwd.set(Math.cos(st.yaw), 0, -Math.sin(st.yaw));
      goal.copy(pWorld).addScaledVector(fwd, -3.5 * L).addScaledVector(up, 1.1 * L);
      const k = 1 - Math.exp(-dt * 2.5);
      camera.position.lerp(goal, k); controls.target.lerp(pWorld, k);
      if (camera.position.distanceTo(goal) < L * 0.5) st.locked = true;
    } else {
      const dx = pWorld.x - lastP.x, dy = pWorld.y - lastP.y, dz = pWorld.z - lastP.z;
      camera.position.x += dx; camera.position.y += dy; camera.position.z += dz;
      controls.target.x += dx; controls.target.y += dy; controls.target.z += dz;
    }
    lastP.copy(pWorld);
  };
  planeUpdate = dt => {
    if (!st.active) return;
    st.s += speedAt(st.s) * dt;
    // the plane stops one runway length after touchdown (the 3-D path can be a few metres longer than that)
    const END = Math.min(TOTAL, TD + ROLL);
    if (st.s >= END - 0.05) { st.s = END; st.active = false; st.done = true; landBtn.textContent = 'Land again'; }
    const f = sample(st.s);
    st.yaw = f.yaw;
    planeGroup.position.set(f.x, f.y + L * 0.03, f.z);
    q1.setFromAxisAngle(up, f.yaw); q2.setFromAxisAngle(zA, f.pitch);
    planeGroup.quaternion.copy(q1).multiply(q2);
    if (st.follow) follow(dt);
  };
  landBtn.onclick = () => {
    st.s = 0; st.active = true; st.done = false; st.locked = false; st.follow = followBox.checked;
    planeGroup.visible = true; landBtn.textContent = 'Landing…';
    lastP.copy(planeGroup.getWorldPosition(new THREE.Vector3()));
  };
  followBox.onchange = () => { st.follow = followBox.checked; st.locked = false; };
} else {
  landBtn.disabled = true; landBtn.textContent = 'No airport in this model';
  followBox.parentElement.style.display = 'none';
}

let lastFrameT = performance.now();
const LOAD_STEPS = [
  ['Downloading the page', 'The whole city is inside this one file: only the 3D engine and the weather are fetched separately.'],
  ['Starting the 3D engine', 'three.js draws the city on your graphics card (WebGL).'],
  ['Unpacking the nearest tiles', 'Geometry is stored compressed; the tiles closest to the camera are decompressed first.'],
  ['Uploading buildings, terrain and roads to the GPU', 'Each mesh becomes a GPU buffer; windows, tints and shadows are set up.'],
  ['Placing trees and traffic', CFG.traffic
    ? 'Cars are put on the real road network and obey the mapped lights and stop signs.'
    : 'Trees are placed from the map data.'],
  ['Drawing the first frame', 'Farther tiles and fine detail keep loading in the background as you look around.'],
];
const lsteps = $('lsteps');
LOAD_STEPS.forEach(([t]) => { const li = document.createElement('li'); li.textContent = t; lsteps.appendChild(li); });
function setLoading(done, total) {
  const k = done === 0 ? 2 : (done < total ? 3 : 5);
  [...lsteps.children].forEach((li, i) => { li.className = i < k ? 'done' : i === k ? 'now' : ''; });
  $('lnote').textContent = LOAD_STEPS[k][1] + (k < 4 ? ` (${done}/${total} chunks ready)` : '');
}
setLoading(0, CFG.groups.length);
renderer.setAnimationLoop(now => {
  const dt = Math.min((now - lastFrameT) / 1000, 0.1);
  const dtReal = Math.min((now - lastFrameT) / 1000, 1);   // the plane keeps real time even when frames are slow
  lastFrameT = now;
  trafficRoot.visible = layers.traffic;
  if (trafficUpdate && layers.traffic) trafficUpdate(dt * (+$('tspeed').value), REAL.on);
  if (planeUpdate) planeUpdate(dtReal);
  keyMove(dt);
  controls.update();
  if (tick++ % 6 === 0) updateTiles();
  adapt(now);
  stepRealistic(now);
  placeSearch.update(now);
  renderer.render(scene, camera);
  if (!window.__ready && tileList.some(t => t.shown >= 0)) {
    $('loading').classList.add('done'); window.__ready = true;
  } else if (!window.__ready) {
    setLoading(loadedGroups, CFG.groups.length);
  }
});
</script>
</body>
</html>
"""
