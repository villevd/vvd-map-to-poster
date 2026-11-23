"""
Local GUI for the 3D city maps.

    python -m map3d.gui            # opens http://127.0.0.1:8777 in your browser

A tiny web app served from your own machine (standard library only, bound to
127.0.0.1): fill in a city, theme, radius and layers, press Generate, watch the
progress, and the interactive 3D model opens in the page. Previous models are
listed so they can be reopened instantly. Generation runs the normal CLI
(``create_map_poster.py --3d``) in a subprocess, so the GUI and the command line
always behave the same.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
POSTERS = ROOT / "posters"
THEMES = ROOT / "themes"
DEFAULT_PORT = 8777
SERVED_SUFFIXES = {".html": "text/html; charset=utf-8", ".png": "image/png",
                   ".glb": "model/gltf-binary", ".stl": "model/stl"}
OUTPUT_CHOICES = ("html", "poster", "glb", "stl")
STAMP_RE = re.compile(r"_3d_(?P<stamp>\d{8}_\d{6})$")
SAVED_RE = re.compile(r"Saved\s+(?P<path>.+?)\s+\([\d.]+ MB\)")
NUMBER_RE = re.compile(r"^-?\d{1,3}(\.\d+)?$")


# --------------------------------------------------------------------------
# Request validation / command building (pure, unit-tested)
# --------------------------------------------------------------------------


def available_themes() -> list[dict]:
    out = []
    for f in sorted(THEMES.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": f.stem, "name": d.get("name", f.stem), "bg": d.get("bg", "#000"), "text": d.get("text", "#fff"),
                    "description": d.get("description", "")})
    return out


def safe_file(name: str) -> Path | None:
    """Resolve a file in posters/ by bare name; None for anything that is not a plain, served file."""
    if not name or Path(name).name != name or "\\" in name or "/" in name:
        return None
    if Path(name).suffix.lower() not in SERVED_SUFFIXES:
        return None
    p = POSTERS / name
    return p if p.is_file() else None


class BadRequest(ValueError):
    pass


def build_command(params: dict, theme_ids: list[str]) -> list[str]:
    """
    Validate GUI parameters and turn them into the CLI invocation.

    Values are passed as ``--option=value`` so no user text can be mistaken for
    another option, and no shell is involved.
    """
    def text(key: str, maxlen: int = 80) -> str:
        v = str(params.get(key, "")).strip()
        if not v or len(v) > maxlen or any(ord(c) < 32 for c in v):
            raise BadRequest(f"'{key}' is required (up to {maxlen} characters).")
        return v

    def number(key: str, lo: float, hi: float, default: float) -> float:
        try:
            v = float(params.get(key, default))
        except (TypeError, ValueError):
            raise BadRequest(f"'{key}' must be a number.") from None
        if not lo <= v <= hi:
            raise BadRequest(f"'{key}' must be between {lo:g} and {hi:g}.")
        return v

    theme = str(params.get("theme", ""))
    if theme not in theme_ids:
        raise BadRequest("Unknown theme.")
    outputs = [o for o in params.get("outputs", ["html"]) if o in OUTPUT_CHOICES]
    if "html" not in outputs:
        outputs.insert(0, "html")           # the viewer is what the GUI shows

    cmd = [
        sys.executable, "-u", str(ROOT / "create_map_poster.py"),
        f"--city={text('city')}", f"--country={text('country')}", f"--theme={theme}", "--3d",
        f"--3d-outputs={','.join(outputs)}",
        f"--distance={int(number('distance', 300, 6000, 1500))}",
        f"--exaggeration={number('exaggeration', 0.2, 6, 1.5):g}",
    ]
    for flag, key in (("--no-buildings", "buildings"), ("--no-trees", "trees"), ("--no-terrain", "terrain"),
                      ("--no-overture", "overture")):
        if not params.get(key, True):
            cmd.append(flag)
    lat, lon = str(params.get("latitude", "")).strip(), str(params.get("longitude", "")).strip()
    if lat or lon:
        if not (NUMBER_RE.match(lat) and NUMBER_RE.match(lon)):
            raise BadRequest("Latitude and longitude must both be decimal numbers, e.g. 48.10 and -77.79.")
        cmd += [f"--latitude={lat}", f"--longitude={lon}"]
    return cmd


# --------------------------------------------------------------------------
# Jobs: one generation at a time, output streamed to the page
# --------------------------------------------------------------------------


class Job:
    def __init__(self, cmd: list[str], label: str):
        self.id = uuid.uuid4().hex[:8]
        self.cmd = cmd
        self.label = label
        self.lines: list[str] = []
        self.state = "running"            # running | done | error | cancelled
        self.files: list[str] = []        # bare names of files the run saved
        self.proc: subprocess.Popen | None = None
        self.started = time.time()

    def run(self) -> None:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                line = line.rstrip()
                if line:
                    self.lines.append(line)
                m = SAVED_RE.search(line)
                if m:
                    self.files.append(Path(m.group("path").strip()).name)
            rc = self.proc.wait()
        except Exception as e:  # could not even start
            self.lines.append(f"Could not start: {e}")
            rc = -1
        if self.state == "cancelled":
            return
        has_view = any(f.endswith(".html") for f in self.files)
        self.state = "done" if rc == 0 and has_view else "error"

    def cancel(self) -> None:
        self.state = "cancelled"
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def current_job() -> Job | None:
    return next((j for j in JOBS.values() if j.state == "running"), None)


def start_job(params: dict) -> Job:
    cmd = build_command(params, [t["id"] for t in available_themes()])
    with JOBS_LOCK:
        if current_job() is not None:
            raise RuntimeError("A model is already being generated.")
        job = Job(cmd, f"{params.get('city', '')} - {params.get('theme', '')}")
        JOBS[job.id] = job
        threading.Thread(target=job.run, daemon=True).start()
    return job


def split_model_name(stem: str, theme_ids: list[str]) -> tuple[str, str, str] | None:
    """``{city}_{theme}_3d_{stamp}`` -> (city, theme, stamp). Theme ids contain underscores, so match known ids."""
    m = STAMP_RE.search(stem)
    if not m:
        return None
    head = stem[:m.start()]
    for theme in sorted(theme_ids, key=len, reverse=True):
        if head.endswith("_" + theme):
            return head[: -len(theme) - 1].replace("_", " "), theme, m.group("stamp")
    return head.replace("_", " "), "", m.group("stamp")


def list_models() -> list[dict]:
    theme_ids = [t["id"] for t in available_themes()]
    models = []
    for f in POSTERS.glob("*_3d_*.html"):
        parts = split_model_name(f.stem, theme_ids)
        if parts is None:
            continue
        city, theme, stamp = parts
        siblings = [ext for ext in ("png", "glb", "stl") if (POSTERS / f"{f.stem}.{ext}").is_file()]
        models.append({
            "file": f.name, "city": city, "theme": theme or "?", "stamp": stamp, "mtime": f.stat().st_mtime,
            "size_mb": round(f.stat().st_size / 1_048_576, 1), "extras": siblings,
        })
    return sorted(models, key=lambda d: d["mtime"], reverse=True)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "map3d-gui"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- plumbing ---------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")          # blocks DNS-rebinding style access

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json", {"Cache-Control": "no-store"})

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "Forbidden host")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            return self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/api/themes":
            return self._json(available_themes())
        if url.path == "/api/models":
            return self._json(list_models())
        if url.path == "/api/job":
            job = JOBS.get(q.get("id", [""])[0])
            if job is None:
                return self._error(404, "Unknown job")
            since = int(q.get("since", ["0"])[0] or 0)
            return self._json({"state": job.state, "lines": job.lines[since:], "next": len(job.lines),
                               "files": job.files, "elapsed": round(time.time() - job.started, 1)})
        if url.path == "/api/current":
            job = current_job()
            return self._json({"id": job.id if job else None})
        if url.path.startswith("/files/"):
            f = safe_file(unquote(url.path[len("/files/"):]))
            if f is None:
                return self._error(404, "Not found")
            ctype = SERVED_SUFFIXES[f.suffix.lower()]
            extra = {} if f.suffix.lower() == ".html" else {"Content-Disposition": f'attachment; filename="{f.name}"'}
            if f.suffix.lower() == ".png":
                extra = {}
            return self._send(200, f.read_bytes(), ctype, extra)
        return self._error(404, "Not found")

    def do_POST(self):
        if not self._host_ok():
            return self._error(403, "Forbidden host")
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self._error(403, "Forbidden origin")        # another website cannot drive this server
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(415, "JSON required")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            params = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
        except ValueError:
            return self._error(400, "Invalid JSON")
        path = urlparse(self.path).path
        if path == "/api/generate":
            try:
                job = start_job(params)
            except BadRequest as e:
                return self._error(400, str(e))
            except RuntimeError as e:
                return self._error(409, str(e))
            return self._json({"id": job.id})
        if path == "/api/cancel":
            job = current_job()
            if job:
                job.cancel()
            return self._json({"ok": True})
        return self._error(404, "Not found")


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Bind to 127.0.0.1, trying the next few ports if one is busy."""
    last: Exception | None = None
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError as e:
            last = e
    raise OSError(f"No free port near {port}: {last}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local GUI for 3D city maps")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    args = ap.parse_args(argv)
    POSTERS.mkdir(exist_ok=True)
    server = make_server(args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"3D city maps GUI running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        job = current_job()
        if job:
            job.cancel()
        server.server_close()
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D City Maps</title>
<style>
  :root { --bg:#10131a; --panel:#171b24; --line:#2a3040; --text:#e8ebf2; --dim:#8f98ad; --accent:#e0b84a; --bad:#ff7b72; --ok:#7ee787; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#eef0f4; --panel:#ffffff; --line:#d5d9e2; --text:#1b2030; --dim:#5d6678; --accent:#a8741a; --bad:#c0362c; --ok:#1a7f37; }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body { display: grid; grid-template-columns: 340px 1fr; }
  aside { background: var(--panel); border-right: 1px solid var(--line); padding: 18px; overflow-y: auto; height: 100vh; }
  main { position: relative; height: 100vh; }
  h1 { font-size: 18px; margin: 0 0 14px; letter-spacing: .04em; }
  h2 { font-size: 12px; margin: 22px 0 8px; text-transform: uppercase; letter-spacing: .1em; color: var(--dim); }
  label { display: block; margin: 10px 0 0; font-size: 12px; color: var(--dim); }
  label output { float: right; color: var(--text); }
  input[type=text], select { width: 100%; margin-top: 4px; padding: 8px 10px; border-radius: 8px; color: var(--text);
    background: var(--bg); border: 1px solid var(--line); font: inherit; }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .row { display: flex; gap: 8px; align-items: center; }
  .sw { flex: none; width: 34px; height: 34px; margin-top: 4px; border-radius: 8px; border: 1px solid var(--line); }
  .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; margin-top: 6px; }
  .checks label { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--text); font-size: 13px; }
  fieldset { border: 0; padding: 0; margin: 14px 0 0; }
  legend { padding: 0; font-size: 12px; color: var(--dim); }
  details { margin-top: 12px; font-size: 12px; color: var(--dim); }
  summary { cursor: pointer; }
  button { font: inherit; cursor: pointer; border-radius: 8px; }
  #go { width: 100%; margin-top: 16px; padding: 11px; border: 0; background: var(--accent); color: #1b1608; font-weight: 600; }
  #go:disabled { opacity: .5; cursor: progress; }
  #stop { width: 100%; margin-top: 8px; padding: 8px; background: transparent; color: var(--text); border: 1px solid var(--line); }
  #status { margin-top: 12px; min-height: 20px; font-size: 13px; }
  #status.err { color: var(--bad); } #status.ok { color: var(--ok); }
  #log { margin: 8px 0 0; padding: 10px; max-height: 190px; overflow: auto; border-radius: 8px; background: var(--bg);
    border: 1px solid var(--line); font: 11px/1.5 ui-monospace, Consolas, monospace; white-space: pre-wrap; color: var(--dim); }
  #log:empty { display: none; }
  #hint { font-size: 12px; color: var(--dim); margin-top: 6px; min-height: 17px; }
  ul { list-style: none; margin: 0; padding: 0; }
  li.m { padding: 9px 10px; margin-bottom: 6px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
  li.m:hover { border-color: var(--dim); } li.m.sel { border-color: var(--accent); }
  li.m b { display: block; } li.m span { color: var(--dim); font-size: 12px; }
  li.m a { color: var(--accent); font-size: 12px; margin-right: 10px; text-decoration: none; }
  iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: var(--bg); }
  #empty { position: absolute; inset: 0; display: grid; place-items: center; text-align: center; color: var(--dim); padding: 30px; }
  #empty div { max-width: 360px; }
  #empty h3 { color: var(--text); margin: 0 0 6px; }
  @media (max-width: 800px) { body { grid-template-columns: 1fr; grid-template-rows: auto 60vh; }
    aside { height: auto; max-height: 55vh; } main { height: 60vh; } }
