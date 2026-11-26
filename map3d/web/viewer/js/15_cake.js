// ---------------------------------------------------------------- easter egg: "Render as cake"
// The model is rendered as a square cake (one still PNG, see renderCakePng): the city is the icing, the sides show layers of fudge, cream and sponge,
// and candles stand on the top with flickering flames. Everything lives in its own group
// (not under `root`), so tiles, themes and chunk loading never touch it. Turning it off restores the colours.
const cake = { on: false, busy: false, group: null, candles: [], flames: [], probeUntil: 0, nextProbe: 0, savedOv: null };
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

// Reads the model's own geometry: the lowest terrain height, and the height of the terrain along each of the four
// edges (from the top of the model's ground-edge wall). Each side is returned in its own frame: x along the side
// (-W..W), y height, with the side's outward direction as local +z (sides 0..3 face +z, +x, -z, -x).
function cakeMeasure(W) {
  root.updateMatrixWorld(true);
  const tol = W * 0.003, bin = W * 0.002, v = new THREE.Vector3();
  let gMin = Infinity;
  const sides = [new Map(), new Map(), new Map(), new Map()];
  root.traverse(o => {
    const role = o.userData.role;
    if (!o.isMesh || !o.geometry.attributes.position || !(role === 'ground' || role === 'park' || role === 'forest' || role === 'skirt')) return;
    const pos = o.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      v.set(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(o.matrixWorld);
      if (role !== 'skirt') { if (v.y < gMin) gMin = v.y; continue; }
      for (let sd = 0; sd < 4; sd++) {
        const th = sd * Math.PI / 2, c = Math.round(Math.cos(th)), sn = Math.round(Math.sin(th));
        const px = v.x * c - v.z * sn, pz = v.x * sn + v.z * c;     // world -> the side's own frame
        if (Math.abs(pz - W) > tol || Math.abs(px) > W + tol) continue;
        const k = Math.round(px / bin), cur = sides[sd].get(k);
        if (!cur || v.y > cur[1]) sides[sd].set(k, [px, v.y]);    // the top of the wall at this spot
      }
    }
  });
  let edgeMin = Infinity;
  const out = sides.map(m => {
    let pts = [...m.values()].sort((p, q) => p[0] - q[0]);
    if (!pts.length) pts = [[-W, gMin], [W, gMin]];
    for (const p of pts) edgeMin = Math.min(edgeMin, p[1]);
    return pts;
  });
  return { sides: out, yCut: Math.min(gMin, edgeMin) - W * 0.002 };
}

// The round cake's cut: the map is clipped by a ring of planes (a 72-sided polygon whose inscribed circle is the cake's edge).
function circlePlanes(W, n) {
  const out = [];
  for (let k = 0; k < n; k++) { const a = k / n * Math.PI * 2; out.push(new THREE.Plane(new THREE.Vector3(-Math.cos(a), 0, -Math.sin(a)), W)); }
  return out;
}

// Terrain height just inside the circular edge, one reading per step around the circle (a ray straight down from above).
function cakeRimHeights(W, n, fallbackY) {
  root.updateMatrixWorld(true);
  const meshes = [];
  root.traverse(o => { if (o.isMesh && o.visible && (o.userData.role === 'ground' || o.userData.role === 'park' || o.userData.role === 'forest')) meshes.push(o); });
  const ray = new THREE.Raycaster(), down = new THREE.Vector3(0, -1, 0), from = new THREE.Vector3();
  const hi = (CFG.zrange[1] * 2 + T) * Math.max(1, root.scale.y) * 2;
  const hs = [];
  for (let k = 0; k < n; k++) {
    const a = k / n * Math.PI * 2;
    from.set(W * 0.995 * Math.cos(a), hi, W * 0.995 * Math.sin(a)); ray.set(from, down);
    const h = ray.intersectObjects(meshes, false)[0];
    hs.push(h ? h.point.y : null);
  }
  return hs.map((v, k) => {   // gaps take the nearest reading around the circle
    if (v !== null) return v;
    for (let d = 1; d < n; d++) {
      const p = hs[(k + d) % n], q = hs[(k - d + n) % n];
      if (p !== null) return p; if (q !== null) return q;
    }
    return fallbackY;
  });
}

