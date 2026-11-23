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
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import chunks

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
        f"--tiles={int(number('tiles', 1, 7, 1))}",
        f"--exaggeration={number('exaggeration', 0.2, 6, 1.5):g}",
        f"--building-exaggeration={number('building_exaggeration', 0.2, 6, 1.5):g}",
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
        # Say why it failed: a crash or out-of-memory kill can end the process without printing anything.
        if rc != 0:
            self.lines.append(f"The build stopped with exit code {rc}"
                              + (" (the process crashed or ran out of memory; try a smaller distance)" if rc < 0 or rc > 255 else "")
                              + ".")
        elif not has_view:
            self.lines.append("The build finished but reported no viewer file (no 'Saved ... .html' line was printed).")
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
        if url.path == "/api/chunk":
            return self._chunk(q)
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

    def _chunk(self, q):
        """Next tile beyond a model's edge, built on demand from real data (see map3d/chunks.py)."""
        try:
            spec, i, j = chunks.parse_request({k: v[0] for k, v in q.items() if v})
        except chunks.ChunkError as e:
            return self._error(400, str(e))
        try:
            payload = chunks.chunk_payload(spec, i, j)
        except Exception as e:      # the viewer marks this cell as failed and tries again later
            traceback.print_exc()
            return self._error(502, f"Chunk {i},{j} could not be built: {e}")
        return self._json(payload)

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


INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D City Maps</title>
<style>
  :root { --bg:#10131a; --panel:#141821; --card:#1b2030; --line:#2a3040; --text:#e8ebf2; --dim:#8f98ad;
    --accent:#e0b84a; --accent-ink:#1b1608; --bad:#ff7b72; --ok:#7ee787; --r:12px; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#eef0f4; --panel:#ffffff; --card:#f6f7fa; --line:#d5d9e2; --text:#1b2030; --dim:#5d6678;
      --accent:#a8741a; --accent-ink:#ffffff; --bad:#c0362c; --ok:#1a7f37; }
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body { display: grid; grid-template-columns: var(--side, 360px) 1fr; transition: grid-template-columns .22s ease; }
  body.collapsed { grid-template-columns: 60px 1fr; }
  main { position: relative; height: 100vh; min-width: 0; }

  /* ---- sidebar shell ---------------------------------------------------------------------- */
  aside { position: relative; display: flex; flex-direction: column; height: 100vh; min-width: 0; overflow: hidden;
    background: var(--panel); border-right: 1px solid var(--line); }
  header.top { display: flex; align-items: center; gap: 10px; padding: 14px 12px 10px 14px; }
  .logo { flex: none; width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 17px;
    background: linear-gradient(135deg, #f0cf68, #9c7418); color: #1b1608; font-weight: 700; }
  .brand-txt { min-width: 0; flex: 1; }
  .brand-txt h1 { font-size: 16px; margin: 0; letter-spacing: .03em; white-space: nowrap; }
  .ellip { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dim { color: var(--dim); font-size: 12px; }
  .icon-btn { flex: none; width: 30px; height: 30px; display: grid; place-items: center; font-size: 18px; line-height: 1;
    background: transparent; color: var(--text); border: 1px solid var(--line); border-radius: 9px; }
  .icon-btn:hover { border-color: var(--dim); }

  #miniProg { margin: 0 14px 10px; padding: 9px 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--card); }
  .bar { height: 8px; border-radius: 6px; background: var(--bg); border: 1px solid var(--line); overflow: hidden; }
  .bar > div { height: 100%; width: 0; background: var(--accent); transition: width .5s ease; position: relative; overflow: hidden; }
  .bar > div::after { content: ''; position: absolute; inset: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,.35), transparent);
    transform: translateX(-100%); animation: sweep 1.4s linear infinite; }
  #prog.idle .bar > div::after { animation: none; }
  @keyframes sweep { to { transform: translateX(100%); } }
  .progRow { display: flex; justify-content: space-between; gap: 8px; margin-top: 6px; font-size: 12px; color: var(--dim); }
  .progRow span:first-child { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .tabs { display: flex; gap: 4px; margin: 0 12px; padding: 4px; background: var(--bg); border: 1px solid var(--line); border-radius: 12px; }
  .tabs button { flex: 1; min-width: 0; border: 0; background: transparent; color: var(--dim); font: inherit; font-weight: 600;
    font-size: 13px; padding: 7px 4px; border-radius: 9px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 5px; }
  .tabs button:hover { color: var(--text); }
  .tabs button[aria-selected="true"] { background: var(--card); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.25); }
  .tabs button:focus-visible, .icon-btn:focus-visible, summary:focus-visible, .sw:focus-visible, li.m:focus-visible, a:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px; }
  .count { font-size: 10px; min-width: 18px; padding: 0 5px; border-radius: 99px; background: var(--accent); color: var(--accent-ink); line-height: 16px; text-align: center; }
  .count:empty { display: none; }

  #sideBody { flex: 1; overflow-y: auto; padding: 14px 14px 28px; scrollbar-width: thin; }
  .tabpanel { animation: fadeIn .2s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

  /* ---- cards (collapsible sections) --------------------------------------------------------- */
  .card { background: var(--card); border: 1px solid var(--line); border-radius: var(--r); margin: 0 0 10px; }
  details.card > summary { list-style: none; display: flex; align-items: center; gap: 8px; padding: 11px 14px; cursor: pointer;
    font-weight: 600; font-size: 13px; user-select: none; border-radius: var(--r); }
  details.card > summary::-webkit-details-marker { display: none; }
  details.card > summary > span:first-child { flex: 1; }
  details.card > summary:hover { color: var(--accent); }
  .chev { color: var(--dim); font-size: 18px; line-height: 1; transition: transform .2s ease; display: inline-block; }
  details[open] > summary .chev { transform: rotate(90deg); }
  .cbody { padding: 0 14px 14px; }
  .pill { font-size: 10px; font-weight: 600; letter-spacing: .06em; padding: 1px 7px; border-radius: 99px; border: 1px solid var(--line);
    color: var(--dim); text-transform: lowercase; white-space: nowrap; }
  .pill.l { color: var(--ok); border-color: var(--ok); }
  .pill.q { color: var(--accent); border-color: var(--accent); }

  /* ---- form ------------------------------------------------------------------------------- */
  label { display: block; margin: 12px 0 0; font-size: 12px; color: var(--dim); }
  label output { float: right; color: var(--text); font-variant-numeric: tabular-nums; }
  input[type=text], input[type=search], select { width: 100%; margin-top: 5px; padding: 8px 10px; border-radius: 8px; color: var(--text);
    background: var(--bg); border: 1px solid var(--line); font: inherit; }
  input[type=text]:focus, input[type=search]:focus { outline: 2px solid var(--accent); outline-offset: -1px; border-color: transparent; }
  input[type=range] { width: 100%; accent-color: var(--accent); margin-top: 6px; }
  .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; margin-top: 8px; }
  .checks label { display: flex; align-items: center; gap: 7px; margin: 0; color: var(--text); font-size: 13px; }
  .checks input { accent-color: var(--accent); width: 15px; height: 15px; }
  .group { margin-top: 14px; }
  .group > .gl { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim); }
  label.dirty { color: var(--accent); } label.dirty::after { content: ' • queued'; font-size: 11px; }
  label.dirty > input[type=text], label.dirty > input[type=range] { outline: 1px solid var(--accent); }
  #hint { font-size: 12px; color: var(--dim); margin-top: 8px; min-height: 0; }
  #hint:empty { display: none; }
  #pend { margin: 12px 0 0; font-size: 12px; color: var(--accent); }
  #pend:empty { display: none; }

  /* footprint: a live picture of the area being built */
  .foot { display: flex; gap: 14px; align-items: center; padding: 10px; border-radius: 10px; background: var(--bg); border: 1px solid var(--line); }
  .grid { flex: none; width: 74px; height: 74px; display: grid; gap: 2px; padding: 3px; border-radius: 8px; border: 1px dashed var(--dim); }
  .grid i { display: block; background: var(--accent); border-radius: 2px; opacity: .85; transition: background .3s; }
  .footTxt { min-width: 0; }
  .big { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
  #footSub { font-size: 12px; color: var(--dim); margin-top: 3px; }
  #footSub .pill { margin-left: 4px; }

  .btn-primary, .btn-ghost { width: 100%; font: inherit; font-weight: 600; border-radius: 10px; cursor: pointer; padding: 11px; margin-top: 14px; }
  .btn-primary { border: 0; background: var(--accent); color: var(--accent-ink); }
  .btn-primary:disabled { opacity: .5; cursor: progress; }
  .btn-ghost { margin-top: 8px; padding: 8px; background: transparent; color: var(--text); border: 1px solid var(--line); font-weight: 500; }
  .btn-ghost:hover { border-color: var(--dim); }

  #prog { margin: 14px 0 0; }
  #status { margin-top: 12px; min-height: 20px; font-size: 13px; }
  #status.err { color: var(--bad); } #status.ok { color: var(--ok); }
  #log { margin: 0; padding: 10px; max-height: 210px; overflow: auto; border-radius: 8px; background: var(--bg);
    border: 1px solid var(--line); font: 11px/1.5 ui-monospace, Consolas, monospace; white-space: pre-wrap; color: var(--dim); }

  /* ---- style: theme swatches ------------------------------------------------------------- */
  .swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; margin-bottom: 8px; }
  .sw { position: relative; height: 56px; padding: 0; border-radius: 10px; border: 2px solid transparent; cursor: pointer; overflow: hidden; }
  .sw:hover { border-color: var(--dim); }
  .sw[aria-pressed="true"] { border-color: var(--accent); box-shadow: 0 0 0 2px var(--panel), 0 0 0 4px var(--accent); }
  .sw span { position: absolute; left: 6px; bottom: 6px; font-size: 11px; font-weight: 600; color: #fff; padding: 1px 6px; border-radius: 6px;
    background: rgba(0,0,0,.5); max-width: calc(100% - 12px); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #themeDesc { min-height: 16px; font-size: 12px; color: var(--dim); }
  #previewMount .none { color: var(--dim); font-size: 12px; margin: 4px 0; }
  /* the viewer's own panel, moved here: drop its floating-card look and fit the sidebar */
  #previewMount #ui { position: static !important; width: auto !important; max-height: none !important; padding: 0 !important;
    background: none !important; border: 0 !important; backdrop-filter: none !important; color: var(--text); }
  #previewMount #ui h1, #previewMount #ui .sub#s { display: none; }
  #previewMount #ui #t { display: none; }
  #previewMount #ui #themeSel, #previewMount #ui > span.row:nth-of-type(1) { display: none; }   /* one theme picker: ours, above */
  #previewMount #ui select { background: var(--bg); border-color: var(--line); }
  #previewMount #ui button { border-color: var(--line); }

  /* ---- library ---------------------------------------------------------------------------- */
  #libSearch { margin-bottom: 10px; }
  ul { list-style: none; margin: 0; padding: 0; }
  li.m { display: grid; grid-template-columns: 62px 1fr; gap: 11px; align-items: center; padding: 8px; margin-bottom: 8px;
    border: 1px solid var(--line); border-radius: 10px; background: var(--card); cursor: pointer; transition: border-color .15s; }
  li.m:hover { border-color: var(--dim); }
  li.m.sel { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
  li.m .thumb { width: 62px; height: 62px; object-fit: cover; border-radius: 8px; background: var(--bg); border: 1px solid var(--line); }
  li.m .ph { display: grid; place-items: center; color: var(--dim); font-size: 22px; }
  li.m b { display: block; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  li.m .meta { display: block; color: var(--dim); font-size: 12px; }
  li.m .links { margin-top: 3px; }
  li.m a { color: var(--accent); font-size: 12px; margin-right: 10px; text-decoration: none; }
  li.m a:hover { text-decoration: underline; }
  li.empty { color: var(--dim); font-size: 13px; padding: 10px 2px; }

  /* ---- insights --------------------------------------------------------------------------- */
  .hero { padding: 14px; border-radius: var(--r); border: 1px solid var(--line); margin-bottom: 10px;
    background: linear-gradient(135deg, var(--card), var(--bg)); }
  .hero .kicker { font-size: 11px; text-transform: uppercase; letter-spacing: .1em; color: var(--accent); }
  .hero .t { font-size: 21px; font-weight: 700; margin: 2px 0 4px; line-height: 1.2; }
  .acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .mini-btn { font: inherit; font-size: 12px; padding: 5px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg);
    color: var(--text); cursor: pointer; text-decoration: none; display: inline-block; }
  .mini-btn:hover { border-color: var(--dim); }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; min-width: 0; }
  .stat .k { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim); }
  .stat .v { font-size: 20px; font-weight: 700; margin-top: 2px; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat .s { font-size: 11px; color: var(--dim); margin-top: 1px; }
  .meter { height: 7px; border-radius: 4px; background: var(--bg); border: 1px solid var(--line); overflow: hidden; margin: 6px 0 4px; }
  .meter i { display: block; height: 100%; background: var(--accent); }
  .rowk { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; padding: 5px 0; border-bottom: 1px solid var(--line); }
  .rowk:last-child { border-bottom: 0; }
  .rowk span { color: var(--dim); }
  .rowk b { font-variant-numeric: tabular-nums; }
  .tips { display: grid; gap: 7px; font-size: 13px; }
  kbd { font: 11px ui-monospace, Consolas, monospace; padding: 1px 6px; border-radius: 5px; border: 1px solid var(--line);
    background: var(--bg); border-bottom-width: 2px; }
  .empty-state { text-align: center; padding: 26px 10px; color: var(--dim); font-size: 13px; }
  .empty-state b { display: block; color: var(--text); font-size: 15px; margin-bottom: 4px; }

  /* ---- resize handle & collapsed rail ----------------------------------------------------- */
  #resizer { position: absolute; top: 0; right: 0; width: 6px; height: 100%; cursor: col-resize; z-index: 5; touch-action: none; }
  #resizer:hover, #resizer:focus-visible { background: linear-gradient(90deg, transparent 2px, var(--accent) 2px, var(--accent) 4px, transparent 4px); outline: none; }
  body.collapsed #sideBody, body.collapsed .tabs, body.collapsed #miniProg, body.collapsed .brand-txt, body.collapsed #resizer { display: none; }
  body.collapsed header.top { flex-direction: column; padding: 14px 0; gap: 14px; }

  /* ---- viewer area ------------------------------------------------------------------------ */
  iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: var(--bg); }
  #empty { position: absolute; inset: 0; display: grid; place-items: center; text-align: center; color: var(--dim); padding: 30px; }
  #empty div { max-width: 360px; }
  #empty h3 { color: var(--text); margin: 0 0 6px; }

  @media (max-width: 800px) {
    body { grid-template-columns: 1fr; grid-template-rows: auto 60vh; }
    aside { height: auto; max-height: 58vh; } main { height: 60vh; }
    #resizer { display: none; }
  }
