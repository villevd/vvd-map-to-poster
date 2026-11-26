"""
HTTP API for the kiosk (vvd-smart-dashboard): city models, the aircraft library and event placement.

    python -m map3d.api --host 0.0.0.0 --port 8765 --city "Val-d'Or" --country Canada

Standard library only (http.server). The routes, payloads and coordinate frames are described in
docs/kiosk-api.md. There is no authentication: run it on the local network, not on the internet.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import service
from .aircraft import AXES, FORMAT, Library, LibraryError

API_VERSION = "1"
DEFAULT_PORT = 8765
MAX_BODY = 64 * 1024
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CITY = {"city": "Val-d'Or", "country": "Canada", "distance": 1500, "exaggeration": 1.5,
                "building_exaggeration": None, "theme": "terracotta",     # None: buildings follow the terrain
                "lat": 48.1020132, "lon": -77.7893768}                    # the model is centred on the Hôtel de Ville
FINAL_APPROACH_AGL_M = 610.0          # 2 000 ft above ground


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


# ---------------------------------------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------------------------------------

def _number(body: dict, key: str, lo: float, hi: float, default=None, required=False) -> float | None:
    if key not in body or body[key] is None:
        if required:
            raise ApiError(400, f"'{key}' is required")
        return default
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ApiError(400, f"'{key}' must be a number")
    if not lo <= value <= hi:
        raise ApiError(400, f"'{key}' must be between {lo:g} and {hi:g}")
    return float(value)


def _text(body: dict, key: str, max_len: int, required=False) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise ApiError(400, f"'{key}' is required")
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise ApiError(400, f"'{key}' must be text of at most {max_len} characters")
    return value.strip()


def _flag(body: dict, key: str) -> bool:
    value = body.get(key, False)
    if not isinstance(value, bool):
        raise ApiError(400, f"'{key}' must be true or false")
    return value


def normalize_model_request(body: dict, themes_dir: Path) -> dict:
    city = _text(body, "city", 80, required=True)
    country = _text(body, "country", 80)
    lat = _number(body, "lat", -90, 90)
    lon = _number(body, "lon", -180, 180)
    if (lat is None) != (lon is None):
        raise ApiError(400, "'lat' and 'lon' must be given together")
    distance = _number(body, "distance", 300, 4000, default=DEFAULT_CITY["distance"])
    exaggeration = _number(body, "exaggeration", 0.1, 8, default=DEFAULT_CITY["exaggeration"])
    building_exaggeration = _number(body, "building_exaggeration", 0.1, 8,
                                    default=DEFAULT_CITY["building_exaggeration"])
    theme = _text(body, "theme", 40) or DEFAULT_CITY["theme"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", theme) or not (themes_dir / f"{theme}.json").exists():
        raise ApiError(400, f"unknown theme '{theme}'")
    mid = service.model_id(city, country, lat, lon, distance, exaggeration, theme, building_exaggeration)
    return {"id": mid, "city": city, "country": country, "lat": lat, "lon": lon,
            "distance": distance, "exaggeration": exaggeration,
            "building_exaggeration": building_exaggeration, "theme": theme}


def normalize_event(body: dict) -> dict:
    kind = body.get("type")
    if kind not in ("lightning", "aircraft"):
        raise ApiError(400, "'type' must be 'lightning' or 'aircraft'")
    event = {
        "type": kind,
        "lat": _number(body, "lat", -90, 90, required=True),
        "lon": _number(body, "lon", -180, 180, required=True),
        "time": _text(body, "time", 40),
    }
    if kind == "aircraft":
        alt_ft = _number(body, "altitude_ft", -1000, 60000)
        event.update(
            altitude_m=None if alt_ft is None else alt_ft * 0.3048,
            on_ground=_flag(body, "on_ground"),
            track_deg=None if (t := _number(body, "track_deg", -720, 720)) is None else t % 360.0,
            type_code=(_text(body, "type_code", 8) or "").upper() or None,
            helicopter=_flag(body, "is_helicopter"),
            callsign=_text(body, "callsign", 16),
            registration=_text(body, "registration", 16),
        )
    return event


def _phase(event: dict, placement: dict | None) -> str:
    if event.get("on_ground"):
        return "on_ground"
    agl = None if placement is None else placement.get("height_above_ground_m")
    if agl is not None and agl <= FINAL_APPROACH_AGL_M:
        return "approach"
    return "airborne"


# ---------------------------------------------------------------------------------------------------------
# Application state and responses
# ---------------------------------------------------------------------------------------------------------

class KioskApi:
    def __init__(self, library: Library, store: service.ModelStore, runner: service.JobRunner | None,
                 default: dict | None, themes_dir: Path, cors_origin: str = "*"):
        self.library = library
        self.store = store
        self.runner = runner
        self.themes_dir = themes_dir
        self.cors_origin = cors_origin
        self.default_request = normalize_model_request(default, themes_dir) if default else None

    @property
    def default_id(self) -> str | None:
        return self.default_request["id"] if self.default_request else None

    def ensure_default(self) -> None:
        """Start building the default city model if it has never been built."""
        if self.runner and self.default_request and self.store.meta(self.default_id) is None:
            self.runner.submit(self.default_request)

    def submit(self, request: dict):
        if self.runner is None:
            raise ApiError(405, "this server does not build models")
        try:
            return self.runner.submit(request)
        except service.BusyError as e:
            raise ApiError(503, str(e), retry_after=60) from e


def _json(status: int, obj, headers: dict | None = None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", **(headers or {})}, body


def _aircraft_view(api: KioskApi, entry: dict) -> dict:
    return {**entry, "glb_url": f"/api/v1/aircraft/{entry['id']}.glb"}


def r_health(api: KioskApi, m, q, body):
    counts = Counter(meta.get("status") for meta in api.store.all())
    return _json(200, {
        "ok": True, "service": "maptoposter kiosk api", "api_version": API_VERSION,
        "aircraft": {"count": len(api.library.entries), "generated": api.library.generated},
        "models": dict(counts), "default_model": api.default_id,
        "builds_idle": api.runner.idle() if api.runner else None,
    })


def r_aircraft_index(api: KioskApi, m, q, body):
    entries = [_aircraft_view(api, e) for e in api.library.entries]
    return _json(200, {"format": FORMAT, "generated": api.library.generated, "axes": AXES,
                       "count": len(entries), "entries": entries})


def r_aircraft_match(api: KioskApi, m, q, body):
    code = (q.get("type") or "").strip().upper() or None
    helicopter = (q.get("helicopter") or "").lower() in ("1", "true", "yes")
    entry, match = api.library.resolve(code, helicopter)
    return _json(200, {"requested": {"type": code, "helicopter": helicopter}, "match": match,
                       "entry": _aircraft_view(api, entry)})


def r_aircraft_glb(api: KioskApi, m, q, body):
    entry = api.library.get(m.group("id"))
    path = api.library.path(entry) if entry else None
    if entry is None or path is None or not path.exists():
        raise ApiError(404, f"no aircraft model '{m.group('id')}'")
    etag = f'"{entry["sha256"]}"'
    headers = {"Content-Type": "model/gltf-binary", "ETag": etag, "Cache-Control": "public, max-age=86400"}
    if q.get("__if_none_match") == etag:
        return 304, headers, b""
    return 200, headers, path.read_bytes()


def r_models_index(api: KioskApi, m, q, body):
    models = [service.public_model(meta) for meta in api.store.all()]
    models.sort(key=lambda x: x.get("created") or "", reverse=True)
    return _json(200, {"count": len(models), "models": models})


def r_models_create(api: KioskApi, m, q, body):
    request = normalize_model_request(body, api.themes_dir)
    meta, queued = api.submit(request)
    status = 200 if meta.get("status") == "ready" else 202
    headers = {"Location": service.public_model(meta)["status_url"]}
    return _json(status, service.public_model(meta), headers)


def r_model_default(api: KioskApi, m, q, body):
    if api.default_request is None:
        raise ApiError(404, "no default model is configured")
    meta = api.store.meta(api.default_id)
    if meta is None:
        meta, _ = api.submit(api.default_request)
    status = 200 if meta.get("status") == "ready" else 202
    return _json(status, service.public_model(meta))


def r_model_get(api: KioskApi, m, q, body):
    meta = api.store.meta(m.group("id"))
    if meta is None:
        raise ApiError(404, f"no model '{m.group('id')}'")
    return _json(200, service.public_model(meta))


def r_model_glb(api: KioskApi, m, q, body):
    meta = api.store.meta(m.group("id"))
    if meta is None:
        raise ApiError(404, f"no model '{m.group('id')}'")
    if meta.get("status") != "ready":
        raise ApiError(409, f"model is {meta.get('status')}, not ready", status_url=f"/api/v1/models/{m.group('id')}")
    path = api.store.glb_path(m.group("id"))
    if not path.exists():
        raise ApiError(404, "model file is missing; request the model again")
    return 200, {"Content-Type": "model/gltf-binary", "Cache-Control": "public, max-age=3600"}, path.read_bytes()


def r_events(api: KioskApi, m, q, body):
    event = normalize_event(body)
    requested = body.get("model")
    model_id = requested if requested else api.default_id
    if model_id is None:
        raise ApiError(400, "no model given and no default model configured")
    if not service.MODEL_ID_RE.fullmatch(str(model_id)):
        raise ApiError(400, "'model' is not a valid model id")
    meta = api.store.meta(model_id)
    if meta is None and model_id == api.default_id:
        meta, _ = api.submit(api.default_request)
    if meta is None:
        raise ApiError(404, f"no model '{model_id}'")
    placement = service.place_event(meta, event) if meta.get("status") == "ready" else None
    response = {
        "event": event,
        "model": service.public_model(meta),
        "placement": placement,
    }
    if event["type"] == "aircraft":
        event["phase"] = _phase(event, placement)
        entry, match = api.library.resolve(event["type_code"], event["helicopter"])
        response["aircraft"] = {
            "match": match,
            "id": entry["id"],
            "name": entry["name"],
            "manufacturer": entry["manufacturer"],
            "category": entry["category"],
            "generic": entry["generic"],
            "dimensions_m": entry["dimensions_m"],
            "glb_url": f"/api/v1/aircraft/{entry['id']}.glb",
        }
    return _json(200, response)


ROUTES = [
    ("GET", re.compile(r"/api/v1/health"), r_health),
    ("GET", re.compile(r"/api/v1/aircraft"), r_aircraft_index),
    ("GET", re.compile(r"/api/v1/aircraft/match"), r_aircraft_match),
    ("GET", re.compile(r"/api/v1/aircraft/(?P<id>[a-z0-9-]+)\.glb"), r_aircraft_glb),
    ("GET", re.compile(r"/api/v1/models"), r_models_index),
    ("POST", re.compile(r"/api/v1/models"), r_models_create),
    ("GET", re.compile(r"/api/v1/models/default"), r_model_default),
    ("GET", re.compile(r"/api/v1/models/(?P<id>[a-z0-9][a-z0-9-]{0,80})"), r_model_get),
    ("GET", re.compile(r"/api/v1/models/(?P<id>[a-z0-9][a-z0-9-]{0,80})/model\.glb"), r_model_glb),
    ("POST", re.compile(r"/api/v1/events"), r_events),
]


class _Handler(BaseHTTPRequestHandler):
    server_version = f"maptoposter-kiosk-api/{API_VERSION}"
    sys_version = ""

    def log_message(self, fmt, *args):
        if not getattr(self.server, "quiet", False):
            sys.stderr.write(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}\n")

    def do_OPTIONS(self):
        self._respond(204, {}, b"")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._respond(*self._error_tuple(ApiError(405, "method not allowed")))

    do_DELETE = do_PATCH = do_PUT

    def _dispatch(self, method: str) -> None:
        api: KioskApi = self.server.api
        try:
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            if len(path) > 1:
                path = path.rstrip("/")
            query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            query["__if_none_match"] = self.headers.get("If-None-Match", "")
            allowed = []
            for route_method, pattern, fn in ROUTES:
                match = pattern.fullmatch(path)
                if match is None:
                    continue
                if route_method != method:
                    allowed.append(route_method)
                    continue
                body = self._read_body() if method == "POST" else {}
                status, headers, payload = fn(api, match, query, body)
                self._respond(status, headers, payload)
                return
            if allowed:
                raise ApiError(405, f"use {' or '.join(sorted(set(allowed)))} for this path")
            raise ApiError(404, f"no route for {path}")
        except ApiError as e:
            self._respond(*self._error_tuple(e))
        except LibraryError as e:
            self._respond(*self._error_tuple(ApiError(500, f"aircraft library: {e}")))
        except Exception:
            sys.stderr.write(traceback.format_exc())
            self._respond(*self._error_tuple(ApiError(500, "internal error")))

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as e:
            raise ApiError(400, "bad Content-Length") from e
        if length > MAX_BODY:
            raise ApiError(413, "request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            raise ApiError(400, "a JSON object body is required")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ApiError(400, f"body is not valid JSON: {e}") from e
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        return body

    def _error_tuple(self, e: ApiError):
        payload = {"error": {"status": e.status, "message": e.message, **e.extra}}
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if "retry_after" in e.extra:
            headers["Retry-After"] = str(e.extra["retry_after"])
        return e.status, headers, json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _respond(self, status: int, headers: dict, payload: bytes) -> None:
        api: KioskApi = self.server.api
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", api.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, If-None-Match")
        self.send_header("Access-Control-Expose-Headers", "ETag, Location, Retry-After")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers.items():
            self.send_header(key, value)
        if status == 304:
            payload = b""
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload and self.command != "HEAD":
            self.wfile.write(payload)


class KioskServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, api: KioskApi, quiet: bool = False):
        super().__init__(address, _Handler)
        self.api = api
        self.quiet = quiet


def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, planes_dir: str | Path = "planes",
                cache_dir: str | Path = "cache/3d-api", default: dict | None = DEFAULT_CITY,
                builder=service.build_city_model, cors_origin: str = "*", themes_dir: str | Path | None = None,
                quiet: bool = False) -> KioskServer:
    """Build the server. ``builder`` is injected so tests can run without the network."""
    themes = Path(themes_dir) if themes_dir else REPO_ROOT / "themes"
    library = Library(planes_dir)
    store = service.ModelStore(cache_dir)
    log = (lambda *_: None) if quiet else print
    runner = service.JobRunner(store, builder=builder, log=log) if builder is not None else None
    api = KioskApi(library, store, runner, default, themes, cors_origin)
    api.ensure_default()
    return KioskServer((host, port), api, quiet=quiet)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Kiosk API: city 3D models, aircraft library, event placement.")
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (0.0.0.0 for the local network)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--planes", default="planes", help="aircraft library folder (built with map3d.aircraft)")
    ap.add_argument("--cache", default="cache/3d-api", help="folder for built city models")
    ap.add_argument("--city", default=DEFAULT_CITY["city"], help="default city for the kiosk")
    ap.add_argument("--country", default=DEFAULT_CITY["country"])
    ap.add_argument("--lat", type=float, default=DEFAULT_CITY["lat"], help="model centre latitude (default: Hôtel de Ville)")
    ap.add_argument("--lon", type=float, default=DEFAULT_CITY["lon"], help="model centre longitude (default: Hôtel de Ville)")
    ap.add_argument("--distance", type=float, default=DEFAULT_CITY["distance"], help="model half-size in metres")
    ap.add_argument("--exaggeration", type=float, default=DEFAULT_CITY["exaggeration"], help="terrain exaggeration")
    ap.add_argument("--building-exaggeration", type=float, default=DEFAULT_CITY["building_exaggeration"],
                    help="building and tree exaggeration (default: same as --exaggeration)")
    ap.add_argument("--theme", default=DEFAULT_CITY["theme"])
    ap.add_argument("--no-default-model", action="store_true", help="do not build the default city model")
    ap.add_argument("--cors-origin", default="*", help="Access-Control-Allow-Origin (the kiosk's origin)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):      # build messages contain symbols a redirected console may not encode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    planes = Path(args.planes).resolve()
    cache = Path(args.cache).resolve()
    os.chdir(REPO_ROOT)          # create_map_poster reads themes/ and writes cache/ relative to the repo
    default = None if args.no_default_model else {
        "city": args.city, "country": args.country, "lat": args.lat, "lon": args.lon, "distance": args.distance,
        "exaggeration": args.exaggeration, "building_exaggeration": args.building_exaggeration,
        "theme": args.theme,
    }
    try:
        server = make_server(args.host, args.port, planes, cache, default=default, cors_origin=args.cors_origin,
                             quiet=args.quiet)
    except LibraryError as e:
        sys.exit(f"{e}\nBuild the library first:  python -m map3d.aircraft {args.planes}")
    host, port = server.server_address[:2]
    print(f"Kiosk API on http://{host}:{port}/api/v1/health  (aircraft: {planes}, models: {cache})")
    if server.api.default_id:
        print(f"Default city model: {server.api.default_id}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