// Round variant: the rolled edge follows the terrain around the circle, with drips of uneven length hanging from it.
function buildRoundRim(grp, W, u, top, fondant) {
  const lipR = W * 0.016, R = W, NR = 96;
  const hs = cakeRimHeights(W, NR, top);
  const rimY = a => {
    const f = (((a / (2 * Math.PI)) % 1) + 1) % 1 * NR, i = Math.floor(f) % NR, j = (i + 1) % NR, t = f - Math.floor(f);
    return hs[i] + (hs[j] - hs[i]) * t;
  };
  const pts = hs.map((h, k) => { const a = k / NR * Math.PI * 2; return new THREE.Vector3(R * Math.cos(a), h - lipR * 0.4, R * Math.sin(a)); });
  const lip = new THREE.Mesh(flatLip(pts, true, lipR * 1.3, lipR * 0.5), fondant);
  lip.castShadow = lip.receiveShadow = true; grp.add(lip);
  // a solid wall from the cake's top layer up to the rim, so the edge is attached to the cake instead of floating
  const wp = [], wi = [], rr = R * 0.998;
  for (let k = 0; k <= NR; k++) {
    const a = k / NR * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
    wp.push(rr * c, hs[k % NR] - lipR * 0.4, rr * s, rr * c, top, rr * s);
  }
  for (let k = 0; k < NR; k++) { const A = 2 * k, B = A + 1, C = A + 2, D = A + 3; wi.push(A, B, C, C, B, D); }
  const wallGeo = new THREE.BufferGeometry();
  wallGeo.setAttribute('position', new THREE.Float32BufferAttribute(wp, 3)); wallGeo.setIndex(wi); wallGeo.computeVertexNormals();
  // plain cream, not the icing picture: the map's colours would stream down the wall
  const wallMat = new THREE.MeshStandardMaterial({ color: 0xfff1d6, roughness: 0.8, side: THREE.DoubleSide });
  wallMat.shadowSide = THREE.BackSide;
  const wall = new THREE.Mesh(wallGeo, wallMat); wall.castShadow = true; wall.receiveShadow = false; grp.add(wall);   // no receiving: no acne speckle
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const put = (geo, x, yy, z) => { const o = new THREE.Mesh(geo, fondant); o.position.set(x, yy, z); o.castShadow = o.receiveShadow = true; grp.add(o); return o; };
  for (let k = 0; k < 96; k++) {
    const a = rnd() * Math.PI * 2, x = R * Math.cos(a), z = R * Math.sin(a);
    const rt = lipR * (0.35 + 0.3 * rnd()), rb = rt * (0.55 + 0.2 * rnd());
    const len = u * (0.25 + 1.0 * Math.pow(rnd(), 2.2));
    const y0 = rimY(a) - lipR * 0.4;
    put(new THREE.CylinderGeometry(rt, rb, len, 14), x, y0 - len / 2, z);
    put(new THREE.SphereGeometry(rb * 1.08, 14, 10), x, y0 - len, z);
    put(new THREE.SphereGeometry(rt * 1.05, 14, 10), x, y0, z);
  }
}

