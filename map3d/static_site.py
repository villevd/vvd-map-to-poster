"""
Static site export: a baked map (.db) as a folder of plain files that any web host can serve.

    python -m map3d.static_site posters/val-d'or_x.db carte/            # then upload carte/ to the host

The folder holds::

    index.html      the viewer, with the map's settings inside (no server, no CDN)
    blob/<id>.bin   the compressed mesh tiles, exactly as stored in the .db
    sat/z/x/y.jpg   the satellite picture tiles the ground uses
    vendor/three/   three.js (MIT), so the page does not depend on a third-party CDN
    embed-example.html, README.txt

What is different from the page the local GUI serves: there is no Python server, so the "edge chunks" (terrain generated
on demand past the model's edge) are switched off, and the underground data is baked into the page instead of being
fetched when the map is opened. Add ``?embed=1`` to the URL to hide the developer panels (see README.txt in the folder).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from . import bakedmap
from . import satellite as satellite_mod

VENDOR = Path(__file__).with_name("web") / "vendor" / "three"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

README = """\
Carte 3D - static export
========================

Everything in this folder is a plain file: copy the whole folder to the web host, for example to
https://villevd.ca/carte/ (any folder works, the page only uses relative links).

Show it on a page with an iframe:

    <iframe src="/carte/index.html?embed=1" title="Carte 3D de Val-d'Or" loading="lazy"
            allow="fullscreen" style="width:100%;height:80vh;border:0"></iframe>

URL options
    ?embed=1    hide the developer panels (performance, activity log, cake, place search)
    &panel=0    also hide the settings panel, leaving only the map

Host requirements
    * Serve it over https (a file:// URL does not work: the page fetches its tiles).
    * Nothing special for the MIME types; the .bin tiles can be served as application/octet-stream.
    * Let the host compress text (index.html) and cache blob/ and sat/ for a long time: those files never change
      under the same name. The .bin and .jpg files are already compressed, do not compress them again.
    * If the host sets a Content-Security-Policy, the page needs: script-src 'self' 'unsafe-inline' blob:;
      worker-src blob:; img-src 'self' data: blob:; connect-src 'self' https://api.open-meteo.com
      (weather) and, only if satellite tiles are missing, https://server.arcgisonline.com.
    * To allow only villevd.ca to frame it: Content-Security-Policy: frame-ancestors 'self' https://villevd.ca

Pieces that call other services from the visitor's browser
    * Weather: api.open-meteo.com (free for non-commercial use; check its terms for a municipal site).
    * Place search: OpenStreetMap Nominatim, switched off in embed mode.

Credits to show next to the map (the page already prints the map's own credits line):
    OpenStreetMap contributors (ODbL), Esri World Imagery, and the Quebec government underground data sets.
    Check each licence before publishing.
"""

EMBED_EXAMPLE = """\
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carte 3D - exemple d'intégration</title>
<style>body{margin:0;font:16px system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:16px}
iframe{width:100%;height:80vh;border:0;border-radius:8px}</style></head>
<body><main><h1>Carte 3D de Val-d'Or</h1>
<iframe src="index.html?embed=1" title="Carte 3D de Val-d'Or" loading="lazy" allow="fullscreen"></iframe>
</main></body></html>
"""


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _stream_rows(db: Path, sql: str):
    """Rows of a (possibly missing) table one at a time, so a multi-hundred-megabyte map is never held whole."""
    with closing(bakedmap._connect_ro(db)) as con:
        try:
            yield from con.execute(sql)
        except sqlite3.OperationalError:       # a map baked before that table existed
            return


def export_static(db_path: str | Path, out_dir: str | Path, satellite: bool = True, log=print) -> Path:
    """Write the static site for a baked map into ``out_dir`` (created; an existing ``index.html`` is replaced)."""
    from . import viewer                        # heavy (the viewer template)

    db, out = Path(db_path), Path(out_dir)
    cfg = bakedmap.read_cfg(db)
    out.mkdir(parents=True, exist_ok=True)

    # Settings: relative URLs, no edge chunks (they need the Python server), underground data baked in
    cfg.pop("chunks", None)
    cfg.update(static=True, blobBase="blob/", blobExt=".bin", satBase="sat/", satExt=".jpg")
    viewer._attach_underground(cfg)

    log("Mesh tiles ...")
    n = size = 0
    for bid, data in _stream_rows(db, "SELECT id, data FROM blobs"):
        if not _SAFE_ID.match(bid):
            raise ValueError(f"unsafe tile id {bid!r}")
        _write(out / "blob" / f"{bid}.bin", bytes(data))
        n += 1
        size += len(data)
    log(f"  {n} tiles, {size / 1_048_576:.0f} MB")

    log("Satellite tiles ...")
    stored = 0
    for key, data in _stream_rows(db, "SELECT key, data FROM sat"):
        z, x, y = (int(v) for v in key.split("/"))
        _write(out / "sat" / str(z) / str(x) / f"{y}.jpg", bytes(data))
        stored += 1
    if not stored and satellite:
        wanted = sorted(satellite_mod.needed_tiles(cfg))
        if len(wanted) > satellite_mod.BAKE_LIMIT:
            log(f"  ! {len(wanted)} picture tiles would be needed (limit {satellite_mod.BAKE_LIMIT}): skipped, "
                "the ground will show without the satellite picture")
        else:
            log(f"  fetching {len(wanted)} tiles (cached on this computer for next time) ...")

            def one(t):
                data = satellite_mod.fetch_tile(*t)
                if data:
                    _write(out / "sat" / str(t[0]) / str(t[1]) / f"{t[2]}.jpg", data)
                return bool(data)

            with ThreadPoolExecutor(max_workers=8) as pool:
                stored = sum(pool.map(one, wanted))
            log(f"  {stored} of {len(wanted)} tiles available")
    elif stored:
        log(f"  {stored} tiles")
    if not stored:
        cfg.pop("satBase", None)               # no tiles: the viewer falls back to fetching them from the source
        cfg.pop("satExt", None)

    log("Page and three.js ...")
    html = viewer.render_page(viewer._cfg_json(cfg), "", cfg.get("title", ""), cfg["bg"], cfg.get("text", "#ffffff"))
    cdn = f"https://cdn.jsdelivr.net/npm/three@{viewer.THREE_VERSION}/"
    for old, new in ((cdn + "build/three.module.js", "./vendor/three/three.module.js"),
                     (cdn + "examples/jsm/", "./vendor/three/addons/")):
        if old not in html:
            raise RuntimeError(f"viewer template no longer references {old}")
        html = html.replace(old, new)
    _write(out / "index.html", html.encode("utf-8"))
    shutil.copytree(VENDOR, out / "vendor" / "three", dirs_exist_ok=True)
    _write(out / "embed-example.html", EMBED_EXAMPLE.encode("utf-8"))
    _write(out / "README.txt", README.encode("utf-8"))
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log(f"Done: {out} ({total / 1_048_576:.0f} MB). See README.txt in that folder.")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a baked map (.db) as a static site folder")
    ap.add_argument("db", help="baked map to export")
    ap.add_argument("out", help="folder to write (created if needed)")
    ap.add_argument("--no-satellite", action="store_true",
                    help="do not fetch satellite tiles missing from the map (tiles stored in the map are always exported)")
    a = ap.parse_args(argv)
    export_static(a.db, a.out, satellite=not a.no_satellite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
