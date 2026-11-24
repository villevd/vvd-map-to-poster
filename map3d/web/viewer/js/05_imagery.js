// ---------------------------------------------------------------- real imagery (Esri World Imagery, as in vvd-kiosk)
// Ground, forest and parks take the satellite picture as a map overlay; buildings take it too, so roofs show
// real roofs and walls show the real surroundings. Tiles are fetched in the browser, one square per model tile,
// and cached. The model's own planar UVs (top-down) place the picture on the geometry.
// ---- water: a glossy, reflective surface that mirrors a sky dome (the skybox), in the sky's current colours
const waterMats = [];
let waterEnv = null, waterEnvKey = '', pmremGen = null;
// The skybox picture (equirectangular, from the model's config). Once loaded it is the background and the reflection source.
let skyTex = null;
if (CFG.skybox) new THREE.TextureLoader().load(CFG.skybox, t => {
  t.mapping = THREE.EquirectangularReflectionMapping; t.colorSpace = THREE.SRGBColorSpace;
  skyTex = t;
  if (!REAL.on) scene.background = t;
  waterEnvKey = '';   // force the reflection map to be rebuilt from the picture
  updateWaterEnv();
});
function updateWaterEnv() {
  const bg = scene.background && scene.background.isColor ? scene.background : new THREE.Color().setStyle(theme.bg, THREE.SRGBColorSpace);
  const white = new THREE.Color(1, 1, 1);   // not the realistic file's WHITE: that one may not exist yet when the theme first sets the sky
  const top = bg.clone().lerp(white, 0.12), horizon = bg.clone().lerp(white, 0.45), ground = new THREE.Color(0.25, 0.22, 0.18).lerp(bg, 0.2);
  const key = skyTex ? 'skybox' : [top, horizon, ground].map(c => c.getHexString()).join();
  if (waterEnv && key === waterEnvKey) return;
  waterEnvKey = key;
  if (!pmremGen) pmremGen = new THREE.PMREMGenerator(renderer);
  let next;
  if (skyTex) next = pmremGen.fromEquirectangular(skyTex).texture;
  else {
    const sky = new THREE.Scene();
    sky.add(new THREE.Mesh(new THREE.SphereGeometry(10, 32, 16), new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: { uTop: { value: top }, uHorizon: { value: horizon }, uGround: { value: ground } },
      vertexShader: 'varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: 'uniform vec3 uTop; uniform vec3 uHorizon; uniform vec3 uGround; varying vec3 vDir;' +
        'void main() { float h = vDir.y; vec3 c = h > 0.0 ? mix(uHorizon, uTop, smoothstep(0.0, 0.6, h)) : mix(uHorizon, uGround, smoothstep(0.0, 0.25, -h)); gl_FragColor = vec4(c, 1.0); }',
    })));
    next = pmremGen.fromScene(sky, 0, 0.1, 100).texture;
  }
  const old = waterEnv;
  waterEnv = next;
  waterMats.forEach(m => { m.envMap = waterEnv; m.needsUpdate = true; });
  if (old) old.dispose();
}
// Lake colour: a clear blue-teal whose lightness follows the theme's water colour (dark themes get deep lakes, pale
// ones lighter lakes). Returns sRGB 0-1 components, like the theme colours.
function lakeTint(c) {
  const lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  const v = new THREE.Color().setHSL(0.57, 0.6, 0.2 + 0.2 * lum, THREE.SRGBColorSpace).getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);
  return [v.r, v.g, v.b];
}
function waterMaterial(flat) {
  if (!waterEnv) updateWaterEnv();
  // Physical material with a clear-coat: the sky dome shows in the water at all angles and strongly at grazing ones
  const m = new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.03, metalness: 0.2, ior: 1.33, specularIntensity: 1,
    clearcoat: PERF.lite ? 0 : 0.8, clearcoatRoughness: 0.04, envMap: waterEnv, envMapIntensity: 1.25, flatShading: flat });
  waterMats.push(m);
  return m;
}
const IMG_KIND = { ground: 'sat', forest: 'sat', park: 'sat', building: 'bld' };
const imgOn = { sat: false, bld: false };
const SAT_CREDIT = 'Imagerie Esri, Maxar, Earthstar Geographics';
const GEO0 = CFG.geo || { lat: 0, lon: 0, half: H };
function metresToLonLat(x, y) {   // model-local metres (east, north) -> lon/lat; linear is exact to well under a metre over a tile
  const f = GEO0.lat * Math.PI / 180;
  const mLat = 111132.92 - 559.82 * Math.cos(2 * f) + 1.175 * Math.cos(4 * f);
  const mLon = 111412.84 * Math.cos(f) - 93.5 * Math.cos(3 * f);
  return [GEO0.lon + x / mLon, GEO0.lat + y / mLat];
}
const tileSlots = { free: 8, queue: [] };
function withTileSlot(fn) {
  return new Promise((resolve, reject) => {
    const run = () => {
      tileSlots.free--;
      fn().then(resolve, reject).finally(() => { tileSlots.free++; const next = tileSlots.queue.shift(); if (next) next(); });
    };
    if (tileSlots.free > 0) run(); else tileSlots.queue.push(run);
  });
}
const satBitmaps = new Map();   // "z/x/y" -> Promise<ImageBitmap | null>, least recently used first
function satBitmap(z, x, y) {
  const k = z + '/' + x + '/' + y;
  if (satBitmaps.has(k)) { const p = satBitmaps.get(k); satBitmaps.delete(k); satBitmaps.set(k, p); return p; }   // touched: keep it
  // Close-up pictures need many more picture tiles than the base ones, so the cache is larger
  if (satBitmaps.size > 400) satBitmaps.delete(satBitmaps.keys().next().value);
  satBitmaps.set(k, withTileSlot(async () => {
    // Stored picture tiles come from IndexedDB; only missing ones go to the network, and then they are stored
    const saved = await idbGet('tiles', k);
    if (saved && saved.blob) { try { return await createImageBitmap(saved.blob); } catch (e) { /* unreadable: fetch again */ } }
    const r = await fetch(`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`);
    if (!r.ok) return null;
    const blob = await r.blob();
    if (blob.size < 32) return null;
    idbPut('tiles', k, { blob, t: Date.now() });
    return createImageBitmap(blob);
  }).catch(() => null));
  return satBitmaps.get(k);
}
/** One canvas texture covering the tile square: north is up, so v = 1 at the north edge. Null when offline.
 *  ``boost`` asks for finer picture tiles and a larger canvas (the close-up upgrade); ``cap`` bounds the fetch count. */