// A flat, ribbon-like lip: an ellipse of half-width halfW (sideways from the edge) and half-height halfH swept along the
// curve, instead of a round tube. Closed curves wrap around; open ones end at their last point.
function flatLip(points, closed, halfW, halfH) {
  const curve = new THREE.CatmullRomCurve3(points, closed);
  const S = points.length * 4, M = 10, up = new THREE.Vector3(0, 1, 0);
  const p = new THREE.Vector3(), tg = new THREE.Vector3(), out = new THREE.Vector3(), pos = [], idx = [];
  const rings = closed ? S : S + 1;
  for (let i = 0; i < rings; i++) {
    curve.getPointAt(i / S, p); curve.getTangentAt(i / S, tg);
    out.crossVectors(up, tg).normalize();
    if (out.x * p.x + out.z * p.z < 0) out.negate();   // point away from the middle of the cake
    for (let j = 0; j < M; j++) {
      const a = j / M * Math.PI * 2, c = Math.cos(a) * halfW;
      pos.push(p.x + out.x * c, p.y + Math.sin(a) * halfH, p.z + out.z * c);
    }
  }
  for (let i = 0; i < (closed ? S : S); i++) for (let j = 0; j < M; j++) {
    const a = i * M + j, b = i * M + (j + 1) % M, c = ((i + 1) % rings) * M + j, d = ((i + 1) % rings) * M + (j + 1) % M;
    idx.push(a, c, b, b, c, d);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
  return g;
}

function buildCake() {
  const W = N * T / 2, u = W * 0.07, round = !!cake.round;
  const grp = new THREE.Group();
  const mat = (color, rough = 0.6, metal = 0) => new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
  const fudge = mat(0x4a2616, 0.32), cream = mat(0xfff1d6, 0.8), sponge = mat(0xe9b768, 0.9);
  const add = (geo, m, x, y, z) => {
    const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = true; grp.add(o); return o;
  };
  // The model's own plinth is hidden, and its ground-edge wall is cut off below the lowest terrain, so that raising
  // the terrain exaggeration only exaggerates the hills, not the base. The cake's layers stand under the cut.
  const { sides, yCut } = cakeMeasure(W);
  const top = yCut;
  const clip = new THREE.Plane(new THREE.Vector3(0, 1, 0), -yCut);   // keeps everything above the cut
  const ringPlanes = round ? circlePlanes(W, 72) : [];
  cake.undo = [];
  root.traverse(o => {
    if (!o.isMesh) return;
    if (o.userData.role === 'frame' && o.visible) { o.visible = false; cake.undo.push(() => { o.visible = true; }); }
    else if (o.userData.role === 'skirt' || round) {
      const m = o.material, prev = m.clippingPlanes;
      m.clippingPlanes = (o.userData.role === 'skirt' ? [clip] : []).concat(ringPlanes); m.needsUpdate = true;
      cake.undo.push(() => { m.clippingPlanes = prev; m.needsUpdate = true; });
    }
  });
  renderer.localClippingEnabled = true; cake.undo.push(() => { renderer.localClippingEnabled = false; });
  // layers, top to bottom: [material, thickness in u, outward bulge in W]. Flush with the map's edge.
  const layers = [[cream, 0.5, 0.004], [fudge, 0.7, 0.012], [cream, 0.5, 0.004], [fudge, 0.7, 0.012], [sponge, 1.2, 0.004]];
  const total = layers.reduce((s, l) => s + l[1] * u, 0);
  let y = top - total;
  for (const [m, th, bulge] of layers.slice().reverse()) {   // built bottom-up
    const w = W * (1 + bulge) * 2, h = th * u;
    const lay = add(round ? new THREE.CylinderGeometry(w / 2, w / 2, h, 128) : new THREE.BoxGeometry(w, h, w), m, 0, y + h / 2, 0);
    if (round) {   // a closed cylinder casting from its back faces does not shadow its own lit side; receiving shadows is what speckles it
      m.shadowSide = THREE.BackSide; lay.receiveShadow = false;
    }
    y += h;
  }
  // The icing takes its colour from a top-down picture of the map (captured in renderCakePng). The picture is clamped
  // at its border, so the map's edge colours (forest green, beige streets, pink roofs) are carried on over the lip
  // and down the drips, as if the printed top layer wrapped over the edge of the cake.
  const fondant = mat(0xffffff, 0.5);
  if (round) fondant.side = THREE.DoubleSide;   // the round wall is seen from both sides
  fondant.onBeforeCompile = sh => {
    sh.uniforms.uTop = { value: cake.topTex }; sh.uniforms.uW = { value: W };
    const NL = String.fromCharCode(10);
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>' + NL + 'varying vec3 vWp;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>' + NL + 'vWp = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', '#include <common>' + NL + 'varying vec3 vWp; uniform sampler2D uTop; uniform float uW;')
      .replace('#include <color_fragment>', '#include <color_fragment>' + NL +
        'diffuseColor.rgb = texture2D(uTop, vec2(vWp.x / (2.0 * uW) + 0.5, 0.5 - vWp.z / (2.0 * uW))).rgb;');
  };
  // A thin rolled edge that follows the terrain along each side, with drips of uneven length.
  const lipR = W * 0.016, zc = W * 1.004, ce = zc;     // ce: the lip runs out to the corner
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const cornerY = [];
  if (round) buildRoundRim(grp, W, u, top, fondant);
  else sides.forEach((pts, sd) => {
    const g = new THREE.Group(); g.rotation.y = sd * Math.PI / 2; grp.add(g);
    const put = (geo, x, yy, z, sz = 1) => {
      const o = new THREE.Mesh(geo, fondant); o.position.set(x, yy, z); o.scale.z = sz; o.castShadow = o.receiveShadow = true; g.add(o); return o;
    };
    const line = [[-ce, pts[0][1]], ...pts, [ce, pts[pts.length - 1][1]]];
    const hAt = x => {
      for (let i = 1; i < line.length; i++) if (x <= line[i][0]) {
        const [x0, y0] = line[i - 1], [x1, y1] = line[i], t = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
        return y0 + (y1 - y0) * Math.min(1, Math.max(0, t));
      }
      return line[line.length - 1][1];
    };
    const step = Math.max(1, Math.floor(line.length / 90));
    const keep = line.filter((_, i) => i % step === 0 || i === line.length - 1);
    put(flatLip(keep.map(([x, yy]) => new THREE.Vector3(x, yy - lipR * 0.4, zc)), false, lipR * 1.3, lipR * 0.5), 0, 0, 0);
    cornerY[sd] = [line[0][1], line[line.length - 1][1]];
    const DR = 16;
    for (let k = 0; k < DR; k++) {
      const x = ((k + 0.15 + rnd() * 0.7) / DR * 2 - 1) * (W * 0.97);
      const rt = lipR * (0.35 + 0.3 * rnd()), rb = rt * (0.55 + 0.2 * rnd());
      const len = u * (0.25 + 1.0 * Math.pow(rnd(), 2.2));     // mostly short, rarely long
      const y0 = hAt(x) - lipR * 0.4, z = W * 1.014;
      put(new THREE.CylinderGeometry(rt, rb, len, 14), x, y0 - len / 2, z, 0.55);
      put(new THREE.SphereGeometry(rb * 1.08, 14, 10), x, y0 - len, z, 0.55);
      put(new THREE.SphereGeometry(rt * 1.05, 14, 10), x, y0, z, 0.55);   // the shoulder, blending into the lip
    }
  });
  // rounded corners: side sd's start corner (local x = -ce) is (-ce, ce) in its own frame
  if (!round) sides.forEach((pts, sd) => {
    const th = sd * Math.PI / 2, c = Math.round(Math.cos(th)), sn = Math.round(Math.sin(th));
    const lx = -ce, lz = ce;
    add(new THREE.SphereGeometry(lipR, 14, 10), fondant, lx * c + lz * sn, cornerY[sd][0] - lipR * 0.4, -lx * sn + lz * c);
  });
  // serving plate
  const plateW = W * 2.3, plateH = W * 0.03;
  add(round ? new THREE.CylinderGeometry(plateW / 2, plateW / 2, plateH, 128) : new THREE.BoxGeometry(plateW, plateH, plateW),
    mat(0xeeeef2, 0.35, 0.1), 0, top - total - plateH / 2, 0);

  // candles on a ring inset from the edge, plus one in the middle
  const spots = [[0, 0]];
  if (round) {   // two rings inside the circle
    for (let k = 0; k < 6; k++) spots.push([0.42 * W * Math.cos(k / 6 * 2 * Math.PI), 0.42 * W * Math.sin(k / 6 * 2 * Math.PI)]);
    for (let k = 0; k < 12; k++) spots.push([0.74 * W * Math.cos(k / 12 * 2 * Math.PI), 0.74 * W * Math.sin(k / 12 * 2 * Math.PI)]);
  } else {
    const ring = 0.72 * W;
    for (const s of [-1, -0.5, 0, 0.5, 1]) for (const [x, z] of [[s * ring, -ring], [s * ring, ring], [-ring, s * ring], [ring, s * ring]]) {
      if (!spots.some(p => Math.abs(p[0] - x) < 1 && Math.abs(p[1] - z) < 1)) spots.push([x, z]);
    }
  }
  const stripes = ['#ff6b81', '#4dabf7', '#ffd43b', '#69db7c'].map(c => cakeStripeTexture('#ffffff', c));
  const glow = cakeGlowTexture();
  const cH = W * 0.2, cR = W * 0.014;
  cake.candles = []; cake.flames = [];
  spots.forEach(([x, z], i) => {
    const c = new THREE.Group(); c.position.set(x, top, z);
    const body = new THREE.Mesh(new THREE.CylinderGeometry(cR, cR, cH, 14), new THREE.MeshStandardMaterial({ map: stripes[i % 4], roughness: 0.5 }));
    body.position.y = cH / 2; body.castShadow = true;
    const wick = new THREE.Mesh(new THREE.CylinderGeometry(cR * 0.08, cR * 0.08, cR * 1.2, 4), mat(0x222222));
    wick.position.y = cH + cR * 0.5;
    const flame = new THREE.Group(); flame.position.y = cH + cR * 2.4;
    const outer = new THREE.Mesh(new THREE.SphereGeometry(cR * 0.85, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffa31a }));
    outer.scale.set(1, 1.9, 1);
    const inner = new THREE.Mesh(new THREE.SphereGeometry(cR * 0.5, 10, 8), new THREE.MeshBasicMaterial({ color: 0xfff3b0 }));
    inner.scale.set(1, 1.7, 1); inner.position.y = -cR * 0.35;
    const core = new THREE.Mesh(new THREE.SphereGeometry(cR * 0.24, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));   // white-hot base of the flame
    core.scale.set(1, 1.6, 1); core.position.y = -cR * 0.5;
    // normal blending: additive wrote alpha into the transparent background (a white disc around each flame)
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, blending: THREE.NormalBlending, depthWrite: false, transparent: true }));
    halo.scale.set(cR * 9, cR * 9, 1);
    halo.visible = false;   // no glow disc: the flame itself stays on a transparent background
    flame.add(outer, inner, core, halo);
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
    cakeFrom.set(c.x, (CFG.zrange[1] * 2 + T) * Math.max(1, root.scale.y) * 2, c.z);
    cakeRay.set(cakeFrom, cakeDown);
    const h = cakeRay.intersectObject(root, true).find(i => i.object.visible && i.object.isMesh && !i.object.userData.impostor);
    if (h) { c.g.position.y = h.point.y - 0.5; c.hit = true; } else missing++;
  }
  return missing;
}

