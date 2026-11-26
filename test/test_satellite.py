"""Offline tests for the satellite picture cache and its storage in baked maps (network is mocked).

Run from the project root:  python -m unittest discover -s test -p "test_satellite.py" -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d import bakedmap, satellite  # noqa: E402

PICTURE = b"\xff\xd8\xff" + b"x" * 64   # looks like a JPEG and is larger than the "no data" size


class Reply:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SatelliteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_cells_stay_within_cap_and_zoom_range(self):
        cells = satellite.cells_for_tile(0.0, 0.0, 2000.0, 1, 48.85, 2.35)
        self.assertTrue(cells)
        self.assertLessEqual(len(cells), satellite.CELL_CAP)
        zs = {z for z, _, _ in cells}
        self.assertEqual(len(zs), 1)
        self.assertTrue(10 <= zs.pop() <= 18)

    def test_high_quality_picks_a_sharper_zoom(self):
        base = satellite.cells_for_tile(0.0, 0.0, 2000.0, 1, 48.85, 2.35)
        high = satellite.cells_for_tile(0.0, 0.0, 2000.0, 1, 48.85, 2.35, high=True)
        self.assertGreaterEqual(high[0][0], base[0][0])

    def test_needed_tiles_dedupes_neighbouring_model_tiles(self):
        group = {"center": [0.0, 0.0], "chunk": [0, 0], "lod": 0}
        one = satellite.needed_tiles({"geo": {"lat": 48.85, "lon": 2.35}, "tileSize": 2000.0, "tiles": 1,
                                      "groups": [group]})
        two = satellite.needed_tiles({"geo": {"lat": 48.85, "lon": 2.35}, "tileSize": 2000.0, "tiles": 1,
                                      "groups": [group, dict(group, lod=1)]})
        self.assertEqual(one, two)

    def test_fetch_tile_downloads_once_then_reads_the_disk_cache(self):
        with mock.patch("urllib.request.urlopen", return_value=Reply(PICTURE)) as opened:
            first = satellite.fetch_tile(18, 1, 2, cache_dir=self.cache)
            second = satellite.fetch_tile(18, 1, 2, cache_dir=self.cache)
        self.assertEqual(first, PICTURE)
        self.assertEqual(second, PICTURE)
        self.assertEqual(opened.call_count, 1)
        self.assertTrue((Path(self.cache) / "satellite" / "18" / "1" / "2.jpg").is_file())

    def test_fetch_tile_treats_empty_answers_as_missing(self):
        with mock.patch("urllib.request.urlopen", return_value=Reply(b"no")):
            self.assertIsNone(satellite.fetch_tile(18, 1, 3, cache_dir=self.cache))

    def test_fetch_tile_network_failure_is_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(satellite.fetch_tile(18, 1, 4, cache_dir=self.cache))

    def test_baked_map_keeps_picture_tiles(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "map.db"
            bakedmap.write(db, {"title": "t"}, {}, sat={"18/1/2": PICTURE})
            self.assertEqual(bakedmap.read_sat(db, "18/1/2"), PICTURE)
            self.assertIsNone(bakedmap.read_sat(db, "18/1/3"))

    def test_older_baked_map_without_picture_table_reads_as_none(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "old.db"
            with closing(sqlite3.connect(db)) as con:   # the layout before picture tiles were stored
                con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB NOT NULL)")
                con.execute("CREATE TABLE assets (ext TEXT PRIMARY KEY, name TEXT NOT NULL, data BLOB NOT NULL)")
                con.commit()
            self.assertIsNone(bakedmap.read_sat(db, "18/1/2"))


if __name__ == "__main__":
    unittest.main()
