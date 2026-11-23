"""
Interactive viewer export: one self-contained ``.html`` file.

The mesh data is embedded (zlib-compressed binary, base64) and rendered with
three.js, loaded from a CDN, so the file opens by double-click in any modern
browser: orbit / zoom / pan, real-time sun shadows, layer toggles, vertical
exaggeration, sun position and a PNG snapshot button.
"""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

import numpy as np

from .mesh import Scene
from .style import hex_color, role_colors

THREE_VERSION = "0.160.0"

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


def _pack(scene: Scene, theme: dict) -> tuple[list[dict], bytes]:
    colors = role_colors(theme)
    blob = bytearray()
    meta: list[dict] = []

    def put(arr: np.ndarray) -> int:
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(arr.tobytes())
        return off

    for m in scene.meshes:
        if m.n_faces == 0 or m.role not in colors:
            continue
        base = np.asarray(colors[m.role])
        entry = {
            "name": m.name, "role": m.role, "group": GROUPS.get(m.role, "terrain"),
            "color": [int(round(c * 255)) for c in base],
            "flat": bool(m.flat), "cast": bool(m.casts_shadow), "receive": bool(m.receives_shadow),
            "overlay": m.role in OVERLAY_ROLES, "nv": m.n_vertices, "nf": m.n_faces,
        }
        entry["pos"] = put(_y_up(m.vertices))
        if m.n_vertices < 65536:
            entry["idx"], entry["idx16"] = put(m.faces.astype(np.uint16).ravel()), True
        else:
            entry["idx"], entry["idx16"] = put(m.faces.astype(np.uint32).ravel()), False
        if m.tint is not None:
            vt = np.ones(m.n_vertices, dtype=np.float64)
            vt[m.faces.ravel()] = np.repeat(m.tint.astype(np.float64), 3)
            rgb = np.clip(base[None, :] * vt[:, None], 0, 1)
            entry["col"] = put((rgb * 255 + 0.5).astype(np.uint8))
        if m.normals is not None and not m.flat:
            n = _y_up(m.normals)
            entry["nrm"] = put(np.round(n * 127).astype(np.int8))
        meta.append(entry)
    return meta, bytes(blob)


def export_html(scene: Scene, theme: dict, path: str | Path, title: str = "", subtitle: str = "",
                azimuth: float = 225.0, elevation: float = 45.0, credits: str = "") -> Path:
    meta, blob = _pack(scene, theme)
    zlo, zhi = scene.z_range
    cfg = {
        "meshes": meta, "half": scene.half, "ledge": float(scene.info.get("ledge", 0.0)),
        "zrange": [zlo, zhi], "bg": theme["bg"], "text": theme.get("text", "#ffffff"),
        "sun": {"bearing": scene.light_bearing, "elevation": scene.light_elevation},
        "camera": {"azimuth": azimuth, "elevation": elevation},
        "title": title, "subtitle": subtitle, "credits": credits,
        "ground": hex_color(role_colors(theme)["ground"]),
    }
    cfg_json = json.dumps(cfg, separators=(",", ":")).replace("<", "\\u003c")
    payload = base64.b64encode(zlib.compress(blob, 6)).decode("ascii")
    html = (TEMPLATE
            .replace("__TITLE__", (title or "3D city model").replace("<", "&lt;"))
            .replace("__THREE__", THREE_VERSION)
            .replace("__BG__", theme["bg"]).replace("__TEXT__", theme.get("text", "#ffffff"))
            .replace("__CFG__", cfg_json)
            .replace("__DATA__", payload))
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path


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
  #ui { position: fixed; top: 16px; left: 16px; width: 260px; padding: 16px; border-radius: 14px;
    background: color-mix(in srgb, var(--bg) 78%, transparent); backdrop-filter: blur(10px);
    border: 1px solid color-mix(in srgb, var(--text) 22%, transparent); }
  #ui h1 { margin: 0 0 2px; font-size: 20px; letter-spacing: .12em; text-transform: uppercase; }
  #ui .sub { margin: 0 0 12px; opacity: .75; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
  #ui label { display: flex; align-items: center; gap: 8px; margin: 6px 0; cursor: pointer; }
  #ui .row { display: block; margin: 10px 0 2px; font-size: 12px; opacity: .85; }
  #ui input[type=range] { width: 100%; accent-color: var(--text); }
  #ui input[type=checkbox] { accent-color: var(--text); }
  #ui .btns { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  #ui button { flex: 1; padding: 7px 10px; border-radius: 8px; cursor: pointer; color: var(--text);
    background: transparent; border: 1px solid color-mix(in srgb, var(--text) 45%, transparent); font: inherit; }
  #ui button:hover { background: color-mix(in srgb, var(--text) 14%, transparent); }
  #credits { position: fixed; right: 12px; bottom: 8px; font-size: 11px; opacity: .6; max-width: 60vw; text-align: right; }
  #loading { position: fixed; inset: 0; display: grid; place-items: center; background: var(--bg);
    letter-spacing: .2em; text-transform: uppercase; font-size: 13px; transition: opacity .4s; }
  #loading.done { opacity: 0; pointer-events: none; }
  #hint { position: fixed; left: 16px; bottom: 8px; font-size: 11px; opacity: .55; }
  @media (max-width: 640px) {
    #ui { width: auto; right: 12px; left: 12px; top: auto; bottom: 12px; padding: 10px 12px; }
    #ui .sub, #hint, #credits { display: none; }
  }