// A message set on an arc standing over the cake, like a rainbow banner: the letters follow the arc and point outward,
// and the arc faces the camera. `centreY` is the height of the arc's centre; `azimuth` is the camera's compass bearing.
function cakeTextArch(text, W, centreY, azimuth) {
  const FONT = '800 220px "Segoe UI", "Trebuchet MS", system-ui, sans-serif', PH = 320;
  const probe = document.createElement('canvas').getContext('2d'); probe.font = FONT;
  const cw = Math.ceil(probe.measureText(text).width) + 120;
  const c = document.createElement('canvas'); c.width = cw; c.height = PH;
  const g = c.getContext('2d'); g.font = FONT; g.textAlign = 'center'; g.textBaseline = 'middle'; g.lineJoin = 'round';
  const grad = g.createLinearGradient(0, 40, 0, PH - 40); grad.addColorStop(0, '#ff8fb8'); grad.addColorStop(1, '#ffc94d');
  g.lineWidth = 34; g.strokeStyle = '#5a2d1b'; g.strokeText(text, cw / 2, PH / 2 + 8);
  g.lineWidth = 12; g.strokeStyle = '#fff6e6'; g.strokeText(text, cw / 2, PH / 2 + 8);
  g.fillStyle = grad; g.fillText(text, cw / 2, PH / 2 + 8);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
  const R = W * 0.95;
  let h = W * 0.3;
  const spanOf = hh => (hh * cw / PH) / (R + hh / 2);
  if (spanOf(h) > 3.3) h *= 3.3 / spanOf(h);            // a long message gets smaller letters, never more than ~190 degrees
  const span = spanOf(h), seg = 120, pos = [], uv = [], idx = [];
  for (let i = 0; i <= seg; i++) {
    const t = i / seg, th = Math.PI / 2 + span / 2 - t * span, cs = Math.cos(th), sn = Math.sin(th);   // left to right over the top
    pos.push(R * cs, R * sn, 0, (R + h) * cs, (R + h) * sn, 0); uv.push(t, 0, t, 1);
    if (i) { const a = 2 * i - 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2)); geo.setIndex(idx);
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.08, side: THREE.DoubleSide, toneMapped: false }));
  const dir = dirFromCompass(azimuth, 27), grp = new THREE.Group();
  grp.rotation.y = Math.atan2(dir.x, dir.z);               // the arc's plane faces the camera
  grp.position.set(0, centreY, 0); grp.add(mesh);
  cake.textTex = tex;
  return { group: grp, top: centreY + R + h };
}