</style>
</head>
<body>
<aside>
  <h1>3D City Maps</h1>
  <form id="f" autocomplete="off">
    <label>City<input type="text" name="city" value="Val-d'Or" required></label>
    <label>Country<input type="text" name="country" value="Canada" required></label>
    <label>Theme
      <div class="row"><select name="theme" id="theme"></select><span class="sw" id="sw"></span></div>
    </label>
    <label>Area (radius) <output id="distO">1500 m</output><input type="range" name="distance" min="500" max="4000" step="100" value="1500"></label>
    <div id="hint"></div>
    <label>Vertical exaggeration <output id="exagO">1.5×</output><input type="range" name="exaggeration" min="0.5" max="4" step="0.1" value="1.5"></label>
    <fieldset><legend>Layers</legend>
      <div class="checks">
        <label><input type="checkbox" name="buildings" checked> Buildings</label>
        <label><input type="checkbox" name="trees" checked> Trees</label>
        <label><input type="checkbox" name="terrain" checked> Real terrain</label>
        <label><input type="checkbox" name="overture" checked> Overture Maps</label>
      </div>
    </fieldset>
    <fieldset><legend>Also save</legend>
      <div class="checks">
        <label><input type="checkbox" name="o_poster"> Poster image</label>
        <label><input type="checkbox" name="o_glb"> GLB model</label>
        <label><input type="checkbox" name="o_stl"> STL (3D print)</label>
      </div>
    </fieldset>
    <details><summary>Advanced: exact centre</summary>
      <label>Latitude<input type="text" name="latitude" placeholder="48.1023"></label>
      <label>Longitude<input type="text" name="longitude" placeholder="-77.7876"></label>
    </details>
    <button type="submit" id="go">Generate 3D model</button>
    <button type="button" id="stop" hidden>Cancel</button>
  </form>
  <div id="status"></div>
  <pre id="log"></pre>
  <h2>Previous models</h2>
  <ul id="models"></ul>
