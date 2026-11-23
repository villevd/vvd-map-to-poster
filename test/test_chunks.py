"""
Offline tests for the chunks beyond a model's edge (no network: the OSM and terrain builds are stubbed).

Run from the project root:  python -m unittest discover -s test -p "test_chunks.py" -v
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d import chunks  # noqa: E402
from map3d import gui  # noqa: E402
from map3d.mesh import Mesh, Scene  # noqa: E402
from map3d.viewer import export_html  # noqa: E402

THEME = {"bg": "#F5EDE4", "text": "#8B4513", "water": "#A8C4C4", "parks": "#E8E0D0", "road_default": "#D9A08A"}

GOOD = {"lat": "48.1", "lon": "-77.78", "half": "1500", "tiles": "3", "tileHalf": "500", "zmin": "300",
        "exag": "1.5", "bexag": "1.5", "maxLod": "2", "theme": "terracotta", "i": "3", "j": "-1"}


def square_mesh(half: float, role: str = "ground", lod: int = 0) -> Mesh:
    v = np.array([[-half, -half, 0], [half, -half, 0], [half, half, 0], [-half, half, 0]], dtype=np.float32)
    f = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return Mesh(name="test", role=role, vertices=v, faces=f, lod=lod)


class ParseRequest(unittest.TestCase):
    def test_valid_request_is_parsed(self):
        spec, i, j = chunks.parse_request(dict(GOOD))
        self.assertEqual((i, j), (3, -1))
        self.assertEqual(spec["tiles"], 3)
        self.assertEqual(spec["theme"], "terracotta")
        self.assertEqual(spec["maxLod"], 2)

    def test_missing_and_bad_values_are_refused(self):
        for key, value in [("lat", "91"), ("lon", "nan"), ("tiles", "2.5"), ("i", "x"), ("theme", "../etc"),
                           ("i", "500"), ("zmin", None)]:
            q = dict(GOOD)
            if value is None:
                del q[key]
            else:
                q[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(chunks.ChunkError):
                chunks.parse_request(q)


class CellGeometry(unittest.TestCase):
    def test_cells_use_the_models_grid(self):
        spec = {"tiles": 3, "tileHalf": 500.0}
        self.assertEqual(chunks.cell_center(spec, 1, 1), (0.0, 0.0))             # the middle of a 3 x 3 model
        self.assertEqual(chunks.cell_center(spec, 0, 2), (-1000.0, 1000.0))
        self.assertEqual(chunks.cell_center(spec, 3, 1), (2000.0, 0.0))         # first cell beyond the east edge

    def test_single_tile_model(self):
        spec = {"tiles": 1, "tileHalf": 1500.0}
        self.assertEqual(chunks.cell_center(spec, 1, 0), (3000.0, 0.0))

    def test_cell_latlon_is_where_the_model_grid_says(self):
        # Each chunk has its own projection, rotated by the meridian convergence (well under a metre at
        # 2 km), so compare distances: the model centre must be as far from the chunk centre as the grid says.
        from map3d.geo import LocalFrame
        spec = {"lat": 48.1, "lon": -77.78, "half": 1500.0, "tiles": 3, "tileHalf": 500.0}
        lat, lon = chunks.cell_latlon(spec, 3, -1)
        x, y = LocalFrame(lat, lon, 500.0).to_xy(spec["lon"], spec["lat"])     # the model centre, seen from the chunk
        cx, cy = chunks.cell_center(spec, 3, -1)
        self.assertAlmostEqual(float(np.hypot(x, y)), float(np.hypot(cx, cy)), delta=0.5)


class Payload(unittest.TestCase):
    def test_meshes_are_placed_and_packed_in_the_viewer_format(self):
        m = square_mesh(100.0)
        m.tags["chunk"] = (0, 0)
        chunks._place([m], 2000.0, -1000.0, 3, -1)
        self.assertAlmostEqual(float(m.vertices[:, 0].min()), 1900.0, places=3)
        self.assertEqual(m.tags["chunk"], (3, -1))
        payload = chunks.pack_payload([m], 3, -1, THEME)
        self.assertEqual((payload["i"], payload["j"]), (3, -1))
        self.assertTrue(payload["groups"])
        for g in payload["groups"]:
            self.assertIn(g["id"], payload["blobs"])
            raw = zlib.decompress(base64.b64decode(payload["blobs"][g["id"]]))
            self.assertGreater(len(raw), 0)
            self.assertEqual(g["chunk"], [3, -1])
            self.assertAlmostEqual(g["center"][0], 2000.0)
            self.assertAlmostEqual(g["center"][1], 1000.0)   # the viewer's z is -north
        json.dumps(payload)                                  # what the endpoint sends must be plain JSON


def model_scene(with_datum: bool) -> Scene:
    info = {"tiles": 3, "tile_half": 500.0, "exaggeration": 1.5, "building_exaggeration": 1.5}
    if with_datum:
        info["dem_min_m"] = 300.0
    return Scene(meshes=[square_mesh(500.0)], half=1500.0, z_range=(0.0, 10.0),
                 center_lonlat=(-77.78, 48.1), info=info)


class ModelExport(unittest.TestCase):
    def cfg_of(self, scene, theme_id="terracotta") -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = export_html(scene, THEME, os.path.join(d, "m.html"), theme_id=theme_id)
            html = p.read_text(encoding="utf-8")
        return json.loads(re.search(r'<script type="application/json" id="cfg">(.*?)</script>', html, re.S).group(1))

    def test_export_carries_the_build_parameters(self):
        cfg = self.cfg_of(model_scene(True))
        c = cfg["chunks"]
        self.assertEqual(c["tiles"], 3)
        self.assertEqual(c["tileHalf"], 500.0)
        self.assertEqual(c["zmin"], 300.0)
        self.assertEqual(c["theme"], "terracotta")
        self.assertAlmostEqual(c["lat"], 48.1)

    def test_models_that_cannot_be_extended_say_so(self):
        self.assertIsNone(self.cfg_of(model_scene(False))["chunks"])
        self.assertIsNone(self.cfg_of(model_scene(True), theme_id="current")["chunks"])


class ChunkRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = gui.make_server(18777)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, query: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/chunk?{query}", timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_route_returns_the_built_payload(self):
        fake = {"i": 3, "j": -1, "groups": [], "blobs": {}}
        with mock.patch.object(chunks, "chunk_payload", return_value=fake) as build:
            status, body = self.get("&".join(f"{k}={v}" for k, v in GOOD.items()))
        self.assertEqual(status, 200)
        self.assertEqual(body, fake)
        spec, i, j = build.call_args.args
        self.assertEqual((i, j), (3, -1))
        self.assertEqual(spec["theme"], "terracotta")

    def test_bad_request_is_400_and_build_failure_is_502(self):
        status, body = self.get("lat=12")
        self.assertEqual(status, 400)
        self.assertIn("'lon' is required", body["error"] if "error" in body else body["message"])
        with mock.patch.object(chunks, "chunk_payload", side_effect=RuntimeError("no data")):
            status, _ = self.get("&".join(f"{k}={v}" for k, v in GOOD.items()))
        self.assertEqual(status, 502)


@unittest.skipUnless(shutil.which("node"), "node is needed for the syntax check")
class ViewerScript(unittest.TestCase):
    def test_viewer_script_parses(self):
        with tempfile.TemporaryDirectory() as d:
            p = export_html(model_scene(True), THEME, os.path.join(d, "m.html"), theme_id="terracotta")
            html = p.read_text(encoding="utf-8")
            scripts = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
            self.assertEqual(len(scripts), 1)
            mjs = os.path.join(d, "viewer.mjs")
            with open(mjs, "w", encoding="utf-8") as f:
                f.write(scripts[0])
            res = subprocess.run(["node", "--check", mjs], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
