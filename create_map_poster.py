"""
Map posters: the command-line entry point and the public API for other scripts.

The code lives in the ``poster`` package (settings, cache, themes, render2d, osm, build3d, cli).
Every name that used to live here is re-exported below, so ``import create_map_poster as cmp`` keeps working.
"""

import functools  # noqa: F401  (kept for code that imports the old module's names)
import os  # noqa: F401
import time  # noqa: F401
import hashlib  # noqa: F401
import requests  # noqa: F401  (tests patch cmp.requests)
import osmnx as ox  # noqa: F401

from poster.build3d import (
    _all_themes,
    _build_scene_3d_impl,
    _map3d_stamp,
    build_scene_3d,
    create_poster_3d,
)
from poster.cache import (
    CacheError,
    _cache_digest,
    _cache_path,
    _cache_try_get,
    _cache_try_set,
    cache_get,
    cache_set,
)
from poster.cli import (
    list_themes,
    print_examples,
)
from poster.osm import (
    _features_cache_key,
    _fetch_with_retries,
    _graph_cache_key,
    _overpass_reachable,
    fetch_features,
    fetch_graph,
    get_coordinates,
)
from poster.render2d import (
    FONTS,
    add_poster_labels,
    create_gradient_fade,
    create_poster,
    draw_road_glow,
    get_crop_limits,
    get_edge_colors_by_type,
    get_edge_widths_by_type,
    is_latin_script,
)
from poster.settings import (
    CACHE_DIR,
    CACHE_DIR_PATH,
    DEFAULT_DISTANCE_2D,
    DEFAULT_DISTANCE_3D,
    FILE_ENCODING,
    FONTS_DIR,
    OVERPASS_MIRRORS,
    POSTERS_DIR,
    THEMES_DIR,
    THREE_D_OUTPUTS,
)
from poster.themes import (
    THEME,
    generate_output_filename,
    get_available_themes,
    load_theme,
    set_active_theme,
)
from poster.cli import main

if __name__ == "__main__":
    main()
