#!/usr/bin/env python3
"""
City Map Poster Generator

This module generates beautiful, minimalist map posters for any city in the world.
It fetches OpenStreetMap data using OSMnx, applies customizable themes, and creates
high-quality poster-ready images with roads, water features, and parks.
"""

import argparse
import asyncio
import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import cast

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import requests
from geopandas import GeoDataFrame
from geopy.geocoders import Nominatim
from lat_lon_parser import parse
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point
from tqdm import tqdm

from font_management import load_fonts


class CacheError(Exception):
    """Raised when a cache operation fails."""


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"

FILE_ENCODING = "utf-8"

FONTS = load_fonts()


def _cache_path(key: str) -> str:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def cache_get(key: str):
    """
    Retrieve a cached object by key.

    Args:
        key: Cache key identifier

    Returns:
        Cached object if found, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    try:
        path = _cache_path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value):
    """
    Store an object in the cache.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)

    Raises:
        CacheError: If cache write operation fails
    """
    try:
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        raise CacheError(f"Cache write failed: {e}") from e


# Font loading now handled by font_management.py module


def is_latin_script(text):
    """
    Check if text is primarily Latin script.
    Used to determine if letter-spacing should be applied to city names.

    :param text: Text to analyze
    :return: True if text is primarily Latin script, False otherwise
    """
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            # Latin Unicode ranges:
            # - Basic Latin: U+0000 to U+007F
            # - Latin-1 Supplement: U+0080 to U+00FF
            # - Latin Extended-A: U+0100 to U+017F
            # - Latin Extended-B: U+0180 to U+024F
            if ord(char) < 0x250:
                latin_count += 1

    # If no alphabetic characters, default to Latin (numbers, symbols, etc.)
    if total_alpha == 0:
        return True

    # Consider it Latin if >80% of alphabetic characters are Latin
    return (latin_count / total_alpha) > 0.8


def generate_output_filename(city, theme_name, output_format):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(POSTERS_DIR):
        os.makedirs(POSTERS_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return os.path.join(POSTERS_DIR, filename)


def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []

    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith(".json"):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes


def load_theme(theme_name="terracotta"):
    """
    Load theme from JSON file in themes directory.
    """
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")

    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.")
        # Fallback to embedded terracotta theme
        return {
            "name": "Terracotta",
            "description": "Mediterranean warmth - burnt orange and clay tones on cream",
            "bg": "#F5EDE4",
            "text": "#8B4513",
            "gradient_color": "#F5EDE4",
            "water": "#A8C4C4",
            "parks": "#E8E0D0",
            "road_motorway": "#A0522D",
            "road_primary": "#B8653A",
            "road_secondary": "#C9846A",
            "road_tertiary": "#D9A08A",
            "road_residential": "#E5C4B0",
            "road_default": "#D9A08A",
            "railway": "#8B4513",
            "building": "#EADFD3",
        }

    with open(theme_file, "r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return theme


# Load theme (can be changed via command line or input)
THEME = dict[str, str]()  # Will be loaded later


def create_gradient_fade(ax, color, location="bottom", zorder=10):
    """
    Creates a fade effect at the top or bottom of the map.
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]

    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def get_edge_colors_by_type(g):
    """
    Assigns colors to edges based on road type hierarchy.
    Returns a list of colors corresponding to each edge in the graph.
    """
    edge_colors = []

    for _u, _v, data in g.edges(data=True):
        # Get the highway type (can be a list or string)
        highway = data.get('highway', 'unclassified')

        # Handle list of highway types (take the first one)
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign color based on road type
        if highway in ["motorway", "motorway_link"]:
            color = THEME["road_motorway"]
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            color = THEME["road_primary"]
        elif highway in ["secondary", "secondary_link"]:
            color = THEME["road_secondary"]
        elif highway in ["tertiary", "tertiary_link"]:
            color = THEME["road_tertiary"]
        elif highway in ["residential", "living_street", "unclassified"]:
            color = THEME["road_residential"]
        else:
            color = THEME['road_default']

        edge_colors.append(color)

    return edge_colors


