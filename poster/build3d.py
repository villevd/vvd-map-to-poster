"""The 3-D scene build and 3-D poster outputs."""

import functools
import hashlib
import json
import matplotlib.pyplot as plt
import os
from pathlib import Path
from .cache import CacheError, _cache_digest, _cache_try_get, _cache_try_set, cache_get
from .osm import _features_cache_key, _fetch_with_retries, _graph_cache_key, fetch_features, fetch_graph
from .render2d import add_poster_labels
from .settings import CACHE_DIR_PATH, FILE_ENCODING, THEMES_DIR
from .themes import THEME, get_available_themes


_MAP3D_DIR = Path(__file__).resolve().parent.parent / "map3d"


def _code_stamp(*patterns: str) -> str:
    """Fingerprint of the 3D source files matching ``patterns`` (relative to map3d/)."""
    h = hashlib.sha1()
    files = sorted({f for pat in patterns for f in _MAP3D_DIR.glob(pat) if f.suffix in (".py", ".js")})
    for f in files:
        h.update(f.relative_to(_MAP3D_DIR).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


@functools.lru_cache(maxsize=1)
def _map3d_stamp() -> str:
    """Fingerprint of all the 3D code: a finished model is reused only while the code that made it is unchanged."""
    return _code_stamp("*", "data/*")


@functools.lru_cache(maxsize=1)
def _data_stamp() -> str:
    """Fingerprint of the code that turns downloads into map data. Editing the scene, traffic or viewer code leaves
    the processed data valid, so a rebake after such an edit skips the download parsing."""
    return _code_stamp("data/*", "geo.py", "overture.py")


@functools.lru_cache(maxsize=1)
def _dem_stamp() -> str:
    """Fingerprint of the code that builds the elevation grid."""
    return _code_stamp("terrain.py", "geo.py")


def build_scene_3d(
    city,
    point,
    dist,
    exaggeration=1.5,
    azimuth=225.0,
    building_exaggeration=None,
    include_buildings=True,
    include_trees=True,
    include_railways=True,
    include_terrain=True,
    use_overture=True,
    include_traffic=True,
    tiles=1,
    include_underground=True,
):
    """
    Build the 3D scene, or load the identical one built earlier.

    Three cache levels, fastest first: the finished scene (same area and settings: instant), the processed
    map data and terrain (same area, other heights or layers: skips every download and the parsing), and the
    raw downloads (OpenStreetMap, Overture, elevation tiles), which never expire.
    """
    lat, lon = point
    options = dict(exaggeration=exaggeration, azimuth=azimuth, building_exaggeration=building_exaggeration,
                   include_buildings=include_buildings, include_trees=include_trees, include_railways=include_railways,
                   include_terrain=include_terrain, use_overture=use_overture, include_traffic=include_traffic,
                   tiles=tiles, include_underground=include_underground)
    key = "scene3d_" + _cache_digest(_map3d_stamp(), round(float(lat), 5), round(float(lon), 5), float(dist), city,
                                      sorted(options.items(), key=lambda kv: kv[0]))
    scene = _cache_try_get(key)
    if scene is not None:
        print(f"Step 1/1 - This exact model was built before: loaded from the cache in an instant "
              f"({scene.n_faces:,} triangles).")
        return scene
    scene = _build_scene_3d_impl(city, point, dist, **options)
    _cache_try_set(key, scene)
    return scene


def _build_scene_3d_impl(
    city,
    point,
    dist,
    exaggeration=1.5,
    azimuth=225.0,
    building_exaggeration=None,
    include_buildings=True,
    include_trees=True,
    include_railways=True,
    include_terrain=True,
    use_overture=True,
    include_traffic=True,
    tiles=1,
    include_underground=True,
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
        dist: Half the side of one square tile, in meters
        tiles: Build a tiles x tiles grid (1 = a single model). Grids get detail levels in the viewer
        exaggeration: Vertical exaggeration of the terrain
        azimuth: Compass bearing of the camera; ground shadows are baked for a light placed to its left
        building_exaggeration: Vertical exaggeration of buildings and trees; None uses ``exaggeration``
    """
    from map3d.data import Providers, collect
    from map3d.geo import LocalFrame
    from map3d.scene import SceneOptions, build_scene, build_tiled_scene
    from map3d.terrain import Dem, fetch_dem

    lat, lon = point
    total = dist * tiles                     # half the side of the whole area
    frame = LocalFrame(lat, lon, total)
    osm_dist = total * 1.06      # a little beyond the tile so nothing is cut short at the edges
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

    print(f"\nCollecting data for a {2 * total / 1000:.1f} x {2 * total / 1000:.1f} km 3D model of {city}"
          + (f" ({tiles} x {tiles} tiles)..." if tiles > 1 else "..."))
    providers = Providers(
        osm_features=osm_features, osm_graph=osm_graph, overture=use_overture, cache_dir=CACHE_DIR_PATH
    )
    data_key = "city3d_data_" + _cache_digest(_data_stamp(), round(float(lat), 5), round(float(lon), 5), float(total),
                                               bool(include_trees), bool(include_railways), bool(include_traffic),
                                               bool(use_overture))
    data = _cache_try_get(data_key)
    if data is not None:
        print("Step 1/3 - Map data for this area was processed before: reusing it (no download, no parsing).")
        state_cached = True
    else:
        state_cached = False
        data = collect(frame, providers, want_trees=include_trees, want_rails=include_railways,
                       want_traffic=include_traffic)
    if state["down"]:
        print("⚠ OpenStreetMap (Overpass) did not answer; continuing with the other providers.")
    elif not state_cached and (len(data.buildings) or len(data.roads)):
        _cache_try_set(data_key, data)       # only a complete download is worth keeping
    if not len(data.buildings) and not len(data.roads):
        raise RuntimeError(
            "No map data could be retrieved (OpenStreetMap is unreachable and Overture Maps is unavailable). "
            "Try again later, or install pyarrow to enable Overture Maps."
        )

    if include_terrain:
        dem_key = "city3d_dem_" + _cache_digest(_dem_stamp(), round(float(lat), 5), round(float(lon), 5), float(total))
        dem = _cache_try_get(dem_key)
        try:
            if dem is None:
                print("Step 2/3 - Downloading elevation data (terrain heights)...")
                dem = fetch_dem(frame, CACHE_DIR_PATH)
                _cache_try_set(dem_key, dem)
            else:
                print("Step 2/3 - Terrain heights for this area were loaded from the cache.")
        except Exception as e:
            print(f"⚠ Terrain unavailable ({e}); the model will be flat.")
            dem = Dem.flat(frame)
    else:
        dem = Dem.flat(frame)

    options = SceneOptions(
        exaggeration=exaggeration,
        building_exaggeration=building_exaggeration,
        buildings=include_buildings,
        trees=include_trees,
        rails=include_railways,
        traffic=include_traffic,
        light_bearing=(azimuth + 45.0) % 360.0,
        light_elevation=40.0,
    )
    if tiles > 1:
        scene = build_tiled_scene(frame, data, dem, options, tiles, title=city)
    else:
        scene = build_scene(frame, data, dem, options, title=city)
    if include_underground:
        # Geology, mines, drill holes and claims for the cutaway view. Optional: any failure just leaves it out
        from map3d.underground import fetch_underground
        print("Step 3/3 - Fetching what is underground (geology, mines, drill holes, claims)...")
        try:
            ug = fetch_underground(frame, CACHE_DIR_PATH, osm_features=osm_features)
        except Exception as e:
            print(f"⚠ Underground data skipped ({e}).")
            ug = None
        if ug:
            scene.info["underground"] = ug
    return scene


def _all_themes():
    """Every theme in themes/ as {id: dict}, so the 3D viewer can switch between them live."""
    out = {}
    for name in get_available_themes():
        try:
            with open(os.path.join(THEMES_DIR, f"{name}.json"), "r", encoding=FILE_ENCODING) as f:
                out[name] = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return out


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
    theme_name="",
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
    from map3d.viewer import export_db, export_html

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
        print("Writing the interactive 3D page: compressing the model, adding every theme, embedding traffic and search...")
        written.append(str(export_html(
            scene, THEME, base + ".html", title=display_city, subtitle=display_country,
            azimuth=azimuth, elevation=elevation, credits=credits,
            themes=_all_themes(), theme_id=theme_name,
        )))
    if "glb" in outputs:
        written.append(str(export_glb(scene, THEME, base + ".glb", credits=credits)))
    if "stl" in outputs:
        written.append(str(export_stl(scene, base + ".stl", size_mm=print_size)))
    if "db" in outputs:
        # The baked map: the viewer's data plus the exports made above, in one file the GUI opens directly
        print("Writing the baked map (.db): viewer data, every theme and the exports in one file...")
        assets = {}
        if "poster" in outputs and output_format.lower() == "png":
            assets["png"] = output_file
        if "glb" in outputs:
            assets["glb"] = base + ".glb"
        if "stl" in outputs:
            assets["stl"] = base + ".stl"
        written.append(str(export_db(
            scene, THEME, base + ".db", title=display_city, subtitle=display_country,
            azimuth=azimuth, elevation=elevation, credits=credits,
            themes=_all_themes(), theme_id=theme_name, assets=assets,
        )))

    for path in written:
        print(f"✓ Saved {path} ({os.path.getsize(path) / 1_048_576:.1f} MB)")
