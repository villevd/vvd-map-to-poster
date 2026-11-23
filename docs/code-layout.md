# Code layout

Where things live, so several people or agents can work in parallel without editing the same file.

## Poster (2-D) and shared entry point

- `create_map_poster.py`: thin shim. Keeps the old names importable (`import create_map_poster as cmp`) and runs `poster.cli.main()`.
- `poster/settings.py`: directories, default distances, 3-D output list, Overpass mirrors.
- `poster/cache.py`: on-disk cache for downloads and built models.
- `poster/themes.py`: theme discovery and loading. `THEME` is the active theme, updated in place by `set_active_theme`.
- `poster/osm.py`: OpenStreetMap downloads with the Overpass fallback.
- `poster/render2d.py`: the 2-D poster drawing.
- `poster/build3d.py`: the 3-D scene build and 3-D outputs.
- `poster/cli.py`: command-line arguments and the run.

## 3-D city model (`map3d/`)

- `map3d/data/`: providers and normalisation. `model` (containers and tags), `parse`, `buildings`, `landcover`, `roads`, `collect` (one area, tiled).
- `map3d/ground/`: terrain, roads, trees, shadows and frame meshes. `trees` holds the crown shapes.
- `map3d/aircraft/`: aircraft library. `spec`, `parts` (geometry), `gltf` (binary glTF writer), `library` (build and CLI).
- `map3d/web/`: browser code for the viewer and the GUI (see `map3d/web/README.md`).
- Other modules keep their single files: `scene`, `chunks`, `viewer`, `gui`, `service`, `api`, `traffic`, `render`, `export`, `mesh`, `geo`, `terrain`, `buildings`, `markings`, `airport`, `style`, `overture`.

Each package's `__init__.py` re-exports its names, so existing imports such as `from map3d.data import collect` and `gnd.plan_trees` still work.