</style>
</head>
<body>
<div id="loading">Building the model…</div>
<div id="ui">
  <h1 id="t"></h1>
  <p class="sub" id="s"></p>
  <label><input type="checkbox" data-group="building" checked> Buildings</label>
  <label><input type="checkbox" data-group="tree" checked> Trees</label>
  <label><input type="checkbox" data-group="roads" checked> Roads &amp; bridges</label>
  <span class="row">Vertical exaggeration</span>
  <input type="range" id="exag" min="0.1" max="2.5" step="0.05" value="1">
  <span class="row">Sun direction</span>
  <input type="range" id="az" min="0" max="360" step="1">
  <span class="row">Sun height</span>
  <input type="range" id="el" min="8" max="85" step="1">
  <div class="btns">
    <button id="rot">Auto-rotate</button><button id="reset">Reset view</button><button id="shot">Save PNG</button>
  </div>
</div>
<div id="hint">Drag to orbit · scroll to zoom · right-drag to pan</div>
<div id="credits"></div>

<script type="application/json" id="cfg">__CFG__</script>
<script type="text/plain" id="blob">__DATA__</script>
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@__THREE__/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@__THREE__/examples/jsm/" } }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const CFG = JSON.parse(document.getElementById('cfg').textContent);
document.getElementById('t').textContent = CFG.title || '3D city';
document.getElementById('s').textContent = CFG.subtitle || '';
document.getElementById('credits').textContent = CFG.credits || '';

async function inflate(b64) {
  const res = await fetch('data:application/octet-stream;base64,' + b64);
  const stream = res.body.pipeThrough(new DecompressionStream('deflate'));
  return await new Response(stream).arrayBuffer();
}

