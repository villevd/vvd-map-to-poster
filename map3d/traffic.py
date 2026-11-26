"""
Traffic for the 3D viewer: small cars that drive the real street network and
stop at the real traffic signals and stop signs.

The lights and signs are OpenStreetMap nodes (``highway=traffic_signals`` and
``highway=stop``), so their positions are real. OpenStreetMap does not record
signal timings, so each signal gets a plausible 60-90 s cycle. The simulation
itself runs in the browser (``traffic_sim.js``); this module only prepares the
road graph and the control points it needs.
"""

from __future__ import annotations

import re

import numpy as np
from scipy.spatial import cKDTree

# Road classes cars drive on, with the cruising speed of each (m/s). Service roads are left out.
# Highways (motorways, and primary roads that carry a route number such as 117) 110 km/h, ordinary roads 50 km/h, small roads 30 km/h.
KMH = 1 / 3.6
HIGHWAY_SPEED = 110 * KMH
CAR_ROAD_SPEED = {"motorway": HIGHWAY_SPEED, "primary": 50 * KMH, "secondary": 50 * KMH, "tertiary": 50 * KMH, "residential": 30 * KMH}
# Relative car density per metre of road by class: busy highways carry many more cars than side streets.
CAR_ROAD_WEIGHT = {"motorway": 6.0, "primary": 3.0, "secondary": 2.0, "tertiary": 1.3, "residential": 1.0}
CAR_LENGTH_FRACTION = 0.00367  # car length as a share of the tile side: about 11 m on a 3 km tile
MIN_CAR_LENGTH = 4.6           # m, a real car
MAX_CAR_LENGTH = 10.7          # m, keeps cars small on large tiles yet still visible at poster scale
# Numbered routes that carry many lorries and pickups (Route 117 is the trunk road through Val-d'Or).
HEAVY_ROUTES = ("117",)
ROUNDABOUT_SPEED = 6.0         # m/s, how fast cars go round a roundabout
ROUNDABOUT_RADIUS = (6.0, 70.0)    # m, circular loops of road inside this range are roundabouts
ROUNDABOUT_ROUNDNESS = 0.055   # max relative spread of a loop's radius for it to count as circular
NODE_SNAP = 1.0                # m, line ends closer than this are the same junction
CONTROL_SNAP = 25.0           # m, a signal or stop sign attaches to the nearest junction within this range
CYCLE_RANGE = (60.0, 90.0)     # s, plausible signal cycle length


def is_heavy_route(ref: str) -> bool:
    """True when a road's ``ref`` ("117", "QC 117", "117;389"...) names one of ``HEAVY_ROUTES``."""
    return any(re.search(rf"(?<!\d){re.escape(r)}(?!\d)", ref or "") for r in HEAVY_ROUTES)


def _turning(xy: np.ndarray, centre: np.ndarray) -> float:
    """Signed sweep of a polyline around ``centre`` (positive = counter-clockwise seen from above, x east / y north)."""
    d = xy - centre
    return float(np.sum(d[:-1, 0] * d[1:, 1] - d[:-1, 1] * d[1:, 0]))


