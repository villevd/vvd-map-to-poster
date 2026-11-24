// ---------------------------------------------------------------- traffic: cars that obey the real signals and stop signs
const trafficRoot = new THREE.Group();
root.add(trafficRoot);
let trafficUpdate = null, trafficLights = null;
if (CFG.traffic && CFG.traffic.edges.length) {
  const tc = CFG.traffic, sim = TrafficSim.createSim(tc, { seed: tc.seed });
  const L = tc.car_len, LANE = Math.min(1.7, L * 0.34);   // cars keep to the right-hand lane
  const SPEC = TrafficSim.TYPES;
  // Side profiles in units of L (x = length, y = height, front at +x), extruded across the width. Real proportions.
  const prof = (pts, depth) => {
    const sh = new THREE.Shape(); pts.forEach(([x, y], i) => i ? sh.lineTo(x * L, y * L) : sh.moveTo(x * L, y * L));
    const g = new THREE.ExtrudeGeometry(sh, { depth: depth, bevelEnabled: false }); g.translate(0, 0, -depth / 2); return g;
  };
  const scaled = (pts, xs, ys) => pts.map(([x, y]) => [x * xs, y * ys]);
  const HATCH = [[-.5, .09], [.5, .09], [.5, .27], [.42, .31], [.2, .34], [.1, .5], [-.2, .5], [-.38, .36], [-.5, .33]];
  const HATCH_GLASS = [[.17, .345], [.09, .47], [-.19, .47], [-.34, .355]];
  const MODELS = [
    { body: scaled(HATCH, 0.82, 0.92), glass: scaled(HATCH_GLASS, 0.82, 0.92), axles: [0.255, -0.255], wheel: 0.08,
      colors: [0xe9ecef, 0xb8bec6, 0x8a2527, 0x1f3350, 0xc9b99a, 0xf1f1ee, 0x2f4a3a, 0xd9a21b] },                    // small car
    { body: HATCH, glass: HATCH_GLASS, axles: [0.31, -0.31], wheel: 0.085,
      colors: [0xe9ecef, 0xb8bec6, 0x6b7178, 0x23262b, 0x1f3350, 0x8a2527, 0x2f4a3a, 0xc9b99a, 0xf1f1ee, 0x3d4650] },  // car
    { body: [[-.65, .10], [.65, .10], [.65, .27], [.56, .32], [.34, .35], [.27, .37], [.12, .58], [-.12, .58], [-.2, .44], [-.65, .44]],
      glass: [[.235, .375], [.115, .55], [-.105, .55], [-.175, .445]], axles: [0.42, -0.42], wheel: 0.095,
      colors: [0x23262b, 0x8a2527, 0xe9ecef, 0x2f4a3a, 0x6b7178, 0x1f3350, 0xb8bec6] },                               // pickup truck
    { body: [[-1.15, .12], [1.15, .12], [1.15, .36], [1.08, .41], [.94, .44], [.88, .74], [.62, .76], [.58, .54], [.46, .54], [.46, .86], [-1.15, .86]],
      glass: [[.90, .47], [.865, .70], [.65, .71], [.63, .49]], axles: [0.78, -0.55, -0.9], wheel: 0.1,
      colors: [0xf1f1ee, 0xe9ecef, 0xb8bec6, 0x1f3350, 0x8a2527, 0x2f4a3a, 0xd9a21b] },                               // lorry
  ];
  const LIGHT = { g: new THREE.Color(0x2ecc71), a: new THREE.Color(0xffb300), r: new THREE.Color(0xe53935) };
  const count = SPEC.map(() => 0), loc = [];
  sim.cars.forEach(c => { loc.push(count[c.type]++); });
  const col = new THREE.Color(), parts = [];
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.55, metalness: 0.05 });
  const glassMat = new THREE.MeshStandardMaterial({ color: 0x141a22, roughness: 0.15, metalness: 0.3 });
  const wheelMat = new THREE.MeshLambertMaterial({ color: 0x111111 });
  const kinds = MODELS.map((m, t) => {
    const W = L * 0.4 * SPEC[t].w, n = Math.max(1, count[t]);
    const wheelGeo = new THREE.CylinderGeometry(L * m.wheel, L * m.wheel, W * 1.02, 10).rotateX(Math.PI / 2);
    const bodies = new THREE.InstancedMesh(prof(m.body, W), bodyMat, n);
    const glass = new THREE.InstancedMesh(prof(m.glass, W * 1.03), glassMat, n);
    const wheels = new THREE.InstancedMesh(wheelGeo, wheelMat, n * m.axles.length);
    bodies.count = glass.count = count[t]; wheels.count = count[t] * m.axles.length;
    bodies.castShadow = glass.castShadow = true;
    bodies.frustumCulled = glass.frustumCulled = wheels.frustumCulled = false;
    parts.push(bodies, glass, wheels);
    return { m, W, bodies, glass, wheels, half: SPEC[t].len * L / 2 };
  });
  sim.cars.forEach((c, i) => {
    const k = kinds[c.type], pal = k.m.colors;
    k.bodies.setColorAt(loc[i], col.setHex(pal[(i * 7 + c.type) % pal.length]));
  });
  kinds.forEach(k => { if (k.bodies.instanceColor) k.bodies.instanceColor.needsUpdate = true; });
  // head / tail lights, lit at night in the realistic view
  const carLightPos = new Float32Array(tc.cars * 12), carLightCol = new Float32Array(tc.cars * 12);
  for (let i = 0; i < tc.cars; i++) for (let j = 0; j < 4; j++) carLightCol.set(j < 2 ? [1, 0.93, 0.75] : [1, 0.1, 0.05], i * 12 + j * 3);
  const carLightGeo = new THREE.BufferGeometry();
  carLightGeo.setAttribute('position', new THREE.BufferAttribute(carLightPos, 3));
  carLightGeo.setAttribute('color', new THREE.BufferAttribute(carLightCol, 3));
  trafficLights = new THREE.Points(carLightGeo, new THREE.PointsMaterial({ size: L * 0.22, sizeAttenuation: true, vertexColors: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  trafficLights.frustumCulled = false;
  const ap = sim.approaches;
  const lights = new THREE.InstancedMesh(new THREE.SphereGeometry(L * 0.2, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }), Math.max(1, ap.length));
  lights.frustumCulled = false;
  const lightShown = new Array(ap.length).fill('');
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), qp = new THREE.Quaternion(), p = new THREE.Vector3(), one = new THREE.Vector3(1, 1, 1);
  const carScale = new THREE.Vector3(1, 1, 1);   // per car, set each frame from the terrain exaggeration
  const noCarScale = new THREE.Vector3(0, 0, 0); // a culled car is drawn at no size
  const up = new THREE.Vector3(0, 1, 0), zAxis = new THREE.Vector3(0, 0, 1), off = new THREE.Vector3();
  ap.forEach((a, i) => { m4.compose(p.set(a.x, a.y + L * 1.3, a.z), q.identity(), one); lights.setMatrixAt(i, m4); });
  lights.instanceMatrix.needsUpdate = true;
  trafficRoot.add(...parts, lights, trafficLights);
  // Rendered pose per car: low-pass filtered so corners, junction hand-overs and stop-and-go never make a car twitch
  const view = [];
  for (let i = 0; i < tc.cars; i++) view.push(null);
  const rearCar = { e: 0, dir: 0, s: 0 };
  trafficUpdate = (dt, real) => {
    sim.step(dt);
    // The root is stretched vertically by the terrain exaggeration. Cars keep their normal height: their own
    // vertical scale is the inverse, so the stretch cancels out while their positions still follow the ground.
    carScale.set(1, 1 / root.scale.y, 1);
    const k = 1 - Math.exp(-Math.min(dt, 0.1) * 12);
    const carCut = PERF.cuts.car, camX = camera.position.x, camZ = camera.position.z;   // dynamic culling: far cars shrink to nothing
    sim.cars.forEach((car, i) => {
      const kind = kinds[car.type], len = car.len, W = kind.W;
      const f = sim.pose(car);                                   // the sim tracks the front bumper
      rearCar.e = car.e; rearCar.dir = car.dir; rearCar.s = Math.max(0, car.s - len * 0.8);
      const r = sim.pose(rearCar);
      let hx = f.x - r.x, hz = f.z - r.z, hl = Math.hypot(hx, hz);
      if (hl < len * 0.3) { hx = f.dx; hz = f.dz; hl = 1; }      // too close to the start of a road: use its direction
      hx /= hl; hz /= hl;
      const rx = -hz, rz = hx;                                   // right of travel
      const tx = (f.x + r.x) / 2 + rx * LANE, tz = (f.z + r.z) / 2 + rz * LANE, ty = (f.y + r.y) / 2;
      let v = view[i];
      const ang = Math.atan2(hz, hx);
      if (!v || Math.hypot(tx - v.x, tz - v.z) > len * 3) v = view[i] = { x: tx, y: ty, z: tz, ang, pitch: 0 };
      else {
        const kp = 1 - Math.exp(-Math.min(dt, 0.1) * 30);       // position tracks closely, heading is damped
        v.x += (tx - v.x) * kp; v.y += (ty - v.y) * kp; v.z += (tz - v.z) * kp;
        let da = ang - v.ang; da -= Math.PI * 2 * Math.round(da / (Math.PI * 2));
        const maxTurn = 2.2 * Math.min(dt, 0.1);                 // rad/s: a car cannot spin on the spot
        v.ang += Math.max(-maxTurn, Math.min(maxTurn, da * (1 - Math.exp(-Math.min(dt, 0.1) * 8))));
      }
      v.hx = Math.cos(v.ang); v.hz = Math.sin(v.ang);
      const pitchT = Math.atan2(f.y - r.y, Math.max(hl, 0.001)); v.pitch += (pitchT - v.pitch) * k;
      q.setFromAxisAngle(up, -v.ang).multiply(qp.setFromAxisAngle(zAxis, v.pitch));
      const far = Math.hypot(v.x - camX, v.z - camZ) > carCut;
      const sc = far ? noCarScale : carScale;
      m4.compose(p.set(v.x, v.y + 0.03, v.z), q, sc);
      kind.bodies.setMatrixAt(loc[i], m4); kind.glass.setMatrixAt(loc[i], m4);
      const axles = kind.m.axles;
      for (let w = 0; w < axles.length; w++) {                   // one cylinder across each axle
        off.set(axles[w] * L, L * kind.m.wheel, 0).applyQuaternion(q);
        m4.compose(p.set(v.x + off.x, v.y + 0.03 + off.y, v.z + off.z), q, sc); kind.wheels.setMatrixAt(loc[i] * axles.length + w, m4);
      }
      if (real) {
        const P = carLightPos, o = i * 12, sx = -v.hz * W * 0.35, sz = v.hx * W * 0.35, hy = far ? -1e6 : v.y + L * 0.2;
        const fx = v.hx * kind.half, fz = v.hz * kind.half;
        P[o] = v.x + fx + sx; P[o + 1] = hy; P[o + 2] = v.z + fz + sz;
        P[o + 3] = v.x + fx - sx; P[o + 4] = hy; P[o + 5] = v.z + fz - sz;
        P[o + 6] = v.x - fx + sx; P[o + 7] = hy; P[o + 8] = v.z - fz + sz;
        P[o + 9] = v.x - fx - sx; P[o + 10] = hy; P[o + 11] = v.z - fz - sz;
      }
    });
    kinds.forEach(k => { k.bodies.instanceMatrix.needsUpdate = k.glass.instanceMatrix.needsUpdate = k.wheels.instanceMatrix.needsUpdate = true; });
    if (real) carLightGeo.attributes.position.needsUpdate = true;
    carLightGeo.setDrawRange(0, real ? tc.cars * 4 : 0);
    let changed = false;
    ap.forEach((a, i) => {
      const st = sim.lightAt(a);
      if (st !== lightShown[i]) { lightShown[i] = st; lights.setColorAt(i, LIGHT[st]); changed = true; }
    });
    if (changed) lights.instanceColor.needsUpdate = true;
  };
} else {
  $('trafficRow').style.display = 'none';
  $('trafficSpeedRow').style.display = 'none';
  $('tspeed').style.display = 'none';
  $('tspeedO').style.display = 'none';
}

const tsp = $('tspeed'), tspO = $('tspeedO');
const showTsp = () => { tspO.textContent = (+tsp.value).toFixed(2) + '×'; };
tsp.oninput = showTsp; showTsp();

// load queue: coarsest level of every tile first (instant whole-model coverage), then what the camera needs, nearest first
const wanted = new Map();
let pumping = false;
function want(tile, lod, prio) {
  const lv = tile.levels[lod]; if (!lv || lv.state !== 'idle') return;
  const k = tile.key + '/' + lod; const cur = wanted.get(k);
  if (!cur || prio < cur.prio) wanted.set(k, { tile, lod, prio });
  if (!pumping) pump();
}
async function pump() {
  pumping = true;
  while (wanted.size) {
    const next = [...wanted.values()].sort((a, b) => a.prio - b.prio)[0];
    wanted.delete(next.tile.key + '/' + next.lod);
    try { await loadGroup(next.tile, next.lod); }
    catch (e) {                                // one bad chunk must not stall the whole queue
      console.error('chunk failed', next.tile.key, next.lod, e);
      const lv = next.tile.levels[next.lod]; if (lv) lv.state = 'failed';
      loadedGroups++;
    }
    await new Promise(r => setTimeout(r, 0));
  }
  pumping = false;
}