function cakeDispose() {
  if (cake.textTex) { cake.textTex.dispose(); cake.textTex = null; }
  if (cake.undo) { cake.undo.forEach(f => f()); cake.undo = null; }   // the model's own plinth and wall come back
  if (!cake.group) return;
  scene.remove(cake.group);
  cake.group.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  if (cake.rt) { cake.rt.dispose(); cake.rt = null; cake.topTex = null; }
  (cake.stripeTex || []).forEach(t => t.dispose()); if (cake.glowTex) cake.glowTex.dispose();
  cake.group = null; cake.candles = []; cake.flames = [];
}

// ---- the button renders ONE still picture: the model is dressed as a cake, photographed from a three-quarter view at
// high resolution, and saved as a PNG. The live 3-D view goes back exactly as it was.
const nextFrames = n => new Promise(res => { let k = 0; const t0 = performance.now();
  (function tick() { if (++k >= n || performance.now() - t0 > 6000) res(); else requestAnimationFrame(tick); })(); });

async function renderCakePng(round) {
  const btn = $(round ? 'cakeRoundBtn' : 'cakeBtn');
  if (cake.busy || !window.__ready) return;
  cake.busy = true; cake.round = !!round; btn.disabled = true; const label = btn.textContent;
  const note = document.createElement('div');
  note.style.cssText = 'position:fixed;inset:0;display:grid;place-items:center;z-index:9;background:rgba(0,0,0,.35);color:#fff;font:600 20px system-ui,sans-serif;pointer-events:all';
  note.textContent = 'Baking your cake…'; document.body.appendChild(note);
  btn.textContent = 'Baking…';
  const sv = {   // everything the render changes, put back at the end
    ov: Object.assign({}, colourOv), exag: $('exag').value, quality: quality.value, pos: camera.position.clone(),
    tgt: controls.target.clone(), sky: skyDir, rot: controls.autoRotate, near: camera.near, far: camera.far,
    bg: scene.background, clear: renderer.getClearColor(new THREE.Color()).getHex(), clearA: renderer.getClearAlpha(),
    perf: { on: PERF.on, aggr: PERF.aggr, gfx: PERF.gfx, floor: PERF.floor }, scaleShown: elevScale.group ? elevScale.group.visible : null,
  };
  try {
    cake.on = true;
    quality.value = 'high'; quality.onchange();                      // full detail, full shadows
    PERF.on = false; PERF.aggr = 0; PERF.gfx = 0; PERF.floor = 0;     // no distance culling in the picture
    controls.autoRotate = false; skyDir = null;
    $('exag').value = Math.min(6, Math.max(4, +sv.exag * 3)); applyExaggeration();   // exaggerated hills
    Object.assign(colourOv, CAKE_COLOURS);
    applyThemeTo(root); setThemeColors();
    runwayMeshes.forEach(m => { m.visible = false; });
    elevScale.show(false);
    await nextFrames(40);                                             // let tiles, shadows and colours settle
    root.updateMatrixWorld(true);
    cake.rt = captureMapTop(); cake.topTex = cake.rt.texture;
    cake.group = buildCake(); scene.add(cake.group);
    for (let i = 0; i < 6; i++) { root.updateMatrixWorld(true); if (!cakeProbe()) break; await nextFrames(8); }
    // three-quarter view, low enough to show the layers on the sides
    const W = N * T / 2, box = new THREE.Box3().setFromObject(cake.group), mid = box.getCenter(new THREE.Vector3());
    // the message arches over the candles; it is added after the cake is measured so it does not move the framing's base
    const msg = ($('cakeText') ? $('cakeText').value : '').trim().slice(0, 60);
    if (msg) {
      const cakeTop = Math.max(...cake.candles.map(c => c.g.position.y)) + W * 0.2 + W * 0.04;   // candle height + flame
      cake.group.add(cakeTextArch(msg, W, cakeTop, CFG.camera.azimuth).group);
    }
    // the look-at point drops, so the camera drops with it and the cake sits higher in the frame (its bottom edge was cut off)
    controls.target.set(0, mid.y - W * 0.02 - W * 0.45 + (msg ? W * 0.5 : 0), 0);
    camera.position.copy(controls.target).add(dirFromCompass(CFG.camera.azimuth, 27).multiplyScalar(W * (msg ? 5.6 : 4.3)));
    controls.update(); clipDist = 0; fitClip();
    await nextFrames(40);
    // one big frame, then straight back to the window size within the same tick, so nobody sees it
    const gl = renderer.getContext();
    const w = Math.min(4096, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), gl.getParameter(gl.MAX_TEXTURE_SIZE)), h = Math.round(w * 3 / 4);
    const aspect = camera.aspect;
    renderer.setPixelRatio(1); renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    scene.background = null; renderer.setClearColor(0x000000, 0);   // no backdrop: the PNG's background is transparent
    renderer.render(scene, camera);
    const blob = await new Promise(res => {
      renderer.domElement.toBlob(res, 'image/png');
      camera.aspect = aspect; camera.updateProjectionMatrix();
      renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight);
    });
    if (blob) {
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
      a.download = (CFG.title || 'city') + (round ? '-round' : '') + '-cake.png'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    }
  } catch (e) {
    console.error('cake render failed', e);
  } finally {
    cake.on = false; cakeDispose();
    for (const k in colourOv) delete colourOv[k];
    Object.assign(colourOv, sv.ov);
    applyThemeTo(root); setThemeColors(); syncPickers();
    scene.background = sv.bg; renderer.setClearColor(sv.clear, sv.clearA);
    runwayMeshes.forEach(m => { m.visible = true; });
    elevScale.show(sv.scaleShown === null ? !$('scaleOn') || $('scaleOn').checked : sv.scaleShown);
    $('exag').value = sv.exag; applyExaggeration();
    quality.value = sv.quality; quality.onchange();
    Object.assign(PERF, { on: sv.perf.on, aggr: sv.perf.aggr, gfx: sv.perf.gfx, floor: sv.perf.floor });
    camera.position.copy(sv.pos); controls.target.copy(sv.tgt); skyDir = sv.sky; controls.autoRotate = sv.rot;
    controls.update(); clipDist = 0; fitClip();
    note.remove(); btn.textContent = label; btn.disabled = false; cake.busy = false;
  }
}
if ($('cakeBtn')) $('cakeBtn').onclick = () => renderCakePng(false);
if ($('cakeRoundBtn')) $('cakeRoundBtn').onclick = () => renderCakePng(true);

function cakeUpdate(now) {   // flames flicker while the cake is dressed up for the picture
  if (!cake.on) return;
  runwayMeshes.forEach(m => { m.visible = false; });   // a runway made by pressing "Land a plane" appears later
  const t = now / 1000;
  for (const f of cake.flames) {
    const s = 1 + 0.12 * Math.sin(t * 11 + f.seed) + 0.07 * Math.sin(t * 23 + f.seed * 2.3);
    f.g.scale.set(1 / Math.sqrt(s), s, 1 / Math.sqrt(s));
    f.g.rotation.z = 0.08 * Math.sin(t * 7 + f.seed);
  }
}
