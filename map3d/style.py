"""
Map the poster themes (flat 2-D colour palettes) onto the roles used by the 3-D
meshes. Existing themes work unchanged; a theme may override any role by adding
the matching key (``ground``, ``forest``, ``tree``, ``building``, ``frame``,
``skirt``, ``bridge``) to its JSON.
"""

from __future__ import annotations

import matplotlib.colors as mcolors

RGB = tuple[float, float, float]


def to_rgb(color: str | RGB) -> RGB:
    return tuple(float(c) for c in mcolors.to_rgb(color))  # type: ignore[return-value]


def luminance(c: RGB) -> float:
    r, g, b = c
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


BLACK: RGB = (0.0, 0.0, 0.0)
WHITE: RGB = (1.0, 1.0, 1.0)


def ensure_contrast(color: RGB, ref: RGB, min_delta: float, toward: RGB) -> RGB:
    """Mix ``color`` towards ``toward`` just enough for its luminance to differ from ``ref`` by ``min_delta``."""
    for k in range(0, 21):
        cand = mix(color, toward, k / 20.0 * 0.9)
        if abs(luminance(cand) - luminance(ref)) >= min_delta:
            return cand
    return mix(color, toward, 0.9)


def role_colors(theme: dict) -> dict[str, RGB]:
    """Resolve every mesh role to an RGB colour (0-1) for ``theme``."""
    def get(key: str, default: RGB) -> RGB:
        return to_rgb(theme[key]) if key in theme else default

    bg = to_rgb(theme["bg"])
    text = to_rgb(theme.get("text", "#000000"))
    light_bg = luminance(bg) > 0.5

    parks = get("parks", mix(bg, text, 0.15))
    water = get("water", mix(bg, (0.2, 0.4, 0.8), 0.3))
    road_default = get("road_default", mix(bg, text, 0.5))
    road_secondary = get("road_secondary", road_default)

    ground = get("ground", mix(bg, text, 0.07))
    # Which way "more contrast" goes: lighter on dark themes, darker on light ones
    push = BLACK if (light_bg and luminance(text) < luminance(bg)) else text
    forest = get("forest", mix(parks, BLACK, 0.12) if light_bg else mix(parks, WHITE, 0.07))
    tree = get("tree", mix(parks, BLACK, 0.32) if luminance(parks) > 0.45 else mix(parks, WHITE, 0.13))
    # The 2-D "building" colour only has to be faintly visible as a footprint; a 3-D block needs to stand out
    if "building_3d" in theme:
        building = to_rgb(theme["building_3d"])
    else:
        building = ensure_contrast(get("building", mix(bg, text, 0.32)), ground, 0.20 if not light_bg else 0.10, push)
    frame = get("frame", (0.11, 0.11, 0.12) if light_bg else mix(bg, text, 0.2))
    skirt = get("skirt", mix(ground, BLACK, 0.4))
    bridge = get("bridge", road_secondary)

    return {
        "ground": ground, "water": water, "forest": forest, "park": parks, "tree": tree, "building": building,
        "road_motorway": get("road_motorway", road_default),
        "road_primary": get("road_primary", road_default),
        "road_secondary": road_secondary,
        "road_tertiary": get("road_tertiary", road_default),
        "road_residential": get("road_residential", road_default),
        "road_service": get("road_residential", road_default),
        "rail": get("railway", road_default),
        "bridge": bridge, "bridge_side": mix(bridge, BLACK, 0.45),
        "frame": frame, "skirt": skirt,
    }


def hex_color(c: RGB) -> str:
    return mcolors.to_hex(c)
