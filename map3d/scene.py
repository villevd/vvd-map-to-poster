"""
Scene assembly: provider data + terrain -> a theme-independent set of meshes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from . import buildings as bld
from . import ground as gnd
from .data import CityData
from .geo import LocalFrame, polygonal
from .mesh import Mesh, Scene
from .terrain import Dem


@dataclass
class SceneOptions:
    exaggeration: float = 1.5        # vertical exaggeration of terrain, buildings and trees
    buildings: bool = True
    roads: bool = True
    rails: bool = True
    trees: bool = True
    shadows: bool = True             # bake ground shadows (used by the PNG renderer)
    light_bearing: float = 300.0     # compass bearing the light comes from (deg)
    light_elevation: float = 42.0    # deg above the horizon
    max_trees: int = 30000
    seed: int = 7


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


def build_scene(frame: LocalFrame, data: CityData, dem: Dem, opts: SceneOptions, title: str = "",
                log=print) -> Scene:
    t0 = time.time()
    half, side = frame.half, frame.side
    tile = frame.tile_polygon()
    exag = opts.exaggeration
    step = max(3.0, side / 450.0)                        # terrain / overlay tessellation
    lift = 0.25 + side * 8e-5                            # roads stand slightly proud of the ground
    base = 0.045 * side                                  # thickness of the model under the lowest ground
    rng = np.random.default_rng(opts.seed)

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
    dem_b = gnd.burn_water(dem, water, recess=1.2 / max(exag, 0.1))

    def ground_z(x, y):
        return (dem_b.sample(x, y).astype(np.float64) - zmin) * exag + base

    meshes: list[Mesh] = []
    info: dict = {"sources": data.sources, "terrain": dem.source, "exaggeration": exag}

    # ---- buildings ------------------------------------------------------------------------------
    b_geom = None
    heights = None
    if opts.buildings and len(data.buildings):
        heights = bld.resolve_heights(data.buildings)
        tint = bld.building_tint(data.buildings)
        b_geom = bld.build_buildings(data.buildings, heights, ground_z, exag, tint)
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
    if opts.roads and data.roads is not None and len(data.roads):
        road_polys = gnd.road_polygons(_lines_by_class(data.roads, bridge=False), road_scale, tile, water)
    road_union = _union(np.asarray(list(road_polys.values()), dtype=object)) if road_polys else None

    # ---- trees ----------------------------------------------------------------------------------------
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
        pos = gnd.plan_trees(forest, parks, data.trees, opts.max_trees, avoid, rng, half)
        if len(pos):
            v, f, tint_t, tree_tops, tree_pos = gnd.tree_geometry(pos, ground_z, exag, rng)
            tree_mesh = Mesh(name="trees", role="tree", vertices=v.astype(np.float32), faces=f,
                             tint=tint_t.astype(np.float32), flat=True, casts_shadow=True)
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
    terrain, boundary_pts = gnd.terrain_meshes(half, step, land, shadow, ground_z)
    meshes = terrain + meshes
    log(f"✓ Terrain mesh: {sum(m.n_faces for m in terrain):,} triangles")

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

    # ---- base frame -----------------------------------------------------------------------------------------------------
    ledge, plinth = 0.03 * side, 0.035 * side
    meshes.extend(gnd.frame_meshes(half, boundary_pts, ground_z, ledge, plinth))

    zs = np.concatenate([m.vertices[:, 2] for m in meshes if m.n_vertices])
    scene = Scene(
        meshes=meshes, half=half, z_range=(float(zs.min()), float(zs.max())),
        center_lonlat=(frame.lon, frame.lat), title=title, info=info,
        light_bearing=opts.light_bearing, light_elevation=opts.light_elevation,
    )
    scene.info["ledge"] = ledge
    scene.info["relief_m"] = float(_zmax - zmin)
    log(f"✓ Scene ready: {scene.n_faces:,} triangles in {time.time() - t0:.1f}s")
    return scene
