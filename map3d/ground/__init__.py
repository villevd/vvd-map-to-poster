"""Everything that sits on the ground: the terrain mesh itself, land-cover
patches, roads, rails, bridges, trees and the model's base frame.

The terrain is *one* conforming Delaunay triangulation. Land-cover polygon
edges are densified and inserted as vertices, interior grid points are kept
away from them, so triangle edges follow the polygon outlines. Each triangle
is then classified by its centroid. The result has crisp water / park / forest
shores, no cracks between layers, and smooth shading across the whole surface."""

from .terrain import (
    ROAD_ORDER,
    ROAD_WIDTH,
    ZFunc,
    _boundaries,
    burn_water,
    conforming_triangulation,
    terrain_meshes,
)
from .roads import (
    bridge_meshes,
    drape_polygons,
    road_polygons,
    split_shadow,
)
from .trees import (
    TREE_VARIANTS,
    _TREE_KINDS,
    _crown,
    _outline_tree,
    _smoothstep,
    feathered_points,
    plan_trees,
    tree_geometry,
)
from .shadows import (
    shadow_union,
)
from .frame import (
    frame_meshes,
)
