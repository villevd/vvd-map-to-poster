// ---------------------------------------------------------------- fake plane: lands on the model's runway
const planeRoot = new THREE.Group();
const runwayMeshes = [];   // hidden while the model is rendered as a cake (15_cake.js)
root.add(planeRoot);
const landBtn = $('landBtn'), followBox = $('followBox');
let planeUpdate = null;
// No runway in the model (older file, or none mapped): lay a straight one across the middle, on the terrain as loaded now.
function syntheticAirport() {
  const half = CFG.half, len = Math.max(0.8 * half, 400), appr = 0.5 * half, STEP = 20;
  const lift = 0.25 + 2 * half * 8e-5, tan3 = Math.tan(3 * Math.PI / 180) / root.scale.y;
  let last = CFG.zrange[0];
  const gy = (x, z) => {
    const y = groundYBelow(new THREE.Vector3(x, CFG.zrange[1] + 10 * H, z));
    if (y !== null) last = y / root.scale.y;
    return last + lift;
  };
  const x0 = len / 2, pts = [], runway = [];
  for (let d = appr; d > 0; d -= STEP) pts.push(x0 + d, gy(x0 + d, 0) + d * tan3, 0);
  pts.push(x0, gy(x0, 0), 0);
  const n = Math.max(2, Math.floor(len / STEP) + 1);
  for (let i = 0; i < n; i++) {
    const x = x0 - len * i / (n - 1), y = gy(x, 0);
    runway.push(x, y, 0);
    if (i) pts.push(x, y, 0);
  }
  return { pts, runway, touchdown_m: appr, roll_m: len, cruise_ms: 70 };
}
function initPlane(ap) {
  const PATH = Float32Array.from(ap.pts), N = PATH.length / 3;
  const cum = new Float64Array(N);
  for (let i = 1; i < N; i++) {
    cum[i] = cum[i - 1] + Math.hypot(PATH[3 * i] - PATH[3 * i - 3], PATH[3 * i + 1] - PATH[3 * i - 2], PATH[3 * i + 2] - PATH[3 * i - 1]);
  }
  const TOTAL = cum[N - 1], TD = ap.touchdown_m, ROLL = ap.roll_m, CRUISE = ap.cruise_ms;
  // a plane about 60 m long on a 3 km model: small, but readable from the default view
  const L = Math.min(90, Math.max(30, CFG.half * 2 * 0.02));
  // runway strip, drawn 45 m wide on top of the ground
  const RW = Float32Array.from(ap.runway), NR = RW.length / 3;
  const rxz = [RW[3 * NR - 3] - RW[0], RW[3 * NR - 1] - RW[2]], rl = Math.hypot(rxz[0], rxz[1]) || 1;
  const px = -rxz[1] / rl * 22.5, pz = rxz[0] / rl * 22.5;
  const strip = new Float32Array(NR * 6), sidx = [];
  for (let i = 0; i < NR; i++) {
    const x = RW[3 * i], y = RW[3 * i + 1] + 0.05, z = RW[3 * i + 2];
    strip.set([x + px, y, z + pz, x - px, y, z - pz], i * 6);
    if (i) sidx.push(2 * i - 2, 2 * i - 1, 2 * i, 2 * i - 1, 2 * i + 1, 2 * i);
  }
  const stripGeo = new THREE.BufferGeometry();
  stripGeo.setAttribute('position', new THREE.BufferAttribute(strip, 3));
  stripGeo.setIndex(sidx);
  const runwayMesh = new THREE.Mesh(stripGeo, new THREE.MeshLambertMaterial({ color: 0x3b3f44, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4, side: THREE.DoubleSide }));
  runwayMesh.receiveShadow = true;
  planeRoot.add(runwayMesh); runwayMeshes.push(runwayMesh);

  // the plane: nose +X, up +Y, left wing -Z (the same axes as the aircraft library)
  const white = new THREE.MeshStandardMaterial({ color: 0xf2f4f6, roughness: 0.45 });
  const grey = new THREE.MeshStandardMaterial({ color: 0x9aa3ad, roughness: 0.4 });
  const plane = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(L * 0.025, L * 0.05, L, 14).rotateZ(Math.PI / 2), white);
  const wing = new THREE.Mesh(new THREE.BoxGeometry(L * 0.2, L * 0.02, L * 0.9).translate(L * 0.02, -L * 0.02, 0), grey);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(L * 0.1, L * 0.012, L * 0.3).translate(-L * 0.42, L * 0.02, 0), grey);
  const fin = new THREE.Mesh(new THREE.BoxGeometry(L * 0.12, L * 0.13, L * 0.012).translate(-L * 0.42, L * 0.09, 0), white);
  plane.add(body, wing, tail, fin);
  for (const z of [-0.22, 0.22]) {
    const eng = new THREE.Mesh(new THREE.CylinderGeometry(L * 0.05, L * 0.04, L * 0.14, 12).rotateZ(Math.PI / 2).translate(L * 0.06, -L * 0.07, z * L), grey);
    plane.add(eng);
  }
  body.castShadow = wing.castShadow = fin.castShadow = true;
  const nav = [[0xff2d2d, [0, 0, -0.45]], [0x2ecc71, [0, 0, 0.45]], [0xffffff, [-0.5, 0.1, 0]]];
  for (const [c, [x, y, z]] of nav) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(L * 0.012, 8, 6), new THREE.MeshBasicMaterial({ color: c }));
    dot.position.set(x * L, y * L, z * L); plane.add(dot);
  }
  // anchor: placed in the (vertically exaggerated) model space; fix: cancels that stretch so the plane keeps its shape
  const planeAnchor = new THREE.Group(), planeFix = new THREE.Group(), planeGroup = new THREE.Group();
  planeGroup.add(plane);
  planeGroup.visible = false;
  planeFix.add(planeGroup); planeAnchor.add(planeFix); planeRoot.add(planeAnchor);

  // position and heading on the path at distance s (metres from the start of the approach)
  const sample = s => {
    s = Math.max(0, Math.min(TOTAL, s));
    let lo = 0, hi = N - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
    const seg = cum[hi] - cum[lo] || 1, f = Math.min(1, Math.max(0, (s - cum[lo]) / seg));
    const dx = PATH[3 * hi] - PATH[3 * lo], dy = PATH[3 * hi + 1] - PATH[3 * lo + 1], dz = PATH[3 * hi + 2] - PATH[3 * lo + 2];
    const hl = Math.hypot(dx, dz) || 1;
    return { x: PATH[3 * lo] + dx * f, y: PATH[3 * lo + 1] + dy * f, z: PATH[3 * lo + 2] + dz * f,
             yaw: Math.atan2(-dz, dx), pitch: Math.atan2(dy, hl) };
  };
  // speed: cruise on the approach, then braking from touchdown to a stop at the far end of the runway
  // constant braking (v^2 falls linearly with distance) stops the plane after exactly one runway length
  const speedAt = s => s < TD ? CRUISE : CRUISE * Math.sqrt(Math.max(0, 1 - (s - TD) / ROLL));

  const st = { active: false, s: 0, yaw: 0, follow: followBox.checked, locked: false, done: false };
  const pWorld = new THREE.Vector3(), lastP = new THREE.Vector3(), fwd = new THREE.Vector3(), goal = new THREE.Vector3();
  const q1 = new THREE.Quaternion(), q2 = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), zA = new THREE.Vector3(0, 0, 1);
  const follow = dt => {
    planeGroup.getWorldPosition(pWorld);
    if (!st.locked) {
      // chase: swing the camera behind and above the plane, then hold that distance while it flies
      fwd.set(Math.cos(st.yaw), 0, -Math.sin(st.yaw));
      goal.copy(pWorld).addScaledVector(fwd, -3.5 * L).addScaledVector(up, 1.1 * L);
      const k = 1 - Math.exp(-dt * 2.5);
      camera.position.lerp(goal, k); controls.target.lerp(pWorld, k);
      if (camera.position.distanceTo(goal) < L * 0.5) st.locked = true;
    } else {
      const dx = pWorld.x - lastP.x, dy = pWorld.y - lastP.y, dz = pWorld.z - lastP.z;
      camera.position.x += dx; camera.position.y += dy; camera.position.z += dz;
      controls.target.x += dx; controls.target.y += dy; controls.target.z += dz;
    }
    lastP.copy(pWorld);
  };
  planeUpdate = dt => {
    planeFix.scale.y = 1 / root.scale.y;
    if (!st.active) return;
    st.s += speedAt(st.s) * dt;
    // the plane stops one runway length after touchdown (the 3-D path can be a few metres longer than that)
    const END = Math.min(TOTAL, TD + ROLL);
    if (st.s >= END - 0.05) { st.s = END; st.active = false; st.done = true; landBtn.textContent = 'Land again'; }
    const f = sample(st.s);
    st.yaw = f.yaw;
    planeAnchor.position.set(f.x, f.y + L * 0.03 / root.scale.y, f.z);
    q1.setFromAxisAngle(up, f.yaw); q2.setFromAxisAngle(zA, f.pitch);
    planeGroup.quaternion.copy(q1).multiply(q2);
    if (st.follow) follow(dt);
  };
  landBtn.onclick = () => {
    st.s = 0; st.active = true; st.done = false; st.locked = false; st.follow = followBox.checked;
    planeGroup.visible = true; landBtn.textContent = 'Landing…';
    lastP.copy(planeGroup.getWorldPosition(new THREE.Vector3()));
  };
  followBox.onchange = () => { st.follow = followBox.checked; st.locked = false; };
}
if (CFG.airport && CFG.airport.pts.length >= 6) initPlane(CFG.airport);
else {
  landBtn.title = 'No runway in the map data: a generated one is used';
  landBtn.onclick = () => { initPlane(syntheticAirport()); landBtn.onclick(); };
}