</style>
</head>
<body>
<aside id="side">
  <header class="top">
    <div class="logo" aria-hidden="true">◈</div>
    <div class="brand-txt">
      <h1>3D City Maps</h1>
      <div class="dim ellip" id="modelNow">No model open</div>
    </div>
    <button type="button" class="icon-btn" id="collapse" aria-expanded="true" aria-controls="sideBody" title="Collapse panel">‹</button>
  </header>

  <div id="miniProg" role="status" aria-live="polite" hidden>
    <div class="bar"><div id="miniFill"></div></div>
    <div class="progRow"><span id="miniStage">Starting…</span><span id="miniPct">0%</span></div>
  </div>

  <nav class="tabs" role="tablist" aria-label="Panel sections">
    <button type="button" role="tab" id="t-create" data-tab="create" aria-controls="tab-create" aria-selected="true">Create</button>
    <button type="button" role="tab" id="t-style" data-tab="style" aria-controls="tab-style" aria-selected="false" tabindex="-1">Style</button>
    <button type="button" role="tab" id="t-insights" data-tab="insights" aria-controls="tab-insights" aria-selected="false" tabindex="-1">Insights</button>
    <button type="button" role="tab" id="t-library" data-tab="library" aria-controls="tab-library" aria-selected="false" tabindex="-1">Library<span class="count" id="libCount"></span></button>
  </nav>

  <div id="sideBody">
    <!-- ================= CREATE ================= -->
    <section class="tabpanel" id="tab-create" role="tabpanel" aria-labelledby="t-create">
      <form id="f" autocomplete="off">
        <details class="card" open>
          <summary><span>Place &amp; area</span><span class="pill q" title="Changes here are queued and applied when you rebuild the model">rebuild</span><span class="chev">›</span></summary>
          <div class="cbody">
            <div class="foot">
              <div class="grid" id="grid" aria-hidden="true"></div>
              <div class="footTxt"><div class="big" id="footBig">—</div><div id="footSub"></div></div>
            </div>
            <label>City<input type="text" name="city" value="Val-d'Or" required></label>
            <label>Country<input type="text" name="country" value="Canada" required></label>
            <label>Area radius <output id="distO"></output><input type="range" name="distance" min="500" max="4000" step="100" value="1500"></label>
            <label>Tiles (grid) <output id="tilesO"></output><input type="range" name="tiles" min="1" max="7" step="1" value="1"></label>
            <div id="hint"></div>
          </div>
        </details>

        <details class="card" open>
          <summary><span>Data sources</span><span class="pill q">rebuild</span><span class="chev">›</span></summary>
          <div class="cbody">
            <div class="checks">
              <label><input type="checkbox" name="terrain" checked> Real terrain</label>
              <label><input type="checkbox" name="overture" checked> Overture Maps</label>
            </div>
          </div>
        </details>

        <details class="card">
          <summary><span>Also save</span><span class="chev">›</span></summary>
          <div class="cbody">
            <div class="checks">
              <label><input type="checkbox" name="o_poster"> Poster image</label>
              <label><input type="checkbox" name="o_glb"> GLB model</label>
              <label><input type="checkbox" name="o_stl"> STL (3D print)</label>
            </div>
          </div>
        </details>

        <details class="card">
          <summary><span>Exact centre</span><span class="chev">›</span></summary>
          <div class="cbody">
            <label>Latitude<input type="text" name="latitude" placeholder="48.1023"></label>
            <label>Longitude<input type="text" name="longitude" placeholder="-77.7876"></label>
          </div>
        </details>

        <div id="pend"></div>
        <button type="submit" id="go" class="btn-primary">Generate 3D model</button>
        <button type="button" id="stop" class="btn-ghost" hidden>Cancel build</button>
      </form>

      <div id="prog" hidden>
        <div class="bar"><div id="fill"></div></div>
        <div class="progRow"><span id="progStage">Starting…</span><span id="progPct">0%</span></div>
      </div>
      <div id="status" role="status"></div>
      <details class="card" id="logCard" hidden style="margin-top:12px">
        <summary><span>Build log</span><span class="chev">›</span></summary>
        <div class="cbody"><pre id="log"></pre></div>
      </details>
    </section>

    <!-- ================= STYLE ================= -->
    <section class="tabpanel" id="tab-style" role="tabpanel" aria-labelledby="t-style" hidden>
      <details class="card" open>
        <summary><span>Theme</span><span class="pill l" title="Applied to the open model immediately">instant</span><span class="chev">›</span></summary>
        <div class="cbody">
          <div class="swatches" id="swatches"></div>
          <div id="themeDesc"></div>
          <select name="theme" id="theme" form="f" hidden></select>
        </div>
      </details>
      <details class="card" open>
        <summary><span>Live controls</span><span class="pill l" title="Applied to the model immediately, no rebuild">instant</span><span class="chev">›</span></summary>
        <div class="cbody">
          <div id="previewMount"><p class="none">Open a model from Library, or build one, and its live controls appear here.</p></div>
        </div>
      </details>
    </section>

    <!-- ================= INSIGHTS ================= -->
    <section class="tabpanel" id="tab-insights" role="tabpanel" aria-labelledby="t-insights" hidden>
      <div id="insights"></div>
    </section>

    <!-- ================= LIBRARY ================= -->
    <section class="tabpanel" id="tab-library" role="tabpanel" aria-labelledby="t-library" hidden>
      <input type="search" id="libSearch" placeholder="Search city, theme or date…" aria-label="Search models">
      <ul id="models"></ul>
    </section>
  </div>
  <div id="resizer" role="separator" aria-orientation="vertical" aria-label="Resize panel (drag, or use arrow keys)" tabindex="0" title="Drag to resize · double-click to reset"></div>
