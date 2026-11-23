"""Local aircraft library for the kiosk API.

Each entry is a low-poly GLB built from the type's dimensions and configuration
(engine count and placement, wing position, tail type, gear), so a 737 looks like
a 737 and a Dash 8 like a Dash 8 without any photogrammetry. The figures are
approximate, written from published general specifications: verify them before
anything public depends on them. A real model can replace a generated one by
dropping a GLB into the library folder and editing its catalog entry.

Axes (metres, glTF convention): nose along +X, up +Y, left wing towards -Z.
Origin: on the ground, under the middle of the fuselage (lowest point of the gear).

    python -m map3d.aircraft planes      # (re)build planes/*.glb and planes/catalog.json"""

from .spec import (
    ALL_SPECS,
    AXES,
    BODY,
    ENGINE,
    FORMAT,
    FORMAT_VERSION,
    GEAR,
    GENERIC_SPECS,
    LibraryError,
    PROP,
    ROTOR,
    SPECS,
    Spec,
    WING,
    _spec,
)
from .parts import (
    Part,
    _blade,
    _body_profile,
    _box,
    _cylinder,
    _fixed_wing_parts,
    _fuselage,
    _heli_parts,
    _hex,
    _loft,
    _merge,
    _nacelle,
    _orient,
    _prism,
    _translate,
    build_parts,
)
from .gltf import (
    _pack,
    _srgb_to_linear,
    _to_gltf,
    write_glb,
)
from .library import (
    Library,
    _engine_count,
    _entry,
    build_library,
    main,
)
