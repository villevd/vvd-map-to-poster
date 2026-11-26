# Browser code for the 3D maps

The viewer and the GUI are built from these files. Edit the part you need; Python joins the parts
at import time, so the page is still one file and every part shares one scope.

| Page | Shell (HTML) | Style | Script parts (joined in name order) |
|------|--------------|-------|--------------------------------------|
| 3D viewer (`map3d/viewer.py`) | `viewer/shell.html` | `viewer/style.css` | `viewer/js/*.js` (module script) |
| Local GUI (`map3d/gui.py`) | `gui/shell.html` | `gui/style.css` | `gui/js/*.js` (classic script) |

Rules that keep parts independent:

- Parts are joined in file-name order, so keep the `NN_` prefix. A part may use names defined in an
  earlier part, never in a later one.
- Each viewer part starts with a `// ----` heading: that heading is the feature it owns (renderer,
  themes, imagery, traffic, chunks, search, weather, ...). Work on one feature in one part.
- Shared state (`theme`, `root`, `REAL`, `CFG`) is defined in the earliest parts; do not reassign it elsewhere.
- After any change, run `python -m pytest test`, and `node --check` on the module script if you
  want a syntax check without a browser.

## Underground cutaway
`04b_cut.js` holds the shared cut region and `cutMaterial()` (any material that should vanish inside the cut calls it);
`09b_underground.js` builds the block of earth (strata walls, slice or pit, mines, drill holes, claims, esker, water table) from
`CFG.underground`, which `map3d/underground.py` fetches. Everything it draws lives under `root`.