</aside>
<main>
  <div id="empty"><div><h3>No model yet</h3>Choose a city and press <b>Generate</b>. The first build downloads map data and
    takes a minute; rebuilding the same area is fast. Drag to orbit, scroll to zoom.</div></div>
  <iframe id="view" title="3D model" hidden></iframe>
</main>
<script>
const $ = s => document.querySelector(s);
const f = $('#f'), status = $('#status'), logEl = $('#log');
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const TABS = ['create', 'style', 'insights', 'library'];
let themes = [], selected = null, polling = null, jobParams = null, currentTab = 'create';
let lastModels = [], modelMeta = {}, modelTheme = {}, lastCfg = null;

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
  // Heights, buildings and trees are live controls in the viewer, so the build always bakes the neutral defaults.
  return { city: v('city').value, country: v('country').value, theme: v('theme').value, distance: +v('distance').value, tiles: +v('tiles').value,
    exaggeration: 1.5, building_exaggeration: 1.5, buildings: true, trees: true,
    terrain: v('terrain').checked, overture: v('overture').checked, latitude: v('latitude').value, longitude: v('longitude').value, outputs };
}
// Settings that need a rebuild are queued: compared against what the open model was built with.
const REBUILD = ['city', 'country', 'distance', 'tiles', 'terrain', 'overture', 'latitude', 'longitude', 'outputs'];
let baked = null, building = false;
const bakedStore = () => { try { return JSON.parse(localStorage.getItem('map3d-baked') || '{}'); } catch (e) { return {}; } };
function rememberBaked(file, p) { try { const s = bakedStore(); s[file] = p; localStorage.setItem('map3d-baked', JSON.stringify(s)); } catch (e) {} }
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function dirtyKeys() {
  if (!baked) return [];
  const p = paramsFromForm();
  return REBUILD.filter(k => !same(typeof p[k] === 'string' ? p[k].trim() : p[k], typeof baked[k] === 'string' ? baked[k].trim() : baked[k]));
}
function updateGo() {
  const go = $('#go'), d = dirtyKeys(), has = !!selected;
  for (const k of REBUILD) { const el = f.elements[k]; const lab = el && el.closest && el.closest('label'); if (lab) lab.classList.toggle('dirty', d.includes(k)); }
  $('#pend').textContent = building ? '' : d.length ? `${d.length} change${d.length > 1 ? 's' : ''} queued - applied when you rebuild. Style changes apply instantly.` : '';
  if (building) { go.disabled = true; go.textContent = 'Building…'; return; }
  if (!has) { go.disabled = false; go.textContent = 'Generate 3D model'; }
  else if (!baked) { go.disabled = false; go.textContent = 'Rebuild this area'; }
  else if (d.length) { go.disabled = false; go.textContent = `Rebuild · apply ${d.length} change${d.length > 1 ? 's' : ''}`; }
  else { go.disabled = true; go.textContent = 'Up to date'; }
}
// Show an opened model's own build settings in the form, so form and view always describe the same thing.
function loadFormFrom(p) {
  const v = n => f.elements[n];
  for (const k of ['city', 'country', 'latitude', 'longitude']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['distance', 'tiles']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['terrain', 'overture']) if (p[k] !== undefined) v(k).checked = p[k];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) v(box).checked = (p.outputs || []).includes(name);
  if (p.theme && [...v('theme').options].some(o => o.value === p.theme)) v('theme').value = p.theme;
}
function saveForm() { store.set('map3d-gui', paramsFromForm()); }
function restoreForm() {
  const p = store.get('map3d-gui', null); if (!p) return;
  const v = n => f.elements[n];
  for (const k of ['city', 'country', 'theme', 'latitude', 'longitude']) if (p[k] !== undefined && v(k)) v(k).value = p[k];
  for (const k of ['distance', 'tiles']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['terrain', 'overture']) if (p[k] !== undefined) v(k).checked = p[k];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) v(box).checked = (p.outputs || []).includes(name);
}

