"""
Scene assembly: provider data + terrain -> a theme-independent set of meshes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from . import buildings as bld
from . import ground as gnd
from .data import CityData, tile_data
from .geo import LocalFrame, polygonal
from .mesh import Mesh, Scene
from .terrain import Dem
from .traffic import build_traffic
from .airport import build_airport


@dataclass
class SceneOptions:
    exaggeration: float = 1.5        # vertical exaggeration of the terrain (and bridges)
    building_exaggeration: float | None = None   # vertical exaggeration of buildings and trees; None = same as terrain
    buildings: bool = True
    roads: bool = True
    rails: bool = True
    traffic: bool = True             # cars that drive the streets and stop at real signals and stop signs
    airport: bool = True             # the runway and the approach path a fake plane can land on
    trees: bool = True
    shadows: bool = True             # bake ground shadows (used by the PNG renderer)
    light_bearing: float = 300.0     # compass bearing the light comes from (deg)
    light_elevation: float = 42.0    # deg above the horizon
    max_trees: int = 30000
    seed: int = 7
    zmin: float | None = None        # ground datum (m above sea level); shared by all tiles of a tiled model
    detail: int = 0                  # level of detail: 0 full, 1 medium, 2 far
    frame_sides: tuple = (True, True, True, True)   # which tile edges get a skirt/plinth (S, E, N, W)


TERRAIN_CREDIT = "Terrain: AWS Terrain Tiles (SRTM, NRCan CDEM, USGS 3DEP and others)"


def credits_text(scene: Scene) -> str:
    """Data attribution line for the data providers that actually contributed to ``scene``."""
    src = scene.info.get("sources", {}) or {}
    names: list[str] = []
    for key in ("buildings", "landcover", "roads"):
        for n in src.get(key, []):
            n = n.split(" 20")[0]                       # drop the Overture release suffix
            if n == "OpenStreetMap":
                n = "© OpenStreetMap contributors"
            if n not in names:
                names.append(n)
    if "Overture Maps" in names:
        names[names.index("Overture Maps")] = "Overture Maps Foundation"
    if scene.info.get("terrain", "").startswith("AWS"):
        names.append(TERRAIN_CREDIT)
    return " · ".join(names)


ELEVATION_GRID_N = 41


def elevation_grid(dem, half: float, n: int = ELEVATION_GRID_N) -> dict:
    """Terrain elevation (metres above sea level) on an n x n grid over the tile.

    ``values_m[row][col]``: row 0 is the southern edge, col 0 the western edge.
    """
    xs = np.linspace(-half, half, n)
    X, Y = np.meshgrid(xs, xs)
    z = np.asarray(dem.sample(X.ravel(), Y.ravel()), dtype=np.float64).reshape(n, n)
    return {"n": n, "half_m": float(half), "values_m": np.round(z, 2).tolist()}


def _union(geoms: np.ndarray) -> BaseGeometry | None:
    if geoms is None or len(geoms) == 0:
        return None
    u = shapely.union_all(geoms)
    return None if u.is_empty else u


def _lines_by_class(roads, bridge: bool) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    if roads is None or len(roads) == 0:
        return out
    sel = roads[roads["bridge"].to_numpy(dtype=bool) == bridge]
    for cls, grp in sel.groupby("cls"):
        out[str(cls)] = np.asarray(grp.geometry.values, dtype=object)
    return out


ROAD_PATH_CLASSES = ("motorway", "primary", "secondary", "tertiary", "residential")
ROAD_PATH_SPACING = 18.0      # metres between sampled points along a road centre line


def road_centerlines(lines: dict, ground_z, tile) -> list[np.ndarray]:
    """Main road centre lines as (n, 3) float32 arrays of (x east, y north, scene height), clipped to the tile."""
    out: list[np.ndarray] = []
    for cls in ROAD_PATH_CLASSES:
        for g in lines.get(cls, []):
            clipped = shapely.intersection(g, tile)
            for part in shapely.get_parts(clipped):
                if part.geom_type != "LineString" or part.length < 6.0:
                    continue
                n = int(part.length // ROAD_PATH_SPACING) + 2
                pts = shapely.line_interpolate_point(part, np.linspace(0.0, part.length, n))
                xy = shapely.get_coordinates(pts)
                z = ground_z(xy[:, 0], xy[:, 1])
                out.append(np.column_stack([xy, z]).astype(np.float32))
    return out


def build_scene(frame: LocalFrame, data: CityData, dem: Dem, opts: SceneOptions, title: str = "",
                log=print) -> Scene:
    t0 = time.time()
    half, side = frame.half, frame.side
    tile = frame.tile_polygon()
    exag = opts.exaggeration                             # terrain
    bexag = exag if opts.building_exaggeration is None else opts.building_exaggeration   # buildings and trees
    detail = max(0, min(2, opts.detail))
    step = max(5.0, side / 450.0) * (1.0, 2.0, 4.0)[detail]   # no finer than the ~6 m source elevation data   # terrain / overlay tessellation
    lift = 0.25 + side * 8e-5                            # roads stand slightly proud of the ground
    base = 0.045 * side                                  # thickness of the model under the lowest ground
    rng = np.random.default_rng(opts.seed)

    log("Step 3/3 - Building the 3D model")
    log("  * Merging water, parks and forests so they never overlap...")
    # ---- land cover polygons, made mutually exclusive -----------------------------------
    water = _union(data.water)
    forest = _union(data.forest)
    parks = _union(data.parks)
    if water is not None:
        water = shapely.intersection(water, tile)
    if forest is not None:
        forest = shapely.intersection(forest, tile)
        if water is not None:
            forest = shapely.difference(forest, water)
    if parks is not None:
        parks = shapely.intersection(parks, tile)
        for other in (water, forest):
            if other is not None:
                parks = shapely.difference(parks, other)
    land = [(name, g) for name, g in (("water", water), ("forest", forest), ("park", parks))
            if g is not None and not g.is_empty]
    land = [(n, g) for n, g in ((n, polygonal([g])[0]) for n, g in land) if g is not None]

    # ---- terrain height function (scene metres) -------------------------------------------
    zmin, _zmax = dem.stats(half)
    if opts.zmin is not None:
        zmin = opts.zmin
    log(f"  * Shaping the terrain from the elevation data ({dem.source}), water carved slightly below the land...")
    dem_b = gnd.burn_water(dem, water, recess=1.2 / max(exag, 0.1))

    def ground_z(x, y):
        return (dem_b.sample(x, y).astype(np.float64) - zmin) * exag + base

    meshes: list[Mesh] = []
    info: dict = {"sources": data.sources, "terrain": dem.source, "exaggeration": exag,
                  "building_exaggeration": bexag}

    # ---- buildings ------------------------------------------------------------------------------
    log("  * Extruding buildings: footprint, walls, roof; heights from the map data or estimated from the type...")
    b_geom = None
    heights = None
    if opts.buildings and len(data.buildings):
        heights = bld.resolve_heights(data.buildings)
        tint = bld.building_tint(data.buildings)
        b_geom = bld.build_buildings(data.buildings, heights, ground_z, bexag, tint, gables=detail == 0,
                                     min_area=(bld.MIN_FOOTPRINT_AREA, 30.0, 120.0)[detail])
        if b_geom.mesh is not None:
            meshes.append(b_geom.mesh)
        info["buildings"] = {
            "count": int(len(data.buildings)), "gabled": b_geom.n_gabled,
            "height_known": int((~heights.estimated).sum()), "height_estimated": int(heights.estimated.sum()),
        }
    log(f"✓ Buildings: {info.get('buildings', {}).get('count', 0)} extruded"
        + (f" ({info['buildings']['gabled']} with pitched roofs, "
           f"{info['buildings']['height_known']} with mapped heights)" if "buildings" in info else ""))

    # ---- roads (polygons), needed early so trees can avoid them ---------------------------------
    road_scale = max(1.0, step / 5.0)
    road_polys: dict[str, BaseGeometry] = {}
    road_lines: dict = {}
    if opts.roads and data.roads is not None and len(data.roads):
        lines = _lines_by_class(data.roads, bridge=False)
        road_lines = dict(lines)
        for cls in ((), ("service",), ("service", "residential"))[detail]:    # far levels keep the main roads only
            lines.pop(cls, None)
        road_polys = gnd.road_polygons(lines, road_scale, tile, water)
    # Road centre lines for the realistic view (traffic, street lights); full detail only
    info["road_paths"] = road_centerlines(road_lines, ground_z, tile) if detail == 0 else []
    road_union = _union(np.asarray(list(road_polys.values()), dtype=object)) if road_polys else None

    # ---- trees ----------------------------------------------------------------------------------------
    log("  * Planting trees in forests and parks, clear of buildings and roads...")
    tree_pos = np.zeros((0, 2))
    tree_tops = np.zeros(0)
    tree_mesh = None
    if opts.trees:
        avoid_parts = []
        if b_geom is not None and len(b_geom.footprints):
            avoid_parts.append(shapely.buffer(_union(b_geom.footprints), 1.5))
        if road_union is not None:
            avoid_parts.append(shapely.buffer(road_union, 1.0))
        if water is not None:
            avoid_parts.append(water)                    # tree fades spill past the shore; keep them out of the lake
        avoid = _union(np.asarray(avoid_parts, dtype=object)) if avoid_parts else None
        # The tree budget follows the tile's area, so density is the same however the area is cut into tiles
        max_trees = max(500, int(opts.max_trees * min(1.0, (side / 3000.0) ** 2)))
        pos = gnd.plan_trees(forest, parks, data.trees, max_trees, avoid, rng, half)
        if len(pos) and detail == 1:
            # Medium distance: camera-facing impostor cards drawn by the viewer instead of 3-D trees
            h_real = rng.uniform(6.0, 13.0, len(pos))
            card = np.column_stack([pos[:, 0], pos[:, 1], ground_z(pos[:, 0], pos[:, 1]), 0.6 * h_real,
                                    h_real * bexag, rng.uniform(0.85, 1.12, len(pos))]).astype(np.float32)
            meshes.append(Mesh(name="tree-impostors", role="tree", vertices=np.zeros((0, 3), np.float32),
                               faces=np.zeros((0, 3), np.int32), tags={"impostors": card}))
        elif len(pos) and detail == 0:
            v, f, tint_t, tree_tops, tree_pos = gnd.tree_geometry(pos, ground_z, bexag, rng)
            tree_mesh = Mesh(name="trees", role="tree", vertices=v.astype(np.float32), faces=f,
                             tint=tint_t.astype(np.float32), flat=True, casts_shadow=True,
                             base=ground_z(v[:, 0], v[:, 1]).astype(np.float32))
            meshes.append(tree_mesh)
        log(f"✓ Trees: {len(pos)}")
        info["trees"] = int(len(pos))

    # ---- shadows -----------------------------------------------------------------------------------------
    shadow = None
    if opts.shadows and b_geom is not None and len(b_geom.footprints):
        # Building shadows only: tree shadows would add tens of thousands of outline vertices for little gain
        shadow = gnd.shadow_union(b_geom.footprints, b_geom.heights_above_ground, opts.light_bearing,
                                  opts.light_elevation, simplify=0.6 * step)
        if shadow is not None:
            shadow = polygonal([shapely.intersection(shadow, tile)])[0]

    # ---- terrain ---------------------------------------------------------------------------------------------
    log("  * Casting building shadows onto the ground and meshing the terrain...")
    terrain, boundary_pts = gnd.terrain_meshes(half, step, land, shadow, ground_z)
    meshes = terrain + meshes
    log(f"✓ Terrain mesh: {sum(m.n_faces for m in terrain):,} triangles")

    log("  * Laying roads and railways on the terrain, lifting bridges over water...")
    # ---- road / rail overlays ----------------------------------------------------------------------------------
    bias = 2.5 * step
    for cls, poly in road_polys.items():
        lit, dark = gnd.split_shadow(poly, shadow)
        for part, flag in ((lit, False), (dark, True)):
            if part is None:
                continue
            v, f = gnd.drape_polygons(np.asarray([part], dtype=object), step, ground_z, lift)
            if len(f):
                meshes.append(Mesh(name=f"road:{cls}", role=f"road_{cls}", vertices=v.astype(np.float32), faces=f,
                                   shadowed=np.full(len(f), flag), flat=True, depth_bias=bias))
    if opts.rails and data.rails is not None and len(data.rails):
        rail_poly = shapely.union_all(shapely.buffer(data.rails, 2.2 * road_scale, cap_style="flat", quad_segs=2))
        rail_poly = shapely.intersection(rail_poly, tile)
        if water is not None:
            rail_poly = shapely.difference(rail_poly, water)
        rail_poly = polygonal([rail_poly])[0]
        if rail_poly is not None:
            v, f = gnd.drape_polygons(np.asarray([rail_poly], dtype=object), step, ground_z, lift * 0.8)
            if len(f):
                meshes.append(Mesh(name="rails", role="rail", vertices=v.astype(np.float32), faces=f,
                                   shadowed=np.zeros(len(f), dtype=bool), flat=True, depth_bias=bias))

    # ---- bridges --------------------------------------------------------------------------------------------------
    n_bridges = 0
    if opts.roads and data.roads is not None and len(data.roads):
        br = data.roads[data.roads["bridge"].to_numpy(dtype=bool)]
        if len(br):
            # Keep only the part of each bridge that is inside the tile
            clipped = shapely.intersection(np.asarray(br.geometry.values, dtype=object), tile)
            lines, classes = [], []
            for g, c in zip(clipped, br["cls"]):
                for part in shapely.get_parts(g):
                    if part.geom_type == "LineString" and part.length > 4.0:
                        lines.append(part)
                        classes.append(c)
            widths = np.array([gnd.ROAD_WIDTH.get(c, 8.0) * road_scale for c in classes])
            for v, f, part in gnd.bridge_meshes(np.asarray(lines, dtype=object), widths, step, ground_z, exag, lift):
                role = "bridge" if part == "deck" else "bridge_side"
                meshes.append(Mesh(name=f"bridge:{part}", role=role, vertices=v.astype(np.float32), faces=f,
                                   flat=True, casts_shadow=True, depth_bias=bias if part == "deck" else 0.0))
            n_bridges = len(lines)
    info["bridges"] = n_bridges

    if opts.traffic and opts.roads:
        log("  * Preparing the traffic network: lanes, junctions, lights and stop signs...")
    # ---- traffic (animated by the viewer, not part of the mesh) ---------------------------------------------------
    if opts.traffic and opts.roads:
        info["traffic"] = build_traffic(data.roads, data.signals, data.stop_signs, ground_z, lift, side, log=log)
    if opts.airport and data.runways:
        info["airport"] = build_airport(data.runways, ground_z, lift, opts.exaggeration, log=log)

    log("  * Adding the base plinth and finishing the model...")
    # ---- base frame -----------------------------------------------------------------------------------------------------
    ledge, plinth = 0.03 * side, 0.035 * side
    meshes.extend(gnd.frame_meshes(half, boundary_pts, ground_z, ledge, plinth, opts.frame_sides))
    for m in meshes:
        m.lod = detail

    zs = np.concatenate([m.vertices[:, 2] for m in meshes if m.n_vertices])
    scene = Scene(
        meshes=meshes, half=half, z_range=(float(zs.min()), float(zs.max())),
        center_lonlat=(frame.lon, frame.lat), title=title, info=info,
        light_bearing=opts.light_bearing, light_elevation=opts.light_elevation,
    )
    scene.info["ledge"] = ledge
    scene.info["relief_m"] = float(_zmax - zmin)
    # What a consumer needs to place things on the model: scene z = (elevation - dem_min_m) * exaggeration + base_m
    scene.info["dem_min_m"] = float(zmin)
    scene.info["base_m"] = float(base)
    scene.info["elevation_grid"] = elevation_grid(dem, half)
    # (scene z of a point = (elevation - dem_min_m) * exaggeration + base_m; buildings and trees add height * building_exaggeration)
    log(f"✓ Scene ready: {scene.n_faces:,} triangles in {time.time() - t0:.1f}s")
    return scene


def build_tiled_scene(frame: LocalFrame, data: CityData, dem: Dem, opts: SceneOptions, tiles: int, title: str = "",
                      lods: bool = True, log=print) -> Scene:
    """
    An ``tiles`` x ``tiles`` model of the area covered by ``frame``.

    Data and elevation cover the whole area in one shared frame, so tile seams line up exactly. Each tile is
    built separately (they stay independent chunks the viewer can load, hide and simplify on their own) at
    up to three levels of detail, all sharing one ground datum. Only the outer edge gets a plinth.
    """
    if tiles <= 1 and not lods:
        return build_scene(frame, data, dem, opts, title, log)
    half_t = frame.half / tiles
    tile_frame = LocalFrame(frame.lat, frame.lon, half_t)
    zmin, zmax = dem.stats(frame.half)
    levels = (0, 1, 2) if lods else (0,)
    quiet = (lambda *_: None)
    meshes: list[Mesh] = []
    road_paths: list[np.ndarray] = []
    t0 = time.time()
    first = None
    for j in range(tiles):
        for i in range(tiles):
            cx, cy = (i - (tiles - 1) / 2) * 2 * half_t, (j - (tiles - 1) / 2) * 2 * half_t
            sub = tile_data(data, cx, cy, half_t)
            sdem = Dem(dem.x0 - cx, dem.y0 - cy, dem.step, dem.z, dem.source)
            sides = (j == 0, i == tiles - 1, j == tiles - 1, i == 0)
            for detail in levels:
                o = replace(opts, zmin=zmin, detail=detail, frame_sides=sides, shadows=opts.shadows and detail == 0,
                            traffic=False, airport=False)   # traffic and runways are built once for the whole area, below
                sc = build_scene(tile_frame, sub, sdem, o, title, log=quiet)
                first = first or sc
                if detail == 0:
                    offset = np.array([cx, cy, 0.0], dtype=np.float32)
                    road_paths.extend(p + offset for p in sc.info.get("road_paths", []))
                for m in sc.meshes:
                    if m.n_vertices:
                        m.vertices = m.vertices + np.array([cx, cy, 0.0], dtype=np.float32)
                    if "impostors" in m.tags:
                        m.tags["impostors"][:, 0] += cx
                        m.tags["impostors"][:, 1] += cy
                    m.tags["chunk"] = (i, j)
                    m.tags["center"] = (cx, cy)
                meshes.extend(sc.meshes)
            log(f"  tile {j * tiles + i + 1}/{tiles * tiles} built ({time.time() - t0:.0f}s)")
    assert first is not None
    zs = np.concatenate([m.vertices[:, 2] for m in meshes if m.n_vertices and m.lod == 0])
    info = dict(first.info)
    info.update({"tiles": tiles, "tile_half": half_t, "dem_min_m": float(zmin), "relief_m": float(zmax - zmin),
                 "base_m": 0.045 * 2 * half_t, "elevation_grid": elevation_grid(dem, frame.half),
                 "road_paths": road_paths})
    if opts.traffic and opts.roads:
        # One road network for the whole area, in the shared frame, so cars drive across tile seams.
        # Same ground height as the tiles: elevation above the datum, exaggerated, plus the tile base.
        base_t = 0.045 * 2 * half_t
        lift_t = 0.25 + 2 * half_t * 8e-5

        def area_ground_z(x, y):
            return (dem.sample(np.asarray(x), np.asarray(y)).astype(np.float64) - zmin) * opts.exaggeration + base_t

        info["traffic"] = build_traffic(data.roads, data.signals, data.stop_signs, area_ground_z, lift_t,
                                        2 * frame.half, log=log)
    if opts.airport and data.runways:
        base_t = 0.045 * 2 * half_t
        lift_t = 0.25 + 2 * half_t * 8e-5

        def area_ground_z_air(x, y):
            return (dem.sample(np.asarray(x), np.asarray(y)).astype(np.float64) - zmin) * opts.exaggeration + base_t

        info["airport"] = build_airport(data.runways, area_ground_z_air, lift_t, opts.exaggeration, log=log)
    scene = Scene(meshes=meshes, half=frame.half, z_range=(float(zs.min()), float(zs.max())),
                  center_lonlat=(frame.lon, frame.lat), title=title, info=info,
                  light_bearing=opts.light_bearing, light_elevation=opts.light_elevation)
    log(f"✓ {tiles}x{tiles} tiles: {sum(m.n_faces for m in meshes if m.lod == 0):,} triangles at full detail, "
        f"{scene.n_faces:,} across all detail levels ({time.time() - t0:.0f}s)")
    return scene
