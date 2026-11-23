"""
Offline tests for the 3D pipeline (synthetic data, no network).

Run from the project root:  python -m unittest discover -s test -p "test_*.py" -v
"""

import json
import os
import struct
import sys
import tempfile
import unittest

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Polygon, box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d import buildings as bld  # noqa: E402
from map3d import ground as gnd  # noqa: E402
from map3d.data import BUILDING_COLUMNS, parse_length  # noqa: E402
from map3d import gui  # noqa: E402
from map3d.export import export_glb, export_stl  # noqa: E402
from map3d.mesh import Scene  # noqa: E402
from map3d.viewer import export_html  # noqa: E402

THEME = {"bg": "#F5EDE4", "text": "#8B4513", "water": "#A8C4C4", "parks": "#E8E0D0", "road_default": "#D9A08A"}


def flat_ground(x, y):
    return np.full(np.shape(x), 100.0)


def make_buildings(geoms, **overrides):
    cols = {"height": np.nan, "levels": np.nan, "min_height": 0.0, "roof_shape": "", "roof_height": np.nan,
            "kind": "yes", "part": False, "source": "osm", "h_src": ""}
    cols.update(overrides)
    data = {c: cols[c] for c in BUILDING_COLUMNS}
    return gpd.GeoDataFrame(data, geometry=list(geoms), index=range(len(geoms)))


class ParseLength(unittest.TestCase):
    def test_units(self):
        self.assertAlmostEqual(parse_length("12"), 12.0)
        self.assertAlmostEqual(parse_length("12.5 m"), 12.5)
        self.assertAlmostEqual(parse_length("40 ft"), 12.192)
        self.assertAlmostEqual(parse_length("40'6\""), 40 * 0.3048 + 6 * 0.0254)

    def test_garbage(self):
        for v in (None, "tall", "-3", "0", "5000", float("nan")):
            self.assertTrue(np.isnan(parse_length(v)), v)


class BuildingGeometry(unittest.TestCase):
    def build(self, geom, **kw):
        b = make_buildings([geom], **kw)
        h = bld.resolve_heights(b)
        return bld.build_buildings(b, h, flat_ground, exag=1.0), h

    def test_flat_box_walls_face_outward_and_roof_faces_up(self):
        res, h = self.build(box(-5, -5, 5, 5), height=12.0, kind="commercial")
        self.assertAlmostEqual(h.eave[0], 12.0)
        m = res.mesh
        n = m.face_normals()
        cen = m.vertices[m.faces].mean(axis=1)
        vertical = np.abs(n[:, 2]) < 1e-6
        self.assertEqual(int(vertical.sum()), 8)              # 4 walls x 2 triangles
        self.assertTrue(np.all(np.einsum("ij,ij->i", n[vertical, :2], cen[vertical, :2]) > 0))
        roof = n[:, 2] > 0.99
        self.assertTrue(np.allclose(m.vertices[m.faces][roof][..., 2], 112.0))   # ground 100 + 12

    def test_small_house_gets_pitched_roof(self):
        house = Polygon([(0, 0), (12, 0), (12, 8), (0, 8)])
        res, h = self.build(house, kind="house")
        self.assertEqual(res.n_gabled, 1)
        z = res.mesh.vertices[:, 2]
        self.assertGreater(z.max(), 100 + h.eave[0] + 0.5)    # ridge above the eaves
        self.assertAlmostEqual(res.heights_above_ground[0], z.max() - 100, places=3)
        n = res.mesh.face_normals()
        slope = (n[:, 2] > 0.2) & (n[:, 2] < 0.99)
        self.assertGreater(int(slope.sum()), 0)

    def test_large_building_stays_flat(self):
        res, _ = self.build(box(0, 0, 60, 40), kind="warehouse")
        self.assertEqual(res.n_gabled, 0)

    def test_tag_heights_beat_estimates(self):
        b = make_buildings([box(0, 0, 10, 10)], levels=5.0, h_src="levels")
        h = bld.resolve_heights(b)
        self.assertAlmostEqual(h.eave[0], 5 * bld.LEVEL_HEIGHT + h.ridge[0] - h.ridge[0], places=3)
        self.assertFalse(h.estimated[0])


class GroundTiling(unittest.TestCase):
    def test_triangles_tile_the_square_exactly(self):
        half, step = 200.0, 8.0
        lake = box(-80, -60, 40, 90).buffer(10)
        pts, tri, cls, shadow, _ = gnd.conforming_triangulation(half, step, [("water", lake)], box(0, 0, 50, 50))
        area = np.abs(0.5 * np.cross(pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]])).sum()
        self.assertAlmostEqual(area, (2 * half) ** 2, delta=1.0)
        water_area = np.abs(0.5 * np.cross(pts[tri[cls == 0][:, 1]] - pts[tri[cls == 0][:, 0]],
                                           pts[tri[cls == 0][:, 2]] - pts[tri[cls == 0][:, 0]])).sum()
        self.assertAlmostEqual(water_area, lake.area, delta=lake.area * 0.03)   # shores follow the outline
        self.assertTrue(shadow.any())

    def test_trees_stay_in_and_around_their_polygon(self):
        poly = box(0, 0, 200, 200)
        pts = gnd.plan_trees(poly, None, np.zeros((0, 2)), 500, None, np.random.default_rng(1), half=400.0)
        self.assertTrue(0 < len(pts) <= 500)
        inside = shapely.contains_xy(poly, pts[:, 0], pts[:, 1])
        self.assertGreater(inside.mean(), 0.7)                        # dense inside ...
        near = shapely.contains_xy(poly.buffer(80.0), pts[:, 0], pts[:, 1])
        self.assertTrue(near.all())                                   # ... thinning out just past the edge


