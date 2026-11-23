import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class PosterPackageTests(unittest.TestCase):
    def test_3d_code_fingerprint_reads_map3d_sources(self):
        from poster.build3d import _map3d_stamp
        stamp = _map3d_stamp()
        self.assertEqual(len(stamp), 10)
        self.assertNotEqual(stamp, "0" * 10)

    def test_old_entry_point_names_still_exported(self):
        import create_map_poster as cmp
        for name in ("build_scene_3d", "load_theme", "get_coordinates", "_cache_digest", "CACHE_DIR_PATH"):
            self.assertTrue(hasattr(cmp, name), name)


if __name__ == "__main__":
    unittest.main()
