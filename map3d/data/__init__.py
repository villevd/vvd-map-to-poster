"""Provider aggregation: turn OpenStreetMap + Overture Maps into one clean,
source-independent description of the city (``CityData``).

* OpenStreetMap (through OSMnx / Overpass) is the primary, freshest source and
  carries rich tags: ``height``, ``building:levels``, ``roof:shape``,
  ``building:part``, bridges, tunnels...
* Overture Maps adds footprints OSM is missing (Microsoft / Google ML
  buildings), heights from other datasets, ESA land cover, water, bridges and a
  complete road network - and keeps working when Overpass is down.

Both are merged by geometric overlap, never by id."""

from .model import (
    BUILDING_COLUMNS,
    CityData,
    OSM_AIRPORT_TAGS,
    OSM_BUILDING_TAGS,
    OSM_LANDCOVER_TAGS,
    OSM_RAIL_TAGS,
    OSM_TRAFFIC_TAGS,
    Providers,
    _LEN_RE,
)
from .parse import (
    _col,
    _first,
    _is_yes,
    parse_length,
    parse_number,
)
from .buildings import (
    _empty_buildings,
    drop_parents_covered_by_parts,
    merge_buildings,
    normalize_osm_buildings,
    normalize_overture_buildings,
)
from .landcover import (
    OV_COVER_ERASE,
    OV_FOREST,
    OV_GREEN,
    OV_WATER_LINE_WIDTH,
    WATERWAY_WIDTH,
    _buffer_lines,
    _cat,
    _polys,
    classify_osm_landcover,
    classify_overture_landcover,
)
from .roads import (
    ROAD_CLASS,
    _complement,
    _flag_intervals,
    _road_frame,
    roads_from_osm_graph,
    roads_from_overture,
)
from .collect import (
    _OV_BUILDING_COLS,
    _try,
    collect,
    tile_data,
)