def find_roundabouts(edge_ends: list[tuple[int, int]], edge_xy: list[np.ndarray], tagged: list[bool]) -> dict[int, np.ndarray]:
    """
    Edges that make up roundabouts: those tagged ``junction=roundabout`` plus every small, circular loop in the
    road graph (Overture has no roundabout tag). Returns {edge index: centre (x, y) of its roundabout}.
    """
    import networkx as nx

    def resample(xy: np.ndarray, step: float = 1.5) -> np.ndarray:
        seg = np.hypot(*np.diff(xy, axis=0).T)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        t = np.arange(0.0, cum[-1], step)
        return np.column_stack([np.interp(t, cum, xy[:, 0]), np.interp(t, cum, xy[:, 1])]) if len(t) > 1 else xy

    by_pair: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(edge_ends):
        by_pair.setdefault((min(a, b), max(a, b)), []).append(i)
    short = nx.Graph()
    for (a, b), idx in by_pair.items():
        if any(float(np.hypot(*np.diff(edge_xy[i], axis=0).T).sum()) < 160.0 for i in idx):
            short.add_edge(a, b)

    rings: list[set[int]] = []
    for cyc in nx.cycle_basis(short):
        pairs = [(min(u, v), max(u, v)) for u, v in zip(cyc, cyc[1:] + cyc[:1])]
        members = {i for pr in pairs for i in by_pair.get(pr, [])}
        pts = np.concatenate([resample(edge_xy[by_pair[pr][0]]) for pr in pairs])
        centre = pts.mean(axis=0)
        r = np.hypot(*(pts - centre).T)
        ring_len = sum(float(np.hypot(*np.diff(edge_xy[by_pair[pr][0]], axis=0).T).sum()) for pr in pairs)
        if (ROUNDABOUT_RADIUS[0] <= r.mean() <= ROUNDABOUT_RADIUS[1] and r.std() / r.mean() < ROUNDABOUT_ROUNDNESS
                and 0.8 <= ring_len / (2 * np.pi * r.mean()) <= 1.2):
            rings.append(members)

    flagged = set().union(*rings) if rings else set()
    flagged |= {i for i, t in enumerate(tagged) if t}
    # group flagged edges that share a junction, then take each group's centre
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in flagged:
        a, b = edge_ends[i]
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in flagged:
        groups.setdefault(find(edge_ends[i][0]), []).append(i)
    out: dict[int, np.ndarray] = {}
    for grp in groups.values():
        centre = np.concatenate([resample(edge_xy[i]) for i in grp]).mean(axis=0)
        for i in grp:
            out[i] = centre
    return out


def car_count(total_km: float) -> int:
    return int(np.clip(round(total_km * 0.8), 12, 120))