</aside>
<main>
  <div id="empty"><div><h3>No model yet</h3>Choose a city and press <b>Generate</b>. The first build downloads map data and
    takes a minute; rebuilding the same area is fast. Drag to orbit, scroll to zoom.</div></div>
  <iframe id="view" title="3D model" hidden></iframe>
</main>
<script>
const $ = s => document.querySelector(s);
const f = $('#f'), status = $('#status'), logEl = $('#log');
let themes = [], selected = null, polling = null;

async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function setStatus(text, cls) { status.textContent = text; status.className = cls || ''; }

function paramsFromForm() {
  const v = n => f.elements[n];
  const outputs = ['html'];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) if (v(box).checked) outputs.push(name);
  return { city: v('city').value, country: v('country').value, theme: v('theme').value, distance: +v('distance').value,
    exaggeration: +v('exaggeration').value, buildings: v('buildings').checked, trees: v('trees').checked,
    terrain: v('terrain').checked, overture: v('overture').checked, latitude: v('latitude').value, longitude: v('longitude').value, outputs };
}
function saveForm() { try { localStorage.setItem('map3d-gui', JSON.stringify(paramsFromForm())); } catch (e) {} }
function restoreForm() {
  try {
    const p = JSON.parse(localStorage.getItem('map3d-gui') || 'null'); if (!p) return;
    const v = n => f.elements[n];
    for (const k of ['city', 'country', 'theme', 'latitude', 'longitude']) if (p[k] !== undefined && v(k)) v(k).value = p[k];
    for (const k of ['distance', 'exaggeration']) if (p[k] !== undefined) v(k).value = p[k];
    for (const k of ['buildings', 'trees', 'terrain', 'overture']) if (p[k] !== undefined) v(k).checked = p[k];
    for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) v(box).checked = (p.outputs || []).includes(name);
  } catch (e) {}
}
function refreshLabels() {
  $('#distO').textContent = f.elements.distance.value + ' m';
  $('#exagO').textContent = (+f.elements.exaggeration.value).toFixed(1) + '×';
  const side = (2 * f.elements.distance.value / 1000).toFixed(1);
  const big = +f.elements.distance.value > 2500;
  $('#hint').textContent = big ? `${side} × ${side} km: large areas take much longer and need more memory.`
                               : `${side} × ${side} km model.`;
  const t = themes.find(t => t.id === f.elements.theme.value);
  if (t) $('#sw').style.background = `linear-gradient(135deg, ${t.bg} 50%, ${t.text} 50%)`;
}
f.addEventListener('input', () => { refreshLabels(); saveForm(); });

