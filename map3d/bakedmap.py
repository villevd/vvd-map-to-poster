"""
Baked maps: one ``.db`` file (SQLite) per city model.

A baked map holds everything the built-in viewer needs, so the GUI opens it directly:

* ``meta``   the viewer settings as JSON (title, themes, facts, camera, chunk spec, ...)
* ``blobs``  the compressed mesh tiles, one row per tile and detail level
* ``assets`` the exports saved next to the model (poster PNG, GLB, STL), by extension

The standalone ``.html`` export is another way to show the same data (see ``viewer.export_html``), and
``convert_html`` turns an existing one into a baked map.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

FORMAT = 1
ASSET_MIME = {"png": "image/png", "glb": "model/gltf-binary", "stl": "model/stl"}
ASSET_EXTS = tuple(ASSET_MIME)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blobs (id TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS assets (ext TEXT PRIMARY KEY, name TEXT NOT NULL, data BLOB NOT NULL);
"""


def _connect_ro(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def write(path: str | Path, cfg: dict, blobs: dict[str, bytes], assets: dict[str, str | Path] | None = None) -> Path:
    """Write a baked map. ``blobs`` maps id -> zlib-compressed bytes; ``assets`` maps extension -> file to store."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with closing(sqlite3.connect(tmp)) as con:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [("format", str(FORMAT)), ("cfg", json.dumps(cfg, separators=(",", ":")))])
        con.executemany("INSERT INTO blobs VALUES (?, ?)", ((k, sqlite3.Binary(v)) for k, v in blobs.items()))
        for ext, src in (assets or {}).items():
            src = Path(src)
            if ext not in ASSET_MIME or not src.is_file():
                continue
            con.execute("INSERT INTO assets VALUES (?, ?, ?)", (ext, src.name, sqlite3.Binary(src.read_bytes())))
        con.commit()
    os.replace(tmp, path)
    return path


def read_cfg(path: str | Path) -> dict:
    with closing(_connect_ro(path)) as con:
        row = con.execute("SELECT value FROM meta WHERE key = 'cfg'").fetchone()
    if row is None:
        raise ValueError(f"{path} is not a baked map (no settings)")
    return json.loads(row[0])


def read_blob(path: str | Path, blob_id: str) -> bytes | None:
    with closing(_connect_ro(path)) as con:
        row = con.execute("SELECT data FROM blobs WHERE id = ?", (blob_id,)).fetchone()
    return bytes(row[0]) if row else None


def list_assets(path: str | Path) -> list[str]:
    """Extensions of the exports stored in the map, e.g. ['png', 'glb']."""
    with closing(_connect_ro(path)) as con:
        return [r[0] for r in con.execute("SELECT ext FROM assets ORDER BY ext")]


def read_asset(path: str | Path, ext: str) -> tuple[str, bytes] | None:
    with closing(_connect_ro(path)) as con:
        row = con.execute("SELECT name, data FROM assets WHERE ext = ?", (ext,)).fetchone()
    return (row[0], bytes(row[1])) if row else None


# ---------------------------------------------------------------- converting the standalone HTML

_CFG_TAG = re.compile(r'<script type="application/json" id="cfg">(.*?)</script>', re.S)
_BLOB_TAG = re.compile(r'<script type="text/plain" id="([^"]+)">([^<]*)</script>')


def convert_html(html_path: str | Path, out: str | Path | None = None) -> Path:
    """Turn a standalone viewer ``.html`` (and its PNG / GLB / STL siblings) into a baked map ``.db``."""
    html_path = Path(html_path)
    text = html_path.read_text(encoding="utf-8")
    m = _CFG_TAG.search(text)
    if not m:
        raise ValueError(f"{html_path} is not a 3D viewer page")
    cfg = json.loads(m.group(1))
    blobs = {bid: base64.b64decode(data) for bid, data in _BLOB_TAG.findall(text)}   # already zlib-compressed
    del text
    assets = {ext: html_path.with_suffix("." + ext) for ext in ASSET_EXTS}
    return write(out or html_path.with_suffix(".db"), cfg, blobs, assets)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Convert standalone 3D viewer pages (.html) into baked maps (.db)")
    ap.add_argument("html", nargs="+", help="standalone viewer page(s) to convert; the .db is written next to each")
    for p in ap.parse_args().html:
        print(f"Converting {p} ...", flush=True)
        out = convert_html(p)
        print(f"  saved {out} ({os.path.getsize(out) / 1_048_576:.1f} MB)", flush=True)
