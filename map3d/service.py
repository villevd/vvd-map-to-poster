"""
City model builds and event placement behind the kiosk API.

A model lives in ``<store>/<id>/`` as ``meta.json`` plus ``model.glb``. The id is a slug of the
city plus a hash of the build parameters, so the same request always lands in the same folder.
Builds run one at a time on a worker thread (they download OpenStreetMap and terrain data), and
a model is only served once its GLB is complete. Event placement needs nothing but the metadata:
a local metric frame, the terrain grid and the vertical exaggeration.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .geo import LocalFrame

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")


class BusyError(RuntimeError):
    """The build queue is full."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")[:40] or "city"


def model_id(city: str, country: str | None, lat: float | None, lon: float | None,
             distance: float, exaggeration: float, theme: str, building_exaggeration: float | None = None) -> str:
    if lat is not None and lon is not None:
        where = f"{lat:.5f},{lon:.5f}"
    else:
        where = f"{city.strip().lower()}|{(country or '').strip().lower()}"
    # The building setting only joins the id when it differs from the terrain one, so ids of plain models stay the same
    same = building_exaggeration is None or building_exaggeration == exaggeration
    bx = "" if same else f"|b{building_exaggeration:.2f}"
    raw = f"{where}|{distance:.0f}|{exaggeration:.2f}{bx}|{theme}"
    return f"{slugify(city)}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"


class ModelStore:
    """Model folders on disk. Metadata is written atomically, so a reader never sees half a file."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        for meta in self.all():
            if meta.get("status") in ("queued", "building"):
                meta.update(status="failed", error="interrupted: the server stopped before the build finished")
                self.save(meta)

    def meta(self, mid: str) -> dict | None:
        if not MODEL_ID_RE.fullmatch(mid or ""):
            return None
        with self._lock:                       # readers and the writer never overlap in this process
            try:
                return json.loads((self.root / mid / "meta.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None

    def save(self, meta: dict) -> None:
        with self._lock:
            folder = self.root / meta["id"]
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / "meta.json.tmp"
            tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(10):          # Windows can refuse the replace while another program has the file open
                try:
                    os.replace(tmp, folder / "meta.json")
                    return
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)

    def glb_path(self, mid: str) -> Path:
        return self.root / mid / "model.glb"

    def all(self) -> list[dict]:
        found = []
        for folder in sorted(self.root.iterdir()) if self.root.exists() else []:
            if folder.is_dir():
                meta = self.meta(folder.name)
                if meta:
                    found.append(meta)
        return found


def build_city_model(request: dict, out_path: Path) -> dict:
    """Fetch the data, build the 3D scene and write its GLB. Needs the network and the repo's themes."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import create_map_poster as cmp            # heavy (matplotlib, OSM stack): only when a build runs
    from .export import export_glb
    from .scene import credits_text

    lat, lon = request["lat"], request["lon"]
    if lat is None or lon is None:
        lat, lon = cmp.get_coordinates(request["city"], request["country"])
    # The geometry does not depend on the theme (colours are applied on export), so it is cached without it:
    # asking for the same city in another theme only re-exports the GLB instead of rebuilding the scene.
    key = model_id(request["city"], request["country"], lat, lon, request["distance"], request["exaggeration"],
                   "", request.get("building_exaggeration"))
    cache = Path(out_path).parent.parent / "_scenes" / f"{key}.pkl"
    scene = None
    try:
        with open(cache, "rb") as f:
            scene = pickle.load(f)
    except Exception:
        scene = None
    if scene is None:
        scene = cmp.build_scene_3d(request["city"], (lat, lon), request["distance"],
                                   exaggeration=request["exaggeration"],
                                   building_exaggeration=request.get("building_exaggeration"))
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump(scene, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cache)
        except OSError:
            pass
    theme = cmp.load_theme(request["theme"])
    export_glb(scene, theme, out_path, credits=credits_text(scene))
    info = scene.info
    return {
        "center": {"lat": float(lat), "lon": float(lon)},
        "half_m": float(scene.half),
        "exaggeration": float(info.get("exaggeration", request["exaggeration"])),
        "building_exaggeration": float(info.get("building_exaggeration", request["exaggeration"])),
        "dem_min_m": info.get("dem_min_m"),
        "base_m": info.get("base_m"),
        "elevation_grid": info.get("elevation_grid"),
        "z_range_m": [float(z) for z in scene.z_range],
        "credits": credits_text(scene),
        "sources": info.get("sources"),
        "stats": {"triangles": int(scene.n_faces), "trees": info.get("trees"),
                  "buildings": (info.get("buildings") or {}).get("count")},
    }


