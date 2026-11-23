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


WEB_DIR = Path(__file__).with_name("web") / "gui"


def _assemble_index() -> str:
    """
    The GUI page: the HTML shell and stylesheet from ``web/gui``, and the script split into
    ``js/NN_name.js`` parts that are joined in name order (one classic script, one shared scope).
    """
    shell = (WEB_DIR / "shell.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    js = "".join((WEB_DIR / "js" / name).read_text(encoding="utf-8") for name in sorted(p.name for p in (WEB_DIR / "js").glob("*.js")))
    return shell.replace("__GUI_CSS__", css).replace("__GUI_JS__", js)


INDEX_HTML = _assemble_index()


if __name__ == "__main__":
    sys.exit(main())