def get_edge_widths_by_type(g):
    """
    Assigns line widths to edges based on road type.
    Major roads get thicker lines.
    """
    edge_widths = []

    for _u, _v, data in g.edges(data=True):
        highway = data.get('highway', 'unclassified')

        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign width based on road importance
        if highway in ["motorway", "motorway_link"]:
            width = 1.2
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            width = 1.0
        elif highway in ["secondary", "secondary_link"]:
            width = 0.8
        elif highway in ["tertiary", "tertiary_link"]:
            width = 0.6
        else:
            width = 0.4

        edge_widths.append(width)

    return edge_widths


def draw_road_glow(ax, g_proj, edge_colors, edge_widths, bgcolor):
    """
    Draws soft halos beneath the road network by re-plotting the same edges
    progressively wider and more transparent. Gives bright roads a luminous
    look on dark themes instead of sitting flat against the background.
    """
    for width_mult, alpha in ((7.0, 0.05), (4.0, 0.09), (2.2, 0.16)):
        ox.plot_graph(
            g_proj, ax=ax, bgcolor=bgcolor,
            node_size=0,
            edge_color=edge_colors,
            edge_linewidth=[w * width_mult for w in edge_widths],
            edge_alpha=alpha,
            show=False,
            close=False,
        )


def get_coordinates(city, country):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    coords = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords)
    if cached:
        print(f"✓ Using cached coordinates for {city}, {country}")
        return cached

    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as e:
        raise ValueError(f"Geocoding failed for {city}, {country}: {e}") from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running event loop in the same thread; raise a clear error.
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        # Use getattr to safely access address (helps static analyzers)
        addr = getattr(location, "address", None)
        if addr:
            print(f"✓ Found: {addr}")
        else:
            print("✓ Found location (address not available)")
        print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
        try:
            cache_set(coords, (location.latitude, location.longitude))
        except CacheError as e:
            print(e)
        return (location.latitude, location.longitude)

    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(g_proj, center_lat_lon, fig, dist):
    """
    Crop inward to preserve aspect ratio while guaranteeing
    full coverage of the requested radius.
    """
    lat, lon = center_lat_lon

    # Project center point into graph CRS
    center = (
        ox.projection.project_geometry(
            Point(lon, lat),
            crs="EPSG:4326",
            to_crs=g_proj.graph["crs"]
        )[0]
    )
    center_x, center_y = center.x, center.y

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    # Start from the *requested* radius
    half_x = dist
    half_y = dist

    # Cut inward to match aspect
    if aspect > 1:  # landscape → reduce height
        half_y = half_x / aspect
    else:  # portrait → reduce width
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def _graph_cache_key(point, dist) -> str:
    """Cache key of a street-network download (shared by fetch_graph and the 3D cache-first lookup)."""
    lat, lon = point
    return f"graph_{lat}_{lon}_{dist}"


def _features_cache_key(point, dist, tags, name) -> str:
    """Cache key of an OSM feature download (shared by fetch_features and the 3D cache-first lookup)."""
    lat, lon = point
    tag_str = "_".join(tags.keys())
    return f"{name}_{lat}_{lon}_{dist}_{tag_str}"


def fetch_graph(point, dist) -> MultiDiGraph | None:
    """
    Fetch street network graph from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches all network types
    within the specified distance from the center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point

    Returns:
        MultiDiGraph of street network, or None if fetch fails
    """
    lat, lon = point
    graph = _graph_cache_key(point, dist)
    cached = cache_get(graph)
    if cached is not None:
        print("✓ Using cached street network")
        return cast(MultiDiGraph, cached)

    try:
        g = ox.graph_from_point(point, dist=dist, dist_type='bbox', network_type='all', truncate_by_edge=True)
        # Rate limit between requests
        time.sleep(0.5)
        try:
            cache_set(graph, g)
        except CacheError as e:
            print(e)
        return g
    except Exception as e:
        print(f"OSMnx error while fetching graph: {e}")
        return None


