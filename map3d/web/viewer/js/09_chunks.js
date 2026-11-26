// ---------------------------------------------------------------- chunks beyond the model's edge (generated as you go)
// Walking past the outer tiles asks the local server for the next ones (/api/chunk, map3d/chunks.py). A chunk is built
// from real data on the model's own grid, datum and exaggeration. Chunks the view has left far behind are unloaded.
const CH = CFG.chunks || null;
const CHUNK_KEEP = 3, CHUNK_RETRY_MS = 20000;
const chunkFailed = new Map(), chunkBusy = new Set();
let chunkInflight = 0, chunkOff = !CH, chunkNote = '';
const chunkQuery = CH && new URLSearchParams({ lat: CH.lat, lon: CH.lon, half: CH.half, tiles: CH.tiles, tileHalf: CH.tileHalf,
  zmin: CH.zmin, exag: CH.exag, bexag: CH.bexag, texag: CH.texag, maxLod: CH.maxLod, theme: CH.theme });
const HINT_TEXT = $('hint').textContent;
function setChunkNote(msg) { chunkNote = msg; $('hint').textContent = msg ? msg : HINT_TEXT; }
if (CH && !/^https?:$/.test(location.protocol)) { chunkOff = true; setChunkNote('Edge chunks only work when this page is opened from the GUI: run python -m map3d.gui and open the model there.'); }
const cellOf = p => [Math.round(p.x / T + (N - 1) / 2), Math.round(-p.z / T + (N - 1) / 2)];

function addChunk(c, data) {
  if (tiles.has(c.k)) return;
  const cx = (c.i - (N - 1) / 2) * T, cz = -(c.j - (N - 1) / 2) * T;
  const t = { key: c.k, ij: [c.i, c.j], extra: true, center: new THREE.Vector3(cx, zc, cz), levels: {}, shown: -1, explored: false };
  t.placeholder = new THREE.Mesh(plane, placeholderMat);
  t.placeholder.position.set(cx, CFG.zrange[0] + T * 0.002, cz); t.placeholder.visible = false; scene.add(t.placeholder);
  for (const g of data.groups) {
    t.levels[g.lod] = { cfg: Object.assign(g, { b64: data.blobs[g.id] }), obj: null, state: 'idle', tris: 0 };
    CFG.groups.push(g);                      // so the stats count chunk groups too
  }
  tiles.set(c.k, t); tileList.push(t);
}
function unloadTile(t) {
  if (Object.values(t.levels).some(l => l.state === 'loading')) return;   // finish the download first
  for (let l = 0; l <= maxLod; l++) wanted.delete(t.key + '/' + l);
  for (const lv of Object.values(t.levels)) if (lv.obj) {
    root.remove(lv.obj);
    lv.obj.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  }
  scene.remove(t.placeholder);
  if (t.sat) t.sat.then(tex => tex && tex.dispose());
  keepSatHi(t);
  tiles.delete(t.key); tileList.splice(tileList.indexOf(t), 1);
}
// Loading skeleton: a pulsing tile outline where a chunk is being generated; removed when the chunk arrives or fails.
const skelMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.2, depthWrite: false, side: THREE.DoubleSide });
const skelLineMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.6 });
const skeletons = new Map();
function showSkeleton(c) {
  const cx = (c.i - (N - 1) / 2) * T, cz = -(c.j - (N - 1) / 2) * T;
  const g = new THREE.Group();
  g.add(new THREE.Mesh(plane, skelMat), new THREE.LineSegments(new THREE.EdgesGeometry(plane), skelLineMat));
  g.position.set(cx, CFG.zrange[0] + T * 0.002, cz);
  scene.add(g); skeletons.set(c.k, g);
}
function hideSkeleton(k) {
  const g = skeletons.get(k); if (!g) return;
  scene.remove(g); g.children[1].geometry.dispose(); skeletons.delete(k);
}
async function loadChunk(c) {
  chunkBusy.add(c.k); chunkInflight++; showSkeleton(c);
  try {
    // The request and the (large) JSON parse both happen in a worker: the page keeps drawing while the server builds it
    const url = new URL('/api/chunk?' + chunkQuery + `&i=${c.i}&j=${c.j}`, location.href).href;
    const actId = actStart('Edge chunk (local server)', `cell ${c.i}, ${c.j} · waiting for the server`);
    let r;
    try {
      r = await workRun('json', { url, init: { cache: 'no-store' } }, [], (stage) => actUpdate(actId, `cell ${c.i}, ${c.j} · ${stage}`));
      actEnd(actId, r.status === 200);
      netAdd('Edge chunk (local server)', r.bytes === undefined ? null : r.bytes);
    } catch (e) { actEnd(actId, false); throw e; }
    if (r.status === 404) throw new TypeError('no chunk route');   // an older GUI process without the route
    if (r.status !== 200) throw new Error(`HTTP ${r.status}: ${r.text || ''}`);
    addChunk(c, r.json);
    if (chunkNote) setChunkNote('');
  } catch (e) {
    if (e instanceof TypeError) {            // no server, or no chunk route: stop asking, the model itself keeps working
      chunkOff = true;
      setChunkNote('Edge chunks are not available: restart the GUI (python -m map3d.gui) and reopen the model.');
    } else {
      chunkFailed.set(c.k, performance.now()); console.warn('chunk failed', c.k, e);
      setChunkNote('Could not build the next chunk (see the terminal running the GUI). Retrying.');
    }
  } finally { hideSkeleton(c.k); chunkBusy.delete(c.k); chunkInflight--; }
}
// Runs with the frame updates: the cells beside the view target (within one tile of it) are requested nearest first,
// two at a time; cells far behind the target are unloaded. The model's own tiles are never unloaded.
function requestChunks() {
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 350);
  skelMat.opacity = 0.12 + 0.12 * pulse; skelLineMat.opacity = 0.35 + 0.35 * pulse;
  if (chunkOff || UG_STATE.on) return;   // the underground view is a closed block: no new chunks beyond its edge
  const [ci, cj] = cellOf(controls.target);
  for (const t of [...tileList]) {
    if (!t.extra) continue;
    if (Math.max(Math.abs(t.ij[0] - ci), Math.abs(t.ij[1] - cj)) > CHUNK_KEEP) unloadTile(t);
  }
  if (chunkInflight >= 2) return;
  const now = performance.now();
  let best = null;
  for (let j = cj - 1; j <= cj + 1; j++) for (let i = ci - 1; i <= ci + 1; i++) {
    const k = i + ',' + j;
    if (tiles.has(k) || chunkBusy.has(k)) continue;
    const f = chunkFailed.get(k); if (f !== undefined && now - f < CHUNK_RETRY_MS) continue;
    const cx = (i - (N - 1) / 2) * T, cz = -(j - (N - 1) / 2) * T;
    const d = Math.hypot(cx - controls.target.x, cz - controls.target.z);
    if (d < T && (!best || d < best.d)) best = { i, j, k, d };
  }
  if (best) loadChunk(best);
}

