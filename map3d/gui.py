"""
Local GUI for the 3D city maps.

    python -m map3d.gui            # opens http://127.0.0.1:8777 in your browser

Hot reload is on by default: a supervisor restarts the server whenever a .py, .js, .css or .html
file under map3d/ changes, and the open page reloads itself. Use --no-reload to turn it off.

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
import mmap
import os
import re
import shutil
import socket
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

from . import bakedmap, chunks

ROOT = Path(__file__).resolve().parent.parent
POSTERS = ROOT / "posters"
THEMES = ROOT / "themes"
DEFAULT_PORT = 8777
PKG_DIR = Path(__file__).resolve().parent
HOT_RELOAD_ENV = "MAP3D_HOT_RELOAD"          # set for the server process the supervisor starts
WATCH_SUFFIXES = (".py", ".js", ".css", ".html")
PROCESS_ID = uuid.uuid4().hex                 # changes every time the server process starts
# Injected into the GUI page in hot reload mode: reload when the server has restarted (its id changed)
RELOAD_JS = ("<script>(function(){var id=null;async function poll(){try{var r=await fetch('/api/dev-stamp',{cache:'no-store'});"
             "if(!r.ok)throw 0;var j=await r.json();if(id===null)id=j.id;else if(j.id!==id){location.reload();return;}}"
             "catch(e){}setTimeout(poll,id===null?400:700);}poll();})();</script>")
SERVED_SUFFIXES = {".db": "application/octet-stream", ".html": "text/html; charset=utf-8", ".png": "image/png",
                   ".glb": "model/gltf-binary", ".stl": "model/stl"}
OUTPUT_CHOICES = ("db", "html", "poster", "glb", "stl")
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


def send_model_page(handler, f: Path) -> bool:
    """
    Serves a saved 3D model page with the viewer code as it is now, so pages built by an older version (WASD speed,
    tree height...) get the current viewer. The mesh data is streamed from the file, not loaded into memory.
    False when the page cannot be rebuilt; the caller then serves the file as it was saved.
    """
    from . import viewer
    with f.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        try:
            parts = viewer.page_parts(mm)
        except Exception:
            parts = None
        if parts is None:
            return False
        head, start, end, tail = parts
        handler.send_response(200)
        handler.send_header("Content-Type", SERVED_SUFFIXES[".html"])
        handler.send_header("Content-Length", str(len(head) + (end - start) + len(tail)))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(head)
        for off in range(start, end, 8 << 20):
            handler.wfile.write(mm[off:min(off + (8 << 20), end)])
        handler.wfile.write(tail)
    return True


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
    outputs = [o for o in params.get("outputs", ["db"]) if o in OUTPUT_CHOICES]
    if "db" not in outputs:
        outputs.insert(0, "db")             # the baked map is what the GUI opens

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
        has_view = any(f.endswith((".db", ".html")) for f in self.files)
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


def posters_stamp() -> dict[str, list]:
    """Every exported file in posters/ with its modification time (ms) and size, for the page's hot reload."""
    out = {}
    for f in POSTERS.iterdir():
        if f.suffix.lower() in SERVED_SUFFIXES and f.is_file():
            try:
                st = f.stat()
                out[f.name] = [int(st.st_mtime * 1000), st.st_size]
            except OSError:
                pass
    return out


