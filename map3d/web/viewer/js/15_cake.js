// ---------------------------------------------------------------- easter egg: "Render as cake"
// The model becomes a square cake: the city is the icing, the sides show layers of fudge, cream and sponge with
// drips on the top edge, and candles stand on the top with flickering flames. Everything lives in its own group
// (not under `root`), so tiles, themes and chunk loading never touch it. Turning it off restores the colours.
const cake = { on: false, group: null, candles: [], flames: [], probeUntil: 0, nextProbe: 0, savedOv: null };
const CAKE_COLOURS = {
  ground: [1.00, 0.93, 0.82], water: [0.52, 0.74, 0.96], park: [0.62, 0.86, 0.62], forest: [0.45, 0.72, 0.50],
  tree: [0.30, 0.62, 0.42], building: [1.00, 0.71, 0.80], road_motorway: [0.45, 0.27, 0.16],
  road_primary: [0.40, 0.24, 0.14], road_secondary: [0.36, 0.22, 0.13], road_tertiary: [0.33, 0.20, 0.12],
  road_residential: [0.30, 0.18, 0.11], rail: [0.55, 0.35, 0.22], bridge: [0.88, 0.62, 0.42],
};

function cakeStripeTexture(a, b) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'); g.fillStyle = a; g.fillRect(0, 0, 64, 64); g.fillStyle = b;
  for (let i = -64; i < 128; i += 32) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i + 16, 0); g.lineTo(i + 80, 64); g.lineTo(i + 64, 64); g.fill(); }
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(1, 3);
  t.colorSpace = THREE.SRGBColorSpace; return t;
}
function cakeGlowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, 'rgba(255,230,150,1)'); r.addColorStop(0.35, 'rgba(255,170,60,0.45)'); r.addColorStop(1, 'rgba(255,140,40,0)');
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

function buildCake() {
  const W = N * T / 2, u = W * 0.07, top = CFG.zrange[0] - T * 0.001;
  const grp = new THREE.Group();
  const mat = (color, rough = 0.6, metal = 0) => new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
  const fudge = mat(0x4a2616, 0.32), cream = mat(0xfff1d6, 0.8), sponge = mat(0xe9b768, 0.9);
  const add = (geo, m, x, y, z) => {
    const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = true; grp.add(o); return o;
  };
  // layers, bottom to top: [material, thickness in u, outward bulge in W]
  const layers = [[sponge, 1.2, 0], [fudge, 0.7, 0.012], [cream, 0.5, 0.004], [fudge, 0.7, 0.012], [cream, 0.5, 0.004], [fudge, 0.8, 0.012]];
  const total = layers.reduce((s, l) => s + l[1] * u, 0);
  let y = top - total;
  for (const [m, th, bulge] of layers) {
    const w = W * (1 + bulge) * 2, h = th * u;
    add(new THREE.BoxGeometry(w, h, w), m, 0, y + h / 2, 0);
    y += h;
  }
  // fudge drips hanging off the top edge
  const dripR = u * 0.22, rng = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
  const topFudge = layers[layers.length - 1][1] * u, edge = W * (1 + 0.012) + dripR * 0.2;
  for (let side = 0; side < 4; side++) {
    const a = side * Math.PI / 2, nx = Math.round(Math.sin(a)), nz = Math.round(Math.cos(a));
    for (let k = 0; k < 16; k++) {
      const t = (k + 0.3 + rng() * 0.4) / 16 * 2 - 1, len = u * (0.35 + rng() * 1.1), r = dripR * (0.7 + rng() * 0.6);
      const cyl = new THREE.CylinderGeometry(r, r, len, 10), cap = new THREE.SphereGeometry(r, 10, 8);
      const px = nx !== 0 ? nx * edge : t * W * 0.97, pz = nz !== 0 ? nz * edge : t * W * 0.97;
      const y0 = top - topFudge * 0.5;   // drips start mid-way down the top fudge layer
      add(cyl, fudge, px, y0 - len / 2, pz);
      add(cap, fudge, px, y0 - len, pz);
    }
  }
  // serving plate
  const plateW = W * 2.28, plateH = W * 0.03;
  add(new THREE.BoxGeometry(plateW, plateH, plateW), mat(0xd8dce2, 0.25, 0.7), 0, top - total - plateH / 2, 0);

  // candles on a ring inset from the edge, plus one in the middle
  const spots = [[0, 0]];
  const ring = 0.72 * W;
  for (const s of [-1, -0.5, 0, 0.5, 1]) for (const [x, z] of [[s * ring, -ring], [s * ring, ring], [-ring, s * ring], [ring, s * ring]]) {
    if (!spots.some(p => Math.abs(p[0] - x) < 1 && Math.abs(p[1] - z) < 1)) spots.push([x, z]);
  }
  const stripes = ['#ff6b81', '#4dabf7', '#ffd43b', '#69db7c'].map(c => cakeStripeTexture('#ffffff', c));
  const glow = cakeGlowTexture();
  const cH = W * 0.2, cR = W * 0.014;
  cake.candles = []; cake.flames = [];
  spots.forEach(([x, z], i) => {
    const c = new THREE.Group(); c.position.set(x, CFG.zrange[0], z);
    const body = new THREE.Mesh(new THREE.CylinderGeometry(cR, cR, cH, 14), new THREE.MeshStandardMaterial({ map: stripes[i % 4], roughness: 0.5 }));
    body.position.y = cH / 2; body.castShadow = true;
    const wick = new THREE.Mesh(new THREE.CylinderGeometry(cR * 0.08, cR * 0.08, cR * 1.2, 4), mat(0x222222));
    wick.position.y = cH + cR * 0.5;
    const flame = new THREE.Group(); flame.position.y = cH + cR * 2.4;
    const outer = new THREE.Mesh(new THREE.SphereGeometry(cR * 0.85, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffa31a }));
    outer.scale.set(1, 1.9, 1);
    const inner = new THREE.Mesh(new THREE.SphereGeometry(cR * 0.5, 10, 8), new THREE.MeshBasicMaterial({ color: 0xfff3b0 }));
    inner.scale.set(1, 1.7, 1); inner.position.y = -cR * 0.35;
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    halo.scale.set(cR * 9, cR * 9, 1);
    flame.add(outer, inner, halo);
    c.add(body, wick, flame); grp.add(c);
    cake.candles.push({ g: c, x, z, hit: false });
    cake.flames.push({ g: flame, seed: i * 1.7 });
  });
  cake.stripeTex = stripes; cake.glowTex = glow;
  return grp;
}