function openModel(file) {
  selected = file;
  $('#empty').hidden = true; const v = $('#view'); v.hidden = false; v.src = '/files/' + encodeURIComponent(file);
  document.querySelectorAll('li.m').forEach(li => li.classList.toggle('sel', li.dataset.file === file));
}
async function loadModels(openFirst) {
  const models = await api('/api/models'); const ul = $('#models'); ul.innerHTML = '';
  if (!models.length) { ul.innerHTML = '<li style="color:var(--dim)">Nothing yet.</li>'; return; }
  for (const m of models) {
    const li = document.createElement('li'); li.className = 'm'; li.dataset.file = m.file;
    const d = m.stamp; const when = `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)} ${d.slice(9,11)}:${d.slice(11,13)}`;
    li.innerHTML = `<b></b><span></span><div></div>`; li.querySelector('b').textContent = m.city;
    li.querySelector('span').textContent = `${m.theme} · ${when} · ${m.size_mb} MB`;
    const links = li.querySelector('div');
    const a = (href, text, dl) => {
      const e = document.createElement('a'); e.href = href; e.textContent = text; e.target = '_blank';
      if (dl) e.download = '';
      e.onclick = ev => ev.stopPropagation(); links.appendChild(e);
    };
    a('/files/' + encodeURIComponent(m.file), 'Open in tab ↗');
    for (const ext of m.extras) a('/files/' + encodeURIComponent(m.file.replace(/\.html$/, '.' + ext)), ext.toUpperCase(), ext !== 'png');
    li.onclick = () => openModel(m.file); ul.appendChild(li);
  }
  if (openFirst && !selected) openModel(models[0].file); else openModel(selected || models[0].file);
}