// sRGB byte -> linear float lookup, so vertex colours match material colours
const LUT = new Float32Array(256);
for (let i = 0; i < 256; i++) { const c = i / 255; LUT[i] = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color().setStyle(CFG.bg, THREE.SRGBColorSpace);

const H = CFG.half;
const zc = (CFG.zrange[0] + CFG.zrange[1]) * 0.25;
const camera = new THREE.PerspectiveCamera(32, window.innerWidth / window.innerHeight, H * 0.02, H * 40);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI / 2 - 0.03;
controls.minDistance = H * 0.15;
controls.maxDistance = H * 14;
controls.target.set(0, zc, 0);

function dirFromCompass(bearingDeg, elevDeg) {
  const b = bearingDeg * Math.PI / 180, e = elevDeg * Math.PI / 180;
  return new THREE.Vector3(Math.sin(b) * Math.cos(e), Math.sin(e), -Math.cos(b) * Math.cos(e));
}
function resetView() {
  const d = dirFromCompass(CFG.camera.azimuth, CFG.camera.elevation).multiplyScalar(H * 4.6);
  camera.position.copy(controls.target).add(d);
  controls.update();
}
resetView();

const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 1.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.5);
sun.castShadow = true;
sun.shadow.mapSize.set(4096, 4096);
const sd = H * 1.45;
Object.assign(sun.shadow.camera, { left: -sd, right: sd, top: sd, bottom: -sd, near: 1, far: H * 10 });
sun.shadow.bias = -0.0003;
sun.shadow.normalBias = 0.8;
scene.add(sun, sun.target);
sun.target.position.set(0, zc, 0);
function setSun(az, el) {
  sun.position.copy(sun.target.position).add(dirFromCompass(az, el).multiplyScalar(H * 5));
  sun.shadow.camera.updateProjectionMatrix();
}
setSun(CFG.sun.bearing, CFG.sun.elevation);

const azInput = document.getElementById('az'), elInput = document.getElementById('el');
azInput.value = ((CFG.sun.bearing % 360) + 360) % 360;
elInput.value = CFG.sun.elevation;
azInput.oninput = elInput.oninput = () => setSun(+azInput.value, +elInput.value);

const root = new THREE.Group();
scene.add(root);
const groups = { building: [], tree: [], roads: [], terrain: [] };

const buf = await inflate(document.getElementById('blob').textContent.trim());
for (const m of CFG.meshes) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buf, m.pos, m.nv * 3), 3));
  g.setIndex(new THREE.BufferAttribute(m.idx16 ? new Uint16Array(buf, m.idx, m.nf * 3) : new Uint32Array(buf, m.idx, m.nf * 3), 1));
  if (m.col !== undefined) {
    const bytes = new Uint8Array(buf, m.col, m.nv * 3);
    const lin = new Float32Array(m.nv * 3);
    for (let i = 0; i < lin.length; i++) lin[i] = LUT[bytes[i]];
    g.setAttribute('color', new THREE.BufferAttribute(lin, 3));
  }
  if (m.nrm !== undefined) g.setAttribute('normal', new THREE.BufferAttribute(new Int8Array(buf, m.nrm, m.nv * 3), 3, true));
  else if (!m.flat) g.computeVertexNormals();
  g.computeBoundingSphere();

  const mat = new THREE.MeshLambertMaterial({
    color: m.col !== undefined ? 0xffffff : new THREE.Color().setRGB(m.color[0] / 255, m.color[1] / 255, m.color[2] / 255, THREE.SRGBColorSpace),
    vertexColors: m.col !== undefined, flatShading: m.flat, side: m.role === 'bridge_side' ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (m.overlay) { mat.polygonOffset = true; mat.polygonOffsetFactor = -2; mat.polygonOffsetUnits = -2; }
  const mesh = new THREE.Mesh(g, mat);
  mesh.castShadow = m.cast; mesh.receiveShadow = m.receive;
  root.add(mesh);
  groups[m.group].push(mesh);
}

document.querySelectorAll('input[data-group]').forEach(cb => {
  cb.onchange = () => groups[cb.dataset.group].forEach(o => o.visible = cb.checked);
});
document.getElementById('exag').oninput = e => { root.scale.y = +e.target.value; };

const rot = document.getElementById('rot');
rot.onclick = () => { controls.autoRotate = !controls.autoRotate; controls.autoRotateSpeed = 0.8; rot.style.opacity = controls.autoRotate ? 1 : .7; };
document.getElementById('reset').onclick = () => { controls.autoRotate = false; resetView(); };
document.getElementById('shot').onclick = () => {
  renderer.render(scene, camera);
  renderer.domElement.toBlob(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b);
    a.download = (CFG.title || 'city') + '-3d.png'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); });
};

addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
document.getElementById('loading').classList.add('done');
window.__ready = true;
</script>
</body>
</html>
"""
