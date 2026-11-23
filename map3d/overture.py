"""
Overture Maps provider (optional, needs ``pyarrow``).

Overture conflates OpenStreetMap with Microsoft and Google ML building
footprints, Esri community data, ESA WorldCover land cover and more, and
publishes them as GeoParquet on a public S3 bucket. We read just the
files/rows that intersect the tile - no API key, no account.

Everything here degrades gracefully: a missing ``pyarrow`` or a network
problem logs one line and returns ``None``, and the rest of the 3D pipeline
carries on with the other providers.
"""

from __future__ import annotations

import hashlib
import io
import pickle
import time
from pathlib import Path

import geopandas as gpd
import requests
import shapely

STAC_CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
STAC_INDEX_URL = "https://stac.overturemaps.org/{release}/collections.parquet"
S3_REGION = "us-west-2"
CACHE_TTL_DAYS = 30
USER_AGENT = "city_map_poster-3d (overture maps reader)"

_index_cache: dict = {}


def is_available() -> bool:
    """True when ``pyarrow`` can be imported."""
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def _latest_release(session: requests.Session) -> str:
    r = session.get(STAC_CATALOG_URL, timeout=30)
    r.raise_for_status()
    return r.json()["latest"]


def _stac_index(release: str, session: requests.Session):
    if release not in _index_cache:
        import pyarrow.parquet as pq
        r = session.get(STAC_INDEX_URL.format(release=release), timeout=120)
        r.raise_for_status()
        _index_cache[release] = pq.read_table(io.BytesIO(r.content))
    return _index_cache[release]


def _cache_file(cache_dir: Path, otype: str, bbox, columns) -> Path:
    key = hashlib.sha1(f"{otype}|{[round(v, 4) for v in bbox]}|{sorted(columns)}".encode()).hexdigest()[:16]
    return cache_dir / f"overture_{otype}_{key}.pkl"


def fetch_layer(otype: str, bbox: tuple[float, float, float, float], columns: list[str],
                cache_dir: str | Path = "cache", log=print) -> gpd.GeoDataFrame | None:
    """
    Rows of one Overture feature type inside ``bbox`` (west, south, east, north).

    Returns a lon/lat GeoDataFrame with the requested columns that exist in the
    schema, or ``None`` when the layer is unavailable.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = _cache_file(cache_dir, otype, bbox, columns)
    if cached.exists() and time.time() - cached.stat().st_mtime < CACHE_TTL_DAYS * 86400:
        try:
            with open(cached, "rb") as f:
                return pickle.load(f)
        except Exception:
            cached.unlink(missing_ok=True)

    if not is_available():
        log("⚠ Overture Maps skipped (optional dependency missing: pip install pyarrow)")
        return None

    try:
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
        import pyarrow.fs as pafs

        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        release = _latest_release(session)
        index = _stac_index(release, session)
        west, south, east, north = bbox

        items = index.filter((pc.field("collection") == otype) & (pc.field("type") == "Feature"))
        items = items.filter(
            (pc.field("bbox", "xmin") < east) & (pc.field("bbox", "xmax") > west)
            & (pc.field("bbox", "ymin") < north) & (pc.field("bbox", "ymax") > south)
        )
        paths = [a["aws"]["alternate"]["s3"]["href"][len("s3://"):] for a in items.column("assets").to_pylist()]
        if not paths:
            return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")

        dataset = ds.dataset(
            paths, filesystem=pafs.S3FileSystem(anonymous=True, region=S3_REGION, connect_timeout=15, request_timeout=90)
        )
        wanted = [c for c in columns if c in dataset.schema.names]
        flt = (
            (pc.field("bbox", "xmin") < east) & (pc.field("bbox", "xmax") > west)
            & (pc.field("bbox", "ymin") < north) & (pc.field("bbox", "ymax") > south)
        )
        df = dataset.to_table(columns=wanted, filter=flt).to_pandas()
        geom = shapely.from_wkb(df.pop("geometry").values)
        gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
        gdf.attrs["release"] = release
    except Exception as e:  # network, S3, schema drift: never fatal
        log(f"⚠ Overture Maps layer '{otype}' unavailable: {e}")
        return None

    try:
        tmp = cached.with_suffix(".part")
        with open(tmp, "wb") as f:
            pickle.dump(gdf, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(cached)
    except Exception:
        pass
    return gdf