// ---- footprint: the area as a live picture of tiles, with a plain-language size ----------------
function refreshLabels() {
  const d = +f.elements.distance.value, n = +f.elements.tiles.value;
  $('#distO').textContent = d + ' m';
  $('#tilesO').textContent = n + ' × ' + n;
  const side = 2 * d * n / 1000, km2 = side * side;
  $('#footBig').textContent = `${side.toFixed(1)} × ${side.toFixed(1)} km`;
  const load = km2 <= 9 ? ['Small', 'Small area, light on memory and data'] : km2 <= 36 ? ['Medium', 'A moderate amount of map data'] :
    km2 <= 100 ? ['Large', 'Large area: more data to download and more memory'] : ['Very large', 'Built tile by tile; expect a long build'];
  $('#footSub').innerHTML = `${n * n} tile${n * n > 1 ? 's' : ''} · ${km2.toFixed(1)} km² <span class="pill" title="${esc(load[1])}">${load[0]}</span>`;
  const g = $('#grid');
  if (g.dataset.n !== String(n)) { g.dataset.n = n; g.style.gridTemplateColumns = `repeat(${n}, 1fr)`; g.innerHTML = '<i></i>'.repeat(n * n); }
  $('#hint').textContent = (n > 1 ? `Each tile is ${(2 * d / 1000).toFixed(1)} km across. ` : '') +
    (km2 > 36 ? 'Large areas take much longer and need more memory.' : '');
  syncSwatches();
}
f.addEventListener('input', () => { refreshLabels(); saveForm(); updateGo(); });

// ---- theme: swatch cards (the hidden select stays the form value and the viewer's source) -----
function renderSwatches() {
  const box = $('#swatches'); box.innerHTML = '';
  for (const t of themes) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'sw'; b.dataset.id = t.id; b.title = t.description || t.name;
    b.setAttribute('aria-label', `Theme ${t.name}`);
    b.style.background = `linear-gradient(135deg, ${t.bg} 55%, ${t.text} 55%)`;
    b.innerHTML = '<span></span>'; b.querySelector('span').textContent = t.name;
    b.onclick = () => setTheme(t.id);
    box.appendChild(b);
  }
}
function setTheme(id) {
  const sel = $('#theme'); if (sel.value === id) return;
  sel.value = id; sel.dispatchEvent(new Event('change'));
}
function syncSwatches() {
  const cur = $('#theme').value; let desc = '';
  document.querySelectorAll('#swatches .sw').forEach(b => {
    const on = b.dataset.id === cur; b.setAttribute('aria-pressed', String(on)); if (on) desc = b.title;
  });
  $('#themeDesc').textContent = desc;
}
// Changing the theme recolours the open model right away (no rebuild, no reload).
$('#theme').addEventListener('change', () => {
  refreshLabels(); saveForm();
  // The viewer's own (hidden) theme picker now lives in this page: drive it, which also works for older exports.
  const vs = document.getElementById('themeSel');
  if (vs && [...vs.options].some(o => o.value === $('#theme').value)) { vs.value = $('#theme').value; vs.dispatchEvent(new Event('change')); }
});