class JobRunner:
    """Builds queued models one at a time on a daemon thread."""

    def __init__(self, store: ModelStore, builder=build_city_model, max_pending: int = 4, log=print):
        self.store = store
        self.builder = builder
        self.log = log
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_pending)
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._worker, name="city-model-builds", daemon=True).start()

    def submit(self, request: dict) -> tuple[dict, bool]:
        """Queue a build unless the model is ready or already queued. Returns (meta, queued_now)."""
        mid = request["id"]
        with self._lock:
            existing = self.store.meta(mid)
            if existing and existing.get("status") == "ready":
                return existing, False
            if mid in self._pending and existing:
                return existing, False
            meta = {"id": mid, "status": "queued", "request": request, "created": utc_now(), "error": None}
            try:
                self._queue.put_nowait(mid)
            except queue.Full as e:
                raise BusyError("the model build queue is full; try again later") from e
            self._pending.add(mid)
            self.store.save(meta)
            return meta, True

    def idle(self) -> bool:
        with self._lock:
            return not self._pending

    def _worker(self) -> None:
        while True:
            mid = self._queue.get()
            try:
                self._build(mid)
            except Exception:
                self.log(f"model {mid}: internal error\n{traceback.format_exc()}")
            finally:
                with self._lock:
                    self._pending.discard(mid)
                self._queue.task_done()

    def _build(self, mid: str) -> None:
        meta = self.store.meta(mid)
        if meta is None:
            return
        meta.update(status="building", started=utc_now(), error=None)
        self.store.save(meta)
        t0 = time.time()
        self.log(f"building 3D model {mid} ...")
        try:
            summary = self.builder(meta["request"], self.store.glb_path(mid))
            meta.update(summary)
            meta.update(status="ready", glb_bytes=self.store.glb_path(mid).stat().st_size,
                        finished=utc_now(), seconds=round(time.time() - t0, 1))
            self.log(f"model {mid} ready in {meta['seconds']} s")
        except Exception as e:
            meta.update(status="failed", error=f"{type(e).__name__}: {e}", finished=utc_now())
            self.log(f"model {mid} failed: {e}")
        self.store.save(meta)


def public_model(meta: dict) -> dict:
    """What the API tells a client about a model (the elevation grid stays server-side)."""
    mid = meta["id"]
    req = meta.get("request", {})
    out = {
        "id": mid,
        "status": meta.get("status"),
        "error": meta.get("error"),
        "request": {k: req.get(k) for k in ("city", "country", "lat", "lon", "distance", "exaggeration",
                                            "building_exaggeration", "theme")},
        "created": meta.get("created"),
        "started": meta.get("started"),
        "finished": meta.get("finished"),
        "status_url": f"/api/v1/models/{mid}",
    }
    if meta.get("status") == "ready":
        out.update(
            glb_url=f"/api/v1/models/{mid}/model.glb",
            glb_bytes=meta.get("glb_bytes"),
            center=meta.get("center"),
            half_m=meta.get("half_m"),
            exaggeration=meta.get("exaggeration"),
            building_exaggeration=meta.get("building_exaggeration"),
            z_range_m=meta.get("z_range_m"),
            credits=meta.get("credits"),
            stats=meta.get("stats"),
            seconds=meta.get("seconds"),
        )
    return out


# ---------------------------------------------------------------------------------------------------------
# Event placement
# ---------------------------------------------------------------------------------------------------------

def _bilinear(grid: dict, east: float, north: float) -> float:
    n, half, z = grid["n"], grid["half_m"], grid["values_m"]
    fx = (min(max(east, -half), half) + half) / (2.0 * half) * (n - 1)
    fy = (min(max(north, -half), half) + half) / (2.0 * half) * (n - 1)
    i0 = min(int(math.floor(fy)), n - 2)
    j0 = min(int(math.floor(fx)), n - 2)
    ty, tx = fy - i0, fx - j0
    a, b = z[i0][j0], z[i0][j0 + 1]
    c, d = z[i0 + 1][j0], z[i0 + 1][j0 + 1]
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def place_event(meta: dict, event: dict) -> dict:
    """Where a lightning strike or an aircraft sits in a ready model.

    Model frame (glTF, what the GLB uses): X east, Y up, Z = -north, metres. ``gltf_position_m``
    is the event's position in that frame. The ground height at the event comes from the terrain
    grid. The terrain is drawn with the terrain exaggeration; heights above ground are scaled by the
    building exaggeration, the same as the buildings, which keeps aircraft level with them.
    """
    center = meta["center"]
    half = float(meta["half_m"])
    exag = float(meta["exaggeration"])
    bexag = float(meta.get("building_exaggeration") or exag)
    dem_min = float(meta["dem_min_m"])
    base = float(meta["base_m"])
    grid = meta["elevation_grid"]

    frame = LocalFrame(center["lat"], center["lon"], half)
    east, north = frame.to_xy(event["lon"], event["lat"])
    east, north = float(east), float(north)
    ground = float(_bilinear(grid, east, north))
    ground_y = (ground - dem_min) * exag + base

    out = {
        "inside_model": abs(east) <= half and abs(north) <= half,
        "east_m": round(east, 1),
        "north_m": round(north, 1),
        "ground_msl_m": round(ground, 1),
        "vertical_exaggeration": exag,
        "building_exaggeration": bexag,
    }
    up = ground_y
    if event["type"] == "aircraft":
        alt = event.get("altitude_m")
        if event.get("on_ground") or alt is None:
            agl = 0.0
        else:
            agl = max(0.0, alt - ground)
        up = ground_y + agl * bexag
        out["height_above_ground_m"] = round(agl, 1)
        track = event.get("track_deg")
        out["gltf_rotation_y_deg"] = round((90.0 - track) % 360.0, 1) if track is not None else None
    out["gltf_position_m"] = [round(east, 2), round(up, 2), round(-north, 2)]
    return out