function busy(on) { $('#go').disabled = on; $('#stop').hidden = !on; $('#go').textContent = on ? 'Generating…' : 'Generate 3D model'; }
async function poll(id) {
  let since = 0; busy(true); logEl.textContent = '';
  clearInterval(polling);
  polling = setInterval(async () => {
    let j; try { j = await api(`/api/job?id=${id}&since=${since}`); } catch (e) { return; }
    if (j.lines.length) { logEl.textContent += (logEl.textContent ? '\n' : '') + j.lines.join('\n'); logEl.scrollTop = logEl.scrollHeight; since = j.next; }
    if (j.state === 'running') { setStatus(`Working… ${Math.round(j.elapsed)} s`); return; }
    clearInterval(polling); busy(false);
    if (j.state === 'done') {
      setStatus('Done ✓', 'ok'); const html = j.files.find(n => n.endsWith('.html'));
      selected = null; await loadModels(false); if (html) openModel(html);
    } else if (j.state === 'cancelled') setStatus('Cancelled');
    else setStatus('Failed - see the log below.', 'err');
  }, 700);
}
f.addEventListener('submit', async e => {
  e.preventDefault(); setStatus('Starting…');
  try { const { id } = await api('/api/generate', paramsFromForm()); poll(id); }
  catch (err) { setStatus(err.message, 'err'); }
});
$('#stop').onclick = () => api('/api/cancel', {}).catch(() => {});

(async () => {
  themes = await api('/api/themes');
  const sel = $('#theme');
  for (const t of themes) {
    const o = document.createElement('option'); o.value = t.id; o.textContent = t.name; o.title = t.description; sel.appendChild(o);
  }
  sel.value = themes.some(t => t.id === 'midnight_blue_contrast') ? 'midnight_blue_contrast' : themes[0].id;
  restoreForm(); refreshLabels();
  await loadModels(true);
  const cur = await api('/api/current'); if (cur.id) poll(cur.id);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