// One sidebar: the model viewer's settings panel is moved out of the frame into the Style tab (same origin),
// together with the viewer's own panel styles. Its event handlers keep working; the viewer looks its controls up here.
function mountPreviewPanel(frame) {
  const mount = $('#previewMount');
  try {
    const d = frame.contentDocument, ui = d.getElementById('ui');
    if (!ui) return;
    let css = '';
    for (const sheet of d.styleSheets) for (const r of sheet.cssRules) {
      if (r.selectorText && /^#(ui|stats|realBox|perf)\b/.test(r.selectorText)) css += r.cssText + '\n';
    }
    let st = $('#previewCss'); if (!st) { st = document.createElement('style'); st.id = 'previewCss'; document.head.appendChild(st); }
    st.textContent = css;
    mount.textContent = '';
    mount.appendChild(document.adoptNode(ui));
  } catch (e) { mount.innerHTML = '<p class="none">Preview settings are shown on the model itself.</p>'; }
}
// The viewer's build facts (buildings, trees, relief...) are in its config block; read them without executing anything.
function readCfg(frame) {
  try { return JSON.parse(frame.contentDocument.getElementById('cfg').textContent); } catch (e) { return null; }
}

// ---- models, library and insights ----------------------------------------------------------------
function timeAgo(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 30 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function openModel(file) {
  selected = file;
  baked = bakedStore()[file] || null;
  if (baked) loadFormFrom(baked); else if (modelTheme[file]) loadFormFrom({ theme: modelTheme[file] });
  refreshLabels(); updateGo();
  const m = modelMeta[file] || {};
  $('#modelNow').textContent = m.city ? `${m.city} · ${m.theme}` : 'Model open';
  $('#empty').hidden = true; const v = $('#view'); v.hidden = false; v.src = '/files/' + encodeURIComponent(file);
  $('#previewMount').innerHTML = '<p class="none">Loading this model’s live controls…</p>';
  $('#insights').innerHTML = '<p class="none">Reading this model…</p>';
  v.onload = () => { v.contentWindow.focus(); mountPreviewPanel(v); lastCfg = readCfg(v); renderInsights(); };
  document.querySelectorAll('li.m').forEach(li => { const on = li.dataset.file === file; li.classList.toggle('sel', on); li.setAttribute('aria-current', on ? 'true' : 'false'); });
}
function stat(k, v, s) {
  return `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>${s ? `<div class="s">${esc(s)}</div>` : ''}</div>`;
}
const num = n => (typeof n === 'number' ? n.toLocaleString('en-US') : '—');
function renderInsights() {
  const box = $('#insights');
  if (!selected) {
    box.innerHTML = `<div class="empty-state"><b>Nothing open yet</b>Open a model from the Library tab, or build one in Create.<br>
      Its facts (area, buildings, relief, build settings) show up here.</div>`;
    return;
  }
  const m = modelMeta[selected] || {}, c = lastCfg;
  if (!c) { box.innerHTML = '<p class="none">This model’s details could not be read.</p>'; return; }
  const facts = c.facts || {}, b = facts.buildings || null, tiles = c.tiles || 1;
  const side = (c.tileSize || 0) * tiles / 1000, km2 = side * side;
  const lat = c.geo ? c.geo.lat : null, lon = c.geo ? c.geo.lon : null;
  const hasCoords = lat != null && lon != null;
  const coords = hasCoords ? `${(+lat).toFixed(4)}, ${(+lon).toFixed(4)}` : '';
  const themeName = c.themes && c.themes[c.theme] ? c.themes[c.theme].name : (c.theme || m.theme || '');
  const created = m.mtime ? new Date(m.mtime * 1000).toLocaleString() : '';
  const hm = b ? Math.round(100 * b.height_known / Math.max(1, b.count)) : null;
  const cards = [
    stat('Area', `${side.toFixed(1)} km`, `${km2.toFixed(1)} km² · ${tiles}×${tiles} tile${tiles > 1 ? 's' : ''}`),
    stat('Buildings', b ? num(b.count) : '—', b ? `${num(b.gabled)} with pitched roofs` : 'rebuild to see this'),
    stat('Trees', num(facts.trees), ''),
    stat('Bridges', num(facts.bridges), ''),
    stat('Relief', facts.relief_m != null ? `${facts.relief_m} m` : '—', facts.dem_min_m != null ? `lowest point ${facts.dem_min_m} m` : ''),
    stat('Traffic', c.traffic ? 'On' : 'Off', c.traffic ? 'stops at real lights and signs' : ''),
  ].join('');
  const heights = b ? `<div class="card" style="padding:12px 14px;margin-bottom:10px">
      <div class="rowk" style="border:0;padding:0"><span>Building heights from map data</span><b>${hm}%</b></div>
      <div class="meter"><i style="width:${hm}%"></i></div>
      <div class="dim">${num(b.height_known)} mapped · ${num(b.height_estimated)} estimated from building type</div></div>` : '';
  const hero = `<div class="hero">
      <div class="kicker">${esc(c.subtitle || 'Model')}</div>
      <div class="t">${esc(c.title || m.city || 'Untitled')}</div>
      <div class="dim">${esc(themeName)}${m.size_mb ? ' · ' + esc(m.size_mb) + ' MB' : ''}${created ? ' · built ' + esc(created) : ''}</div>
      ${hasCoords ? `<div class="acts"><button type="button" class="mini-btn" id="copyCoords">Copy coordinates ${esc(coords)}</button>
        <a class="mini-btn" target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${+lat}&amp;mlon=${+lon}#map=13/${+lat}/${+lon}">Map ↗</a></div>` : ''}
    </div>`;
  const note = c.facts ? '' : `<p class="dim" style="margin:0 0 10px">This model was built before these details were recorded. Rebuild it to see buildings, trees and relief.</p>`;
  box.innerHTML = `${hero}${note}<div class="stats">${cards}</div>${heights}
    <details class="card" open><summary><span>Build settings</span><span class="chev">›</span></summary>
      <div class="cbody">
        <div class="rowk"><span>Terrain exaggeration</span><b>×${esc(c.exag ? c.exag.terrain : '—')}</b></div>
        <div class="rowk"><span>Building height</span><b>×${esc(c.exag ? c.exag.building : '—')}</b></div>
        <div class="rowk"><span>Tiles</span><b>${tiles} × ${tiles}</b></div>
        <div class="rowk"><span>Map centre</span><b>${hasCoords ? esc(coords) : '—'}</b></div>
      </div></details>
    <details class="card"><summary><span>Viewer controls</span><span class="chev">›</span></summary>
      <div class="cbody"><ul class="tips">
        <li><kbd>Drag</kbd> orbit the model</li>
        <li><kbd>Scroll</kbd> zoom in and out</li>
        <li><kbd>Right-drag</kbd> pan across the city</li>
        <li><kbd>Ctrl</kbd> + <kbd>F</kbd> find a place</li>
      </ul></div></details>`;
  const cb = $('#copyCoords');
  if (cb) cb.onclick = () => { try { navigator.clipboard.writeText(coords).then(() => { cb.textContent = 'Copied ✓'; }, () => {}); } catch (e) {} };
}

function renderLibrary() {
  const ul = $('#models'), q = $('#libSearch').value.trim().toLowerCase();
  $('#libCount').textContent = lastModels.length ? String(lastModels.length) : '';
  ul.innerHTML = '';
  if (!lastModels.length) { ul.innerHTML = '<li class="empty">No models yet. Build one in the Create tab.</li>'; return; }
  const list = lastModels.filter(m => !q || `${m.city} ${m.theme} ${new Date(m.mtime * 1000).toLocaleDateString()}`.toLowerCase().includes(q));
  if (!list.length) { ul.innerHTML = `<li class="empty">Nothing matches “${esc(q)}”.</li>`; return; }
  for (const m of list) {
    const li = document.createElement('li');
    li.className = 'm' + (m.file === selected ? ' sel' : ''); li.dataset.file = m.file;
    li.tabIndex = 0; li.setAttribute('role', 'button'); li.setAttribute('aria-label', `Open ${m.city}`);
    const png = m.extras.includes('png') ? m.file.replace(/\.html$/, '.png') : null;
    li.innerHTML = `${png ? `<img class="thumb" loading="lazy" alt="" src="/files/${encodeURIComponent(png)}">` : '<div class="thumb ph" aria-hidden="true">◈</div>'}
      <div class="mbody"><b></b><span class="meta"></span><div class="links"></div></div>`;
    li.querySelector('b').textContent = m.city;
    li.querySelector('.meta').textContent = `${m.theme} · ${timeAgo(m.mtime)} · ${m.size_mb} MB`;
    const links = li.querySelector('.links');
    const a = (href, text, dl) => {
      const e = document.createElement('a'); e.href = href; e.textContent = text; e.target = '_blank'; e.rel = 'noopener';
      if (dl) e.download = '';
      e.onclick = ev => ev.stopPropagation(); e.onkeydown = ev => ev.stopPropagation(); links.appendChild(e);
    };
    a('/files/' + encodeURIComponent(m.file), 'Open in tab ↗');
    for (const ext of m.extras) a('/files/' + encodeURIComponent(m.file.replace(/\.html$/, '.' + ext)), ext.toUpperCase(), ext !== 'png');
    li.onclick = () => openModel(m.file);
    li.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openModel(m.file); } };
    ul.appendChild(li);
  }
}
async function loadModels(openFirst) {
  const models = await api('/api/models');
  lastModels = models;
  for (const m of models) { modelTheme[m.file] = m.theme; modelMeta[m.file] = m; }
  renderLibrary();
  if (!models.length) { renderInsights(); return; }
  if (openFirst && !selected) openModel(models[0].file); else openModel(selected || models[0].file);
}
$('#libSearch').addEventListener('input', renderLibrary);

// ---- progress: the build prints a known sequence of lines; each one moves the bar to its milestone ----
const STAGES = [
  [/Collecting data/, 4, 'Planning the area'],
  [/Step 1\/3/, 6, 'Downloading map data'],
  [/building outlines/, 10, 'Downloading buildings'],
  [/water, parks, forests/, 20, 'Downloading land cover'],
  [/railway lines/, 28, 'Downloading railways'],
  [/traffic lights and stop signs/, 33, 'Downloading traffic lights & stop signs'],
  [/street network/, 38, 'Downloading the street network'],
  [/Converting everything/, 46, 'Projecting to a local grid'],
  [/OpenStreetMap gave/, 50, 'Map data received'],
  [/Overture Maps: filling/, 54, 'Merging Overture Maps data'],
  [/Downloading elevation/, 62, 'Downloading terrain heights'],
  [/Step 3\/3/, 66, 'Building the 3D model'],
  [/Shaping the terrain/, 69, 'Shaping the terrain'],
  [/Extruding buildings/, 73, 'Extruding buildings'],
  [/Planting trees/, 79, 'Planting trees'],
  [/Casting building shadows/, 83, 'Meshing terrain and shadows'],
  [/Laying roads/, 88, 'Laying roads and bridges'],
  [/Preparing the traffic/, 92, 'Preparing traffic'],
  [/Adding the base plinth/, 95, 'Finishing the model'],
  [/built before|Reusing it|loaded from the cache/, 60, 'Reusing cached data'],
  [/Writing the interactive/, 97, 'Writing the 3D page'],
  [/generation complete/, 100, 'Done'],
];
let progPct = 0, progCap = 0;
function setProgress(pct, label) {
  progPct = Math.max(progPct, pct);
  const txt = Math.round(progPct) + '%';
  $('#fill').style.width = progPct + '%'; $('#progPct').textContent = txt;
  $('#miniFill').style.width = progPct + '%'; $('#miniPct').textContent = txt;
  if (label) { $('#progStage').textContent = label; $('#miniStage').textContent = label; }
}
function progressFromLines(lines) {
  for (const line of lines) {
    for (const [re, pct, label] of STAGES) if (re.test(line) && pct >= progPct) { setProgress(pct, label); progCap = pct; }
    const m = /tile (\d+)\/(\d+) built/.exec(line);   // big areas build tile by tile between 66% and 95%
    if (m) setProgress(66 + 29 * (+m[1] / +m[2]), `Building tile ${m[1]} of ${m[2]}`);
  }
}
setInterval(() => {   // slow downloads: creep towards the next milestone so the bar never looks frozen
  if (!$('#prog').hidden && !$('#prog').classList.contains('idle') && progPct < 96) setProgress(progPct + 0.15);
}, 500);
function syncMini() { $('#miniProg').hidden = !(building && currentTab !== 'create'); }
function busy(on) {
  const pr = $('#prog');
  if (on) { progPct = 0; pr.hidden = false; pr.className = ''; setProgress(1, 'Starting…'); }
  building = on; $('#stop').hidden = !on; updateGo(); syncMini();
}
async function poll(id) {
  let since = 0; busy(true); logEl.textContent = ''; $('#logCard').hidden = true;
  clearInterval(polling);
  polling = setInterval(async () => {
    let j; try { j = await api(`/api/job?id=${id}&since=${since}`); } catch (e) { return; }
    if (j.lines.length) {
      logEl.textContent += (logEl.textContent ? '\n' : '') + j.lines.join('\n'); logEl.scrollTop = logEl.scrollHeight;
      $('#logCard').hidden = false; since = j.next; progressFromLines(j.lines);
    }
    if (j.state === 'running') { setStatus(`Working… ${Math.round(j.elapsed)} s`); return; }
    clearInterval(polling); busy(false);
    $('#prog').classList.add('idle');
    if (j.state === 'done') {
      setProgress(100, 'Done');
      setStatus('Done ✓', 'ok'); const html = j.files.find(n => n.endsWith('.html'));
      if (html) rememberBaked(html, jobParams || paramsFromForm());
      selected = null; await loadModels(false); if (html) openModel(html);
    } else if (j.state === 'cancelled') { setStatus('Cancelled'); $('#progStage').textContent = 'Cancelled'; $('#miniStage').textContent = 'Cancelled'; }
    else {
      if (!logEl.textContent) logEl.textContent = 'The build stopped without printing anything.';
      $('#logCard').hidden = false; $('#logCard').open = true;
      setStatus('Failed - see the log below.', 'err'); $('#progStage').textContent = 'Failed'; $('#miniStage').textContent = 'Failed';
    }
  }, 700);
}
f.addEventListener('submit', async e => {
  e.preventDefault(); setStatus('Starting…');
  try { jobParams = paramsFromForm(); const { id } = await api('/api/generate', jobParams); poll(id); }
  catch (err) { setStatus(err.message, 'err'); }
});
$('#stop').onclick = () => api('/api/cancel', {}).catch(() => {});

// ---- tabs: keyboard-friendly (arrow keys, Home, End), remembered between visits ------------------
function showTab(name) {
  if (!TABS.includes(name)) name = 'create';
  currentTab = name;
  document.querySelectorAll('[data-tab]').forEach(b => {
    const on = b.dataset.tab === name; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.tabpanel').forEach(p => { p.hidden = p.id !== 'tab-' + name; });
  store.set('map3d-tab', name); syncMini();
}
document.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });
$('.tabs').addEventListener('keydown', e => {
  const i = TABS.indexOf(currentTab);
  let j = null;
  if (e.key === 'ArrowRight') j = (i + 1) % TABS.length;
  else if (e.key === 'ArrowLeft') j = (i + TABS.length - 1) % TABS.length;
  else if (e.key === 'Home') j = 0;
  else if (e.key === 'End') j = TABS.length - 1;
  if (j === null) return;
  e.preventDefault(); showTab(TABS[j]); document.querySelector(`[data-tab="${TABS[j]}"]`).focus();
});

// ---- collapse to a slim rail, and drag to resize the panel ---------------------------------------
function setCollapsed(on) {
  document.body.classList.toggle('collapsed', on);
  const b = $('#collapse'); b.textContent = on ? '›' : '‹';
  b.title = on ? 'Expand panel' : 'Collapse panel'; b.setAttribute('aria-expanded', String(!on));
  store.set('map3d-collapsed', on);
}
$('#collapse').onclick = () => setCollapsed(!document.body.classList.contains('collapsed'));
(() => {
  const r = $('#resizer');
  const clamp = w => Math.max(280, Math.min(600, Math.round(w)));
  let cur = clamp(store.get('map3d-side', 360));
  const apply = w => { cur = clamp(w); document.documentElement.style.setProperty('--side', cur + 'px'); };
  apply(cur);
  r.addEventListener('pointerdown', e => { r.setPointerCapture(e.pointerId); e.preventDefault(); });
  r.addEventListener('pointermove', e => { if (r.hasPointerCapture(e.pointerId)) apply(e.clientX); });
  r.addEventListener('pointerup', e => {
    if (r.hasPointerCapture(e.pointerId)) { r.releasePointerCapture(e.pointerId); store.set('map3d-side', cur); }
  });
  r.addEventListener('dblclick', () => { apply(360); store.set('map3d-side', cur); });
  r.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault(); apply(cur + (e.key === 'ArrowRight' ? 16 : -16)); store.set('map3d-side', cur);
  });
})();

// ---- start -----------------------------------------------------------------------------------------
(async () => {
  themes = await api('/api/themes');
  const sel = $('#theme');
  for (const t of themes) {
    const o = document.createElement('option'); o.value = t.id; o.textContent = t.name; o.title = t.description; sel.appendChild(o);
  }
  sel.value = themes.some(t => t.id === 'midnight_blue_contrast') ? 'midnight_blue_contrast' : themes[0].id;
  renderSwatches();
  restoreForm(); refreshLabels(); updateGo();
  await loadModels(true);
  const cur = await api('/api/current'); if (cur.id) poll(cur.id);
})();
setCollapsed(!!store.get('map3d-collapsed', false));
showTab(store.get('map3d-tab', 'create'));

// Ctrl+F while a model is showing searches the model (a place), not the page
addEventListener('keydown', e => {
  const v = $('#view');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && !v.hidden && v.contentWindow) {
    e.preventDefault(); v.contentWindow.focus(); v.contentWindow.postMessage({ type: 'find' }, '*');
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