def build_traffic(roads, signals, stop_signs, ground_z, lift: float, side: float, seed: int = 7,
                  log=print, right_hand: bool = True) -> dict | None:
    """
    Road graph and traffic controls for the 3D viewer, in three.js coordinates.

    Args:
        roads: GeoDataFrame of road LineStrings with ``cls`` and ``bridge`` columns (scene metres)
        signals, stop_signs: (n, 2) arrays of scene (x, y) positions of OSM traffic signals and stop signs
        ground_z: function (x, y) -> scene z of the ground, as used for the roads
        lift: height of the road surface above the ground (scene units)
        side: side length of the tile (scene metres), used to size the cars
        right_hand: cars drive on the right (roundabouts turn counter-clockwise); False for left-hand traffic
    Returns:
        A JSON-serialisable dict, or None when there are no drivable roads.
    """
    if roads is None or len(roads) == 0:
        return None
    rng = np.random.default_rng(seed)

    node_index: dict[tuple[int, int], int] = {}
    node_xy: list[tuple[float, float]] = []

    def node_id(x: float, y: float) -> int:
        key = (round(x / NODE_SNAP), round(y / NODE_SNAP))
        i = node_index.get(key)
        if i is None:
            i = len(node_xy)
            node_index[key] = i
            node_xy.append((float(x), float(y)))
        return i

    edges: list[dict] = []
    edge_xy: list[np.ndarray] = []
    edge_ends: list[tuple[int, int]] = []
    edge_tagged: list[bool] = []
    total_m = 0.0
    classes = roads["cls"].astype(str).to_numpy()
    bridges = roads["bridge"].astype(bool).to_numpy()
    refs = roads["ref"].astype(str).to_numpy() if "ref" in roads.columns else np.full(len(roads), "")
    rings = roads["roundabout"].astype(bool).to_numpy() if "roundabout" in roads.columns else np.zeros(len(roads), bool)
    for geom, cls, bridge, ref, ring in zip(roads.geometry.values, classes, bridges, refs, rings):
        if geom is None or bridge or cls not in CAR_ROAD_SPEED:
            continue
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type != "LineString":
                continue
            xy = np.asarray(part.coords)[:, :2]
            if len(xy) < 2:
                continue
            length = float(np.hypot(*np.diff(xy, axis=0).T).sum())
            if length < 2.0:
                continue
            a, b = node_id(*xy[0]), node_id(*xy[-1])
            if a == b:
                continue
            z = np.asarray(ground_z(xy[:, 0], xy[:, 1]), dtype=np.float64) + lift
            pts = np.column_stack([xy[:, 0], z, -xy[:, 1]])           # three.js: x, y (up), z = -north
            numbered = bool(re.search(r"\d", ref or ""))      # a route number makes a primary road a highway
            speed = HIGHWAY_SPEED if cls == "primary" and numbered else CAR_ROAD_SPEED[cls]
            edge = {"a": a, "b": b, "v": round(speed, 3), "w": CAR_ROAD_WEIGHT[cls],
                    "p": np.round(pts, 2).ravel().tolist()}
            if is_heavy_route(ref):
                edge["h"] = 1
            edges.append(edge)
            edge_xy.append(xy)
            edge_ends.append((a, b))
            edge_tagged.append(bool(ring))
            total_m += length

    if not edges:
        return None

    # Roundabouts: one-way, counter-clockwise (clockwise for left-hand traffic), taken slowly
    n_round = 0
    ring_nodes: set[int] = set()
    for i, centre in find_roundabouts(edge_ends, edge_xy, edge_tagged).items():
        ccw = _turning(edge_xy[i], centre) > 0
        edges[i]["o"] = 1
        edges[i]["d"] = 0 if ccw == right_hand else 1       # the one direction cars may drive this edge in
        edges[i]["v"] = min(edges[i]["v"], ROUNDABOUT_SPEED)
        ring_nodes.update(edge_ends[i])
        n_round += 1
    n_heavy = sum(1 for e in edges if e.get("h"))

    nxy = np.asarray(node_xy, dtype=np.float64)
    nz = np.asarray(ground_z(nxy[:, 0], nxy[:, 1]), dtype=np.float64) + lift
    nodes = np.round(np.column_stack([nxy[:, 0], nz, -nxy[:, 1]]), 2).ravel().tolist()

    # Attach each signal / stop sign to the nearest junction. Signals win over stop signs at the same junction.
    tree = cKDTree(nxy)
    node_ctrl = np.full(len(nxy), -1, dtype=np.int64)
    # roundabouts have no signals or stop signs: cars on the ring never stop, entering cars give way
    ctrls: list[dict] = []

    def attach(points, kind: str) -> int:
        if points is None or len(points) == 0:
            return 0
        dist, idx = tree.query(np.asarray(points, dtype=np.float64)[:, :2], k=1, distance_upper_bound=CONTROL_SNAP)
        count = 0
        for d, i in zip(dist, idx):
            if not np.isfinite(d) or i >= len(nxy) or node_ctrl[i] >= 0 or int(i) in ring_nodes:
                continue
            ctrl: dict = {"k": kind}
            if kind == "signal":
                cyc = float(rng.uniform(*CYCLE_RANGE))
                ctrl.update(cyc=round(cyc, 1), off=round(float(rng.uniform(0, cyc)), 1))
            node_ctrl[i] = len(ctrls)
            ctrls.append(ctrl)
            count += 1
        return count

    n_sig = attach(signals, "signal")
    n_stop = attach(stop_signs, "stop")
    log(f"✓ Traffic: {n_sig} signals and {n_stop} stop signs, {n_round} roundabout segments, "
        f"{n_heavy} segments of heavy-traffic routes {'/'.join(HEAVY_ROUTES)}")

    car_len = min(MAX_CAR_LENGTH, max(MIN_CAR_LENGTH, side * CAR_LENGTH_FRACTION))
    return {
        "cars": car_count(total_m / 1000.0),
        "seed": int(seed),
        "car_len": round(float(car_len), 2),
        "spacing": round(float(car_len * 1.4), 2),
        "nodes": nodes,
        "edges": edges,
        "nodeCtrl": node_ctrl.tolist(),
        "ctrls": ctrls,
    }