class Exporters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b = make_buildings([box(-5, -5, 5, 5), Polygon([(20, 0), (32, 0), (32, 8), (20, 8)])], height=10.0)
        res = bld.build_buildings(b, bld.resolve_heights(b), flat_ground, 1.0,
                                  tint=np.array([0.95, 1.05], dtype=np.float32))
        ground = box(-50, -50, 50, 50)
        verts, faces = gnd.drape_polygons(np.asarray([ground], dtype=object), 25.0, flat_ground, 0.0)
        from map3d.mesh import Mesh
        terrain = Mesh(name="terrain:ground", role="ground", vertices=verts.astype(np.float32), faces=faces, flat=True)
        cls.scene = Scene(meshes=[terrain, res.mesh], half=50.0, z_range=(99.6, 112.0), center_lonlat=(0.0, 0.0),
                          title="Test", info={"ledge": 0.0})

    def test_glb(self):
        with tempfile.TemporaryDirectory() as d:
            p = export_glb(self.scene, THEME, os.path.join(d, "m.glb"))
            raw = p.read_bytes()
        magic, version, length = struct.unpack("<4sII", raw[:12])
        self.assertEqual((magic, version, length), (b"glTF", 2, len(raw)))
        jlen, jtype = struct.unpack("<I4s", raw[12:20])
        doc = json.loads(raw[20:20 + jlen])
        self.assertEqual(jtype, b"JSON")
        self.assertEqual(len(doc["meshes"]), 2)
        self.assertIn("COLOR_0", doc["meshes"][1]["primitives"][0]["attributes"])   # tinted buildings

    def test_stl_size_matches_triangle_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = export_stl(self.scene, os.path.join(d, "m.stl"), size_mm=100)
            raw = p.read_bytes()
        count = struct.unpack("<I", raw[80:84])[0]
        self.assertEqual(len(raw), 84 + 50 * count)
        tris = np.frombuffer(raw[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        self.assertAlmostEqual(float(tris["v"][..., 0].max() - tris["v"][..., 0].min()), 100.0, delta=0.01)
        self.assertAlmostEqual(float(tris["v"][..., 2].min()), 0.0, places=4)

    def test_html_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            p = export_html(self.scene, THEME, os.path.join(d, "m.html"), title="Test <city>")
            html = p.read_text(encoding="utf-8")
        self.assertIn("three@", html)
        self.assertIn('id="cfg"', html)
        self.assertNotIn("__DATA__", html)
        self.assertNotIn("<city>", html)       # title is escaped


class GuiValidation(unittest.TestCase):
    THEMES = ["noir", "midnight_blue", "midnight_blue_contrast"]

    def cmd(self, **kw):
        params = {"city": "Val-d'Or", "country": "Canada", "theme": "noir", "distance": 1500, "exaggeration": 1.5}
        params.update(kw)
        return gui.build_command(params, self.THEMES)

    def test_command_uses_equals_form_so_text_cannot_become_a_flag(self):
        cmd = self.cmd(city="--help", country="-d 1")
        self.assertIn("--city=--help", cmd)
        self.assertIn("--country=-d 1", cmd)
        self.assertIn("--3d", cmd)
        self.assertIn("--3d-outputs=html", cmd)          # the viewer is always produced

    def test_layers_and_outputs(self):
        cmd = self.cmd(trees=False, overture=False, outputs=["glb", "bogus", "stl"])
        self.assertIn("--no-trees", cmd)
        self.assertIn("--no-overture", cmd)
        self.assertNotIn("--no-buildings", cmd)
        self.assertIn("--3d-outputs=html,glb,stl", cmd)

    def test_rejects_bad_input(self):
        for bad in ({"theme": "../../etc"}, {"city": ""}, {"city": "x" * 200}, {"distance": 999999},
                    {"exaggeration": "tall"}, {"latitude": "48.1", "longitude": "abc"}, {"latitude": "48.1"}):
            with self.assertRaises(gui.BadRequest, msg=bad):
                self.cmd(**bad)

    def test_exact_centre_accepted(self):
        cmd = self.cmd(latitude="48.1023", longitude="-77.7876")
        self.assertIn("--latitude=48.1023", cmd)
        self.assertIn("--longitude=-77.7876", cmd)

    def test_model_names_with_underscored_themes(self):
        got = gui.split_model_name("val-d'or_midnight_blue_contrast_3d_20261123_103338", self.THEMES)
        self.assertEqual(got, ("val-d'or", "midnight_blue_contrast", "20261123_103338"))
        got = gui.split_model_name("new_york_noir_3d_20260101_000000", self.THEMES)
        self.assertEqual(got, ("new york", "noir", "20260101_000000"))

    def test_only_plain_poster_files_are_served(self):
        for bad in ("../create_map_poster.py", "..\\x.html", "a/b.html", "", "create_map_poster.py", "notes.txt"):
            self.assertIsNone(gui.safe_file(bad), bad)


if __name__ == "__main__":
    unittest.main()