def list_models() -> list[dict]:
    theme_ids = [t["id"] for t in available_themes()]
    models = []
    maps = list(POSTERS.glob("*_3d_*.db"))
    # standalone pages from before baked maps: listed too, unless a baked map of the same model already exists
    maps += [f for f in POSTERS.glob("*_3d_*.html") if not (POSTERS / f"{f.stem}.db").is_file()]
    for f in maps:
        parts = split_model_name(f.stem, theme_ids)
        if parts is None:
            continue
        city, theme, stamp = parts
        if f.suffix == ".db":
            try:
                siblings = bakedmap.list_assets(f)
            except Exception:           # a half-written or damaged map is skipped, not fatal for the list
                continue
        else:
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
            page = INDEX_HTML
            if os.environ.get(HOT_RELOAD_ENV):
                page = page.replace("</body>", RELOAD_JS + "</body>", 1)
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/api/dev-stamp" and os.environ.get(HOT_RELOAD_ENV):
            return self._json({"id": PROCESS_ID})
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
        if url.path == "/api/posters-stamp":
            return self._json({"files": posters_stamp()})
        if url.path.startswith("/maps/"):
            return self._map(url.path[len("/maps/"):])
        if url.path.startswith("/files/"):
            f = safe_file(unquote(url.path[len("/files/"):]))
            if f is None:
                return self._error(404, "Not found")
            ctype = SERVED_SUFFIXES[f.suffix.lower()]
            if f.suffix.lower() == ".db":
                return self._send_file(f, ctype, {"Content-Disposition": f'attachment; filename="{f.name}"'})
            extra = {} if f.suffix.lower() == ".html" else {"Content-Disposition": f'attachment; filename="{f.name}"'}
            if f.suffix.lower() == ".png":
                extra = {}
            if f.suffix.lower() == ".html" and send_model_page(self, f):
                return
            return self._send(200, f.read_bytes(), ctype, extra)
        return self._error(404, "Not found")

    def _send_file(self, path: Path, ctype: str, extra: dict | None = None) -> None:
        """A large file (a baked map), streamed rather than read into memory."""
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        with open(path, "rb") as src:
            shutil.copyfileobj(src, self.wfile, 1 << 20)

    def _map(self, rest: str) -> None:
        """A baked map: its viewer page (/maps/<name>), one mesh tile (/maps/<name>/blob/<id>) or an export
        (/maps/<name>/asset/<ext>). The map file is read in pieces, so nothing is loaded whole."""
        parts = rest.split("/")
        f = safe_file(unquote(parts[0]))
        if f is None or f.suffix.lower() != ".db":
            return self._error(404, "Not a baked map")
        from . import viewer                 # heavy (the viewer template): only when a map is opened
        try:
            if len(parts) == 1:
                return self._send(200, viewer.db_page(f, f.name).encode("utf-8"), "text/html; charset=utf-8")
            if len(parts) == 3 and parts[1] == "blob":
                data = bakedmap.read_blob(f, unquote(parts[2]))
                return self._error(404, "No such tile") if data is None else self._send(200, data, "application/octet-stream", {"Cache-Control": "no-store"})
            if len(parts) == 3 and parts[1] == "asset":
                found = bakedmap.read_asset(f, unquote(parts[2]))
                if found is None:
                    return self._error(404, "No such export")
                name, data = found
                ext = Path(name).suffix.lower()
                disp = "inline" if ext == ".png" else f'attachment; filename="{name}"'
                return self._send(200, data, bakedmap.ASSET_MIME.get(ext.lstrip("."), "application/octet-stream"),
                                  {"Content-Disposition": disp})
        except ValueError as e:              # not a baked map after all
            return self._error(500, str(e))
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


def _snapshot() -> dict[str, int]:
    """Modification time of every source file the server runs from (map3d/**.py, web assets)."""
    files = {}
    for p in PKG_DIR.rglob("*"):
        if p.suffix in WATCH_SUFFIXES and "__pycache__" not in p.parts:
            try:
                files[str(p)] = p.stat().st_mtime_ns
            except OSError:
                pass
    return files


def _free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise OSError(f"No free port near {start}")


def _supervise(args) -> int:
    """
    Hot reload: run the server in a child process and restart it whenever a source file changes.
    The page notices the restart (new process id) and reloads itself. A crash waits for the next save.
    """
    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    env = dict(os.environ, **{HOT_RELOAD_ENV: "1"})
    opened = False
    child = None
    try:
        while True:
            print(f"[reload] starting the GUI at {url}", flush=True)
            child = subprocess.Popen([sys.executable, "-m", "map3d.gui", "--no-browser", "--port", str(port)],
                                     cwd=str(PKG_DIR.parent), env=env)
            if not opened and not args.no_browser:
                threading.Timer(1.5, lambda: webbrowser.open(url)).start()
                opened = True
            seen = _snapshot()
            reported = False
            while True:
                time.sleep(0.5)
                now = _snapshot()
                if now != seen:
                    names = sorted(k for k in set(now) | set(seen) if now.get(k) != seen.get(k))
                    shown = ", ".join(Path(n).name for n in names[:4]) + (" …" if len(names) > 4 else "")
                    print(f"[reload] changed: {shown} — restarting", flush=True)
                    break
                if not reported and child.poll() is not None:
                    reported = True
                    print("[reload] the GUI stopped (see the error above). Save a file to start it again.", flush=True)
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
    except KeyboardInterrupt:
        print("\nStopped.")
        if child is not None and child.poll() is None:
            child.terminate()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Chunks are built inside this process and print status marks (✓, ⚠); on Windows the console
    # code page can't encode them, which made every chunk fail. Write UTF-8 and never crash on a mark.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Local GUI for 3D city maps")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    ap.add_argument("--no-reload", action="store_true", help="Do not restart the server when the source files change")
    args = ap.parse_args(argv)
    if not args.no_reload and not os.environ.get(HOT_RELOAD_ENV):
        return _supervise(args)
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
