"""
Offline test of the Overpass endpoint probe (no network): the main server is down, a mirror answers.

Run from the project root:  python -m unittest discover -s test -p "test_*.py" -v
"""

import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import create_map_poster as cmp  # noqa: E402
import osmnx as ox  # noqa: E402


class _Resp:
    def __init__(self, code):
        self.status_code = code


class OverpassMirrorTests(unittest.TestCase):
    def setUp(self):
        self.original = ox.settings.overpass_url
        ox.settings.overpass_url = "https://overpass-api.de/api"

    def tearDown(self):
        ox.settings.overpass_url = self.original

    def test_falls_back_to_a_mirror_when_the_main_server_is_down(self):
        def fake_get(url, **kwargs):
            if url.startswith("https://overpass-api.de"):
                raise requests.ConnectionError("down")
            return _Resp(200)

        with mock.patch.object(cmp.requests, "get", side_effect=fake_get), \
                mock.patch("builtins.print"):
            self.assertTrue(cmp._overpass_reachable(timeout=1))
        self.assertEqual(ox.settings.overpass_url, cmp.OVERPASS_MIRRORS[1])

    def test_reports_failure_when_no_server_answers(self):
        with mock.patch.object(cmp.requests, "get", side_effect=requests.ConnectionError("down")), \
                mock.patch("builtins.print"):
            self.assertFalse(cmp._overpass_reachable(timeout=1))
        self.assertEqual(ox.settings.overpass_url, "https://overpass-api.de/api")

    def test_main_server_is_used_when_it_answers(self):
        with mock.patch.object(cmp.requests, "get", return_value=_Resp(200)) as get, \
                mock.patch("builtins.print"):
            self.assertTrue(cmp._overpass_reachable(timeout=1))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(ox.settings.overpass_url, "https://overpass-api.de/api")


if __name__ == "__main__":
    unittest.main()
