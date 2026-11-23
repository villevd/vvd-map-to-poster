# Kiosk API

The API serves what the vvd-smart-dashboard kiosk needs to show an event in 3D: a city model
(built from the same pipeline as `--3d`), an aircraft library stored locally, and the position of
a lightning strike or an aircraft inside that model.

Everything is JSON except the models, which are binary glTF (`.glb`).

## Run it

```powershell
.venv\Scripts\python.exe -m map3d.api --host 0.0.0.0 --port 8765 --city "Val-d'Or" --country Canada
```

- `--host 127.0.0.1` (the default) keeps it on this machine. Use `0.0.0.0` so the kiosk can reach it on the network.
- `--cors-origin https://your-kiosk-origin` restricts which web page may read it (default `*`).
- The first start builds the default city model. That downloads OpenStreetMap and terrain data and takes minutes; later starts reuse `cache/3d-api/`. Check `GET /api/v1/health` for `"builds_idle": true`.
- There is no authentication. Run it on the local network only.

The aircraft library is built once with `python -m map3d.aircraft planes` and committed in `planes/`
(39 models, about 3 MB). Rebuild it after changing the specs in `map3d/aircraft.py`.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/api/v1/health` | Status, counts, default model id |
| GET | `/api/v1/aircraft` | Whole library: every entry with dimensions, codes and `glb_url` |
| GET | `/api/v1/aircraft/match?type=B738&helicopter=0` | Model for an ICAO type designator (`match`: `exact` or `category`) |
| GET | `/api/v1/aircraft/{id}.glb` | One aircraft model. `ETag` = sha256, so cached copies can revalidate with `If-None-Match` (304) |
| GET | `/api/v1/models` | City models, newest first |
| POST | `/api/v1/models` | Request a city model (see body below). 200 if ready, 202 if queued |
| GET | `/api/v1/models/default` | The kiosk's default city model, queued if it does not exist yet |
| GET | `/api/v1/models/{id}` | Status of one model: `queued`, `building`, `ready` or `failed` (with `error`) |
| GET | `/api/v1/models/{id}/model.glb` | The city model. 409 until it is `ready` |
| POST | `/api/v1/events` | Place a lightning strike or an aircraft in a model (see below) |

Errors use one shape: `{"error": {"status": 400, "message": "..."}}`. A full build queue returns 503 with `Retry-After`.

### Request a city model

```json
{"city": "Val-d'Or", "country": "Canada", "distance": 1500, "exaggeration": 1.5, "theme": "terracotta"}
```

`exaggeration` scales the terrain's relief. `building_exaggeration` (optional, 0.1 to 8) scales building and tree
heights separately; without it, buildings use `exaggeration`.

`distance` is the half-size of the square model in metres (300 to 4000). `lat` and `lon` are optional;
give both to skip geocoding. The `id` is derived from these values, so the same request always maps
to the same model. Requests for the same model while it is queued or building do not start a second build.

### Post an event

Lightning:

```json
{"type": "lightning", "lat": 48.12, "lon": -77.71, "time": "2026-11-23T14:02:11Z"}
```

Aircraft (values as in the kiosk's ADS-B snapshot):

```json
{"type": "aircraft", "lat": 48.09, "lon": -77.76, "altitude_ft": 1450, "on_ground": false,
 "track_deg": 84, "type_code": "DH8D", "is_helicopter": false, "callsign": "JZA1234"}
```

Optional: `"model": "<id>"` to use a model other than the default. Without it the server uses the default city.

Response (aircraft, model ready; the numbers are illustrative):

```json
{
  "event": {"type": "aircraft", "lat": 48.09, "lon": -77.76, "altitude_m": 441.96, "on_ground": false,
            "track_deg": 84.0, "type_code": "DH8D", "helicopter": false, "callsign": "JZA1234",
            "phase": "approach"},
  "model": {"id": "val-d-or-…", "status": "ready", "glb_url": "/api/v1/models/val-d-or-…/model.glb", "…": "…"},
  "placement": {
    "inside_model": true, "east_m": -612.4, "north_m": 1034.0, "ground_msl_m": 331.2,
    "height_above_ground_m": 102.0, "vertical_exaggeration": 1.5, "building_exaggeration": 1.5,
    "gltf_position_m": [-612.4, 170.2, -1034.0], "gltf_rotation_y_deg": 6.0
  },
  "aircraft": {"match": "exact", "id": "dh8d", "name": "De Havilland Dash 8-400", "manufacturer": "De Havilland Canada",
               "category": "turboprop", "generic": false, "dimensions_m": {"length": 32.8, "span": 28.4, "height": 8.4},
               "glb_url": "/api/v1/aircraft/dh8d.glb"}
}
```

`phase` is `on_ground`, `approach` (no more than 2 000 ft above the ground) or `airborne`.
`placement` is `null` while the model is not ready; the aircraft match is returned either way.
A lightning event has no `aircraft` block and no `phase`.

## Coordinate frames

**City model (glTF):** X points east, Y up, Z points south (Z = −north), in metres. The model's
centre is at X = 0, Z = 0. Heights are not measured from the bottom of the model: the terrain at height
`elevation` sits at `Y = base_m + (elevation − dem_min_m) × exaggeration`, so the lowest ground is at
`Y = base_m`, and the plinth reaches below Y = 0. `z_range_m` in the model metadata gives the model's full
vertical extent. `placement.gltf_position_m` is in this frame, so it can be used as-is with the GLB.

**Heights:** `height_above_ground_m` is scaled by `building_exaggeration` when placed (the metadata's
`building_exaggeration`, which equals `vertical_exaggeration` unless it was set separately). The buildings
are drawn with that same exaggeration, so an aircraft at 100 m over the ground stays level with them.

**Aircraft model (glTF):** nose along +X, up along +Y, left wing towards −Z, metres. The origin is on
the ground under the middle of the fuselage, so the wheels touch Y = 0.

**Heading:** `gltf_rotation_y_deg` is the rotation about Y that turns the model to the aircraft's
track (a track of 90° is east, which needs no rotation). It is `null` for an event without a track.

## Using it from the kiosk

```ts
const API = "http://<api-host>:8765";

const res = await fetch(`${API}/api/v1/events`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ type: "aircraft", lat, lon, altitude_ft, on_ground, track_deg, type_code, is_helicopter }),
});
const { model, placement, aircraft } = await res.json();

if (model.status === "ready" && placement) {
  const city = await gltfLoader.loadAsync(API + model.glb_url);       // cache by model.id
  const plane = await gltfLoader.loadAsync(API + aircraft.glb_url);   // cache by aircraft.id
  plane.scene.position.set(...placement.gltf_position_m);
  plane.scene.rotation.y = THREE.MathUtils.degToRad(placement.gltf_rotation_y_deg ?? 0);
  scene.add(city.scene, plane.scene);
}
```

Fetch the whole library once with `GET /api/v1/aircraft` and cache the files by their `sha256`.

## Limits

- **Aircraft are generated, not scanned.** Each model is built from the type's approximate published
  dimensions and configuration (engine count and placement, wing position, tail, gear). It shows the
  right silhouette for the type, not the airline's livery or exact shape. Real models can replace a file
  in `planes/` without changing the API: keep the catalog entry's `id` and `codes`.
- **Unknown types** get the generic airliner (or generic helicopter), reported as `"match": "category"`.
- **Terrain resolution** is a 41 × 41 grid over the model (about 75 m spacing for a 1500 m model), so
  ground height at an event is accurate to a few metres.
- **Builds are slow and online.** The first build for a city needs internet access and minutes of processing.
  At most 4 builds are queued; further requests get 503.
