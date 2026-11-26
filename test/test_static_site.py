"""The static site export: a baked map becomes plain files with relative URLs, no chunks and no CDN."""
import json
import re
import zlib

from map3d import bakedmap, static_site


def _bake(tmp_path, sat=None):
    cfg = {"title": "Test", "bg": "#101010", "text": "#ffffff", "groups": [], "chunks": {"lat": 1, "lon": 2},
           "themes": {}, "theme": "t"}
    return bakedmap.write(tmp_path / "m.db", cfg, {"b0": zlib.compress(b"tile0"), "b1": zlib.compress(b"tile1")}, sat=sat)


def test_export_writes_plain_files(tmp_path):
    db = _bake(tmp_path, sat={"10/5/6": b"x" * 64})
    out = static_site.export_static(db, tmp_path / "site", log=lambda *_: None)
    assert (out / "blob" / "b0.bin").read_bytes() == zlib.compress(b"tile0")
    assert (out / "sat" / "10" / "5" / "6.jpg").read_bytes() == b"x" * 64
    assert (out / "vendor" / "three" / "three.module.js").is_file()
    assert (out / "vendor" / "three" / "addons" / "controls" / "OrbitControls.js").is_file()
    assert (out / "README.txt").is_file() and (out / "embed-example.html").is_file()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html
    assert '"three": "./vendor/three/three.module.js"' in html
    cfg = json.loads(re.search(r'id="cfg">(.*?)</script>', html, re.S).group(1))
    assert "chunks" not in cfg
    assert cfg["static"] is True and cfg["blobBase"] == "blob/" and cfg["satBase"] == "sat/"


def test_big_tiles_are_cut_in_parts(tmp_path):
    db = _bake(tmp_path)
    out = static_site.export_static(db, tmp_path / "site", log=lambda *_: None, part_bytes=10)
    raw = zlib.compress(b"tile0")
    assert len(raw) > 10
    joined = b"".join((out / "blob" / f"b0.bin.{i}").read_bytes() for i in range(2))
    assert joined == raw and not (out / "blob" / "b0.bin").exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    cfg = json.loads(re.search(r'id="cfg">(.*?)</script>', html, re.S).group(1))
    assert cfg["blobParts"]["b0"] == 2


def test_export_without_satellite_lets_the_viewer_use_the_source(tmp_path):
    db = _bake(tmp_path)
    out = static_site.export_static(db, tmp_path / "site", satellite=False, log=lambda *_: None)
    html = (out / "index.html").read_text(encoding="utf-8")
    cfg = json.loads(re.search(r'id="cfg">(.*?)</script>', html, re.S).group(1))
    assert "satBase" not in cfg
