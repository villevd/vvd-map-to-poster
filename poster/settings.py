"""Paths, defaults and the public server list shared by the poster code."""

import os
from pathlib import Path


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"

FILE_ENCODING = "utf-8"


DEFAULT_DISTANCE_2D = 18000
DEFAULT_DISTANCE_3D = 1500          # a 3D model is about buildings, so it covers a much smaller area
THREE_D_OUTPUTS = ("poster", "db", "html", "glb", "stl")


# Public Overpass servers to try, in order, when the main one does not answer
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
)