def fetch_features(point, dist, tags, name) -> GeoDataFrame | None:
    """
    Fetch geographic features (water, parks, etc.) from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches features matching
    the specified OSM tags within distance from center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point
        tags: Dictionary of OSM tags to filter features
        name: Name for this feature type (for caching and logging)

    Returns:
        GeoDataFrame of features, or None if fetch fails
    """
    features = _features_cache_key(point, dist, tags, name)
    cached = cache_get(features)
    if cached is not None:
        print(f"✓ Using cached {name}")
        return cast(GeoDataFrame, cached)

    try:
        data = ox.features_from_point(point, tags=tags, dist=dist)
        # Rate limit between requests
        time.sleep(0.3)
        try:
            cache_set(features, data)
        except CacheError as e:
            print(e)
        return data
    except Exception as e:
        print(f"OSMnx error while fetching features: {e}")
        return None


def add_poster_labels(ax, display_city, display_country, fonts, width, height, include_labels=True):
    """
    Draw the city name and country at the bottom of a poster axes.

    Shared by the flat 2D poster and the 3D render, so both get identical
    typography. Positions use axes coordinates (0-1) so they work for any
    poster size.
    """
    # Calculate scale factor based on smaller dimension (reference 12 inches)
    # This ensures text scales properly for both portrait and landscape orientations
    scale_factor = min(height, width) / 12.0

    # Base font sizes (at 12 inches width)
    base_main = 60
    base_sub = 22

    # Typography - use custom fonts if provided, otherwise use default FONTS
    active_fonts = fonts or FONTS
    if active_fonts:
        # font_main is calculated dynamically later based on length
        font_sub = FontProperties(
            fname=active_fonts["light"], size=base_sub * scale_factor
        )
    else:
        # Fallback to system fonts
        font_sub = FontProperties(
            family="monospace", weight="normal", size=base_sub * scale_factor
        )

    # Format city name based on script type
    # Latin scripts: apply uppercase and letter spacing for aesthetic
    # Non-Latin scripts (CJK, Thai, Arabic, etc.): no spacing, preserve case structure
    if is_latin_script(display_city):
        # Latin script: uppercase with letter spacing (e.g., "P A R I S")
        spaced_city = " ".join(list(display_city.upper()))
    else:
        # Non-Latin script: no spacing, no forced uppercase
        # For scripts like Arabic, Thai, Japanese, etc.
        spaced_city = display_city

    # Dynamically adjust font size based on city name length to prevent truncation
    # We use the already scaled "main" font size as the starting point.
    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)

    # Heuristic: If length is > 10, start reducing.
    if city_char_count > 10:
        length_factor = 10 / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(
            fname=active_fonts["bold"], size=adjusted_font_size
        )
    else:
        font_main_adjusted = FontProperties(
            family="monospace", weight="bold", size=adjusted_font_size
        )

    # --- BOTTOM TEXT ---
    if include_labels:
        ax.text(
            0.5,
            0.14,
            spaced_city,
            transform=ax.transAxes,
            color=THEME["text"],
            ha="center",
            fontproperties=font_main_adjusted,
            zorder=11,
        )

        ax.text(
            0.5,
            0.10,
            display_country.upper(),
            transform=ax.transAxes,
            color=THEME["text"],
            ha="center",
            fontproperties=font_sub,
            zorder=11,
        )


