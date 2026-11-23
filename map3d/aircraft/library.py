"""The aircraft library: builds every entry, indexes it and runs the command-line tool."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from .gltf import write_glb
from .parts import build_parts
from .spec import ALL_SPECS, AXES, FORMAT, FORMAT_VERSION, LibraryError, Spec


# ---------------------------------------------------------------------------------------------------------
# Library: build once, serve from disk, resolve ICAO type codes.
# ---------------------------------------------------------------------------------------------------------

def _engine_count(spec: Spec) -> int:
    if spec.mount == "heli":
        return 0
    if spec.mount == "nose":
        return 1
    return 2 * len(spec.engines)


def _entry(spec: Spec, path: Path) -> dict:
    data = path.read_bytes()
    return {
        "id": spec.id,
        "codes": list(spec.codes),
        "name": spec.name,
        "manufacturer": spec.maker,
        "category": spec.category,
        "generic": spec.generic,
        "dimensions_m": {"length": spec.length, "span": spec.span, "height": spec.height},
        "configuration": {"wing": spec.wing, "engines": _engine_count(spec), "mount": spec.mount,
                          "tail": spec.tail, "winglets": spec.winglets, "propeller_blades": spec.blades},
        "livery_accent": spec.accent,
        "source": "procedural",
        "approximate": spec.approx,
        "file": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_library(out_dir: str | Path, specs: tuple[Spec, ...] = ALL_SPECS, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    wanted = set()
    for spec in specs:
        path = out / f"{spec.id}.glb"
        parts = build_parts(spec)
        extras = {"id": spec.id, "name": spec.name, "codes": list(spec.codes), "axes": AXES,
                  "dimensions_m": {"length": spec.length, "span": spec.span, "height": spec.height}}
        write_glb(path, parts, extras)
        entries.append(_entry(spec, path))
        wanted.add(path.name)
        log(f"  {spec.id:<16} {spec.name:<26} {len(parts)} parts  {path.stat().st_size / 1024:6.1f} KB")
    for stale in out.glob("*.glb"):
        if stale.name not in wanted:
            stale.unlink()
    catalog = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "axes": AXES,
        "note": "Procedural models from approximate published dimensions; see map3d/aircraft.py.",
        "entries": entries,
    }
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


class Library:
    """A built library folder: catalog.json plus one GLB per entry."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        try:
            data = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise LibraryError(f"no aircraft catalog in {self.root}: {e}") from e
        if data.get("format") != FORMAT:
            raise LibraryError(f"{self.root}/catalog.json is not an aircraft library")
        self.generated = data.get("generated")
        self.entries: list[dict] = data["entries"]
        self._by_id = {e["id"]: e for e in self.entries}
        self._by_code: dict[str, dict] = {}
        for e in self.entries:
            for code in e["codes"]:
                self._by_code.setdefault(code.upper(), e)

    def get(self, entry_id: str) -> dict | None:
        return self._by_id.get(entry_id)

    def path(self, entry: dict) -> Path:
        return self.root / entry["file"]

    def resolve(self, type_code: str | None, helicopter: bool = False) -> tuple[dict, str]:
        """The model for an ICAO type designator: ("exact", entry) or ("category", generic stand-in)."""
        code = (type_code or "").strip().upper()
        if code and code in self._by_code:
            return self._by_code[code], "exact"
        fallback = self._by_id.get("generic-heli" if helicopter else "generic-jet")
        if fallback is None:
            raise LibraryError("catalog has no generic fallback models")
        return fallback, "category"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build the procedural aircraft library.")
    ap.add_argument("out", nargs="?", default="planes", help="output folder (default: planes)")
    args = ap.parse_args(argv)
    print(f"Building aircraft library in {args.out}")
    catalog = build_library(args.out)
    print(f"{len(catalog['entries'])} models written, catalog at {Path(args.out) / 'catalog.json'}")


if __name__ == "__main__":
    main()
