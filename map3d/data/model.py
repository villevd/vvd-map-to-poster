"""Data containers, OSM tag sets and the height parser for the 3D city model."""

import geopandas as gpd
import numpy as np
import re
from dataclasses import dataclass, field
from typing import Callable


# --------------------------------------------------------------------------
# Provider wiring
# --------------------------------------------------------------------------


@dataclass
class Providers:
    """
    Where data comes from. The OSM callables are injected so this package does
    not depend on the CLI module (and so tests can feed canned data).
    """

    osm_features: Callable[[str, dict], gpd.GeoDataFrame | None] | None = None
    osm_graph: Callable[[], object | None] | None = None
    overture: bool = True
    cache_dir: str = "cache"


@dataclass
class CityData:
    buildings: gpd.GeoDataFrame                 # Polygons, local metres (see BUILDING_COLUMNS)
    water: np.ndarray                           # (multi)polygons
    forest: np.ndarray
    parks: np.ndarray
    roads: gpd.GeoDataFrame                     # LineStrings: cls, bridge
    rails: np.ndarray                           # LineStrings
    trees: np.ndarray                           # (n, 2) positions of individually mapped trees
    sources: dict = field(default_factory=dict)  # human-readable provenance, for logs and the viewer
    stats: dict = field(default_factory=dict)    # building merge statistics
    signals: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))     # traffic signals (x, y), OSM
    stop_signs: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))  # stop signs (x, y), OSM
    runways: list = field(default_factory=list)  # runway centre lines (LineStrings, local metres), OSM aeroway=runway


BUILDING_COLUMNS = ["height", "levels", "min_height", "roof_shape", "roof_height", "kind", "part", "source", "h_src"]

OSM_LANDCOVER_TAGS = {
    "natural": ["water", "bay", "strait", "wood", "scrub", "grassland", "heath", "wetland", "tree"],
    "waterway": ["riverbank", "river", "stream", "canal", "drain"],
    "landuse": ["forest", "grass", "meadow", "recreation_ground", "cemetery", "village_green", "orchard"],
    "leisure": ["park", "garden", "playground", "pitch", "golf_course", "common"],
}
OSM_BUILDING_TAGS = {"building": True, "building:part": True}
OSM_RAIL_TAGS = {"railway": "rail"}
OSM_TRAFFIC_TAGS = {"highway": ["traffic_signals", "stop"]}
OSM_AIRPORT_TAGS = {"aeroway": ["runway"]}

# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------

_LEN_RE = re.compile(
    r"^\s*(?P<v>-?\d+(?:[.,]\d+)?)\s*(?P<u>m|meters?|metres?|ft|feet|foot|')?\s*(?:(?P<i>\d+(?:\.\d+)?)\s*(?:\"|in))?\s*$",
    re.IGNORECASE,
)