function adapt(now) {
  if (cake.busy) { frames = 0; lastFpsT = now; return; }   // the cake picture sets its own size
  frames++;
  if (now - lastFpsT < 1000) return;
  fps = frames * 1000 / (now - lastFpsT); frames = 0; lastFpsT = now;
  PRECIP.tick(fps);
  // (render resolution is adapted by PERF.applyGfx, together with shadows and textures)
  const info = renderer.info.render;
  $('stats').textContent = `${fps.toFixed(0)} fps · ${(info.triangles / 1000).toFixed(0)}k tris · ${info.calls} calls\n` +
    `res ${(ratio * 100).toFixed(0)}% · ${loadedGroups}/${CFG.groups.length} chunks · ${tileList.filter(t => t.shown >= 0).length}/${tileList.length} tiles`;
}

$('rot').onclick = () => { controls.autoRotate = !controls.autoRotate; controls.autoRotateSpeed = +$('rotSpeed').value; $('rot').style.opacity = controls.autoRotate ? 1 : .7; };
$('rotSpeed').oninput = () => { controls.autoRotateSpeed = +$('rotSpeed').value; };   // the speed only; the Auto-rotate button turns it on
$('reset').onclick = () => {
  controls.autoRotate = false; resetView(); camSave();
  tileList.forEach(t => { t.explored = t.center.distanceTo(controls.target) < 0.5 * T; });
};
$('shot').onclick = () => {
  renderer.render(scene, camera);
  renderer.domElement.toBlob(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b);
    a.download = (CFG.title || 'city') + '-3d.png'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); });
};
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);
});
$('exag').value = EX.t; $('bexag').value = EX.b; $('texag').value = EX.r; applyExaggeration();