const cakeRay = new THREE.Raycaster(), cakeDown = new THREE.Vector3(0, -1, 0), cakeFrom = new THREE.Vector3();
function cakeProbe() {   // stand each candle on whatever is under it (roof, ground, tree); tiles may still be loading
  let missing = 0;
  for (const c of cake.candles) {
    if (c.hit) continue;
    cakeFrom.set(c.x, CFG.zrange[1] * 2 + T, c.z);
    cakeRay.set(cakeFrom, cakeDown);
    const h = cakeRay.intersectObject(root, true).find(i => i.object.visible && i.object.isMesh && !i.object.userData.impostor);
    if (h) { c.g.position.y = h.point.y - 0.5; c.hit = true; } else missing++;
  }
  return missing;
}

function cakeDispose() {
  if (!cake.group) return;
  scene.remove(cake.group);
  cake.group.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  (cake.stripeTex || []).forEach(t => t.dispose()); if (cake.glowTex) cake.glowTex.dispose();
  cake.group = null; cake.candles = []; cake.flames = [];
}

function setCake(on) {
  if (on === cake.on) return;
  cake.on = on;
  const btn = $('cakeBtn'); if (btn) btn.textContent = on ? 'Back to the map' : 'Render as cake';
  if (on) {
    cake.savedOv = Object.assign({}, colourOv);
    Object.assign(colourOv, CAKE_COLOURS);
    cake.group = buildCake(); scene.add(cake.group);
    cakeProbe(); cake.probeUntil = performance.now() + 20000;
    controls.autoRotate = true; controls.autoRotateSpeed = 1.2;
  } else {
    for (const k in colourOv) delete colourOv[k];
    Object.assign(colourOv, cake.savedOv || {});
    cakeDispose();
    controls.autoRotate = false;
  }
  applyThemeTo(root); setThemeColors(); syncPickers();
}
if ($('cakeBtn')) $('cakeBtn').onclick = () => setCake(!cake.on);

function cakeUpdate(now) {
  if (!cake.on) return;
  const t = now / 1000;
  for (const f of cake.flames) {
    const s = 1 + 0.12 * Math.sin(t * 11 + f.seed) + 0.07 * Math.sin(t * 23 + f.seed * 2.3);
    f.g.scale.set(1 / Math.sqrt(s), s, 1 / Math.sqrt(s));
    f.g.rotation.z = 0.08 * Math.sin(t * 7 + f.seed);
  }
  if (now > cake.nextProbe && now < cake.probeUntil) { cake.nextProbe = now + 1000; cakeProbe(); }
}
