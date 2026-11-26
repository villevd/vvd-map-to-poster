"""Underground layer: offline tests with canned responses (no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from map3d import underground as ug  # noqa: E402
from map3d.geo import LocalFrame  # noqa: E402


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


FRAME = LocalFrame(48.10, -77.78, 2000)

MACRO_MAP = {"success": {"data": [
    {"scale": "tiny", "name": "World unit", "lith": "volcanic", "color": "#111111", "best_int_name": "Archean"},
    {"scale": "small", "name": "Neoarchean volcanic", "lith": "volcanic: mafic rocks", "color": "#FF5CAD",
     "best_int_name": "Neoarchean", "t_age": 2500, "b_age": 2800},
]}}
MACRO_UNITS = {"success": {"data": [
    {"unit_id": 1, "unit_name": "Lake deposits", "t_age": 0, "b_age": 0.01, "max_thick": 0, "min_thick": 0,
     "lith": [{"name": "silt"}, {"name": "sand"}], "color": "#A57B54"},
    {"unit_id": 2, "unit_name": "Gowganda", "strat_name_long": "Gowganda Formation", "t_age": 2335, "b_age": 2350,
     "max_thick": 1000, "min_thick": 0, "lith": [{"name": "shale"}], "color": "#F58D2C"},
]}}


class Normalisers(unittest.TestCase):
    def test_surface_prefers_the_most_detailed_map(self):
        out = ug._strata_from_macrostrat({"map": MACRO_MAP["success"]["data"], "units": MACRO_UNITS["success"]["data"]})
        self.assertEqual(out["surface"]["name"], "Neoarchean volcanic")
        self.assertEqual([u["name"] for u in out["column"]], ["Lake deposits", "Gowganda Formation"])
        self.assertTrue(out["column"][0]["unconsolidated"])
        self.assertIsNone(out["column"][0]["thick_m"])                 # a thickness the data lacks stays unknown
        self.assertEqual(out["column"][1]["thick_m"], 1000)

    def test_drill_holes_become_local_metres_and_keep_their_angles(self):
        raw = {"drill": [
            {"geometry": {"type": "Point", "coordinates": [-77.78, 48.10]},
             "properties": {"NUMR_ORGN_FORG": "A-1", "AZMT_DEPR": 154, "PLON_DEPR": -45, "PROF_FIN_UNITE_LITH": 200, "AN_FORG": 2011}},
            {"geometry": {"type": "Point", "coordinates": [-77.78, 48.10]}, "properties": {"PROF_FIN_UNITE_LITH": None}},
        ]}
        holes = ug.drillholes_from_sigeom(raw, FRAME)
        self.assertEqual(len(holes), 1)                                 # no length: nothing to draw
        self.assertAlmostEqual(holes[0]["x"], 0.0, delta=1.0)
        self.assertAlmostEqual(holes[0]["y"], 0.0, delta=1.0)
        self.assertEqual((holes[0]["length_m"], holes[0]["plunge"], holes[0]["azimuth"]), (200.0, -45.0, 154.0))

    def test_missing_mine_depth_stays_none(self):
        raw = {"deposits": [{"geometry": {"type": "Point", "coordinates": [-77.78, 48.10]},
                             "properties": {"NOM_CORPS_MINR": "Mine Sullivan", "ETAT_CORPS_MINR_EN": "Closed mine",
                                            "SUBST_PRINC_EN": "Gold", "AN_DECV": 1911}}],
               "projects": [{"geometry": {"type": "Point", "coordinates": [-77.77, 48.10]},
                             "properties": {"NOM_MINE_PROJE": "Lamaque", "SIGN_STAT_MINE_PROJE_ANG": "Active mine"}}]}
        sites = ug.sites_from_sigeom(raw, FRAME)
        self.assertEqual([s["name"] for s in sites], ["Mine Sullivan", "Lamaque"])
        self.assertTrue(all(s["depth_m"] is None for s in sites))
        self.assertEqual([s["state"] for s in sites], ["closed", "active"])

    def test_claims_drop_holder_details_and_close_the_ring(self):
        ring = [[-77.79, 48.09], [-77.78, 48.09], [-77.78, 48.10], [-77.79, 48.10], [-77.79, 48.09]]
        raw = {"claims": [{"geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
                           "properties": {"TITRE": "CDC-1", "STATUT": "Actif - (A)", "TMN_DATE_EXPIR": "2027-05-14T03:59:59Z",
                                          "TMN_SUPRF": 53.4, "LISTE_TITUL": "Some Holder Inc. (1)"}}]}
        claims = ug.claims_from_gestim(raw, FRAME)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["expires"], "2027-05-14")
        self.assertNotIn("Holder", json.dumps(claims))
        self.assertEqual(claims[0]["ring"][0], claims[0]["ring"][-1])

    def test_esker_lines_are_clipped_to_the_model_and_keep_only_what_is_data(self):
        raw = {"esker": [{"geometry": {"type": "MultiLineString",
                                       "coordinates": [[[-77.80, 48.10], [-77.78, 48.10], [-77.50, 48.10]]]},
                          "properties": {"ESK_NO_SEQ": 7, "ESK_FORME_FLUVI_EN": "Esker (direction known)"}}]}
        out = ug.eskers_from_sigeom(raw, FRAME)
        self.assertEqual(len(out), 1)
        self.assertTrue(all(abs(x) <= FRAME.half + 1 and abs(y) <= FRAME.half + 1 for x, y in out[0]["line"]))
        self.assertEqual(set(out[0]), {"id", "kind", "line", "source"})       # no invented width or depth

    def test_nearby_duplicates_from_other_sources_are_merged(self):
        a = {"name": "Mine A", "x": 0.0, "y": 0.0}
        b = {"name": "Mine A (USGS)", "x": 40.0, "y": 30.0}
        c = {"name": "Mine B", "x": 900.0, "y": 0.0}
        self.assertEqual([s["name"] for s in ug._dedupe([a, b, c])], ["Mine A", "Mine B"])

    def test_layer_stack_marks_what_is_data_and_what_is_typical(self):
        layers = ug.build_layers(ug._strata_from_macrostrat({"map": MACRO_MAP["success"]["data"], "units": []}), [], [], [])
        self.assertEqual([l["kind"] for l in layers], ["soil", "overburden", "bedrock"])
        self.assertEqual([l["provenance"] for l in layers], ["generic", "generic", "data"])
        nodata = ug.build_layers({"surface": None, "column": []}, [], [], [])
        self.assertEqual(nodata[-1]["provenance"], "generic")


class Fetching(unittest.TestCase):
    def _session(self, fail_all=False):
        s = mock.Mock()

        def get(url, params=None, timeout=None):
            if fail_all:
                raise ConnectionError("offline")
            if "geologic_units/map" in url:
                return Resp(MACRO_MAP)
            if url.endswith("/units"):
                return Resp(MACRO_UNITS)
            if "usgs.gov" in url:
                return Resp({"features": [{"attributes": {"site_name": "Far Mine", "dev_stat": "Past Producer", "code_list": " AU",
                                                          "dep_id": "1"}, "geometry": {"x": -77.775, "y": 48.101}}]})
            return Resp({"features": []})
        s.get.side_effect = get
        return s

    def test_result_is_json_safe_and_cached(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._session()
            out = ug.fetch_underground(FRAME, d, session=s, log=lambda *_: None)
            json.dumps(out)
            self.assertEqual(out["layers"][-1]["name"], "Neoarchean volcanic")
            self.assertIn("macrostrat", out["sources"])
            self.assertEqual(out["sites"][0]["name"], "Far Mine")
            first = s.get.call_count
            ug.fetch_underground(FRAME, d, session=s, log=lambda *_: None)
            self.assertEqual(s.get.call_count, first)                  # a warm cache never touches the network

    def test_everything_unreachable_gives_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ug.fetch_underground(FRAME, d, session=self._session(fail_all=True), log=lambda *_: None))

    def test_osm_mine_workings_are_included_and_read_the_depth_tag(self):
        import geopandas as gpd
        from shapely.geometry import Point
        gdf = gpd.GeoDataFrame({"man_made": ["mineshaft"], "name": ["Shaft 3"], "depth": ["320 m"]},
                               geometry=[Point(-77.781, 48.101)], crs="EPSG:4326")
        with tempfile.TemporaryDirectory() as d:
            out = ug.fetch_underground(FRAME, d, osm_features=lambda name, tags: gdf, session=self._session(), log=lambda *_: None)
        shaft = next(s for s in out["sites"] if s["source"] == "osm")
        self.assertEqual((shaft["kind"], shaft["depth_m"]), ("shaft", 320.0))


class ViewerWiring(unittest.TestCase):
    def test_viewer_parts_exist_in_load_order_and_have_valid_syntax(self):
        js = Path(__file__).resolve().parent.parent / "map3d" / "web" / "viewer" / "js"
        names = sorted(p.name for p in js.glob("*.js"))
        self.assertLess(names.index("04b_cut.js"), names.index("05_imagery.js"))     # cutMaterial before loadGroup
        self.assertLess(names.index("09_chunks.js"), names.index("09b_underground.js"))
        import shutil
        import subprocess
        if shutil.which("node"):
            for n in ("04b_cut.js", "09b_underground.js"):
                r = subprocess.run(["node", "--check", str(js / n)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
