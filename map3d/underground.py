"""
What is under the model: bedrock geology, mines, deposits, drill holes and mining claims.

Everything here is best effort and never fatal: each source is optional, cached on disk, and a failure just
leaves that part out (the viewer then says "no data"). Nothing is invented; a depth the sources do not give
stays ``None``. The only generic part is the thin topsoil layer, which is labelled as such.

Sources
  * Macrostrat (macrostrat.org, CC-BY 4.0): the mapped bedrock unit under the centre, and the regional column.
  * SIGEOM (Quebec, MERN): metallic deposits, mines and projects, and diamond-drill holes (azimuth, plunge, length).
  * GESTIM (Quebec, MERN): active exclusive exploration rights (claims) and mining leases.
  * USGS MRDS: mineral deposits and mines (mostly the US, patchy elsewhere).
  * OpenStreetMap: mine shafts, adits and quarries (through the caller's cached provider).

Everything is returned in the model's local metres (x east, y north) with elevations in metres above sea level,
so the viewer can place it with the same datum as the terrain.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

USER_AGENT = "city_map_poster-3d (underground layer; https://github.com)"
TIMEOUT = 45
TTL_SECONDS = 7 * 24 * 3600            # claims change daily, geology never: a week is a fair middle
MAX_CLAIMS = 600
MAX_DRILLHOLES = 1500
MAX_SITES = 400

MACROSTRAT = "https://macrostrat.org/api/v2"
SIGEOM_WFS = "https://servicesvectoriels.atlas.gouv.qc.ca/IDS_SGM_EN_WFS/service.svc/get"
GESTIM_WFS = "https://servicesvecto3.mern.gouv.qc.ca/geoserver/GestimPub/wfs"
MRDS = "https://energy.usgs.gov/arcgis/rest/services/Hosted/Mineral_Resource_Data_System/FeatureServer/0/query"

QUEBEC_BOX = (-80.0, 44.5, -57.0, 63.5)   # west, south, east, north: rough extent of the Quebec services

OSM_MINING_TAGS = {
    "man_made": ["mineshaft", "adit", "petroleum_well"],
    "historic": ["mine", "mine_shaft", "mine_adit"],
    "landuse": ["quarry"],
    "industrial": ["mine"],
}

SOURCE_CREDITS = {
    "macrostrat": "Geology: Macrostrat (CC-BY 4.0)",
    "sigeom": "Mines and drill holes: SIGEOM, MERN Quebec",
    "gestim": "Claims: GESTIM, MERN Quebec",
    "mrds": "Mines: USGS MRDS",
    "osm": "Mine workings: OpenStreetMap contributors",
}

# Colours of the generic layers and of rock families, as hex. Bedrock uses the Macrostrat colour when given.
TOPSOIL_COLOR = "#5b3a1e"
OVERBURDEN_COLOR = "#8a6a43"
BEDROCK_COLOR = "#7d7d80"


# ---------------------------------------------------------------------------------------------------------
# Disk cache (JSON, with a time to live). A fresh answer, even an empty one, is not asked for again.
# ---------------------------------------------------------------------------------------------------------

def _cache_path(cache_dir, name: str, *parts) -> Path:
    digest = hashlib.sha1(repr(parts).encode()).hexdigest()[:14]
    return Path(cache_dir) / "underground" / f"{name}_{digest}.json"


def _cache_read(path: Path):
    try:
        if time.time() - path.stat().st_mtime > TTL_SECONDS:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_write(path: Path, value) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get_json(session, url, params, timeout=TIMEOUT):
    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _cached(cache_dir, name, key_parts, fetch, log):
    """Run ``fetch()`` unless a fresh answer is on disk. Failures are logged and give ``None`` (not cached)."""
    path = _cache_path(cache_dir, name, *key_parts)
    hit = _cache_read(path)
    if hit is not None:
        return hit["v"]
    try:
        value = fetch()
    except Exception as e:      # network, HTTP, JSON: this layer is optional
        log(f"⚠ Underground data ({name}) unavailable: {e}")
        return None
    _cache_write(path, {"v": value})
    return value


# ---------------------------------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------------------------------

def _rings(geom):
    """Outer rings of a (Multi)Polygon GeoJSON geometry, as lists of [lon, lat]."""
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def _point(geom):
    if geom and geom.get("type") == "Point":
        return geom["coordinates"][0], geom["coordinates"][1]
    return None


def _simplify_ring(ring, tol=2.0):
    """Drop near-collinear vertices (Douglas-Peucker lite via shapely) and round to decimetres."""
    try:
        import shapely.geometry as sg
        poly = sg.Polygon(ring).simplify(tol, preserve_topology=True)
        if poly.is_empty:
            return None
        pts = list(poly.exterior.coords)
    except Exception:
        pts = ring
    return [[round(x, 1), round(y, 1)] for x, y in pts]


def _clip_ring(ring, half, tol=2.0):
    """The part of a ring inside the model's square (the block of earth ends there), simplified, as [x, y] pairs."""
    try:
        import shapely.geometry as sg
        poly = sg.Polygon(ring).buffer(0).intersection(sg.box(-half, -half, half, half))
        if poly.is_empty:
            return None
        if poly.geom_type != "Polygon":      # a claim cut in two by the edge: keep the larger piece
            poly = max((g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"), key=lambda g: g.area, default=None)
            if poly is None:
                return None
        poly = poly.simplify(tol, preserve_topology=True)
        return [[round(x, 1), round(y, 1)] for x, y in poly.exterior.coords]
    except Exception:
        return None


def _in_box(box, west, south, east, north):
    return not (east < box[0] or west > box[2] or north < box[1] or south > box[3])


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Macrostrat: geology under the centre
# ---------------------------------------------------------------------------------------------------------

_SCALE_RANK = {"large": 3, "medium": 2, "small": 1, "tiny": 0}
_UNCONSOLIDATED = ("till", "sand", "gravel", "silt", "mud", "clay", "alluvium", "colluvium", "loess", "peat")


def _lith_names(lith) -> list[str]:
    if isinstance(lith, str):
        return [s.strip() for s in lith.replace(";", ",").split(",") if s.strip()]
    return [str(item.get("name")) for item in (lith or []) if isinstance(item, dict) and item.get("name")]


def _macrostrat(session, lat, lon, cache_dir, log):
    def fetch():
        mapped = _get_json(session, f"{MACROSTRAT}/geologic_units/map",
                           {"lat": lat, "lng": lon, "format": "json"})["success"]["data"]
        units = _get_json(session, f"{MACROSTRAT}/units",
                          {"lat": lat, "lng": lon, "response": "long", "format": "json"})["success"]["data"]
        return {"map": mapped, "units": units}
    return _cached(cache_dir, "macrostrat", (round(lat, 3), round(lon, 3)), fetch, log)


def _strata_from_macrostrat(raw) -> dict:
    """Surface bedrock (from the geologic map) and the regional column (ordered young to old)."""
    out: dict = {"surface": None, "column": []}
    if not raw:
        return out
    maps = [m for m in raw.get("map", []) if isinstance(m, dict)]
    if maps:
        best = max(maps, key=lambda m: _SCALE_RANK.get(m.get("scale"), -1))
        out["surface"] = {
            "name": best.get("name") or best.get("strat_name") or "Bedrock",
            "lith": best.get("lith") or "",
            "age": best.get("best_int_name") or best.get("t_int_name") or "",
            "t_age_ma": _num(best.get("t_age")), "b_age_ma": _num(best.get("b_age")),
            "color": best.get("color") or BEDROCK_COLOR,
            "scale": best.get("scale"),
        }
    units = sorted((u for u in raw.get("units", []) if isinstance(u, dict)),
                   key=lambda u: _num(u.get("t_age")) or 0.0)
    seen = set()
    for u in units:
        key = u.get("unit_id")
        if key in seen:
            continue
        seen.add(key)
        liths = _lith_names(u.get("lith"))
        thick = [t for t in (_num(u.get("min_thick")), _num(u.get("max_thick"))) if t]
        out["column"].append({
            "name": u.get("strat_name_long") or u.get("unit_name") or "Unnamed",
            "lith": ", ".join(liths[:3]),
            "t_age_ma": _num(u.get("t_age")), "b_age_ma": _num(u.get("b_age")),
            "thick_m": round(sum(thick) / len(thick)) if thick else None,    # often missing: stays None
            "color": u.get("color") or BEDROCK_COLOR,
            "unconsolidated": any(any(w in l.lower() for w in _UNCONSOLIDATED) for l in liths),
        })
    out["column"] = out["column"][:14]
    return out


# ---------------------------------------------------------------------------------------------------------
# SIGEOM / GESTIM / MRDS
# ---------------------------------------------------------------------------------------------------------

def _wfs(session, url, type_name, bbox, count, srs_bbox_order="latlon", extra=None, timeout=TIMEOUT):
    west, south, east, north = bbox
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name,
        "count": count, "outputFormat": "application/json",
    }
    if srs_bbox_order == "latlon":     # SIGEOM: axis order of EPSG:4326, geometry comes back lon/lat
        params["bbox"] = f"{south},{west},{north},{east},urn:ogc:def:crs:EPSG::4326"
    else:                              # GeoServer: lon/lat and ask for lon/lat back
        params["bbox"] = f"{west},{south},{east},{north},EPSG:4326"
        params["srsName"] = "EPSG:4326"
    params.update(extra or {})
    return _get_json(session, url, params, timeout=timeout).get("features", [])


def _sigeom(session, bbox, cache_dir, log):
    if not _in_box(QUEBEC_BOX, *bbox):
        return None

    def fetch():
        def one(item):
            key, name = item
            try:
                return key, _wfs(session, SIGEOM_WFS, f"SGM_EN:{name}", bbox,
                                 MAX_DRILLHOLES if key == "drill" else MAX_SITES)
            except Exception as e:
                log(f"⚠ SIGEOM {name} unavailable: {e}")
                return key, []
        layers = (("deposits", "Metallic_deposits"), ("projects", "Mines_projects_en"),
                  ("nonmetal", "Non_metal_deposits"), ("drill", "Diamond_drillings"),
                  ("esker", "Quat_Generalized_eskers"))
        with ThreadPoolExecutor(len(layers)) as pool:
            return dict(pool.map(one, layers))
    return _cached(cache_dir, "sigeom", ("v2",) + tuple(round(v, 3) for v in bbox), fetch, log)


def _gestim(session, bbox, cache_dir, log):
    if not _in_box(QUEBEC_BOX, *bbox):
        return None

    def fetch():
        def one(item):
            key, name = item
            try:
                return key, _wfs(session, GESTIM_WFS, f"GestimPub:{name}", bbox, MAX_CLAIMS,
                                 srs_bbox_order="lonlat", timeout=75)
            except Exception as e:
                log(f"⚠ GESTIM {name} unavailable: {e}")
                return key, []
        layers = (("claims", "Titre_actif_DEE"), ("leases", "Titre_actif_BM"),
                  ("bulk", "Titre_actif_BEX"), ("concessions", "Titre_actif_CM"))
        with ThreadPoolExecutor(len(layers)) as pool:
            return dict(pool.map(one, layers))
    return _cached(cache_dir, "gestim", tuple(round(v, 3) for v in bbox), fetch, log)


def _mrds(session, bbox, cache_dir, log):
    west, south, east, north = bbox

    def fetch():
        data = _get_json(session, MRDS, {
            "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326, "outFields": "site_name,dev_stat,code_list,dep_id",
            "f": "json", "resultRecordCount": MAX_SITES,
        })
        return [{"name": f["attributes"].get("site_name"), "status": f["attributes"].get("dev_stat"),
                 "commodity": (f["attributes"].get("code_list") or "").strip(), "id": f["attributes"].get("dep_id"),
                 "lon": f["geometry"]["x"], "lat": f["geometry"]["y"]}
                for f in data.get("features", []) if f.get("geometry")]
    return _cached(cache_dir, "mrds", tuple(round(v, 3) for v in bbox), fetch, log)


# ---------------------------------------------------------------------------------------------------------
# Normalisers (pure functions: easy to test with canned responses)
# ---------------------------------------------------------------------------------------------------------

def _status_kind(status: str) -> str:
    s = (status or "").lower()
    if "closed" in s or "past" in s or "abandon" in s or "former" in s or "inactive" in s or "prospect" in s:
        return "closed"
    if "active" in s or "producer" in s or "operating" in s or "open" in s:
        return "active"
    return "other"


def sites_from_sigeom(raw, frame) -> list[dict]:
    out = []
    if not raw:
        return out
    for feat in raw.get("deposits", []) + raw.get("nonmetal", []):
        p, props = _point(feat.get("geometry")), feat.get("properties") or {}
        if not p:
            continue
        x, y = frame.to_xy(*p)
        out.append({
            "name": props.get("NOM_CORPS_MINR") or "Deposit",
            "kind": "mine" if "mine" in str(props.get("ETAT_CORPS_MINR_EN", "")).lower() else "deposit",
            "status": props.get("ETAT_CORPS_MINR_EN") or "", "state": _status_kind(props.get("ETAT_CORPS_MINR_EN")),
            "commodity": props.get("SUBST_PRINC_EN") or props.get("ELMN_CHIM_PERD_EN") or "",
            "year": props.get("AN_DECV"), "depth_m": None, "x": round(x, 1), "y": round(y, 1), "source": "sigeom",
        })
    for feat in raw.get("projects", []):
        p, props = _point(feat.get("geometry")), feat.get("properties") or {}
        if not p:
            continue
        x, y = frame.to_xy(*p)
        status = props.get("SIGN_STAT_MINE_PROJE_ANG") or ""
        out.append({
            "name": props.get("NOM_MINE_PROJE") or "Mine", "kind": "mine", "status": status,
            "state": _status_kind(status), "commodity": props.get("SIGN_MINR_ANG") or "",
            "operator": props.get("NOM_SOCIE") or "", "depth_m": None,
            "x": round(x, 1), "y": round(y, 1), "source": "sigeom",
        })
    return out


def eskers_from_sigeom(raw, frame) -> list[dict]:
    """Esker centre lines (glaciofluvial sand and gravel ridges, the aquifers of the Abitibi) in local metres,
    cut to the model's square. Only the line is data: how wide and deep the esker is under it is not."""
    out = []
    try:
        import shapely.geometry as sg
    except Exception:
        return out
    box = sg.box(-frame.half, -frame.half, frame.half, frame.half)
    for feat in (raw or {}).get("esker", []):
        geom, props = feat.get("geometry") or {}, feat.get("properties") or {}
        parts = geom.get("coordinates", []) if geom.get("type") == "MultiLineString" else             [geom["coordinates"]] if geom.get("type") == "LineString" else []
        for part in parts:
            if len(part) < 2:
                continue
            xs, ys = frame.to_xy([c[0] for c in part], [c[1] for c in part])
            line = sg.LineString(list(zip(xs, ys))).intersection(box)
            if line.is_empty:
                continue
            for piece in getattr(line, "geoms", [line]):
                if piece.geom_type != "LineString" or piece.length < 5:
                    continue
                piece = piece.simplify(3.0)
                out.append({"id": str(props.get("ESK_NO_SEQ") or ""), "kind": props.get("ESK_FORME_FLUVI_EN") or "Esker",
                            "line": [[round(x, 1), round(y, 1)] for x, y in piece.coords], "source": "sigeom"})
    return out[:60]


def drillholes_from_sigeom(raw, frame) -> list[dict]:
    out = []
    for feat in (raw or {}).get("drill", []):
        p, props = _point(feat.get("geometry")), feat.get("properties") or {}
        length = _num(props.get("PROF_FIN_UNITE_LITH"))
        if not p or not length or length <= 0:
            continue
        az = _num(props.get("AZMT_DEPR"))
        plunge = _num(props.get("PLON_DEPR"))
        x, y = frame.to_xy(*p)
        out.append({
            "id": str(props.get("NUMR_ORGN_FORG") or props.get("NUMR_INTER") or ""),
            "x": round(x, 1), "y": round(y, 1), "length_m": round(length, 1),
            "azimuth": az if az is not None else 0.0,
            "plunge": plunge if plunge is not None else -90.0,      # no angle given: treat as vertical
            "year": props.get("AN_FORG"), "source": "sigeom",
        })
    return out


def sites_from_mrds(raw, frame) -> list[dict]:
    out = []
    for r in raw or []:
        x, y = frame.to_xy(r["lon"], r["lat"])
        out.append({
            "name": r.get("name") or "Mineral site", "kind": "mine" if _status_kind(r.get("status")) != "other" else "deposit",
            "status": r.get("status") or "", "state": _status_kind(r.get("status")),
            "commodity": r.get("commodity") or "", "depth_m": None, "x": round(x, 1), "y": round(y, 1), "source": "mrds",
        })
    return out


def claims_from_gestim(raw, frame) -> list[dict]:
    """Claims and leases as local-metre rings. Holder names are left out on purpose."""
    out = []
    kinds = (("claims", "claim"), ("leases", "lease"), ("bulk", "bulk sample"), ("concessions", "concession"))
    for key, kind in kinds:
        for feat in (raw or {}).get(key, []):
            props = feat.get("properties") or {}
            for ring in _rings(feat.get("geometry")):
                xs, ys = frame.to_xy([c[0] for c in ring], [c[1] for c in ring])
                simple = _clip_ring(list(zip(xs, ys)), frame.half)
                if not simple or len(simple) < 4:
                    continue
                out.append({
                    "id": props.get("TITRE") or "", "kind": kind, "status": props.get("STATUT") or "",
                    "issued": (props.get("TMN_DATE_EMISS") or "")[:10], "expires": (props.get("TMN_DATE_EXPIR") or "")[:10],
                    "area_ha": _num(props.get("TMN_SUPRF") or props.get("PLT_SUPRF_CALCU")),
                    "ring": simple, "source": "gestim",
                })
    return out[:MAX_CLAIMS]


def sites_from_osm(gdf, frame) -> list[dict]:
    """Mine shafts, adits and quarries from an OSM feature table (centroids, local metres)."""
    out = []
    if gdf is None or not len(gdf):
        return out
    cols = set(gdf.columns)

    def val(row, col):
        return str(row[col]) if col in cols and row[col] == row[col] and row[col] is not None else ""

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        c = geom.centroid
        x, y = frame.to_xy(c.x, c.y)
        mm, hist, lu = val(row, "man_made"), val(row, "historic"), val(row, "landuse")
        kind = ("shaft" if "shaft" in mm + hist else "adit" if "adit" in mm + hist
                else "quarry" if lu == "quarry" else "mine")
        depth = _num(val(row, "depth").replace("m", "").strip()) if val(row, "depth") else None
        out.append({
            "name": val(row, "name") or kind.capitalize(), "kind": kind, "status": "", "state": "other",
            "commodity": val(row, "resource") or val(row, "substance"), "depth_m": depth,
            "x": round(x, 1), "y": round(y, 1), "source": "osm",
        })
    return out[:MAX_SITES]


def _dedupe(sites: list[dict], radius=150.0) -> list[dict]:
    """Keep the first site of a cluster (sources are added in order of detail)."""
    kept: list[dict] = []
    for s in sites:
        if any(math.hypot(s["x"] - k["x"], s["y"] - k["y"]) < radius for k in kept):
            continue
        kept.append(s)
    return kept


# ---------------------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------------------

def build_layers(geology: dict, sites: list, drill: list, claims: list) -> list[dict]:
    """
    The cutaway layer stack, shallow to deep. Real thicknesses are rarely public, so only what the sources say is
    marked ``data``; the thin topsoil and the typical glacial cover are ``generic`` and say so in the legend.
    """
    surface = geology.get("surface")
    column = geology.get("column") or []
    layers = [{"name": "Topsoil", "kind": "soil", "top_m": 0.0, "bottom_m": 1.0, "color": TOPSOIL_COLOR,
               "provenance": "generic", "note": "Typical A/B horizon"}]
    cover = next((u for u in column if u["unconsolidated"]), None)
    layers.append({
        "name": cover["name"] if cover else "Overburden", "kind": "overburden", "top_m": 1.0, "bottom_m": 8.0,
        "color": OVERBURDEN_COLOR, "provenance": "generic",
        "note": ("Unconsolidated cover mapped here; thickness varies" if cover else "Typical glacial or weathered cover"),
    })
    if surface:
        layers.append({
            "name": surface["name"], "kind": "bedrock", "top_m": 8.0, "bottom_m": None, "color": surface["color"],
            "provenance": "data", "note": ", ".join(x for x in (surface["lith"], surface["age"]) if x),
        })
    else:
        layers.append({"name": "Bedrock", "kind": "bedrock", "top_m": 8.0, "bottom_m": None, "color": BEDROCK_COLOR,
                       "provenance": "generic", "note": "No geologic map data for this location"})
    return layers


def fetch_underground(frame, cache_dir, osm_features=None, log=print, session=None) -> dict | None:
    """
    Gather the underground data for the area of ``frame``. Returns a JSON-safe dict, or ``None`` when
    there is nothing at all to show (no geology and no mines, drill holes or claims).
    """
    session = session or _session()
    bbox = frame.bbox_lonlat(0.0)
    west, south, east, north = bbox
    lat, lon = frame.lat, frame.lon
    used: list[str] = []

    with ThreadPoolExecutor(4) as pool:     # the sources are independent: ask them all at once
        f_geo = pool.submit(_macrostrat, session, lat, lon, cache_dir, log)
        f_sig = pool.submit(_sigeom, session, bbox, cache_dir, log)
        f_mrds = pool.submit(_mrds, session, bbox, cache_dir, log)
        f_gestim = pool.submit(_gestim, session, bbox, cache_dir, log)
        raw_geo, sig, raw_mrds, raw_gestim = f_geo.result(), f_sig.result(), f_mrds.result(), f_gestim.result()

    geology = _strata_from_macrostrat(raw_geo)
    if geology["surface"] or geology["column"]:
        used.append("macrostrat")

    sites: list[dict] = []
    drill = drillholes_from_sigeom(sig, frame)
    sites += sites_from_sigeom(sig, frame)
    if sig:
        used.append("sigeom")

    osm_sites: list[dict] = []
    if osm_features is not None:
        try:
            osm_sites = sites_from_osm(osm_features("mining", OSM_MINING_TAGS), frame)
        except Exception as e:
            log(f"⚠ OpenStreetMap mine workings unavailable: {e}")
    sites += osm_sites
    if osm_sites:
        used.append("osm")

    mrds = sites_from_mrds(raw_mrds, frame)
    sites += mrds
    if mrds:
        used.append("mrds")
    sites = _dedupe(sites)

    claims = claims_from_gestim(raw_gestim, frame)
    if claims:
        used.append("gestim")

    eskers = eskers_from_sigeom(sig, frame)
    if not (geology["surface"] or sites or drill or claims or eskers):
        return None

    depths = [d["length_m"] for d in drill] + [s["depth_m"] for s in sites if s.get("depth_m")]
    return {
        "version": 1,
        "layers": build_layers(geology, sites, drill, claims),
        "column": geology["column"],
        "sites": sites,
        "drillholes": drill,
        "claims": claims,
        "eskers": eskers,
        # No open measured groundwater levels exist for most places: the water table is an estimate (a subdued copy
        # of the ground a few metres down) and the viewer says so.
        "water_table": {"depth_m": 3.0, "provenance": "estimate",
                        "note": "Estimated: follows the ground, about 3 m down. Not measured."},
        "max_depth_m": round(max(depths), 1) if depths else None,
        "sources": used,
        "credits": [SOURCE_CREDITS[s] for s in used if s in SOURCE_CREDITS],
    }
