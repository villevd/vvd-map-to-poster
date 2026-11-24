// ---------------------------------------------------------------- easter egg: "Render as cake"
// The model becomes a square cake: the city is the icing, the sides show layers of fudge, cream and sponge,
// and candles stand on the top with flickering flames. Everything lives in its own group
// (not under `root`), so tiles, themes and chunk loading never touch it. Turning it off restores the colours.
const cake = { on: false, group: null, candles: [], flames: [], probeUntil: 0, nextProbe: 0, savedOv: null };
const CAKE_COLOURS = {
  ground: [1.00, 0.93, 0.82], water: [0.52, 0.74, 0.96], park: [0.62, 0.86, 0.62], forest: [0.45, 0.72, 0.50],
  tree: [0.30, 0.62, 0.42], building: [1.00, 0.71, 0.80], road_motorway: [0.45, 0.27, 0.16],
  frame: [0.29, 0.15, 0.09], skirt: [1.00, 0.93, 0.82],   // the model's own plinth (fudge) and ground edge (icing, like the top)
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

function captureMapTop() {   // orthographic picture from straight above, edges clamped when sampled
  const W = N * T / 2, size = 1024;
  const rt = new THREE.WebGLRenderTarget(size, size, { depthBuffer: true });
  const y = Math.max(Math.abs(CFG.zrange[0]), Math.abs(CFG.zrange[1])) * 3 + T * 4;
  const cam2 = new THREE.OrthographicCamera(-W, W, W, -W, 1, y * 2 + T * 4);
  cam2.up.set(0, 0, -1); cam2.position.set(0, y, 0); cam2.lookAt(0, 0, 0);
  const hidden = [], hide = o => { if (o && o.visible) { o.visible = false; hidden.push(o); } };
  hide(trafficRoot); hide(planeRoot);
  const bg = scene.background, fog = scene.fog;
  scene.background = null; scene.fog = null;
  renderer.setRenderTarget(rt); renderer.setClearColor(0xffffff, 1); renderer.clear();
  renderer.render(scene, cam2);
  renderer.setRenderTarget(null);
  scene.background = bg; scene.fog = fog; hidden.forEach(o => { o.visible = true; });
  return rt;
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
  // The model has its own plinth (a ring 6% wider than the map, role "frame", painted fudge below) that ends at
  // `top`; the layers stack under it with the same footprint.
  const Wc = W * 1.06;
  const layers = [[cream, 0.5, 0.004], [fudge, 0.7, 0.012], [cream, 0.5, 0.004], [fudge, 0.7, 0.012], [sponge, 1.2, 0.004]];
  const total = layers.reduce((s, l) => s + l[1] * u, 0);
  let y = top - total;
  for (const [m, th, bulge] of layers.slice().reverse()) {   // built bottom-up: the list above is top-down
    const w = Wc * (1 + bulge) * 2, h = th * u;
    add(new THREE.BoxGeometry(w, h, w), m, 0, y + h / 2, 0);
    y += h;
  }
  // fondant: the icing on top is a rounded lip that spills over the plinth's ledge, with thick drips of uneven
  // length running down the sides. Sizes follow the model's own plinth and ground edge when they are loaded.
  const fb = new THREE.Box3(), sb = new THREE.Box3();
  root.traverse(o => {
    if (!o.isMesh || !o.userData.role) return;
    if (o.userData.role === 'frame') fb.expandByObject(o); else if (o.userData.role === 'skirt') sb.expandByObject(o);
  });
  const ph = fb.isEmpty() ? W * 0.07 : fb.max.y - fb.min.y;            // plinth height
  const ledgeY = fb.isEmpty() ? top + ph : fb.max.y;                   // the ledge top, where the lip rests
  const skirtH = sb.isEmpty() ? W * 0.03 : Math.max(sb.max.y - ledgeY, 0);
  const lipR = W * 0.05, lipV = Math.min(W * 0.15, skirtH * 0.5 + W * 0.012), c = Wc - W * 0.01;
  const lipY = ledgeY + skirtH * 0.5;   // centred on the ground-edge wall, so the rounded edge reaches the map's surface
  // The icing takes its colour from a top-down picture of the map (captured in setCake). The picture is clamped
  // at its border, so the map's edge colours (forest green, beige streets, pink roofs) are carried on over the lip
  // and down the drips, as if the printed top layer wrapped over the edge of the cake.
  const fondant = mat(0xffffff, 0.5);
  fondant.onBeforeCompile = sh => {
    sh.uniforms.uTop = { value: cake.topTex }; sh.uniforms.uW = { value: W };
    const NL = String.fromCharCode(10);
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>' + NL + 'varying vec3 vWp;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>' + NL + 'vWp = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', '#include <common>' + NL + 'varying vec3 vWp; uniform sampler2D uTop; uniform float uW;')
      .replace('#include <color_fragment>', '#include <color_fragment>' + NL +
        'diffuseColor.rgb = texture2D(uTop, vec2(vWp.x / (2.0 * uW) + 0.5, 0.5 - vWp.z / (2.0 * uW))).rgb;');
  };
  const lipGeo = new THREE.CylinderGeometry(lipR, lipR, 2 * c, 20).rotateZ(Math.PI / 2).scale(1, lipV / lipR, 1);
  const cornerGeo = new THREE.SphereGeometry(lipR, 20, 14).scale(1, lipV / lipR, 1);
  for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) add(cornerGeo, fondant, sx * c, lipY, sz * c);
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const DR = 22;
  for (let side = 0; side < 4; side++) {
    const g = new THREE.Group(); g.rotation.y = side * Math.PI / 2; grp.add(g);
    const put = (geo, x, y, z, sz = 1) => {
      const o = new THREE.Mesh(geo, fondant); o.position.set(x, y, z); o.scale.z = sz; o.castShadow = o.receiveShadow = true; g.add(o); return o;
    };
    put(lipGeo, 0, lipY, c);
    for (let k = 0; k < DR; k++) {
      const x = ((k + 0.15 + rnd() * 0.7) / DR * 2 - 1) * (c - lipR * 0.3);
      const rt = lipR * (0.4 + 0.35 * rnd()), rb = rt * (0.55 + 0.2 * rnd());
      const len = ph * (0.4 + 2.2 * Math.pow(rnd(), 2.2));      // mostly short, a few long
      const y0 = ledgeY, z = c + lipR * 0.25;
      // a tapered column that widens where it leaves the lip, ending in a rounded bulb
      put(new THREE.CylinderGeometry(rt, rb, len, 14), x, y0 - len / 2, z, 0.55);
      put(new THREE.SphereGeometry(rb * 1.08, 14, 10), x, y0 - len, z, 0.55);
      put(new THREE.SphereGeometry(rt * 1.05, 14, 10), x, y0, z, 0.55);   // the shoulder, blending into the lip
    }
  }
  // serving plate
  const plateW = W * 2.45, plateH = W * 0.03;
  add(new THREE.BoxGeometry(plateW, plateH, plateW), mat(0xeeeef2, 0.35, 0.1), 0, top - total - plateH / 2, 0);

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
  if (cake.rt) { cake.rt.dispose(); cake.rt = null; cake.topTex = null; }
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
    applyThemeTo(root); setThemeColors(); syncPickers();   // the picture below needs the cake colours already on the map
    cake.rt = captureMapTop(); cake.topTex = cake.rt.texture;
    cake.group = buildCake(); scene.add(cake.group);
    runwayMeshes.forEach(m => { m.visible = false; });
    elevScale.show(false);   // the corner elevation poles would stick out of the cake
    cakeProbe(); cake.probeUntil = performance.now() + 20000;
    controls.autoRotate = true; controls.autoRotateSpeed = 1.2;
  } else {
    for (const k in colourOv) delete colourOv[k];
    Object.assign(colourOv, cake.savedOv || {});
    cakeDispose();
    runwayMeshes.forEach(m => { m.visible = true; });
    elevScale.show(!$('scaleOn') || $('scaleOn').checked);
    controls.autoRotate = false;
  }
  applyThemeTo(root); setThemeColors(); syncPickers();
}
if ($('cakeBtn')) $('cakeBtn').onclick = () => setCake(!cake.on);

function cakeUpdate(now) {
  if (!cake.on) return;
  runwayMeshes.forEach(m => { m.visible = false; });   // a runway made by pressing "Land a plane" appears later
  const t = now / 1000;
  for (const f of cake.flames) {
    const s = 1 + 0.12 * Math.sin(t * 11 + f.seed) + 0.07 * Math.sin(t * 23 + f.seed * 2.3);
    f.g.scale.set(1 / Math.sqrt(s), s, 1 / Math.sqrt(s));
    f.g.rotation.z = 0.08 * Math.sin(t * 7 + f.seed);
  }
  if (now > cake.nextProbe && now < cake.probeUntil) { cake.nextProbe = now + 1000; cakeProbe(); }
}
