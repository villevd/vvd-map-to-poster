"""
Offline tests for the kiosk API: aircraft library, event placement, build queue and HTTP routes.

No network: city models are produced by a fake builder that writes a tiny GLB.

Run from the project root:  python -m unittest discover -s test -p "test_*.py" -v
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d import api as kapi  # noqa: E402
from map3d import service  # noqa: E402
from map3d.aircraft import ALL_SPECS, Library, Part, build_library, build_parts, write_glb  # noqa: E402
from map3d.geo import LocalFrame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "themes"
CENTER = (48.1, -77.78)
HALF = 1500.0


def spec_by_id(sid):
    return next(s for s in ALL_SPECS if s.id == sid)


def glb_doc(raw: bytes) -> dict:
    magic, version, length = struct.unpack("<4sII", raw[:12])
    assert (magic, version, length) == (b"glTF", 2, len(raw))
    jlen, jtype = struct.unpack("<I4s", raw[12:20])
    assert jtype == b"JSON"
    return json.loads(raw[20:20 + jlen])


def grid_for(values_fn, half=HALF, n=41):
    xs = np.linspace(-half, half, n)
    X, Y = np.meshgrid(xs, xs)
    return {"n": n, "half_m": half, "values_m": np.round(values_fn(X, Y), 2).tolist()}


def fake_summary(lat, lon, half=HALF, exag=1.5):
    return {
        "center": {"lat": lat, "lon": lon},
        "half_m": half,
        "exaggeration": exag,
        "dem_min_m": 300.0,
        "base_m": 2.0,
        "elevation_grid": grid_for(lambda X, Y: 300.0 + 0.01 * X, half),   # 10 m per km eastwards
        "z_range_m": [2.0, 30.0],
        "credits": "test",
        "sources": {},
        "stats": {"triangles": 2},
    }


def fake_builder_factory(gate=None, fail_for=None):
    calls = []

    def builder(request, out_path):
        calls.append(request["id"])
        if gate is not None:
            gate.wait(10)
        if fail_for and request["city"] == fail_for:
            raise RuntimeError("no map data (test)")
        verts = np.array([[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0], [0, 0, 3]], float)
        faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
        write_glb(out_path, [Part("ground", (0.5, 0.5, 0.5), verts, faces, False)], {"test": True})
        lat, lon = request["lat"] or CENTER[0], request["lon"] or CENTER[1]
        return fake_summary(lat, lon, half=request["distance"], exag=request["exaggeration"])

    builder.calls = calls
    return builder


class AircraftGeometry(unittest.TestCase):
    def test_extents_follow_the_published_dimensions(self):
        for sid in ("a320", "b788", "e75l", "dh8d", "c172", "b06", "a388"):
            s = spec_by_id(sid)
            v = np.vstack([p.verts for p in build_parts(s)])
            length = v[:, 0].max() - v[:, 0].min()
            span = v[:, 1].max() - v[:, 1].min()
            height = v[:, 2].max() - v[:, 2].min()
            self.assertAlmostEqual(length / s.length, 1.0, delta=0.05, msg=f"{sid} length")
            self.assertAlmostEqual(span / s.span, 1.0, delta=0.03, msg=f"{sid} span")
            self.assertAlmostEqual(height / s.height, 1.0, delta=0.03, msg=f"{sid} height")
            self.assertLess(abs(v[:, 2].min()), 0.05, msg=f"{sid} rests on the ground")
            self.assertLess(abs(v[:, 1].max() + v[:, 1].min()), 0.05, msg=f"{sid} is left/right symmetric")
            self.assertTrue(np.isfinite(v).all(), sid)

    def test_nose_points_forward_and_tail_is_aft(self):
        s = spec_by_id("a320")
        parts = {p.name: p for p in build_parts(s)}
        fus = parts["fuselage"].verts
        self.assertAlmostEqual(fus[:, 0].max(), s.length / 2, delta=0.05)
        fin = parts["tail"].verts
        self.assertLess(fin[:, 2].max(), s.height + 0.01)
        self.assertLess(fin[:, 0].mean(), 0.0)                       # the tail sits aft of the middle
        self.assertGreater(fin[:, 2].max(), s.height - 0.01)          # the fin reaches the stated height

    def test_wing_sweeps_back_and_engines_sit_under_the_wing(self):
        s = spec_by_id("b738")
        parts = {p.name: p for p in build_parts(s)}
        wing = parts["wings"].verts
        tip = wing[np.abs(wing[:, 1]) > 0.9 * s.span / 2]
        root = wing[np.abs(wing[:, 1]) < 0.5]
        self.assertLess(tip[:, 0].mean(), root[:, 0].mean() - 1.0)   # swept back
        eng = parts["engines"].verts
        self.assertLess(eng[:, 2].max(), wing[:, 2].max())            # nacelles hang below the wing top
        self.assertLess(eng[:, 0].mean(), s.length / 2)

    def test_twin_jets_have_two_engines_and_quads_have_four(self):
        from map3d.aircraft import _engine_count
        self.assertEqual(_engine_count(spec_by_id("a320")), 2)
        self.assertEqual(_engine_count(spec_by_id("a388")), 4)
        self.assertEqual(_engine_count(spec_by_id("c172")), 1)
        s = spec_by_id("a388")
        eng = next(p for p in build_parts(s) if p.name == "engines")
        self.assertLess(eng.verts[:, 1].min(), -0.6 * s.span / 2)     # outboard engines on both sides
        self.assertGreater(eng.verts[:, 1].max(), 0.6 * s.span / 2)

    def test_helicopter_rotor_spans_the_stated_diameter(self):
        s = spec_by_id("ec35")
        rotor = next(p for p in build_parts(s) if p.name == "rotors")
        self.assertAlmostEqual((rotor.verts[:, 1].max() - rotor.verts[:, 1].min()) / s.span, 1.0, delta=0.02)


class AircraftGlb(unittest.TestCase):
    def test_written_glb_is_valid_and_one_node_per_part(self):
        s = spec_by_id("dh8d")
        parts = build_parts(s)
        with tempfile.TemporaryDirectory() as d:
            path = write_glb(Path(d) / "dh8d.glb", parts, {"id": "dh8d"})
            doc = glb_doc(path.read_bytes())
        self.assertEqual(len(doc["nodes"]), len([p for p in parts if len(p.faces)]))
        self.assertEqual(doc["asset"]["extras"]["id"], "dh8d")
        self.assertTrue(all(m["doubleSided"] for m in doc["materials"]))
        for acc in doc["accessors"]:
            self.assertGreater(acc["count"], 0)

    def test_gltf_frame_puts_nose_on_plus_x_and_up_on_plus_y(self):
        s = spec_by_id("c172")
        with tempfile.TemporaryDirectory() as d:
            path = write_glb(Path(d) / "c172.glb", build_parts(s))
            doc = glb_doc(path.read_bytes())
        accs = [doc["accessors"][doc["meshes"][n["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
                for n in doc["nodes"]]
        lo = np.min([a["min"] for a in accs], axis=0)
        hi = np.max([a["max"] for a in accs], axis=0)
        self.assertAlmostEqual(hi[0], s.length / 2, delta=0.15)            # +X is forward (spinner adds ~0.1 m)
        self.assertLess(abs(lo[1]), 0.05)                                  # Y is up; the wheels touch Y = 0
        self.assertAlmostEqual(lo[2], -hi[2], delta=0.1)                   # Z is the wing axis, symmetric

    def test_all_geometry_is_finite(self):
        for s in ALL_SPECS:
            v = np.vstack([p.verts for p in build_parts(s)])
            self.assertTrue(np.isfinite(v).all(), s.id)


class LibraryFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="planes-test-")
        cls.catalog = build_library(cls.tmp, specs=ALL_SPECS, log=lambda *a: None)
        cls.lib = Library(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_catalog_lists_every_spec_with_a_matching_file(self):
        import hashlib
        self.assertEqual(len(self.catalog["entries"]), len(ALL_SPECS))
        for e in self.catalog["entries"]:
            data = (Path(self.tmp) / e["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"], e["id"])
            self.assertEqual(len(data), e["bytes"], e["id"])

    def test_icao_codes_resolve_exactly_and_are_case_insensitive(self):
        entry, match = self.lib.resolve("B738")
        self.assertEqual((entry["id"], match), ("b738", "exact"))
        entry, match = self.lib.resolve(" b737 ")
        self.assertEqual((entry["id"], match), ("b738", "exact"))
        entry, match = self.lib.resolve("DH8D")
        self.assertEqual(entry["manufacturer"], "De Havilland Canada")

    def test_unknown_types_fall_back_to_a_generic_stand_in(self):
        entry, match = self.lib.resolve("ZZZZ")
        self.assertEqual((entry["id"], match, entry["generic"]), ("generic-jet", "category", True))
        entry, match = self.lib.resolve(None, helicopter=True)
        self.assertEqual((entry["id"], match), ("generic-heli", "category"))
        entry, _ = self.lib.resolve("ZZZZ", helicopter=True)
        self.assertEqual(entry["id"], "generic-heli")

    def test_a_folder_without_catalog_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Exception):
                Library(d)

    def test_the_shipped_library_matches_the_current_specs(self):
        shipped = ROOT / "planes"
        if not (shipped / "catalog.json").exists():
            self.skipTest("planes/ not built yet (python -m map3d.aircraft planes)")
        lib = Library(shipped)
        self.assertEqual({e["id"] for e in lib.entries}, {s.id for s in ALL_SPECS})
        self.assertEqual(lib.resolve("A320")[1], "exact")


class EventMath(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "center": {"lat": CENTER[0], "lon": CENTER[1]},
            "half_m": HALF, "exaggeration": 1.5, "dem_min_m": 300.0, "base_m": 2.0,
            "elevation_grid": grid_for(lambda X, Y: 300.0 + 0.01 * X),
        }

    def place(self, east, north, **extra):
        lon, lat = LocalFrame(CENTER[0], CENTER[1], HALF).to_lonlat(east, north)
        event = {"type": "lightning", "lat": float(lat), "lon": float(lon), **extra}
        return service.place_event(self.meta, event)

    def test_strike_position_and_terrain_height(self):
        p = self.place(1000.0, 500.0)
        self.assertAlmostEqual(p["east_m"], 1000.0, delta=0.5)
        self.assertAlmostEqual(p["north_m"], 500.0, delta=0.5)
        self.assertTrue(p["inside_model"])
        self.assertAlmostEqual(p["ground_msl_m"], 310.0, delta=0.1)
        # scene height: (310 - 300) * 1.5 + 2.0
        self.assertAlmostEqual(p["gltf_position_m"][1], 17.0, delta=0.05)
        self.assertAlmostEqual(p["gltf_position_m"][2], -500.0, delta=0.5)   # glTF Z is -north

    def test_point_outside_the_tile_is_flagged(self):
        self.assertFalse(self.place(2500.0, 0.0)["inside_model"])

    def test_aircraft_altitude_above_ground_and_heading(self):
        alt_m = 1000.0
        p = service.place_event(self.meta, {
            "type": "aircraft", "lat": CENTER[0], "lon": CENTER[1], "altitude_m": alt_m,
            "on_ground": False, "track_deg": 90.0,
        })
        self.assertAlmostEqual(p["height_above_ground_m"], alt_m - 300.0, delta=0.5)
        self.assertAlmostEqual(p["gltf_position_m"][1], 2.0 + (alt_m - 300.0) * 1.5, delta=0.5)
        self.assertAlmostEqual(p["gltf_rotation_y_deg"], 0.0)            # heading east: nose already on +X
        north = self.place_aircraft_rotation(0.0)
        self.assertAlmostEqual(north, 90.0)                              # heading north: turn 90 deg about Y

    def place_aircraft_rotation(self, track):
        p = service.place_event(self.meta, {"type": "aircraft", "lat": CENTER[0], "lon": CENTER[1],
                                            "altitude_m": 400.0, "on_ground": False, "track_deg": track})
        return p["gltf_rotation_y_deg"]

    def test_landed_aircraft_sits_on_the_ground(self):
        p = service.place_event(self.meta, {"type": "aircraft", "lat": CENTER[0], "lon": CENTER[1],
                                            "altitude_m": 320.0, "on_ground": True, "track_deg": None})
        self.assertEqual(p["height_above_ground_m"], 0.0)
        self.assertIsNone(p["gltf_rotation_y_deg"])

    def test_bilinear_is_exact_for_a_plane_grid(self):
        got = service._bilinear(self.meta["elevation_grid"], 123.4, -777.7)
        self.assertAlmostEqual(got, 300.0 + 0.01 * 123.4, places=3)


class Validation(unittest.TestCase):
    def test_event_rules(self):
        ok = kapi.normalize_event({"type": "aircraft", "lat": 48, "lon": -77, "altitude_ft": 1000,
                                   "track_deg": 370, "type_code": "b738", "is_helicopter": False})
        self.assertAlmostEqual(ok["altitude_m"], 304.8)
        self.assertAlmostEqual(ok["track_deg"], 10.0)
        self.assertEqual(ok["type_code"], "B738")
        for bad in ({"type": "tornado", "lat": 0, "lon": 0}, {"type": "lightning", "lat": 91, "lon": 0},
                    {"type": "lightning", "lat": True, "lon": 0}, {"type": "lightning", "lon": 0},
                    {"type": "aircraft", "lat": 0, "lon": 0, "on_ground": "yes"}):
            with self.assertRaises(kapi.ApiError, msg=str(bad)):
                kapi.normalize_event(bad)

    def test_model_request_rules(self):
        r = kapi.normalize_model_request({"city": "Val-d'Or", "country": "Canada"}, THEMES)
        self.assertEqual(r["distance"], 1500)
        self.assertRegex(r["id"], r"^val-d-or-[0-9a-f]{10}$")
        other = kapi.normalize_model_request({"city": "Val-d'Or", "country": "Canada", "distance": 2000}, THEMES)
        self.assertNotEqual(r["id"], other["id"])
        for bad in ({"city": ""}, {"city": "X", "lat": 48}, {"city": "X", "distance": 50},
                    {"city": "X", "theme": "../secrets"}, {"city": "X", "theme": "nope"}):
            with self.assertRaises(kapi.ApiError, msg=str(bad)):
                kapi.normalize_model_request(bad, THEMES)


class BuildQueue(unittest.TestCase):
    def wait_for(self, store, mid, statuses, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            meta = store.meta(mid)
            if meta and meta["status"] in statuses:
                return meta
            time.sleep(0.02)
        self.fail(f"{mid} did not reach {statuses}")

    def test_build_then_ready_and_no_rebuild(self):
        tmp = tempfile.mkdtemp(prefix="models-test-")
        try:
            store = service.ModelStore(tmp)
            builder = fake_builder_factory()
            runner = service.JobRunner(store, builder=builder, log=lambda *a: None)
            req = kapi.normalize_model_request({"city": "Testville"}, THEMES)
            meta, queued = runner.submit(req)
            self.assertTrue(queued)
            ready = self.wait_for(store, req["id"], ("ready", "failed"))
            self.assertEqual(ready["status"], "ready")
            self.assertTrue(store.glb_path(req["id"]).exists())
            again, queued = runner.submit(req)
            self.assertFalse(queued)
            self.assertEqual(again["status"], "ready")
            self.assertEqual(builder.calls, [req["id"]])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_failures_are_recorded_and_can_be_retried(self):
        tmp = tempfile.mkdtemp(prefix="models-test-")
        try:
            store = service.ModelStore(tmp)
            builder = fake_builder_factory(fail_for="Broken")
            runner = service.JobRunner(store, builder=builder, log=lambda *a: None)
            req = kapi.normalize_model_request({"city": "Broken"}, THEMES)
            runner.submit(req)
            failed = self.wait_for(store, req["id"], ("failed",))
            self.assertIn("no map data", failed["error"])
            _, queued = runner.submit(req)
            self.assertTrue(queued)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_interrupted_builds_are_marked_failed_on_restart(self):
        tmp = tempfile.mkdtemp(prefix="models-test-")
        try:
            store = service.ModelStore(tmp)
            store.save({"id": "ghost-0000000000", "status": "building", "request": {}})
            service.ModelStore(tmp)
            self.assertEqual(store.meta("ghost-0000000000")["status"], "failed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_model_ids_are_checked_before_touching_the_disk(self):
        tmp = tempfile.mkdtemp(prefix="models-test-")
        try:
            store = service.ModelStore(tmp)
            self.assertIsNone(store.meta("../etc"))
            self.assertIsNone(store.meta("Upper-Case"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kiosk-api-test-")
        cls.planes = Path(cls.tmp) / "planes"
        build_library(cls.planes, specs=ALL_SPECS, log=lambda *a: None)
        cls.builder = fake_builder_factory()
        cls.server = kapi.make_server(
            "127.0.0.1", 0, planes_dir=cls.planes, cache_dir=Path(cls.tmp) / "cache",
            default={"city": "Val-d'Or", "country": "Canada"}, builder=cls.builder, quiet=True,
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.default_id = cls.server.api.default_id

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None and "Content-Type" not in (headers or {}):
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get_json(self, path):
        status, _, body = self.call("GET", path)
        return status, json.loads(body)

    def wait_ready(self, mid, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            status, meta = self.get_json(f"/api/v1/models/{mid}")
            if status == 200 and meta["status"] in ("ready", "failed"):
                return meta
            time.sleep(0.02)
        self.fail("model did not finish")

    def test_health_and_cors(self):
        status, body = self.get_json("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["aircraft"]["count"], len(ALL_SPECS))
        status, headers, _ = self.call("OPTIONS", "/api/v1/events")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))

    def test_aircraft_index_match_and_glb(self):
        status, body = self.get_json("/api/v1/aircraft")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], len(ALL_SPECS))
        self.assertTrue(all(e["glb_url"].endswith(".glb") for e in body["entries"]))

        status, body = self.get_json("/api/v1/aircraft/match?type=B738")
        self.assertEqual((status, body["match"], body["entry"]["id"]), (200, "exact", "b738"))
        status, body = self.get_json("/api/v1/aircraft/match?type=ZZZZ&helicopter=1")
        self.assertEqual((body["match"], body["entry"]["id"]), ("category", "generic-heli"))

        status, headers, raw = self.call("GET", "/api/v1/aircraft/a320.glb")
        self.assertEqual(status, 200)
        self.assertEqual(raw[:4], b"glTF")
        self.assertEqual(headers["Content-Type"], "model/gltf-binary")
        etag = headers["ETag"]
        status, _, raw = self.call("GET", "/api/v1/aircraft/a320.glb", headers={"If-None-Match": etag})
        self.assertEqual((status, raw), (304, b""))
        self.assertEqual(self.call("GET", "/api/v1/aircraft/nope.glb")[0], 404)

    def test_default_model_builds_at_startup_and_serves_its_glb(self):
        meta = self.wait_ready(self.default_id)
        self.assertEqual(meta["status"], "ready")
        status, headers, raw = self.call("GET", f"/api/v1/models/{self.default_id}/model.glb")
        self.assertEqual((status, raw[:4]), (200, b"glTF"))
        status, body = self.get_json(f"/api/v1/models/{self.default_id}")
        self.assertNotIn("elevation_grid", body)
        self.assertEqual(body["glb_url"], f"/api/v1/models/{self.default_id}/model.glb")
        status, body = self.get_json("/api/v1/models/default")
        self.assertEqual((status, body["id"]), (200, self.default_id))

    def test_post_models_queues_and_reports_status(self):
        status, _, raw = self.call("POST", "/api/v1/models", {"city": "Rouyn-Noranda", "country": "Canada",
                                                              "distance": 1200})
        self.assertIn(status, (200, 202))
        body = json.loads(raw)
        ready = self.wait_ready(body["id"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["request"]["distance"], 1200)
        status, body = self.get_json("/api/v1/models")
        self.assertTrue(any(m["id"] == ready["id"] for m in body["models"]))

    def test_events_lightning_and_aircraft(self):
        self.wait_ready(self.default_id)
        lon, lat = LocalFrame(CENTER[0], CENTER[1], HALF).to_lonlat(1000.0, 500.0)
        status, _, raw = self.call("POST", "/api/v1/events", {"type": "lightning", "lat": lat, "lon": lon})
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual(body["model"]["status"], "ready")
        self.assertAlmostEqual(body["placement"]["east_m"], 1000.0, delta=1.0)
        self.assertAlmostEqual(body["placement"]["north_m"], 500.0, delta=1.0)
        self.assertNotIn("aircraft", body)

        status, _, raw = self.call("POST", "/api/v1/events", {
            "type": "aircraft", "lat": lat, "lon": lon, "altitude_ft": 400, "on_ground": False,
            "track_deg": 180, "type_code": "DH8D", "is_helicopter": False, "callsign": "TEST1"})
        body = json.loads(raw)
        self.assertEqual(body["aircraft"]["match"], "exact")
        self.assertEqual(body["aircraft"]["id"], "dh8d")
        self.assertEqual(body["event"]["phase"], "approach")
        self.assertEqual(body["placement"]["gltf_rotation_y_deg"], 270.0)   # heading south: 90 - 180

        status, _, raw = self.call("POST", "/api/v1/events", {
            "type": "aircraft", "lat": lat, "lon": lon, "on_ground": True, "type_code": "ZZZZ"})
        body = json.loads(raw)
        self.assertEqual((body["aircraft"]["match"], body["aircraft"]["id"]), ("category", "generic-jet"))
        self.assertEqual(body["event"]["phase"], "on_ground")

    def test_error_responses(self):
        cases = [
            ("POST", "/api/v1/events", b"not json", 400),
            ("POST", "/api/v1/events", b"[1, 2]", 400),
            ("POST", "/api/v1/events", json.dumps({"type": "lightning", "lat": 1, "lon": 500}).encode(), 400),
            ("POST", "/api/v1/models", json.dumps({"city": "X", "distance": 99999}).encode(), 400),
            ("POST", "/api/v1/models", json.dumps({"city": "X", "theme": "../../x"}).encode(), 400),
            ("POST", "/api/v1/events", json.dumps({"type": "lightning", "lat": 1, "lon": 1,
                                                   "model": "../../etc"}).encode(), 400),
            ("POST", "/api/v1/events", json.dumps({"type": "lightning", "lat": 1, "lon": 1,
                                                   "model": "missing-0000000000"}).encode(), 404),
            ("GET", "/api/v1/models/missing-0000000000", None, 404),
            ("GET", "/api/v1/models/missing-0000000000/model.glb", None, 404),
            ("GET", "/api/v1/nothing-here", None, 404),
            ("GET", "/api/v1/events", None, 405),
            ("PUT", "/api/v1/models", b"{}", 405),
        ]
        for method, path, raw, expected in cases:
            status, headers, body = self.call(method, path, raw=raw)
            self.assertEqual(status, expected, f"{method} {path}: {body[:200]}")
            self.assertIn("error", json.loads(body))

    def test_glb_is_409_while_a_build_is_still_running(self):
        gate = threading.Event()
        slow = fake_builder_factory(gate=gate)
        store = self.server.api.store
        runner = service.JobRunner(store, builder=slow, log=lambda *a: None)
        req = kapi.normalize_model_request({"city": "Slowtown"}, THEMES)
        runner.submit(req)
        try:
            status, body = self.get_json(f"/api/v1/models/{req['id']}")
            self.assertIn(body["status"], ("queued", "building"))
            status, _, raw = self.call("GET", f"/api/v1/models/{req['id']}/model.glb")
            self.assertEqual(status, 409)
        finally:
            gate.set()
        self.wait_ready_in(store, req["id"])

    def wait_ready_in(self, store, mid, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            meta = store.meta(mid)
            if meta and meta["status"] in ("ready", "failed"):
                return meta
            time.sleep(0.02)
        self.fail("build did not finish")


if __name__ == "__main__":
    unittest.main()
