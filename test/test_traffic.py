"""
Offline tests for the fake traffic (synthetic street grid, no network).

Run from the project root:  python -m unittest discover -s test -p "test_*.py" -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d.traffic import build_traffic, car_count  # noqa: E402
from map3d.viewer import export_html  # noqa: E402
from map3d.mesh import Scene  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_JS = os.path.join(HERE, "traffic_sim_check.js")


def grid_roads(n: int = 5, block: float = 120.0):
    """
    An n x n street grid centred on the origin, all residential, split at every junction the way
    OpenStreetMap ways are, plus one bridge that must be ignored.
    """
    lines, classes, bridges = [], [], []
    half = (n - 1) * block / 2
    ticks = [-half + i * block for i in range(n)]
    for x in ticks:                      # north-south streets
        for a, b in zip(ticks[:-1], ticks[1:]):
            lines.append(LineString([(x, a), (x, b)]))
            classes.append("residential")
            bridges.append(False)
    for y in ticks:                      # east-west streets
        for a, b in zip(ticks[:-1], ticks[1:]):
            lines.append(LineString([(a, y), (b, y)]))
            classes.append("residential")
            bridges.append(False)
    lines.append(LineString([(-half - 500, 0), (-half - 400, 0)]))
    classes.append("residential")
    bridges.append(True)
    return gpd.GeoDataFrame({"cls": classes, "bridge": bridges}, geometry=lines)


def flat(x, y):
    return np.zeros_like(np.asarray(x, dtype=float))


class TrafficBuildTests(unittest.TestCase):
    def setUp(self):
        self.roads = grid_roads()
        # signals on two inner junctions (the grid is 480 m wide, junctions every 120 m), stop signs on two others
        self.signals = np.array([[0.0, 0.0], [120.0, 0.0]])
        self.stops = np.array([[240.0, 240.0], [-240.0, 0.0]])
        self.cfg = build_traffic(self.roads, self.signals, self.stops, flat, lift=0.5, side=1000.0,
                                 log=lambda *_: None)

    def test_builds_drivable_graph_without_bridges(self):
        self.assertIsNotNone(self.cfg)
        # 5 vertical + 5 horizontal streets, each cut into 4 blocks
        self.assertEqual(len(self.cfg["edges"]), 40)
        self.assertEqual(sum(1 for c in self.cfg["nodeCtrl"] if c >= 0), 4)
        self.assertEqual(len(self.cfg["nodes"]) // 3, 25)
        for e in self.cfg["edges"]:
            self.assertEqual(len(e["p"]) % 3, 0)
            self.assertGreater(e["v"], 0)

    def test_signals_and_stop_signs_attach_to_junctions(self):
        kinds = sorted(c["k"] for c in self.cfg["ctrls"])
        self.assertEqual(kinds, ["signal", "signal", "stop", "stop"])
        controlled = [i for i in self.cfg["nodeCtrl"] if i >= 0]
        self.assertEqual(len(controlled), 4)
        for c in self.cfg["ctrls"]:
            if c["k"] == "signal":
                self.assertTrue(60.0 <= c["cyc"] <= 90.0)
                self.assertTrue(0.0 <= c["off"] < c["cyc"])

    def test_car_count_and_size(self):
        self.assertGreaterEqual(self.cfg["cars"], 12)
        self.assertGreaterEqual(self.cfg["car_len"], 4.5)
        self.assertEqual(car_count(0.0), 12)
        self.assertEqual(car_count(10000.0), 120)

    def test_no_roads_means_no_traffic(self):
        self.assertIsNone(build_traffic(gpd.GeoDataFrame({"cls": [], "bridge": []}, geometry=[]), np.zeros((0, 2)),
                                        np.zeros((0, 2)), flat, 0.5, 1000.0))

    def test_viewer_embeds_traffic(self):
        scene = Scene(meshes=[], half=500.0, z_range=(0.0, 1.0), center_lonlat=(0.0, 0.0),
                      info={"traffic": self.cfg})
        theme = {"bg": "#FFFFFF", "text": "#000000", "water": "#AAAAAA", "parks": "#BBBBBB"}
        tmp = tempfile.mkdtemp()
        try:
            path = export_html(scene, theme, os.path.join(tmp, "city.html"))
            html = path.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("TrafficSim", html)
        self.assertIn('"traffic":{"cars"', html)
        self.assertNotIn("__TRAFFIC_JS__", html)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the browser simulation headlessly")
class TrafficSimulationTests(unittest.TestCase):
    def test_cars_obey_signals_and_stop_signs(self):
        roads = grid_roads(n=6)
        # junctions of the 6 x 6 grid (ticks every 120 m from -300 to 300)
        signals = np.array([[60.0, 60.0], [-60.0, 180.0], [180.0, -60.0], [-180.0, -180.0]])
        stops = np.array([[300.0, -300.0], [-180.0, -60.0]])
        cfg = build_traffic(roads, signals, stops, flat, lift=0.5, side=1500.0, log=lambda *_: None)
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            out = subprocess.run(["node", CHECK_JS, path, "1800"], capture_output=True, text=True, timeout=300)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        stats = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertGreater(stats["crossings"], 50, stats)
        self.assertGreater(stats["signalStops"], 0, stats)     # some cars really waited at a light
        self.assertEqual(stats["redRuns"], 0, stats)           # nobody drives through a red they could stop for
        self.assertEqual(stats["stopSignSkips"], 0, stats)     # everybody stops at a stop sign they could stop for
        self.assertEqual(stats["nonFinite"], 0, stats)


class AirportTests(unittest.TestCase):
    def test_glide_path_lands_on_the_runway(self):
        from map3d.airport import APPROACH_M, GLIDE_DEG, build_airport
        runway = LineString([(-1100.0, -700.0), (900.0, -700.0)])     # 2000 m, outside the model centre
        out = build_airport([runway], flat, lift=0.5, exaggeration=1.5, log=lambda *_: None)
        self.assertIsNotNone(out)
        pts = np.asarray(out["pts"]).reshape(-1, 3)
        # The threshold is the end farther from the centre (-1100, -700); the approach comes in from the west
        self.assertLess(pts[0][0], -2000.0)
        self.assertAlmostEqual(out["touchdown_m"], APPROACH_M)
        # Height above the runway (lift only) at the start of the approach, in unexaggerated metres
        agl0 = (pts[0][1] - 0.5) / 1.5
        self.assertAlmostEqual(np.degrees(np.arctan(agl0 / APPROACH_M)), GLIDE_DEG, delta=0.05)
        # The descent is monotonic until the wheels touch down, and the runway is level at the lift height
        approach = pts[pts[:, 0] <= -1100.0 + 1e-6]
        approach = approach[approach[:, 0] <= pts[0][0] + 1e-6]
        self.assertTrue(np.all(np.diff(pts[:len(approach), 1]) <= 1e-6))
        end = pts[-1]
        self.assertAlmostEqual(end[1], 0.5, delta=0.01)

    def test_no_runway_no_airport(self):
        from map3d.airport import build_airport
        self.assertIsNone(build_airport([], flat, 0.5, 1.5, log=lambda *_: None))
        short = LineString([(0.0, 0.0), (100.0, 0.0)])                  # taxiway stub, too short
        self.assertIsNone(build_airport([short], flat, 0.5, 1.5, log=lambda *_: None))


if __name__ == "__main__":
    unittest.main()


MOTION_JS = os.path.join(HERE, "traffic_motion_check.js")


def curvy_and_roundabout_roads():
    """
    A road with many bends (driven both ways), plus a roundabout of 8 arcs with 4 approach roads, plus a numbered
    heavy route (ref 117) running past it. Lines are split at junctions, like OpenStreetMap ways.
    """
    lines, refs = [], []
    r = 22.0
    angles = np.radians(np.arange(0, 360, 45))
    for a0, a1 in zip(angles, list(angles[1:]) + [angles[0] + 2 * np.pi]):
        ts = np.linspace(a0, a1, 7)
        lines.append(LineString([(r * np.cos(t), r * np.sin(t)) for t in ts]))
        refs.append("")
    for k in range(4):                                         # approach roads, every 90 degrees, each with bends
        t = np.radians(90 * k)
        d = np.array([np.cos(t), np.sin(t)])
        n = np.array([-d[1], d[0]])
        pts = [tuple(r * d + i * 30.0 * d + 6.0 * np.sin(i) * n) for i in range(0, 8)]
        lines.append(LineString(pts))
        refs.append("117" if k in (0, 2) else "")
    # a long bendy loop road joining the far ends, so cars keep circulating instead of dead-ending
    ends = [np.array(ln.coords[-1]) for ln in lines[8:]]
    for a, b in zip(ends, ends[1:] + ends[:1]):
        mid = (a + b) / 2 * 1.35
        lines.append(LineString([tuple(a), tuple(mid * 0.7 + a * 0.3), tuple(mid), tuple(mid * 0.7 + b * 0.3), tuple(b)]))
        refs.append("")
    # Route 117 is a primary road (a highway in the sim); the rest are side streets. Lorries must keep to the first.
    cls = ["primary" if r == "117" else "residential" for r in refs]
    return gpd.GeoDataFrame({"cls": cls, "bridge": [False] * len(lines), "ref": refs}, geometry=lines)


class VehicleBehaviourTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is needed to run the browser simulation headlessly")
    def run_sim(self, cars: int = 150, seconds: int = 900):
        roads = curvy_and_roundabout_roads()
        cfg = build_traffic(roads, np.zeros((0, 2)), np.zeros((0, 2)), flat, lift=0.5, side=600.0, log=lambda *_: None)
        self.cfg = cfg
        cfg["cars"] = cars
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w") as f:
                json.dump(cfg, f)
            res = subprocess.run(["node", MOTION_JS, path, str(seconds)], capture_output=True, text=True, timeout=240)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_roundabout_is_found_and_one_way_counter_clockwise(self):
        roads = curvy_and_roundabout_roads()
        cfg = build_traffic(roads, np.zeros((0, 2)), np.zeros((0, 2)), flat, lift=0.5, side=600.0, log=lambda *_: None)
        ring = [e for e in cfg["edges"] if e.get("o")]
        self.assertEqual(len(ring), 8)                         # the 8 arcs, and nothing else (the bendy loop is not round)
        self.assertTrue(all(e["d"] in (0, 1) for e in ring))
        self.assertTrue(all(e["v"] <= 6.0 for e in ring))      # taken slowly

    def test_no_teleporting_on_bendy_roads_and_cars_circle_the_right_way(self):
        out = self.run_sim()
        self.assertEqual(out["teleports"], 0)                  # the old pose code mirrored every segment of a reversed road
        self.assertGreater(out["ringCcw"], 500)
        self.assertEqual(out["ringCw"], 0)                     # nobody drives the wrong way round

    def test_cars_give_way_when_entering_the_roundabout(self):
        out = self.run_sim()
        self.assertGreater(out["entries"], 30)
        self.assertLess(out["entriesTooClose"] / out["entries"], 0.05)

    def test_lorries_and_pickups_favour_the_numbered_route(self):
        out = self.run_sim()
        # Judged on the vehicles as they are placed: afterwards they wander, and on a tiny test network a lorry that
        # leaves the route has nowhere to go but ordinary streets
        heavy, other = out["startHeavy"], out["startOther"]
        self.assertGreater(out["startHeavyCount"], 12)
        self.assertGreater(heavy["lorry"] + heavy["pickup"], 0.55)
        self.assertLess(other["lorry"] + other["pickup"], 0.3)
        self.assertGreater(other["small"] + other["car"], 0.7)
        # and while driving, lorries still show up more on the route than elsewhere
        self.assertGreater(out["mixHeavy"]["lorry"], out["mixOther"]["lorry"])