def create_poster(
    city,
    country,
    point,
    dist,
    output_file,
    output_format,
    width=12,
    height=16,
    country_label=None,
    name_label=None,
    display_city=None,
    display_country=None,
    fonts=None,
    include_railways=True,
    include_buildings=True,
    include_labels=True,
    include_gradient=True,
):
    """
    Generate a complete map poster with roads, water, parks, and typography.

    Creates a high-quality poster by fetching OSM data, rendering map layers,
    applying the current theme, and adding text labels with coordinates.

    Args:
        city: City name for display on poster
        country: Country name for display on poster
        point: (latitude, longitude) tuple for map center
        dist: Map radius in meters
        output_file: Path where poster will be saved
        output_format: File format ('png', 'svg', or 'pdf')
        width: Poster width in inches (default: 12)
        height: Poster height in inches (default: 16)
        country_label: Optional override for country text on poster
        _name_label: Optional override for city name (unused, reserved for future use)

    Raises:
        RuntimeError: If street network data cannot be retrieved
    """
    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    print(f"\nGenerating map for {city}, {country}...")

    # Progress bar for data fetching
    total_steps = 3 + int(include_railways) + int(include_buildings)
    with tqdm(
        total=total_steps,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as pbar:
        # 1. Fetch Street Network
        pbar.set_description("Downloading street network")
        compensated_dist = dist * (max(height, width) / min(height, width)) / 4  # To compensate for viewport crop
        g = fetch_graph(point, compensated_dist)
        if g is None:
            raise RuntimeError("Failed to retrieve street network data.")
        pbar.update(1)

        # 2. Fetch Water Features
        pbar.set_description("Downloading water features")
        water = fetch_features(
            point,
            compensated_dist,
            tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
            name="water",
        )
        pbar.update(1)

        # 3. Fetch Parks
        pbar.set_description("Downloading parks/green spaces")
        parks = fetch_features(
            point,
            compensated_dist,
            tags={"leisure": "park", "landuse": "grass"},
            name="parks",
        )
        pbar.update(1)

        # 4. Fetch Railways (optional layer)
        railways = None
        if include_railways:
            pbar.set_description("Downloading railways")
            railways = fetch_features(
                point,
                compensated_dist,
                tags={"railway": "rail"},
                name="railways",
            )
            pbar.update(1)

        # 5. Fetch Buildings (optional layer)
        buildings = None
        if include_buildings:
            pbar.set_description("Downloading buildings")
            buildings = fetch_features(
                point,
                compensated_dist,
                tags={"building": True},
                name="buildings",
            )
            pbar.update(1)

    print("✓ All data retrieved successfully!")

    # 2. Setup Plot
    print("Rendering map...")
    fig, ax = plt.subplots(figsize=(width, height), facecolor=THEME["bg"])
    ax.set_facecolor(THEME["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))

    # Project graph to a metric CRS so distances and aspect are linear (meters)
    g_proj = ox.project_graph(g)

    # 3. Plot Layers
    # Layer 1: Polygons (filter to only plot polygon/multipolygon geometries, not points)
    if water is not None and not water.empty:
        # Filter to only polygon/multipolygon geometries to avoid point features showing as dots
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            # Project water features into the SAME CRS as the graph (not an
            # independently auto-picked UTM zone, which can differ near zone
            # boundaries and silently plot the layer off-canvas)
            water_polys = water_polys.to_crs(g_proj.graph['crs'])
            water_polys.plot(ax=ax, facecolor=THEME['water'], edgecolor='none', zorder=0.5)

    if parks is not None and not parks.empty:
        # Filter to only polygon/multipolygon geometries to avoid point features showing as dots
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            # Project park features into the same CRS as the graph (see water note above)
            parks_polys = parks_polys.to_crs(g_proj.graph['crs'])
            parks_polys.plot(ax=ax, facecolor=THEME['parks'], edgecolor='none', zorder=0.8)

    if buildings is not None and not buildings.empty:
        # Filter to only polygon/multipolygon geometries to avoid point features showing as dots
        building_polys = buildings[buildings.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not building_polys.empty:
            # Project building features into the same CRS as the graph (see water note above)
            building_polys = building_polys.to_crs(g_proj.graph['crs'])
            building_polys.plot(
                ax=ax,
                facecolor=THEME.get('building', THEME['parks']),
                edgecolor='none',
                zorder=0.9,
            )

    # Layer 2: Roads with hierarchy coloring
    print("Applying road hierarchy colors...")
    edge_colors = get_edge_colors_by_type(g_proj)
    edge_widths = get_edge_widths_by_type(g_proj)

    # Determine cropping limits to maintain the poster aspect ratio
    crop_xlim, crop_ylim = get_crop_limits(g_proj, point, fig, compensated_dist)

    # Soft glow halos beneath the roads (theme-controlled, e.g. midnight_blue_contrast)
    if THEME.get('glow'):
        draw_road_glow(ax, g_proj, edge_colors, edge_widths, THEME['bg'])

    # Plot the projected graph and then apply the cropped limits
    ox.plot_graph(
        g_proj, ax=ax, bgcolor=THEME['bg'],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop_xlim)
    ax.set_ylim(crop_ylim)

    # Layer 2b: Railways (plotted above roads so the line is visible crossing them)
    if railways is not None and not railways.empty:
        railway_lines = railways[railways.geometry.type.isin(["LineString", "MultiLineString"])]
        if not railway_lines.empty:
            # Project railway features into the same CRS as the graph (see water note above)
            railway_lines = railway_lines.to_crs(g_proj.graph['crs'])
            railway_lines.plot(
                ax=ax,
                color=THEME.get('railway', THEME['road_default']),
                linewidth=0.8,
                linestyle=(0, (4, 2)),
                zorder=1.5,
            )

    # Layer 3: Gradients (Top and Bottom)
    if include_gradient:
        create_gradient_fade(ax, THEME['gradient_color'], location='bottom', zorder=10)
        create_gradient_fade(ax, THEME['gradient_color'], location='top', zorder=10)

    # 4. Typography (city + country at the bottom of the poster)
    add_poster_labels(ax, display_city, display_country, fonts, width, height, include_labels)

    # 5. Save
    print(f"Saving to {output_file}...")

    fmt = output_format.lower()
    save_kwargs = dict(
        facecolor=THEME["bg"],
        bbox_inches="tight",
        pad_inches=0.05,
    )

    # DPI matters mainly for raster formats
    if fmt == "png":
        save_kwargs["dpi"] = 300

    plt.savefig(output_file, format=fmt, **save_kwargs)

    plt.close()
    print(f"✓ Done! Poster saved as {output_file}")


DEFAULT_DISTANCE_2D = 18000
DEFAULT_DISTANCE_3D = 1500          # a 3D model is about buildings, so it covers a much smaller area
THREE_D_OUTPUTS = ("poster", "html", "glb", "stl")


def _overpass_reachable(timeout=8):
    """Quick health probe of the Overpass endpoint OSMnx will use."""
    url = ox.settings.overpass_url.rstrip("/") + "/status"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "city_map_poster"})
        return r.status_code == 200
    except requests.RequestException:
        return False


def _fetch_with_retries(fetch_once, what, state):
    """
    Run a network fetcher that returns None on failure, with a fast failure mode.

    The public Overpass servers are often busy or down, and OSMnx waits minutes
    on a dead one. So the first network request probes the server for a few
    seconds; if it does not answer, every remaining request is skipped. If it
    does answer, a failing request is retried a couple of times.
    """
    if state.get("down"):
        return None
    if not state.get("probed"):
        state["probed"] = True
        if not _overpass_reachable():
            state["down"] = True
            return None
    for attempt in range(3):
        result = fetch_once()
        if result is not None:
            return result
        if attempt < 2:
            wait = 6 * (attempt + 1)
            print(f"  ↻ {what}: retrying in {wait}s...")
            time.sleep(wait)
    state["down"] = True
    return None


def build_scene_3d(
    city,
    point,
    dist,
    exaggeration=1.5,
    azimuth=225.0,
    include_buildings=True,
    include_trees=True,
    include_railways=True,
    include_terrain=True,
    use_overture=True,
):
    """
    Fetch every available data source around ``point`` and build the 3D scene.

    Sources, merged by geometric overlap: OpenStreetMap (OSMnx) for buildings,
    streets, water and vegetation; Overture Maps for extra building footprints,
    heights, land cover and roads (optional, needs ``pyarrow``); AWS Terrain
    Tiles for elevation. Any source that is unreachable is skipped with a
    warning and the others carry on. The scene is theme independent, so it can
    be rendered once per theme.

    Args:
        city: Name used as the scene title
        point: (latitude, longitude) of the model centre
        dist: Half the side of the square model, in meters
        exaggeration: Vertical exaggeration of relief, buildings and trees
        azimuth: Compass bearing of the camera; ground shadows are baked for a light placed to its left
    """
    from map3d.data import Providers, collect
    from map3d.geo import LocalFrame
    from map3d.scene import SceneOptions, build_scene
    from map3d.terrain import Dem, fetch_dem

    lat, lon = point
    frame = LocalFrame(lat, lon, dist)
    osm_dist = dist * 1.06      # a little beyond the tile so nothing is cut short at the edges
    state = {"down": False}

    def cached_or_fetch(key, fetch_once, what):
        # A warm cache never needs the network, even when Overpass is down
        try:
            cached = cache_get(key)
        except CacheError:
            cached = None
        if cached is not None:
            return cached
        return _fetch_with_retries(fetch_once, what, state)

    def osm_features(name, tags):
        return cached_or_fetch(
            _features_cache_key(point, osm_dist, tags, name),
            lambda: fetch_features(point, osm_dist, tags, name),
            f"OpenStreetMap {name}",
        )

    def osm_graph():
        return cached_or_fetch(
            _graph_cache_key(point, osm_dist),
            lambda: fetch_graph(point, osm_dist),
            "OpenStreetMap streets",
        )

    print(f"\nCollecting data for a {2 * dist / 1000:.1f} x {2 * dist / 1000:.1f} km 3D model of {city}...")
    providers = Providers(
        osm_features=osm_features, osm_graph=osm_graph, overture=use_overture, cache_dir=CACHE_DIR_PATH
    )
    data = collect(frame, providers, want_trees=include_trees, want_rails=include_railways)
    if state["down"]:
        print("⚠ OpenStreetMap (Overpass) did not answer; continuing with the other providers.")
    if not len(data.buildings) and not len(data.roads):
        raise RuntimeError(
            "No map data could be retrieved (OpenStreetMap is unreachable and Overture Maps is unavailable). "
            "Try again later, or install pyarrow to enable Overture Maps."
        )

    if include_terrain:
        try:
            dem = fetch_dem(frame, CACHE_DIR_PATH)
        except Exception as e:
            print(f"⚠ Terrain unavailable ({e}); the model will be flat.")
            dem = Dem.flat(frame)
    else:
        dem = Dem.flat(frame)

    options = SceneOptions(
        exaggeration=exaggeration,
        buildings=include_buildings,
        trees=include_trees,
        rails=include_railways,
        light_bearing=(azimuth + 45.0) % 360.0,
        light_elevation=40.0,
    )
    return build_scene(frame, data, dem, options, title=city)


def create_poster_3d(
    scene,
    city,
    country,
    output_file,
    output_format,
    width=12,
    height=16,
    outputs=("poster", "html"),
    azimuth=225.0,
    elevation=45.0,
    zoom=1.0,
    display_city=None,
    display_country=None,
    fonts=None,
    include_labels=True,
    print_size=200.0,
):
    """
    Render a 3D scene with the current theme and write the requested outputs.

    Args:
        scene: Scene from build_scene_3d()
        output_file: Path for the poster image; other outputs reuse its name with their own extension
        output_format: 'png', 'svg' or 'pdf' for the poster image
        outputs: Any of 'poster' (rendered image with title), 'html' (interactive viewer),
                 'glb' (colored 3D model) and 'stl' (print-ready geometry)
        azimuth / elevation / zoom: Camera for the poster image and the viewer's starting view
        print_size: Width of the STL model in millimetres
    """
    from map3d.export import export_glb, export_stl
    from map3d.render import Camera, render_scene
    from map3d.scene import credits_text
    from map3d.viewer import export_html

    display_city = display_city or city
    display_country = display_country or country
    base = os.path.splitext(output_file)[0]
    credits = credits_text(scene)
    written = []

    if "poster" in outputs:
        print("Rendering 3D poster...")
        fig, ax = render_scene(scene, THEME, width, height, Camera(azimuth, elevation, zoom=zoom))
        add_poster_labels(ax, display_city, display_country, fonts, width, height, include_labels)
        fmt = output_format.lower()
        save_kwargs = dict(facecolor=THEME["bg"])
        if fmt == "png":
            save_kwargs["dpi"] = 300
        plt.savefig(output_file, format=fmt, **save_kwargs)
        plt.close(fig)
        written.append(output_file)

    if "html" in outputs:
        written.append(str(export_html(
            scene, THEME, base + ".html", title=display_city, subtitle=display_country,
            azimuth=azimuth, elevation=elevation, credits=credits,
        )))
    if "glb" in outputs:
        written.append(str(export_glb(scene, THEME, base + ".glb", credits=credits)))
    if "stl" in outputs:
        written.append(str(export_stl(scene, base + ".stl", size_mm=print_size)))

    for path in written:
        print(f"✓ Saved {path} ({os.path.getsize(path) / 1_048_576:.1f} MB)")


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "New York" --country "USA" -l 40.776676 -73.971321 --theme neon_cyberpunk
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        "--All-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=None,
        help=f"Map radius in meters (default: {DEFAULT_DISTANCE_2D}, or {DEFAULT_DISTANCE_3D} with --3d)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20 )",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument(
        "--list-themes", action="store_true", help="List all available themes"
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument(
        "--no-railways",
        dest="railways",
        action="store_false",
        help="Disable the railway line layer (enabled by default)",
    )
    parser.add_argument(
        "--no-buildings",
        dest="buildings",
        action="store_false",
        help="Disable the building footprint layer (enabled by default)",
    )
    parser.add_argument(
        "--no-labels",
        dest="labels",
        action="store_false",
        help="Disable the city/country title text (useful for tiled/panorama panels)",
    )
    parser.add_argument(
        "--no-gradient",
        dest="gradient",
        action="store_false",
        help="Disable the top/bottom vignette fade (useful for tiled/panorama panels, "
             "where each panel's fade would otherwise double up at the seams)",
    )
    parser.add_argument(
        "--3d",
        dest="three_d",
        action="store_true",
        help="Build a 3D city model (terrain, extruded buildings, trees, bridges) instead of a flat poster",
    )
    parser.add_argument(
        "--3d-outputs",
        dest="outputs",
        type=str,
        default="poster,html",
        help="With --3d: comma-separated outputs from poster (rendered image), html (interactive viewer), "
             "glb (colored 3D model), stl (3D-printing geometry). Default: poster,html",
    )
    parser.add_argument(
        "--azimuth",
        type=float,
        default=225.0,
        help="With --3d: compass bearing of the camera in degrees; 180 looks north from the south (default: 225)",
    )
    parser.add_argument(
        "--elevation",
        type=float,
        default=45.0,
        help="With --3d: camera height angle in degrees, 90 is straight down (default: 45)",
    )
    parser.add_argument(
        "--exaggeration",
        type=float,
        default=1.5,
        help="With --3d: vertical exaggeration of terrain, buildings and trees (default: 1.5)",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="With --3d: >1 crops in on the model in the rendered poster (default: 1)",
    )
    parser.add_argument(
        "--print-size",
        dest="print_size",
        type=float,
        default=200.0,
        help="With --3d and the stl output: width of the printed model in millimetres (default: 200)",
    )
    parser.add_argument(
        "--no-trees",
        dest="trees",
        action="store_false",
        help="With --3d: leave out trees",
    )
    parser.add_argument(
        "--no-terrain",
        dest="terrain",
        action="store_false",
        help="With --3d: flat ground instead of real elevation",
    )
    parser.add_argument(
        "--no-overture",
        dest="overture",
        action="store_false",
        help="With --3d: use OpenStreetMap only, skip Overture Maps footprints/heights/land cover",
    )
    parser.set_defaults(railways=True, buildings=True, labels=True, gradient=True, trees=True, terrain=True,
                        overture=True)

    args = parser.parse_args()

    # If no arguments provided, show examples
    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    # List themes if requested
    if args.list_themes:
        list_themes()
        sys.exit(0)

    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    # Enforce maximum dimensions
    if args.width > 20:
        print(
            f"⚠ Width {args.width} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.width = 20.0
    if args.height > 20:
        print(
            f"⚠ Height {args.height} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.height = 20.0

    outputs_3d = [o.strip().lower() for o in args.outputs.split(",") if o.strip()]
    if args.three_d:
        unknown = [o for o in outputs_3d if o not in THREE_D_OUTPUTS]
        if unknown or not outputs_3d:
            print(f"Error: unknown --3d-outputs {unknown or args.outputs!r}. Choose from: {', '.join(THREE_D_OUTPUTS)}")
            sys.exit(1)
        if not 5 <= args.elevation <= 90:
            print("Error: --elevation must be between 5 and 90 degrees.")
            sys.exit(1)
        if not 0.1 <= args.exaggeration <= 8:
            print("Error: --exaggeration must be between 0.1 and 8.")
            sys.exit(1)
        if args.zoom <= 0:
            print("Error: --zoom must be positive.")
            sys.exit(1)
    if args.distance is None:
        args.distance = DEFAULT_DISTANCE_3D if args.three_d else DEFAULT_DISTANCE_2D
    if args.three_d and args.distance > 4000:
        print(f"⚠ A {args.distance} m radius is large for a 3D model: expect heavy downloads, slow builds and "
              f"hundreds of thousands of buildings. {DEFAULT_DISTANCE_3D}-2500 m works best.")

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    # Load custom fonts if specified
    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    # Get coordinates and generate poster
    try:
        if args.latitude and args.longitude:
            lat = parse(args.latitude)
            lon = parse(args.longitude)
            coords = [lat, lon]
            print(f"✓ Coordinates: {', '.join([str(i) for i in coords])}")
        else:
            coords = get_coordinates(args.city, args.country)

        if args.three_d:
            # The 3D scene does not depend on the theme: build it once, then render it per theme
            scene_3d = build_scene_3d(
                args.city,
                coords,
                args.distance,
                exaggeration=args.exaggeration,
                azimuth=args.azimuth,
                include_buildings=args.buildings,
                include_trees=args.trees,
                include_railways=args.railways,
                include_terrain=args.terrain,
                use_overture=args.overture,
            )

        for theme_name in themes_to_generate:
            THEME = load_theme(theme_name)
            if args.three_d:
                output_file = generate_output_filename(args.city, f"{theme_name}_3d", args.format)
                create_poster_3d(
                    scene_3d,
                    args.city,
                    args.country,
                    output_file,
                    args.format,
                    args.width,
                    args.height,
                    outputs=outputs_3d,
                    azimuth=args.azimuth,
                    elevation=args.elevation,
                    zoom=args.zoom,
                    display_city=args.display_city or args.city,
                    display_country=args.display_country or args.country_label or args.country,
                    fonts=custom_fonts,
                    include_labels=args.labels,
                    print_size=args.print_size,
                )
                continue
            output_file = generate_output_filename(args.city, theme_name, args.format)
            create_poster(
                args.city,
                args.country,
                coords,
                args.distance,
                output_file,
                args.format,
                args.width,
                args.height,
                country_label=args.country_label,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
                include_railways=args.railways,
                include_buildings=args.buildings,
                include_labels=args.labels,
                include_gradient=args.gradient,
            )

        print("\n" + "=" * 50)
        print("✓ 3D model generation complete!" if args.three_d else "✓ Poster generation complete!")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
