// ---------------------------------------------------------------- underground: the earth under the city, cut open
// The model becomes a block of earth, Minecraft style: topsoil, overburden, bedrock down to a floor, with a slice or a
// pit cut out so the inside shows. Mines, drill holes and mining claims (CFG.underground, from map3d/underground.py)
// sit in it at their real depths. Depth is real metres times the terrain exaggeration the model was built with, so the
// block follows the Terrain exaggeration slider like everything else. Only the first metres (topsoil and overburden)
// are drawn thicker than true so they stay visible; the legend says so. All geometry lives under `root`.
const UGD = CFG.underground || null;
function ugInit() {
  const BIG = 1e9;
  const ui = {
    box: $('ugBox'), on: $('ugOn'), mode: $('ugMode'), pos: $('ugPos'), size: $('ugSize'), depth: $('ugDepth'),
    mines: $('ugMines'), drill: $('ugDrill'), claims: $('ugClaims'), fit: $('ugFit'), dig: $('ugDig'), flip: $('ugFlip'),
    legend: $('ugLegend'), posRow: $('ugPosRow'), sizeRow: $('ugSizeRow'), depthO: $('ugDepthO'), posO: $('ugPosO'), sizeO: $('ugSizeO'),
    digRow: $('ugDigRow'),
  };
  ui.box.hidden = false;
  const grp = new THREE.Group(); grp.visible = false; grp.name = 'underground'; root.add(grp);
  const tip = document.createElement('div'); tip.id = 'ugTip'; tip.hidden = true; document.body.appendChild(tip);
  const S = { pit: { x: 0, z: 0 }, flip: false, hCache: new Map(), list: null, timer: 0, saved: null, feat: null, labels: [] };

  // ---- depth scale: real metres below the ground -> local y units
  function depthM() { return Math.round(50 * Math.pow(100, +ui.depth.value / 100)); }
  const fy = () => depthM() * EX.t;                                   // floor depth, local units
  const dSoil = () => Math.max(EX.t, 0.012 * fy()), dOver = () => Math.max(EX.t * 6, 0.035 * fy());
  const THIN = 8;   // real metres of the two thin layers; they are drawn thicker than true
  function dispDepth(dReal) {   // real depth in metres below ground -> local units below ground
    if (dReal <= 1) return dReal * dSoil();
    if (dReal <= THIN) return dSoil() + (dReal - 1) / (THIN - 1) * dOver();
    return dSoil() + dOver() + (dReal - THIN) * EX.t;
  }
  const px = () => dSoil() / 2;   // one texture pixel, so the topsoil is two pixels thick

  // ---- terrain height under (x, z): ray down onto the ground meshes of the tiles that are loaded
  const ray = new THREE.Raycaster(), DOWN = new THREE.Vector3(0, -1, 0), ORG = new THREE.Vector3();
  function groundList() {
    const out = [];
    for (const t of tileList) {
      if (t.extra) continue;
      let lv = null;
      for (let l = 0; l <= maxLod; l++) if (t.levels[l] && t.levels[l].state === 'ready') { lv = t.levels[l]; break; }
      if (lv) lv.obj.traverse(o => { if (o.isMesh && PIVOT_ROLES.has(o.userData.role)) out.push(o); });
    }
    return out;
  }
  function heightAt(x, z) {
    const k = Math.round(x * 2) + ',' + Math.round(z * 2);
    let h = S.hCache.get(k); if (h !== undefined) return h;
    ray.set(ORG.set(x, 1e5, z), DOWN);
    const hit = ray.intersectObjects(S.list, false)[0];
    if (!hit) return null;
    h = hit.point.y / root.scale.y; S.hCache.set(k, h); return h;
  }
  const clampH = v => Math.max(-H + 0.3, Math.min(H - 0.3, v));   // a ray exactly on the model's edge can slip between tiles
  function surface(x, z, fallback) {
    const h = heightAt(clampH(x), clampH(z));
    if (h === null) return S.last === undefined ? fallback : S.last;
    S.last = h; return h;
  }

  // ---- pixel textures (cubes of earth)
  let seed = 7;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  function pixTex(painter, size = 64) {
    const c = document.createElement('canvas'); c.width = c.height = size;
    const g = c.getContext('2d'), img = g.createImageData(size, size);
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const [r, gg, b] = painter(x, y);
      const i = (y * size + x) * 4; img.data[i] = r; img.data[i + 1] = gg; img.data[i + 2] = b; img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.flipY = false;
    t.magFilter = THREE.NearestFilter; t.minFilter = THREE.LinearMipmapLinearFilter; t.anisotropy = 8;
    return t;
  }
  const mix3 = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  const hex3 = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const jit = (c, a) => { const k = 1 + (rnd() - 0.5) * a; return [c[0] * k, c[1] * k, c[2] * k]; };
  const layerData = UGD.layers || [];
  const bedrockLayer = layerData.find(l => l.kind === 'bedrock') || { color: '#7d7d80' };
  const TEX = {
    soil: pixTex((x, y) => y < 1 ? jit([88, 160, 52], 0.35) : y < 2 ? jit(mix3([88, 160, 52], [110, 76, 44], rnd() < 0.5 ? 0.3 : 0.8), 0.3)
      : jit(rnd() < 0.08 ? [70, 48, 28] : [112, 78, 46], 0.28)),
    over: pixTex(() => jit(rnd() < 0.14 ? [118, 120, 116] : rnd() < 0.3 ? [150, 126, 92] : [140, 112, 78], 0.3)),
    rock: pixTex(() => {
      const base = mix3([120, 120, 126], hex3(bedrockLayer.color), 0.28);
      return jit(rnd() < 0.07 ? [70, 70, 76] : rnd() < 0.05 ? [158, 158, 164] : base, 0.26);
    }),
    floor: pixTex(() => jit(rnd() < 0.3 ? [58, 58, 64] : [22, 22, 26], 0.4)),
  };
  const wallMat = (map, vc = true) => new THREE.MeshLambertMaterial({ map, vertexColors: vc });
  const MAT = { soil: wallMat(TEX.soil, false), over: wallMat(TEX.over, false), rock: wallMat(TEX.rock),
    floor: new THREE.MeshLambertMaterial({ map: TEX.floor, side: THREE.DoubleSide }) };

  // ---- the cut region
  const mode = () => ui.mode.value;
  function cutBox() {   // [minX, minZ, maxX, maxZ] in world x/z, or null for the whole block
    const m = mode(), p = (+ui.pos.value / 100) * H, s = (+ui.size.value / 100) * H;
    if (m === 'sliceX') return S.flip ? [-BIG, -BIG, p, BIG] : [p, -BIG, BIG, BIG];
    if (m === 'sliceZ') return S.flip ? [-BIG, p, BIG, BIG] : [-BIG, -BIG, BIG, p];
    if (m === 'pit') return [S.pit.x - s, S.pit.z - s, S.pit.x + s, S.pit.z + s];
    return null;
  }
  function applyCut() {
    const b = cutBox();
    CUT.on.value = UG_STATE.on && b ? 1 : 0;
    if (b) CUT.box.value.set(b[0], b[1], b[2], b[3]);
  }
  const removed = (b, x, z) => !!b && x > b[0] && x < b[2] && z > b[1] && z < b[3];

  // ---- geometry helpers
  const sharedMats = () => Object.values(MAT);
  function dispose(o) {
    o.traverse(c => {
      if (c.geometry) c.geometry.dispose();
      if (c.material && !sharedMats().includes(c.material)) { if (c.isSprite && c.material.map) c.material.map.dispose(); c.material.dispose(); }
    });
  }
  function wall(parent, pts, n, floorY) {
    // pts: [{x, z, y}] along the wall (y = ground height); n: outward normal [x, z]. Soil, overburden, then rock to the floor.
    if (pts.length < 2) return;
    const t = [pts[pts.length - 1].x - pts[0].x, pts[pts.length - 1].z - pts[0].z];
    const flip = (-t[1] * n[0] + t[0] * n[1]) < 0;
    const lift = Math.max(0.4, T * 0.0003), cell = 64 * px();
    const d1 = dSoil(), d2 = d1 + dOver();
    const bands = [['soil', () => 0, () => d1], ['over', () => d1, () => d2], ['rock', () => d2, null]];
    for (const [key, top, bot] of bands) {
      const pos = [], uv = [], col = [], nrm = [], idx = [];
      let u = 0;
      pts.forEach((p, i) => {
        if (i) u += Math.hypot(p.x - pts[i - 1].x, p.z - pts[i - 1].z);
        const yt = p.y + lift - top(), yb = bot ? p.y + lift - bot() : floorY;
        pos.push(p.x, yt, p.z, p.x, yb, p.z);
        uv.push(u / cell, (p.y + lift - yt) / cell, u / cell, (p.y + lift - yb) / cell);
        const shade = key === 'rock' ? (a => [1 - 0.6 * a, 1 - 0.6 * a, 1 - 0.55 * a]) : (() => [1, 1, 1]);
        const span = Math.max(1, p.y - floorY);
        col.push(...shade(Math.max(0, (p.y - yt) / span)), ...shade(Math.max(0, (p.y - yb) / span)));
        nrm.push(n[0], 0, n[1], n[0], 0, n[1]);
        if (i) { const a = (i - 1) * 2, b = i * 2; idx.push(...(flip ? [a, b + 1, a + 1, a, b, b + 1] : [a, a + 1, b + 1, a, b + 1, b])); }
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
      g.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3));
      if (key === 'rock') g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
      g.setIndex(idx);
      const m = new THREE.Mesh(g, MAT[key]);
      m.userData.ugWall = true; parent.add(m);
    }
  }
  // sample positions along an axis-aligned segment, with extra stops where the cut starts and ends
  function samples(a, b, extra, count) {
    const out = new Set([a, b]);
    for (let i = 1; i < count; i++) out.add(a + (b - a) * i / count);
    for (const e of extra) if (e > Math.min(a, b) && e < Math.max(a, b)) out.add(e);
    return [...out].sort((p, q) => p - q);
  }
  function run(parent, fixedAxis, fixed, lo, hi, n, keep, floorY, fallback) {
    // one wall along x (fixedAxis 'z') or z (fixedAxis 'x'), between lo and hi, keeping only the parts keep(x, z) accepts
    const b = cutBox();
    const stops = b ? (fixedAxis === 'z' ? [b[0], b[2]] : [b[1], b[3]]) : [];
    const us = samples(lo, hi, stops, 150);
    let cur = [];
    const flush = () => { if (cur.length > 1) wall(parent, cur, n, floorY); cur = []; };
    for (let i = 0; i < us.length; i++) {
      const pt = fixedAxis === 'z' ? { x: us[i], z: fixed } : { x: fixed, z: us[i] };
      if (i) {
        const mx = fixedAxis === 'z' ? (us[i] + us[i - 1]) / 2 : fixed, mz = fixedAxis === 'z' ? fixed : (us[i] + us[i - 1]) / 2;
        if (!keep(mx, mz)) { flush(); continue; }
        if (!cur.length) { const q = fixedAxis === 'z' ? { x: us[i - 1], z: fixed } : { x: fixed, z: us[i - 1] }; q.y = surface(q.x, q.z, fallback); cur.push(q); }
      }
      pt.y = surface(pt.x, pt.z, fallback);
      if (i) cur.push(pt);
    }
    flush();
  }
  function floorQuad(parent, x0, z0, x1, z1, y) {
    const g = new THREE.BufferGeometry(), cell = 64 * px() * 4;
    g.setAttribute('position', new THREE.Float32BufferAttribute([x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1], 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute([x0 / cell, z0 / cell, x1 / cell, z0 / cell, x1 / cell, z1 / cell, x0 / cell, z1 / cell], 2));
    g.setAttribute('normal', new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], 3));
    g.setIndex([0, 2, 1, 0, 3, 2]);
    const m = new THREE.Mesh(g, MAT.floor); m.userData.ugWall = true; parent.add(m);
  }

  // ---- sprites for labels and the depth ruler
  function textSprite(text, color = '#ffffff', size = 1) {
    const c = document.createElement('canvas'), g = c.getContext('2d'), fs = 44;
    g.font = `600 ${fs}px system-ui, sans-serif`;
    const w = Math.ceil(g.measureText(text).width) + 24; c.width = w; c.height = fs + 20;
    g.font = `600 ${fs}px system-ui, sans-serif`; g.textBaseline = 'middle';
    g.fillStyle = 'rgba(8,8,12,0.62)'; g.beginPath(); g.roundRect(0, 0, c.width, c.height, 14); g.fill();
    g.fillStyle = color; g.fillText(text, 12, c.height / 2 + 2);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true }));
    sp.renderOrder = 20; sp.userData.aspect = c.width / c.height; sp.userData.size = size; sp.userData.own = true;
    return sp;
  }
  const niceTicks = max => { const out = [0], steps = [1, 8, 25, 50, 100, 250, 500, 1000, 2000, 3000, 4000, 5000]; for (const s of steps) if (s <= max) out.push(s); return out; };

  // ---- features: mines, drill holes, claims
  const COMMODITY = [['gold', '#ffd23a'], ['copper', '#ff9a52'], ['zinc', '#9fd0ff'], ['nickel', '#7fe3c8'], ['lithium', '#d0a8ff'],
    ['iron', '#c4ccd8'], ['silver', '#e8eef5'], ['uranium', '#9cff3a'], ['lead', '#a8a8c0'], ['molybdenum', '#7ab0ff']];
  const commodityColor = s => { const k = String(s || '').toLowerCase(); for (const [n, c] of COMMODITY) if (k.includes(n)) return c; return '#ff7a59'; };
  const gradTex = (() => { const c = document.createElement('canvas'); c.width = 2; c.height = 64; const g = c.getContext('2d');
    const gr = g.createLinearGradient(0, 0, 0, 64); gr.addColorStop(0, '#fff'); gr.addColorStop(1, '#000'); g.fillStyle = gr; g.fillRect(0, 0, 2, 64);
    return new THREE.CanvasTexture(c); })();

  function buildFeatures(floorY, fallback) {
    const f = new THREE.Group(), pick = { mines: [], drill: [], claims: [] };
    const r = Math.max(H * 0.0035, 3);
    // mines, deposits, shafts: a beacon at the surface; a solid shaft where the depth is known, a fading beam where it is not
    const sites = UGD.sites || [];
    const mineG = new THREE.Group(); mineG.userData.kind = 'mines';
    sites.forEach((s, i) => {
      const x = s.x, z = -s.y, y = surface(x, z, fallback), col = new THREE.Color(commodityColor(s.commodity));
      const dim = s.state === 'closed' ? 0.55 : 1, known = s.depth_m > 0;
      const g = new THREE.Group(); g.position.set(x, y, z);
      const bm = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.9 * dim, depthWrite: false, blending: THREE.AdditiveBlending });
      if (!known) bm.alphaMap = gradTex;   // depth not recorded: the shaft fades out instead of ending at a made-up depth
      const len = known ? s.depth_m * EX.t : fy() * 0.3;
      const down = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.5, r * 0.5, len, 10, 1, true), bm);
      down.position.y = -len / 2; g.add(down);
      const up = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.2, r * 0.5, r * 14, 8, 1, true), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.35 * dim, depthWrite: false, blending: THREE.AdditiveBlending }));
      up.position.y = r * 7; g.add(up);
      const gem = new THREE.Mesh(new THREE.OctahedronGeometry(r * 1.6), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: dim }));
      gem.position.y = r * 15; gem.userData.pulse = s.state === 'active'; g.add(gem);
      [down.material, up.material].forEach(cutMaterial); cutMaterial(gem.material);
      g.userData.site = s; mineG.add(g);
      if (i < 24) {
        const sp = textSprite(s.name + (known ? ` · ${Math.round(s.depth_m)} m` : ''), '#' + col.getHexString(), 1);
        sp.position.set(x, y + r * 21, z); S.labels.push(sp); f.add(sp);
      }
      pick.mines.push({ x, y: y + r * 15, z, s });
    });
    f.add(mineG);
    // drill holes: one merged mesh of thin prisms, collar at the ground, going down along the real azimuth and plunge
    const holes = UGD.drillholes || [];
    if (holes.length) {
      const rr = Math.max(T * 0.0009, 0.8), pos = [], idx = [];
      const dir = new THREE.Vector3(), side = new THREE.Vector3(), up = new THREE.Vector3();
      holes.forEach((h, k) => {
        const x = h.x, z = -h.y, y = surface(x, z, fallback);
        const az = h.azimuth * Math.PI / 180, pl = Math.min(h.plunge, 0) * Math.PI / 180;
        const hz = Math.cos(pl) * h.length_m;
        dir.set(Math.sin(az) * hz, Math.sin(pl) * h.length_m * EX.t, -Math.cos(az) * hz);   // east, up, north -> viewer x, y, -z
        side.set(-dir.z, 0, dir.x); if (side.lengthSq() < 1e-9) side.set(1, 0, 0); side.normalize().multiplyScalar(rr);
        up.crossVectors(dir, side).normalize().multiplyScalar(rr);
        const base = pos.length / 3;
        for (const q of [0, 1]) {
          const cx = x + dir.x * q, cy = y + dir.y * q, cz = z + dir.z * q;
          pos.push(cx + side.x, cy + side.y, cz + side.z, cx + up.x, cy + up.y, cz + up.z, cx - side.x, cy - side.y, cz - side.z, cx - up.x, cy - up.y, cz - up.z);
        }
        for (let e = 0; e < 4; e++) { const a = base + e, b = base + (e + 1) % 4, c = a + 4, d = b + 4; idx.push(a, c, b, b, c, d); }
        pick.drill.push({ x, y, z, h, end: new THREE.Vector3(x + dir.x, y + dir.y, z + dir.z) });
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx);
      const m = new THREE.Mesh(g, cutMaterial(new THREE.MeshBasicMaterial({ color: 0x3df2ff, side: THREE.DoubleSide })));
      m.userData.kind = 'drill'; f.add(m);
    }
    // claims: a faint curtain from the surface to the floor, and a bright outline on the ground
    const claims = UGD.claims || [];
    if (claims.length) {
      const cg = new THREE.Group(); cg.userData.kind = 'claims';
      const cpos = [], cidx = [], lpos = [];
      claims.forEach((c, k) => {
        const ring = c.ring.slice(0, -1), ys = ring.map(p => surface(p[0], -p[1], fallback));
        const base = cpos.length / 3;
        ring.forEach((p, i) => { cpos.push(p[0], ys[i], -p[1], p[0], floorY, -p[1]); });
        for (let i = 0; i < ring.length; i++) { const j = (i + 1) % ring.length; cidx.push(base + i * 2, base + i * 2 + 1, base + j * 2, base + j * 2, base + i * 2 + 1, base + j * 2 + 1); }
        ring.forEach((p, i) => { const j = (i + 1) % ring.length; lpos.push(p[0], ys[i] + 1.5, -p[1], ring[j][0], ys[j] + 1.5, -ring[j][1]); });
        pick.claims.push({ c, ring: ring.map(p => [p[0], -p[1]]) });
      });
      const cgeo = new THREE.BufferGeometry(); cgeo.setAttribute('position', new THREE.Float32BufferAttribute(cpos, 3)); cgeo.setIndex(cidx);
      cg.add(new THREE.Mesh(cgeo, cutMaterial(new THREE.MeshBasicMaterial({ color: 0xffc23a, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false }))));
      const lgeo = new THREE.BufferGeometry(); lgeo.setAttribute('position', new THREE.Float32BufferAttribute(lpos, 3));
      cg.add(new THREE.LineSegments(lgeo, cutMaterial(new THREE.LineBasicMaterial({ color: 0xffd45e, transparent: true, opacity: 0.85 }))));
      f.add(cg);
    }
    S.pick = pick;
    return f;
  }

  // ---- (re)build everything
  function rebuild() {
    S.timer = 0;
    if (!UG_STATE.on) return;
    root.updateMatrixWorld(true); S.last = undefined;
    S.list = groundList();
    if (!S.list.length) return;   // no ground loaded yet: ugTileReady() calls again
    for (const c of [...grp.children]) { grp.remove(c); dispose(c); }
    S.labels = [];
    const b = cutBox(), fallback = zc;
    // the floor sits a depth below the lowest ground sampled along the block's sides
    let low = Infinity;
    for (let i = 0; i <= 24; i++) for (const [x, z] of [[-H + 2 * H * i / 24, -H], [-H + 2 * H * i / 24, H], [-H, -H + 2 * H * i / 24], [H, -H + 2 * H * i / 24]]) {
      const h = heightAt(Math.max(-H + 0.5, Math.min(H - 0.5, x)), Math.max(-H + 0.5, Math.min(H - 0.5, z))); if (h !== null && h < low) low = h;
    }
    if (!isFinite(low)) low = CFG.zrange[0];
    const floorY = low - dispDepth(depthM()) - (dSoil() + dOver());
    // outer sides (skipped where the cut removes them) and the cut walls
    const inside = (x, z) => !removed(b, x, z);
    const e = 0.0001;
    run(grp, 'z', H, -H, H, [0, 1], inside, floorY, fallback);
    run(grp, 'z', -H, -H, H, [0, -1], inside, floorY, fallback);
    run(grp, 'x', H, -H, H, [1, 0], inside, floorY, fallback);
    run(grp, 'x', -H, -H, H, [-1, 0], inside, floorY, fallback);
    if (b) {
      const lo = v => Math.max(-H, Math.min(H, v)), hi = lo;
      const sides = [['x', b[0], b[1], b[3], [1, 0]], ['x', b[2], b[1], b[3], [-1, 0]], ['z', b[1], b[0], b[2], [0, 1]], ['z', b[3], b[0], b[2], [0, -1]]];
      for (const [axis, at, a0, a1, n] of sides) {
        if (Math.abs(at) >= H - e) continue;                      // on or beyond the model edge: no wall there
        const l = lo(a0), h = hi(a1); if (h - l < 1) continue;
        run(grp, axis, at, l, h, n, () => true, floorY, fallback);
      }
    }
    // floor under what is left of the block
    let fx0 = -H, fx1 = H, fz0 = -H, fz1 = H;
    if (b && (mode() === 'sliceX' || mode() === 'sliceZ')) {
      if (mode() === 'sliceX') { if (b[0] > -BIG / 2) fx1 = b[0]; else fx0 = b[2]; } else { if (b[1] > -BIG / 2) fz1 = b[1]; else fz0 = b[3]; }
    }
    if (fx1 > fx0 && fz1 > fz0) floorQuad(grp, fx0, fz0, fx1, fz1, floorY);
    S.floorY = floorY;
    // the ruler, at the block corner nearest the default view
    const rx = -H - H * 0.015, rz = H + H * 0.015, rg = surface(-H + 1, H - 1, fallback);
    const lg = new THREE.BufferGeometry();
    const tp = [rx, rg, rz, rx, floorY, rz];
    const ticks = niceTicks(depthM());
    ticks.forEach(d => { const y = rg - dispDepth(d); tp.push(rx - H * 0.012, y, rz, rx + H * 0.012, y, rz); });
    lg.setAttribute('position', new THREE.Float32BufferAttribute(tp, 3));
    grp.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 })));
    ticks.forEach(d => { const sp = textSprite(d === 0 ? 'ground' : d + ' m', '#ffffff', 0.7); sp.position.set(rx - H * 0.05, rg - dispDepth(d), rz); S.labels.push(sp); grp.add(sp); });
    S.feat = buildFeatures(floorY, fallback);
    applyFeatureVis(); grp.add(S.feat);
    applyCut();
  }
  function schedule(ms = 160) { if (!UG_STATE.on) return; clearTimeout(S.timer); S.timer = setTimeout(rebuild, ms); }
  function applyFeatureVis() {
    if (!S.feat) return;
    S.feat.traverse(o => {
      const k = o.userData.kind;
      if (k === 'mines') o.visible = ui.mines.checked;
      if (k === 'drill') o.visible = ui.drill.checked;
      if (k === 'claims') o.visible = ui.claims.checked;
    });
    for (const s of S.labels) if (s.userData.own && s.parent === S.feat) s.visible = ui.mines.checked;
  }
  function frameVis() {   // the model's own ledge and skirt would sit inside the block's walls
    for (const t of tileList) for (const lv of Object.values(t.levels)) if (lv.obj) lv.obj.traverse(o => {
      if (o.userData.role === 'frame' || o.userData.role === 'skirt') o.visible = !UG_STATE.on;
    });
  }

  // ---- UI
  function syncUi() {
    const m = mode();
    ui.posRow.hidden = !(m === 'sliceX' || m === 'sliceZ'); ui.sizeRow.hidden = m !== 'pit'; ui.digRow.hidden = m !== 'pit';
    ui.depthO.textContent = depthM().toLocaleString() + ' m';
    ui.posO.textContent = Math.round(+ui.pos.value) + '%'; ui.sizeO.textContent = Math.round(+ui.size.value) + '%';
  }
  function enter() {
    UG_STATE.on = true;
    S.saved = { polar: controls.maxPolarAngle, min: controls.minDistance };
    controls.maxPolarAngle = Math.PI - 0.04;
    grp.visible = true; frameVis(); syncUi();
    if (mode() === 'pit' && !S.pit.set) { S.pit.x = controls.target.x; S.pit.z = controls.target.z; S.pit.set = true; }
    applyCut(); rebuild();
  }
  function leave() {
    UG_STATE.on = false; clearTimeout(S.timer);
    if (S.saved) { controls.maxPolarAngle = S.saved.polar; controls.update(); }
    grp.visible = false; CUT.on.value = 0; frameVis(); tip.hidden = true;
    for (const c of [...grp.children]) { grp.remove(c); dispose(c); }
    S.feat = null; S.pick = null;
  }
  ui.on.onchange = () => ui.on.checked ? enter() : leave();
  const autoSide = () => {   // remove the side of the cut that faces the camera, so the wall shows
    const p = (+ui.pos.value / 100) * H;
    S.flip = mode() === 'sliceX' ? camera.position.x < p : camera.position.z > p;
  };
  ui.mode.onchange = () => {
    if (mode() === 'pit') { S.pit.x = controls.target.x; S.pit.z = controls.target.z; S.pit.set = true; } else autoSide();
    syncUi(); if (UG_STATE.on) { applyCut(); schedule(30); }
  };
  ui.pos.oninput = () => { syncUi(); if (UG_STATE.on) { applyCut(); schedule(); } };
  ui.size.oninput = ui.depth.oninput = () => { syncUi(); if (UG_STATE.on) { applyCut(); schedule(); } };
  ui.flip.onclick = () => { S.flip = !S.flip; if (UG_STATE.on) { applyCut(); schedule(30); } };
  ui.dig.onclick = () => { S.pit.x = controls.target.x; S.pit.z = controls.target.z; if (UG_STATE.on) { applyCut(); schedule(30); } };
  ui.fit.onclick = () => {
    const m = UGD.max_depth_m || 600, v = Math.log(Math.max(50, Math.min(5000, m * 1.08)) / 50) / Math.log(100) * 100;
    ui.depth.value = v; syncUi(); if (UG_STATE.on) schedule(30);
  };
  for (const el of [ui.mines, ui.drill, ui.claims]) el.onchange = applyFeatureVis;
  ugTileReady = () => { if (UG_STATE.on) { S.hCache.clear(); frameVis(); schedule(350); } };

  // ---- legend
  (function legend() {
    const L = ui.legend, esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const rows = layerData.map(l => `<div class="ugRow"><i style="background:${esc(l.color)}"></i><span><b>${esc(l.name)}</b><small>${l.provenance === 'data' ? 'mapped' : 'typical'}${l.note ? ' · ' + esc(l.note) : ''}</small></span></div>`);
    const nS = (UGD.sites || []).length, nD = (UGD.drillholes || []).length, nC = (UGD.claims || []).length;
    const facts = [];
    if (nS) facts.push(`${nS} mine${nS > 1 ? 's' : ''} &amp; deposits`);
    if (nD) facts.push(`${nD} drill holes${UGD.max_depth_m ? ' (deepest ' + Math.round(UGD.max_depth_m).toLocaleString() + ' m)' : ''}`);
    if (nC) facts.push(`${nC} claims`);
    const col = (UGD.column || []).filter(u => !u.unconsolidated).slice(0, 5).map(u => `${esc(u.name)}${u.lith ? ' — ' + esc(u.lith) : ''}`);
    L.innerHTML = rows.join('') + (facts.length ? `<p>${facts.join(' · ')}</p>` : '<p>No mines, drill holes or claims on record here.</p>')
      + `<p class="ugNote">Depths are real metres below ground (× terrain exaggeration). The first 8 m are drawn thicker so they show. Unknown mine depths fade out instead of being guessed.</p>`
      + (col.length ? `<details><summary>Regional rock column</summary><p>${col.join('<br>')}</p></details>` : '');
  })();

  // ---- hover: mines first, then drill collars, then the claim under the cursor
  const v3 = new THREE.Vector3();
  function screenOf(x, y, z) { v3.set(x, y * root.scale.y, z).project(camera); return v3.z < 1 && v3.z > -1 ? [(v3.x + 1) / 2 * innerWidth, (1 - v3.y) / 2 * innerHeight] : null; }
  function describe(hit) {
    const f = v => v === null || v === undefined || v === '' ? '—' : v;
    if (hit.s) { const s = hit.s; return `<b>${s.name}</b><br>${f(s.kind)}${s.status ? ' · ' + s.status : ''}<br>${s.commodity ? s.commodity + '<br>' : ''}Depth: ${s.depth_m ? Math.round(s.depth_m) + ' m' : 'not recorded'}${s.year ? '<br>Found ' + s.year : ''}<br><small>${s.source}</small>`; }
    if (hit.h) { const h = hit.h; return `<b>Drill hole ${h.id}</b><br>${Math.round(h.length_m)} m long · plunge ${Math.round(h.plunge)}° · azimuth ${Math.round(h.azimuth)}°${h.year ? '<br>Drilled ' + h.year : ''}<br><small>${h.source}</small>`; }
    const c = hit.c; return `<b>${c.kind} ${c.id}</b><br>${c.status}<br>${c.area_ha ? Math.round(c.area_ha) + ' ha · ' : ''}expires ${f(c.expires)}<br><small>${c.source}</small>`;
  }
  function pointIn(ring, x, z) { let ins = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, zi] = ring[i], [xj, zj] = ring[j]; if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) ins = !ins; } return ins; }
  let moveEv = null;
  renderer.domElement.addEventListener('pointermove', e => { moveEv = e; });
  function hover() {
    const e = moveEv; moveEv = null;
    if (!e || !UG_STATE.on || !S.pick || e.buttons) { if (e && e.buttons) tip.hidden = true; return; }
    let best = null, bd = 16;
    if (ui.mines.checked) for (const m of S.pick.mines) { const s = screenOf(m.x, m.y, m.z); if (s) { const d = Math.hypot(s[0] - e.clientX, s[1] - e.clientY); if (d < bd) { bd = d; best = m; } } }
    if (!best && ui.drill.checked) { bd = 9; for (const m of S.pick.drill) { const s = screenOf(m.x, m.y, m.z), t = screenOf(m.end.x, m.end.y, m.end.z); for (const q of [s, t]) if (q) { const d = Math.hypot(q[0] - e.clientX, q[1] - e.clientY); if (d < bd) { bd = d; best = m; } } } }
    if (!best && ui.claims.checked) {
      const p = terrainPointAt(e.clientX, e.clientY);
      if (p) for (const c of S.pick.claims) if (pointIn(c.ring, p.x, p.z)) { best = c; break; }
    }
    if (!best) { tip.hidden = true; return; }
    tip.innerHTML = describe(best); tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 16, innerWidth - 260) + 'px'; tip.style.top = Math.min(e.clientY + 16, innerHeight - 140) + 'px';
  }

  // ---- per frame: labels keep a steady size on screen, active mines pulse
  ugUpdate = now => {
    if (!UG_STATE.on) return;
    hover();
    const k = 0.0042, pulse = 1 + 0.18 * Math.sin(now / 280);
    for (const sp of S.labels) {
      const d = camera.position.distanceTo(sp.getWorldPosition(v3));
      const h = d * k * sp.userData.size; sp.scale.set(h * sp.userData.aspect, h, 1);
    }
    if (S.feat) S.feat.traverse(o => { if (o.userData.pulse) o.scale.setScalar(pulse); });
  };
  ui.mode.value = 'block'; syncUi();
  if (UGD && new URLSearchParams(location.search).has('underground')) { ui.on.checked = true; enter(); }
}
if (UGD) ugInit();
