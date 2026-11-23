"""
Offline tests for the download caches (network is mocked).

Run from the project root:  python -m unittest discover -s test -p "test_caching.py" -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from map3d import overture, terrain  # noqa: E402


class Response:
    def __init__(self, status, content=b"", payload=None):
        self.status_code = status
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class TerrainMisses(unittest.TestCase):
    def test_tile_outside_the_dataset_is_asked_for_only_once(self):
        session = mock.Mock()
        session.get.return_value = Response(404)
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            self.assertIsNone(terrain._download_tile(14, 1, 2, cache, session))
            self.assertIsNone(terrain._download_tile(14, 1, 2, cache, session))
            self.assertEqual(session.get.call_count, 1)
            self.assertTrue((cache / "14_1_2.missing").exists())


class OvertureCaches(unittest.TestCase):
    def test_latest_release_is_reused_for_a_day(self):
        session = mock.Mock()
        session.get.return_value = Response(200, payload={"latest": "2026-10-01"})
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            self.assertEqual(overture._latest_release(session, cache), "2026-10-01")
            self.assertEqual(overture._latest_release(session, cache), "2026-10-01")
            self.assertEqual(session.get.call_count, 1)

    def test_stale_release_is_looked_up_again(self):
        session = mock.Mock()
        session.get.return_value = Response(200, payload={"latest": "2026-11-01"})
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            (cache / "overture_release.txt").write_text("2026-10-01")
            old = os.path.getmtime(cache / "overture_release.txt") - 3 * 86400
            os.utime(cache / "overture_release.txt", (old, old))
            self.assertEqual(overture._latest_release(session, cache), "2026-11-01")
            self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
