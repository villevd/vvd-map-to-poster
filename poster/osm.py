"""OpenStreetMap downloads (streets, features, coordinates) with the Overpass fallback."""

import asyncio
import osmnx as ox
import requests
import time
from geopandas import GeoDataFrame
from geopy.geocoders import Nominatim
from networkx import MultiDiGraph
from typing import cast
from .cache import CacheError, cache_get, cache_set
from .settings import OVERPASS_MIRRORS


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


def _overpass_reachable(timeout=8):
    """
    Probe the Overpass servers in turn. The first one that answers becomes the endpoint OSMnx uses.
    Returns False only when none of them answers.
    """
    main = ox.settings.overpass_url.rstrip("/")
    candidates = [main] + [m for m in OVERPASS_MIRRORS if m.rstrip("/") != main]
    for base in candidates:
        try:
            r = requests.get(base + "/status", timeout=timeout, headers={"User-Agent": "city_map_poster"})
            ok = r.status_code == 200
        except requests.RequestException:
            ok = False
        if ok:
            if base != main:
                print(f"  ↪ Overpass: using the mirror {base}")
            ox.settings.overpass_url = base
            return True
        print(f"  ↪ Overpass: {base} did not answer")
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