async function buildSatCanvas(tile, boost = 0, cap = 144) {
  const th = T / 2, cx = tile.center.x, my = -tile.center.z;
  const [w, s] = metresToLonLat(cx - th, my - th), [e, n] = metresToLonLat(cx + th, my + th);
  const S = boost ? (N > 1 ? 4096 : 6144) : (N > 1 ? 1024 : 2048);   // close-up: about three times the base sharpness
  const merc = (lon, lat, z) => [(lon + 180) / 360 * 2 ** z,
    (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * 2 ** z];
  const tile2lon = (x, z) => x / 2 ** z * 360 - 180;
  const tile2lat = (y, z) => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / 2 ** z))) * 180 / Math.PI;
  // Zoom that gives about one picture pixel per canvas pixel, lowered until the tile needs a sane number of fetches
  let z = Math.min(boost ? 19 : 18, Math.max(10,Math.round(Math.log2(156543.03 * Math.cos(GEO0.lat * Math.PI / 180) / (T / S)))));
  let x0, x1, y0, y1;
  for (;;) {
    const a = merc(w, n, z), b = merc(e, s, z);
    x0 = Math.floor(a[0]); x1 = Math.floor(b[0]); y0 = Math.floor(a[1]); y1 = Math.floor(b[1]);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) <= cap || z <= 10) break;
    z -= 1;
  }
  const cells = [];
  for (let ty = y0; ty <= y1; ty++) for (let tx = x0; tx <= x1; tx++) cells.push([tx, ty]);
  const bitmaps = await Promise.all(cells.map(([tx, ty]) => satBitmap(z, tx, ty)));
  if (!bitmaps.some(Boolean)) return null;
  const c = document.createElement('canvas'); c.width = c.height = S;
  const g = c.getContext('2d');
  g.fillStyle = '#1a1c16'; g.fillRect(0, 0, S, S);
  c.incomplete = bitmaps.some(b => !b);   // a missing picture tile leaves a dark hole: such a picture is not worth storing
  const px = lon => (lon - w) / (e - w) * S, py = lat => (n - lat) / (n - s) * S;
  cells.forEach(([tx, ty], i) => {
    const bmp = bitmaps[i]; if (!bmp) return;
    const l = px(tile2lon(tx, z)), r = px(tile2lon(tx + 1, z)), t = py(tile2lat(ty, z)), b = py(tile2lat(ty + 1, z));
    g.drawImage(bmp, l, t, r - l, b - t);
  });
  return c;
}
const satTexs = new Set();   // every live satellite texture, so PERF can shrink them all
/** A picture (canvas or ImageBitmap) as a texture for the ground and roofs. */
function satTextureFrom(src) {
  // An ImageBitmap is not flipped on upload the way a canvas is, so a picture read back from storage would sit upside
  // down against the ground (north/south mirrored, roads no longer on their picture). Redraw it on a canvas first.
  if (typeof ImageBitmap !== 'undefined' && src instanceof ImageBitmap) {
    const c = document.createElement('canvas'); c.width = src.width; c.height = src.height;
    c.getContext('2d').drawImage(src, 0, 0);
    if (src.close) src.close();
    src = c;
  }
  const tex = new THREE.Texture(src);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.full = src;                      // PERF re-cuts it smaller under load, from this full-size picture
  satTexs.add(tex);
  tex.addEventListener('dispose', () => { if (!tex.userData.resizing) satTexs.delete(tex); });
  tex.anisotropy = PERF.lite ? 1 : Math.min(16, renderer.capabilities.getMaxAnisotropy());
  PERF.fitTexture(tex);
  tex.needsUpdate = true;
  return tex;
}
async function buildSatTexture(tile) {
  const c = await buildSatCanvas(tile);
  return c ? satTextureFrom(c) : null;
}

