#!/bin/bash
# Generates the pre-baked wallpaper set for villevd.ca/fond-decran (the "Fond d'écran" /
# background generator page in vvd-smart-dashboard). Renders every available theme (see
# themes/*.json) at a handful of fixed zoom levels, centered on the exact point
# vvd-smart-dashboard/web/src/mapProjection.ts already treats as Val-d'Or's map center — so if
# one of these ever replaces the dashboard's own background.png, that file's pin-placement math
# stays correct without needing to be re-derived.
#
# OSM data is cached per (lat, lon, distance) — not per theme (see create_map_poster.py's
# cache_get/cache_set keys) — so --all-themes only pays the real network-fetch cost once per
# zoom level below, not once per theme.
#
# Usage:
#   ./scripts/generate-vdo-wallpapers.sh
#
# Output:
#   posters/wallpapers/<zoom-slug>/<theme-id>.png
#   posters/wallpapers/manifest.json
#
# Once done, copy the whole posters/wallpapers/ directory into:
#   vvd-smart-dashboard/web/public/img/wallpapers/
# — that's the path the generator page's manifest fetch and <img>/download logic expect.

set -euo pipefail
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

CITY="Val-d'Or"
COUNTRY="Canada"
# Same center mapProjection.ts documents (CENTER_LAT/CENTER_LON) — maptoposter's own geocoded
# "Val-d'Or, Canada" result, pinned explicitly here rather than re-geocoded so this always lines
# up with that file even if Nominatim's answer ever drifts.
LAT="48.1023356"
LON="-77.7875715"

# 20x11.25in @ 300dpi = 6000x3375px, 16:9 — the largest 16:9 canvas create_map_poster.py allows
# (it hard-caps each dimension at 20in). Big enough that the web generator can crop-to-fit any
# real desktop/kiosk resolution (including most multi-monitor spans) without needing a separate
# render per target size.
WIDTH=20
HEIGHT=11.25

OUT_ROOT="posters/wallpapers"
mkdir -p "$OUT_ROOT"

# slug:distance(meters):label — edit/add rows to change what gets generated. Distance is the
# *requested* radius before create_map_poster.py's own aspect-ratio compensation (see
# get_crop_limits/compensated_dist in create_map_poster.py); roughly follows its own documented
# distance guide (4-6km small/dense, 8-12km medium, 15-20km+ large metro), scaled down a bit
# since Val-d'Or is a small town, not a metro.
ZOOM_LEVELS=(
  "serre:6000:Rapproché"
  "ville:12000:Ville"
  "region:20000:Régional"
)

for entry in "${ZOOM_LEVELS[@]}"; do
  IFS=':' read -r slug dist label <<< "$entry"
  zoom_dir="$OUT_ROOT/$slug"
  mkdir -p "$zoom_dir"

  echo "===================================================="
  echo "Zoom: $label ($slug, ${dist}m)"
  echo "===================================================="

  marker="$(mktemp)"

  uv run python3 create_map_poster.py \
    -c "$CITY" -C "$COUNTRY" \
    -lat "$LAT" -long "$LON" \
    --all-themes \
    -d "$dist" \
    -W "$WIDTH" -H "$HEIGHT" \
    --no-labels --no-gradient \
    -f png

  # create_map_poster.py always writes timestamped files straight into posters/ (see
  # generate_output_filename) — move just this run's new files into the organized zoom folder,
  # renamed to "<theme-id>.png". Matched by mtime rather than a filename pattern since the city
  # slug can itself contain punctuation (Val-d'Or's apostrophe survives city.lower().replace(" ",
  # "_")) that's awkward to glob for reliably.
  find posters -maxdepth 1 -name '*.png' -newer "$marker" -print0 |
    while IFS= read -r -d '' f; do
      base="$(basename "$f")"
      theme_name="${base#*_}"
      # Strips the trailing _YYYYMMDD_HHMMSS.png timestamp specifically (by digit-count, not by
      # underscore-splitting) since several theme ids (midnight_blue, gradient_roads, ...)
      # contain underscores of their own.
      theme_name="${theme_name%_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].png}"
      mv "$f" "$zoom_dir/$theme_name.png"
    done

  rm -f "$marker"
done

echo "Writing manifest.json..."
uv run python3 - "$OUT_ROOT" <<'PYEOF'
import json
import sys
from pathlib import Path

out_root = Path(sys.argv[1])
themes_dir = Path("themes")

# Keep in sync with the ZOOM_LEVELS array above.
zoom_levels = [
    {"slug": "serre", "distanceM": 6000, "label": "Rapproché"},
    {"slug": "ville", "distanceM": 12000, "label": "Ville"},
    {"slug": "region", "distanceM": 20000, "label": "Régional"},
]

themes = []
for theme_file in sorted(themes_dir.glob("*.json")):
    data = json.loads(theme_file.read_text(encoding="utf-8"))
    theme_id = theme_file.stem
    # Only list a theme if it actually rendered for every zoom level — keeps the manifest (and
    # so the web picker) honest if a run was interrupted or a theme failed.
    if not all((out_root / z["slug"] / f"{theme_id}.png").exists() for z in zoom_levels):
        print(f"  (skipping '{theme_id}' — missing a render for at least one zoom level)")
        continue
    themes.append(
        {
            "id": theme_id,
            "name": data.get("name", theme_id),
            "description": data.get("description", ""),
            "bg": data.get("bg"),
            "text": data.get("text"),
            "accent": data.get("road_motorway"),
        }
    )

manifest = {
    "city": "Val-d'Or",
    "sourceWidthPx": 6000,
    "sourceHeightPx": 3375,
    "zoomLevels": zoom_levels,
    "themes": themes,
}
manifest_path = out_root / "manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {manifest_path} ({len(themes)} theme(s) x {len(zoom_levels)} zoom level(s))")
PYEOF

echo "===================================================="
echo "Done. Copy $OUT_ROOT into vvd-smart-dashboard/web/public/img/wallpapers/"
echo "===================================================="
