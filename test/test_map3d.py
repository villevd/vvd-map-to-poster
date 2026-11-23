"""
Offline tests for the 3D pipeline (synthetic data, no network).

Run from the project root:  python -m unittest discover -s test -p "test_*.py" -v
"""

import json
import os
import re
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

    def test_html_has_place_search_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = export_html(self.scene, THEME, os.path.join(d, "m.html"), title="Test", subtitle="Canada")
            html = p.read_text(encoding="utf-8")
        self.assertIn('id="find"', html)
        cfg = json.loads(re.search(r'<script type="application/json" id="cfg">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(cfg["geo"]["place"], "Test, Canada")
        self.assertAlmostEqual(cfg["geo"]["half"], self.scene.half)


class RoadExport(unittest.TestCase):
    def test_centre_lines_go_to_viewer_frame(self):
        from map3d.viewer import _road_cfg
        # scene frame: x east, y north, z height -> viewer frame: x east, y height, z = -north
        path = np.array([[0.0, 10.0, 100.0], [5.0, 20.0, 101.0]], dtype=np.float32)
        flat = _road_cfg([path, np.array([[1.0, 2.0, 3.0]], dtype=np.float32)])   # a single point is dropped
        self.assertEqual(flat, [[0.0, 100.0, -10.0, 5.0, 101.0, -20.0]])


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

    def test_terrain_and_building_exaggeration_are_separate_flags(self):
        cmd = self.cmd(exaggeration=1.2, building_exaggeration=2.5)
        self.assertIn("--exaggeration=1.2", cmd)
        self.assertIn("--building-exaggeration=2.5", cmd)

    def test_rejects_bad_input(self):
        for bad in ({"theme": "../../etc"}, {"city": ""}, {"city": "x" * 200}, {"distance": 999999},
                    {"exaggeration": "tall"}, {"latitude": "48.1", "longitude": "abc"}, {"latitude": "48.1"}):
            with self.assertRaises(gui.BadRequest, msg=bad):
                self.cmd(**bad)

    def test_tiles_option(self):
        self.assertIn("--tiles=3", self.cmd(tiles=3))
        for bad in (0, 8, "many"):
            with self.assertRaises(gui.BadRequest, msg=bad):
                self.cmd(tiles=bad)

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


class Tiling(unittest.TestCase):
    """A tiles x tiles model: every building lands in exactly one tile, tiles share one ground datum."""

    @classmethod
    def setUpClass(cls):
        from map3d.data import CityData, tile_data
        from map3d.geo import LocalFrame
        from map3d.scene import SceneOptions, build_tiled_scene
        from map3d.terrain import Dem
        cls.tile_data = staticmethod(tile_data)
        half = 200.0                                     # whole area is 400 m wide, 2 x 2 tiles of 200 m
        homes = [Polygon([(x, y), (x + 10, y), (x + 10, y + 8), (x, y + 8)])
                 for x in (-150, -50, 50, 150 - 10) for y in (-150, -50, 50, 150 - 8)]
        cls.buildings = make_buildings(homes, kind="house", height=6.0)
        frame = LocalFrame(48.1, -77.8, half)
        data = CityData(buildings=cls.buildings, water=np.asarray([], dtype=object), forest=np.asarray([box(-190, -190, -60, 60)], dtype=object),
                        parks=np.asarray([], dtype=object), roads=gpd.GeoDataFrame({"cls": [], "bridge": []}, geometry=[]),
                        rails=np.asarray([], dtype=object), trees=np.zeros((0, 2)))
        n = 25
        x0 = -half - 20.0
        z = 100.0 + 0.05 * (np.arange(n)[None, :] * 20.0 + x0) + 0.0 * np.arange(n)[:, None]    # gentle west-east slope
        dem = Dem(x0, x0, 20.0, z, "synthetic")
        cls.scene = build_tiled_scene(frame, data, dem, SceneOptions(shadows=False), tiles=2, log=lambda *_: None)

    def test_every_building_lives_in_exactly_one_tile(self):
        from map3d.data import CityData
        data = CityData(buildings=self.buildings, water=np.asarray([], dtype=object), forest=np.asarray([], dtype=object),
                        parks=np.asarray([], dtype=object), roads=None, rails=np.asarray([], dtype=object), trees=np.zeros((0, 2)))
        counts = [len(self.tile_data(data, cx, cy, 100.0).buildings) for cx in (-100.0, 100.0) for cy in (-100.0, 100.0)]
        self.assertEqual(sum(counts), len(self.buildings))
        self.assertTrue(all(c > 0 for c in counts))

    def test_chunks_and_detail_levels(self):
        chunks = {tuple(m.tags["chunk"]) for m in self.scene.meshes}
        self.assertEqual(chunks, {(0, 0), (1, 0), (0, 1), (1, 1)})
        for chunk in chunks:
            levels = {m.lod for m in self.scene.meshes if tuple(m.tags["chunk"]) == chunk}
            self.assertEqual(levels, {0, 1, 2})

    def test_far_levels_are_lighter(self):
        def faces(lod):
            return sum(m.n_faces for m in self.scene.meshes if m.lod == lod)
        self.assertLess(faces(2), faces(1))
        self.assertLess(faces(1), faces(0))

    def test_tiles_are_placed_side_by_side_on_one_datum(self):
        xs = np.concatenate([m.vertices[:, 0] for m in self.scene.meshes if m.lod == 0 and m.role == "ground"])
        self.assertAlmostEqual(float(xs.min()), -200.0, delta=1.0)
        self.assertAlmostEqual(float(xs.max()), 200.0, delta=1.0)
        # the west-east slope must continue across the seam: the east tile sits higher than the west tile

        def mean_ground(chunk):
            m = [m for m in self.scene.meshes if m.lod == 0 and m.role == "ground" and tuple(m.tags["chunk"]) == chunk][0]
            return float(m.vertices[:, 2].mean())
        self.assertGreater(mean_ground((1, 0)), mean_ground((0, 0)) + 3.0)

    def test_medium_level_swaps_trees_for_impostors(self):
        imp = [m for m in self.scene.meshes if "impostors" in m.tags]
        self.assertTrue(imp)
        self.assertTrue(all(m.lod == 1 and m.n_faces == 0 for m in imp))
        self.assertEqual(imp[0].tags["impostors"].shape[1], 6)
        self.assertFalse([m for m in self.scene.meshes if m.lod == 1 and m.role == "tree" and m.n_faces])

    def test_static_outputs_ignore_coarser_levels(self):
        with tempfile.TemporaryDirectory() as d:
            stl = export_stl(self.scene, os.path.join(d, "t.stl"), size_mm=100)
            count = struct.unpack("<I", stl.read_bytes()[80:84])[0]
        full = sum(m.n_faces for m in self.scene.meshes if m.lod == 0 and m.role not in
                   ("rail", "road_motorway", "road_primary", "road_secondary", "road_tertiary", "road_residential", "road_service"))
        self.assertEqual(count, full)

    def test_viewer_packs_one_chunk_per_tile_and_level(self):
        with tempfile.TemporaryDirectory() as d:
            html = export_html(self.scene, THEME, os.path.join(d, "t.html")).read_text(encoding="utf-8")
        cfg = json.loads(re.search(r'<script type="application/json" id="cfg">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(len(cfg["groups"]), 4 * 3)
        self.assertEqual((cfg["tiles"], cfg["maxLod"]), (2, 2))
        self.assertAlmostEqual(cfg["tileSize"], 200.0)


class ViewerFacts(unittest.TestCase):
    def test_facts_are_plain_json_numbers(self):
        from map3d.viewer import _facts
        info = {"buildings": {"count": np.int64(12), "gabled": np.int64(3), "height_known": 5, "height_estimated": 7},
                "trees": np.int64(40), "bridges": 2, "relief_m": np.float32(18.26), "dem_min_m": 301.0,
                "road_paths": [[1, 2]], "traffic": {"x": 1}}
        facts = _facts(info)
        self.assertEqual(facts["buildings"], {"count": 12, "gabled": 3, "height_known": 5, "height_estimated": 7})
        self.assertEqual((facts["trees"], facts["bridges"]), (40, 2))
        self.assertEqual(facts["relief_m"], 18.3)
        self.assertNotIn("road_paths", facts)
        json.dumps(facts)                       # must serialise into the viewer's config block
        self.assertEqual(_facts({}), {})


if __name__ == "__main__":
    unittest.main()