let lastFrameT = performance.now();
const LOAD_STEPS = [
  ['Downloading the page', 'The whole city is inside this one file: only the 3D engine and the weather are fetched separately.'],
  ['Starting the 3D engine', 'three.js draws the city on your graphics card (WebGL).'],
  ['Unpacking the nearest tiles', 'Geometry is stored compressed; the tiles closest to the camera are decompressed first.'],
  ['Uploading buildings, terrain and roads to the GPU', 'Each mesh becomes a GPU buffer; windows, tints and shadows are set up.'],
  ['Placing trees and traffic', CFG.traffic
    ? 'Cars are put on the real road network and obey the mapped lights and stop signs.'
    : 'Trees are placed from the map data.'],
  ['Drawing the first frame', 'Farther tiles and fine detail keep loading in the background as you look around.'],
];
const lsteps = $('lsteps');
LOAD_STEPS.forEach(([t]) => { const li = document.createElement('li'); li.textContent = t; lsteps.appendChild(li); });
// The loading screen: which step we are on, how many tiles are ready, shown, downloading or failed, the data
// received so far, the time elapsed and the last errors. Refreshed by a timer, so it moves even when frames are not drawn.
const LOAD_T0 = performance.now();
const escHtml = s => String(s).replace(/[&<>]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch]));
function loadCounts() {
  let ready = 0, busy = 0, failed = 0, shown = 0;
  for (const t of tileList) {
    if (t.shown >= 0) shown++;
    for (const lv of Object.values(t.levels)) {
      if (lv.state === 'ready') ready++; else if (lv.state === 'loading') busy++; else if (lv.state === 'failed') failed++;
    }
  }
  return { ready, busy, failed, shown, tiles: tileList.length, total: CFG.groups.length };
}
function setLoading() {
  const c = loadCounts();
  const k = c.shown > 0 ? 5 : (c.ready > 0 || c.busy > 0 ? 3 : 2);
  [...lsteps.children].forEach((li, i) => { li.className = i < k ? 'done' : i === k ? 'now' : ''; });
  const secs = ((performance.now() - LOAD_T0) / 1000).toFixed(0);
  let html = `${escHtml(LOAD_STEPS[k][1])}<br>` +
    `Tiles ready ${c.ready}/${c.total} · on screen ${c.shown}/${c.tiles} · downloading ${c.busy} · failed ${c.failed}<br>` +
    `Data received ${(LOAD.bytes / 1048576).toFixed(1)} MB · ${LOAD.retries} retries · ${secs} s`;
  if (LOAD.errors.length) html += `<div class="lerr">${LOAD.errors.slice(-4).map(escHtml).join('<br>')}</div>`;
  $('lnote').innerHTML = html;
}
setLoading();
const loadTimer = setInterval(() => { if (window.__ready) clearInterval(loadTimer); else setLoading(); }, 500);

// Extra lines in the bottom-right activity panel: what the loader is doing overall
ACT.info = () => {
  const c = loadCounts();
  return [
    `Tiles: ${c.ready}/${c.total} ready · ${c.busy} loading · ${wanted.size} queued${c.failed ? ` · ${c.failed} failed` : ''}`,
    WORK.info(),
    `Data: ${(LOAD.bytes / 1048576).toFixed(1)} MB unpacked · ${LOAD.retries} retries · on screen ${c.shown}/${c.tiles}`,
  ];
};