// Close-up pictures are kept in IndexedDB, so a model's sharp ground comes back on the next visit without a download.
// Close-up keys are per model (its place and size) and per tile.
// Two stores: 'closeup' (the sharp composite per tile) and 'tiles' (every raw ArcGIS picture tile, so any rebuild
// of a picture reads from here instead of the network). Each keeps its own most-recent-N limit.
const SAT_DB = 'city3d-satellite', SAT_LIMIT = { closeup: 60, tiles: 4000 };
let satDbP = null;
function satDb() {
  if (!satDbP) satDbP = new Promise((res, rej) => {
    if (!('indexedDB' in window)) return rej(new Error('no IndexedDB'));
    const req = indexedDB.open(SAT_DB, 2);
    req.onupgradeneeded = () => {
      for (const name of Object.keys(SAT_LIMIT)) {
        if (!req.result.objectStoreNames.contains(name)) req.result.createObjectStore(name).createIndex('t', 't');
      }
    };
    req.onsuccess = () => res(req.result);
    req.onerror = () => rej(req.error);
  }).catch(() => null);      // no storage (private window, blocked): the picture is simply fetched again
  return satDbP;
}
async function idbGet(store, key) {
  try {
    const db = await satDb(); if (!db) return null;
    return await new Promise(res => {
      const r = db.transaction(store).objectStore(store).get(key);
      r.onsuccess = () => res(r.result || null); r.onerror = () => res(null);
    });
  } catch (e) { return null; }
}
async function idbPut(store, key, value) {
  try {
    const db = await satDb(); if (!db) return;
    await new Promise(res => {
      const tx = db.transaction(store, 'readwrite'), st = tx.objectStore(store);
      st.put(value, key);
      const cnt = st.count();
      cnt.onsuccess = () => {                       // over the limit: drop the oldest entries
        let excess = cnt.result - SAT_LIMIT[store];
        if (excess > 0) st.index('t').openCursor().onsuccess = e => {
          const c = e.target.result;
          if (c && excess-- > 0) { c.delete(); c.continue(); }
        };
      };
      tx.oncomplete = tx.onerror = tx.onabort = () => res();
    });
  } catch (e) { /* storage full or blocked: keep going without it */ }
}
function satHiKeyOf(tile) {
  return ['v3', GEO0.lat, GEO0.lon, T, N, H, tile.key].join('|');   // v3: older entries may hold dark holes
}
/** The close-up picture for a tile: from IndexedDB when stored, otherwise fetched, then stored for next time. */
async function loadSatHi(tile) {
  const key = satHiKeyOf(tile);
  const saved = await idbGet('closeup', key);
  if (saved && saved.blob) {
    try { return satTextureFrom(await createImageBitmap(saved.blob)); } catch (e) { /* unreadable: fetch again */ }
  }
  const c = await buildSatCanvas(tile, 1, 1000);
  if (!c) return null;
  if (!c.incomplete) try {
    c.toBlob(blob => { if (blob) idbPut('closeup', key, { blob, t: Date.now() }); }, 'image/jpeg', 0.9);
  } catch (e) { /* a canvas that cannot be exported is still shown, just not stored */ }
  return satTextureFrom(c);
}
function satTexture(tile) {
  if (tile.satHi) return Promise.resolve(tile.satHi);   // the close-up picture replaces the base one once it is ready
  if (!tile.sat) tile.sat = buildSatTexture(tile).catch(() => null);
  return tile.sat;
}
/** Close to a tile the ground picture is re-cut at about twice the resolution, once; the base picture stays until then. */
const satHiKept = new Map();   // tile key -> close-up picture of a tile that unloaded, so it comes back sharp without a re-download
function keepSatHi(tile) {
  if (!tile.satHi) return;
  satHiKept.delete(tile.key); satHiKept.set(tile.key, tile.satHi); tile.satHi = null;
  while (satHiKept.size > 12) { const [k, old] = satHiKept.entries().next().value; satHiKept.delete(k); old.dispose(); }
}
function upgradeSat(tile) {
  if (tile.satHiReq) return;
  tile.satHiReq = true;
  if (satHiKept.has(tile.key)) {
    tile.satHi = satHiKept.get(tile.key); satHiKept.delete(tile.key);
    if (imgOn.sat || imgOn.bld) syncImagery(tile);
    return;
  }
  loadSatHi(tile).then(tex => {
    if (!tex) return;
    tile.satHi = tex;
    if (imgOn.sat || imgOn.bld) syncImagery(tile);
  }).catch(() => {});
}
/** Planar UVs from the top-down position: the tile square maps onto [0, 1] (u east, v north). */
function tileUV(pos, tile) {
  const th = T / 2, x0 = tile.center.x - th, zN = tile.center.z - th, n = pos.length / 3;
  const uv = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    uv[i * 2] = (pos[i * 3] - x0) / T;                 // viewer x = east
    uv[i * 2 + 1] = (zN + T - pos[i * 3 + 2]) / T;     // viewer z = -north: the north edge (zN) gives v = 1
  }
  return uv;
}
/** Put the picture on (or take it off) every loaded mesh of ``tile`` that the imagery toggles cover. */
async function syncImagery(tile) {
  const meshes = [];
  for (const lv of Object.values(tile.levels)) if (lv.obj) lv.obj.traverse(o => { if (o.userData.img && o.material) meshes.push(o); });
  if (!meshes.length) return;
  // Under heavy load (PERF.texScale 0) the pictures are not even fetched: flat theme colours cost nothing
  const tex = PERF.texScale > 0 && meshes.some(m => imgOn[m.userData.img]) ? await satTexture(tile) : null;
  if (tex) PERF.fitTexture(tex);
  for (const m of meshes) {
    const on = !!tex && imgOn[m.userData.img];
    m.userData.textured = on;
    if (m.material.map !== (on ? tex : null)) { m.material.map = on ? tex : null; m.material.needsUpdate = true; }
    applyThemeTo(m);
  }
  updateCredits();
}
function updateCredits() {
  const parts = [CFG.credits, imgOn.sat || imgOn.bld ? SAT_CREDIT : ''].filter(Boolean);
  $('credits').textContent = parts.join(' · ');
}
async function loadGroup(tile, lod) {
  const lv = tile.levels[lod]; if (!lv || lv.state !== 'idle') return;
  lv.state = 'loading';
  const actId = actStart(lv.cfg.id ? 'Model tile' : 'Edge chunk tile', `detail ${lod} · ${(lv.cfg.meshes || []).length} meshes`);
  const el = lv.cfg.id ? $(lv.cfg.id) : null;  // standalone pages keep model tiles in a <script>; chunks carry them inline
  let src;
  if (lv.cfg.b64) src = lv.cfg.b64;
  else if (CFG.blobBase && lv.cfg.id) {        // baked map: the tile is fetched from the map (compressed, as stored)
    const r = await fetch(CFG.blobBase + encodeURIComponent(lv.cfg.id), { cache: 'no-store' });
    if (!r.ok) throw new Error(`tile ${lv.cfg.id}: HTTP ${r.status}`);
    src = await r.arrayBuffer();
  } else src = el.textContent.trim();
  const buf = await inflate(src);
  lv.cfg.b64 = null; if (el) el.textContent = '';   // free the base64 text once decoded
  const group = new THREE.Group(); group.visible = false;
  for (const m of lv.cfg.meshes) {
    const g = new THREE.BufferGeometry();
    const posArr = new Float32Array(buf, m.pos, m.nv * 3);
    g.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
    if (IMG_KIND[m.role]) g.setAttribute('uv', new THREE.BufferAttribute(tileUV(posArr, tile), 2));
    g.setIndex(new THREE.BufferAttribute(m.idx16 ? new Uint16Array(buf, m.idx, m.nf * 3) : new Uint32Array(buf, m.idx, m.nf * 3), 1));
    if (m.tint !== undefined) {
      const bytes = new Uint8Array(buf, m.tint, m.nv), t = new Float32Array(m.nv * 3);
      for (let i = 0; i < m.nv; i++) { const v = bytes[i] / 127.5; t[i * 3] = t[i * 3 + 1] = t[i * 3 + 2] = v; }
      g.setAttribute('color', new THREE.BufferAttribute(t, 3));
    }
    if (m.nrm !== undefined) g.setAttribute('normal', new THREE.BufferAttribute(new Int8Array(buf, m.nrm, m.nv * 3), 3, true));
    else if (!m.flat) g.computeVertexNormals();
    g.computeBoundingSphere();
    const mat = m.role === 'water' ? waterMaterial(m.flat)
      : new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: m.tint !== undefined, flatShading: m.flat,
          side: m.role === 'bridge_side' ? THREE.DoubleSide : THREE.FrontSide });
    if (m.overlay) { mat.polygonOffset = true; mat.polygonOffsetFactor = -2; mat.polygonOffsetUnits = -2; }
    if (m.group === 'roads' || m.group === 'markings') { roadMats.add(mat); if (roadAlpha < 1) applyRoadAlpha(mat); }
    if (m.role === 'building') addWindows(mat);
    if (m.base !== undefined) g.setAttribute('aBase', new THREE.BufferAttribute(new Float32Array(buf, m.base, m.nv), 1));
    if (m.base !== undefined) heightScale(mat, m.role === 'tree' ? heightU.uTF : heightU.uBF);
    cutMaterial(mat);   // the underground cutaway can discard what lies inside its cut region
    const mesh = new THREE.Mesh(g, mat);
    if (m.variant !== undefined && m.tint !== undefined) {
      const ca = g.getAttribute('color').array, grey = new Float32Array(m.nv);
      for (let i = 0; i < m.nv; i++) grey[i] = ca[i * 3];
      mesh.userData.treeVar = { n: m.nv, grey, variant: new Uint8Array(buf, m.variant, m.nv) };
    }
    if (m.cast && (m.base !== undefined || CFG.underground)) {
      mesh.customDepthMaterial = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
      if (m.base !== undefined) heightScale(mesh.customDepthMaterial, m.role === 'tree' ? heightU.uTF : heightU.uBF);
      cutMaterial(mesh.customDepthMaterial);   // a cut-away piece of the city must not keep casting its shadow
    }
    Object.assign(mesh.userData, { role: m.role, group: m.group, cast: m.cast, receive: m.receive, img: IMG_KIND[m.role] });
    if (m.cell) mesh.userData.cellC = new THREE.Vector3(m.cell[0], m.cell[1], m.cell[2]);   // tree cell: culled by its own distance
    group.add(mesh); lv.tris += m.nf;
  }
  if (lv.cfg.imp) {
    group.add(makeImpostors(new Float32Array(buf, lv.cfg.imp.off, lv.cfg.imp.n * 6), lv.cfg.imp.n));
    lv.tris += lv.cfg.imp.n * 2;
  }
  applyThemeTo(group); paintTreeVariants(group); setThemeColors();
  root.add(group); lv.obj = group; lv.state = 'ready'; loadedGroups++;
  actEnd(actId);
  setLayers(group);
  if (imgOn.sat || imgOn.bld) syncImagery(tile);
  if (ugTileReady) ugTileReady();   // the underground block follows the terrain: it needs this tile's ground
}
for (const [id, kind] of [['satOn', 'sat'], ['texOn', 'bld']]) {
  $(id).onchange = () => { imgOn[kind] = $(id).checked; tileList.forEach(t => syncImagery(t)); updateCredits(); };
}
document.querySelectorAll('input[data-group]').forEach(cb => {
  cb.onchange = () => { layers[cb.dataset.group] = cb.checked; tileList.forEach(t => Object.values(t.levels).forEach(l => l.obj && setLayers(l.obj))); };
});
// Road transparency: roads and their markings fade out so the buildings and ground show through.
// 1 is opaque (the default); below 1 the road materials become transparent.
const roadMats = new Set();
let roadAlpha = 1;
function applyRoadAlpha(mat) {
  mat.transparent = roadAlpha < 1; mat.opacity = roadAlpha; mat.depthWrite = roadAlpha >= 1;
  mat.needsUpdate = true;
}
function setRoadAlpha(v) {
  roadAlpha = v;
  $('roadOpO').textContent = Math.round(v * 100) + '%';
  roadMats.forEach(applyRoadAlpha);
}
$('roadOp').oninput = () => setRoadAlpha(+$('roadOp').value);
$('roadOpO').textContent = '100%';
// Terrain exaggeration scales the whole model vertically; building and tree height then rescale heights above the
// ground (per-vertex 'aBase') so the three are independent. All start at the values the model was built with.
const EXC = CFG.exag || { terrain: 1, building: 1 };   // models made before this setting existed
const EX = { t: Math.max(EXC.terrain, 0.01), b: Math.max(EXC.building, 0.01),
             r: Math.max(EXC.tree ?? EXC.building, 0.01) };
const heightU = { uBF: { value: 1 }, uTF: { value: 1 } };   // uBF: buildings, uTF: trees
function heightScale(mat, u = heightU.uBF) {
  const prev = mat.onBeforeCompile;
  mat.onBeforeCompile = (sh, r) => {
    if (prev) prev(sh, r);
    sh.uniforms.uBF = u;
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nattribute float aBase; uniform float uBF;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed.y = aBase + (transformed.y - aBase) * uBF;');
  };
  mat.customProgramCacheKey = () => 'hs' + (prev ? 'w' : '');
}
function applyExaggeration() {
  const T = +$('exag').value, B = +$('bexag').value, R = +$('texag').value;
  $('vExagO').textContent = T.toFixed(1) + '×'; $('vBexagO').textContent = B.toFixed(1) + '×';
  $('vTexagO').textContent = R.toFixed(1) + '×';
  root.scale.y = T / EX.t;
  heightU.uBF.value = (B / EX.b) / (T / EX.t);
  heightU.uTF.value = (R / EX.r) / (T / EX.t);
  elevScale.update(T);   // the corner scales follow the terrain height
}
$('exag').oninput = $('bexag').oninput = $('texag').oninput = applyExaggeration;

