// ---------------------------------------------------------------- remember the viewer's settings (this browser)
// Layers, imagery, exaggeration, sun, fog and quality come back as they were left. The theme is not stored here:
// the GUI chooses it per model.
const VIEW_KEY = 'map3d-view';
const VIEW_IDS = ['satOn', 'texOn', 'treeMatch', 'treeRandom', 'followBox', 'fogOn', 'shadowsOn', 'fowOn', 'exag', 'bexag', 'texag', 'az', 'el', 'dd', 'tspeed', 'rotSpeed', 'quality', 'cullOn', 'cullMin', 'roadOp', 'ugMode', 'ugPos', 'ugSize', 'ugDepth', 'ugXray', 'ugWater', 'ugEsker', 'ugMines', 'ugDrill', 'ugClaims'];
const VIEW_GROUPS = ['building', 'tree', 'roads', 'markings', 'traffic'];
const groupBox = g => document.querySelector(`input[data-group="${g}"]`) || (PARENT_DOC && PARENT_DOC.querySelector(`input[data-group="${g}"]`));
let viewRestoring = false;
function viewSave() {
  const s = {};
  for (const id of VIEW_IDS) { const el = $(id); if (el) s[id] = el.type === 'checkbox' ? el.checked : el.value; }
  for (const g of VIEW_GROUPS) { const el = groupBox(g); if (el) s['group:' + g] = el.checked; }
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(s)); } catch (e) {}
}
function viewRestore() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem(VIEW_KEY) || '{}') || {}; } catch (e) { s = {}; }
  const touched = [];
  viewRestoring = true;
  for (const id of VIEW_IDS) {
    const el = $(id); if (!el || !(id in s)) continue;
    if (el.type === 'checkbox') el.checked = !!s[id];
    else if (el.tagName === 'SELECT') { if (![...el.options].some(o => o.value === String(s[id]))) continue; el.value = String(s[id]); }
    else el.value = String(s[id]);
    if (id !== 'rotSpeed') touched.push(el);   // the speed slider must not switch auto-rotate on
    else controls.autoRotateSpeed = +el.value;
  }
  for (const g of VIEW_GROUPS) {
    const el = groupBox(g); if (!el || !('group:' + g in s)) continue;
    el.checked = !!s['group:' + g]; touched.push(el);
  }
  // Fire the controls' own handlers (sliders, layers, imagery, fog) so the scene follows the restored values
  for (const el of touched) {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  viewRestoring = false;
}
for (const doc of [document, PARENT_DOC]) {
  if (!doc) continue;
  for (const type of ['change', 'input']) doc.addEventListener(type, () => { if (!viewRestoring) viewSave(); }, true);
}
viewRestore();

// The camera: where you left it, per model (so a reload does not turn you back to the start view)
const CAM_KEY = 'map3d-cam:' + [CFG.geo.lat, CFG.geo.lon, CFG.geo.half, CFG.tiles].join(',');
function camSave() {
  try { localStorage.setItem(CAM_KEY, JSON.stringify({ p: camera.position.toArray(), t: controls.target.toArray() })); } catch (e) {}
}
function camRestore() {
  try {
    const s = JSON.parse(localStorage.getItem(CAM_KEY) || 'null');
    if (s && s.p && s.t) { camera.position.fromArray(s.p); controls.target.fromArray(s.t); controls.update(); return true; }
  } catch (e) {}
  return false;
}
camRestore();
horizonView();   // Sky view is the default on every load (the saved position is kept; only the tilt and low elevation are applied)
controls.addEventListener('end', camSave);

// Depth precision: the near and far planes follow the camera instead of spanning the whole model all the time.
// A 30 m / 90 km range leaves centimetres of depth error far away, which is what makes roofs shimmer (z-fighting).
let clipDist = 0;
function fitClip() {
  const dist = camera.position.distanceTo(controls.target);
  if (clipDist && Math.abs(dist - clipDist) < 0.03 * clipDist) return;
  clipDist = dist;
  camera.near = Math.max(0.5, dist * 0.02);
  camera.far = dist + H * 6;
  camera.updateProjectionMatrix();
}
let trafficAcc = 0, trafficFrame = 0;
renderer.setAnimationLoop(now => {
  const cpu0 = performance.now();
  const dt = Math.min((now - lastFrameT) / 1000, 0.1);
  const dtReal = Math.min((now - lastFrameT) / 1000, 1);   // the plane keeps real time even when frames are slow
  lastFrameT = now;
  const trafficShown = layers.traffic && !PERF.noTraffic;   // under heavy load traffic stops being simulated at all
  trafficRoot.visible = trafficShown;
  trafficAcc += dt;
  if (trafficUpdate && trafficShown && !(PERF.halfTraffic && (trafficFrame++ & 1))) {   // half rate under load, same speed
    trafficUpdate(trafficAcc * (+$('tspeed').value), REAL.on); trafficAcc = 0;
  } else if (!trafficShown) trafficAcc = 0;
  if (planeUpdate) planeUpdate(dtReal);
  keyMove(dt);
  // Left-drag pan moves the view by a share of its distance, so zoomed right in one drag covers only metres and the
  // city cannot be crossed. Pan faster as the camera gets closer: 1x at the start distance, up to 6x when very close.
  controls.panSpeed = THREE.MathUtils.clamp(Math.sqrt(startDist / Math.max(1e-6, camera.position.distanceTo(controls.target))), 1, 6);
  controls.update();
  if (skyDir) camera.lookAt(camera.position.x + skyDir.x, camera.position.y + skyDir.y, camera.position.z + skyDir.z);
  fitClip();
  PRECIP.update(now);
  PERF.frame(now);
  if (tick++ % 6 === 0) updateTiles();
  adapt(now);
  stepRealistic(now);
  cakeUpdate(now);
  placeSearch.update(now);
  if (ugUpdate) ugUpdate(now);
  renderer.render(scene, camera);
  PERF.cpuSample(performance.now() - cpu0);   // JavaScript time of this frame: the bottleneck probe compares it to the frame time
  if (!window.__ready && tileList.some(t => t.shown >= 0)) {
    $('loading').classList.add('done'); window.__ready = true;
  } else if (!window.__ready) {
    setLoading(loadedGroups, CFG.groups.length);
  }
});
