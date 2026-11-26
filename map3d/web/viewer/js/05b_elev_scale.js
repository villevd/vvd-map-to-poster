// ---------------------------------------------------------------- elevation scales at the four corners
// A pole per corner of the model, ticked in metres above sea level: 0 is the ocean level, then the elevations up to the
// highest ground. Scene height of an elevation e is ((e - dem_min) * EX.t + base) * (T / EX.t), where T is the terrain
// exaggeration slider, so the ticks move with it. Labels are sprites, so they stay readable and unstretched.
const elevScale = (() => {
  const F = CFG.facts || {};
  if (F.dem_min_m == null || F.base_m == null || F.relief_m == null) return { update() {}, show() {} };
  const dmin = F.dem_min_m, base = F.base_m, top = dmin + F.relief_m;
  const lo = Math.min(0, dmin), hi = Math.max(0, top);
  const span = Math.max(hi - lo, 1);
  let step = 1;   // a round step that gives about 4-8 ticks
  for (const s of [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]) { step = s; if (span / s <= 8) break; }
  const ticks = [];
  for (let e = Math.ceil(lo / step) * step; e <= hi + 1e-6; e += step) ticks.push(e);
  if (!ticks.includes(0)) ticks.push(0);
  if (hi - ticks[ticks.length - 1] > step * 0.3) ticks.push(Math.round(hi));   // the highest point too
  ticks.sort((a, b) => a - b);

  const group = new THREE.Group();
  group.renderOrder = 10;
  scene.add(group);
  const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, depthTest: false });
  const seaMat = new THREE.LineBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.95, depthTest: false });
  const cache = new Map();
  function labelTex(text, sea, right) {
    const key = text + (sea ? '|s' : '') + (right ? '|r' : '');
    if (cache.has(key)) return cache.get(key);
    const c = document.createElement('canvas'), g = c.getContext('2d');
    c.width = 256; c.height = 64;
    g.font = 'bold 34px system-ui, sans-serif'; g.textAlign = right ? 'right' : 'left'; g.textBaseline = 'middle';
    g.lineWidth = 7; g.strokeStyle = 'rgba(0,0,0,.85)'; g.lineJoin = 'round';
    const x0 = right ? 250 : 6;
    g.strokeText(text, x0, 33); g.fillStyle = sea ? '#4fc3f7' : '#ffffff'; g.fillText(text, x0, 33);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const v = { tex };
    cache.set(key, v); return v;
  }
  const lh = H * 0.045, tickLen = H * 0.035;   // label height, tick length (scene units)
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sz]) => ({ x: sx * H, z: sz * H, sx, sz }));
  const sprites = [];
  for (const c of corners) for (const e of ticks) {
    const sea = e === 0, v = labelTex(sea ? '0 m · ocean' : e + ' m', sea, c.sx < 0);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: v.tex, transparent: true, depthTest: false }));
    sp.scale.set(lh * 4, lh, 1); sp.center.set(c.sx < 0 ? 1 : 0, 0.5);   // the text grows away from the pole
    sp.renderOrder = 11; sp.userData = { c, e };
    group.add(sp); sprites.push(sp);
  }
  let poles = null;
  function update(T) {
    const k = T / EX.t, yw = e => ((e - dmin) * EX.t + base) * k;
    if (poles) { group.remove(poles); poles.traverse(o => o.geometry && o.geometry.dispose()); }
    poles = new THREE.Group();
    const pts = [], sea = [];
    for (const c of corners) {
      pts.push(c.x, yw(lo), c.z, c.x, yw(hi), c.z);
      for (const e of ticks) (e === 0 ? sea : pts).push(c.x, yw(e), c.z, c.x - c.sx * tickLen * (e === 0 ? 2 : 1), yw(e), c.z);
    }
    for (const [arr, mat] of [[pts, lineMat], [sea, seaMat]]) {
      const geo = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
      const ls = new THREE.LineSegments(geo, mat); ls.renderOrder = 10; ls.frustumCulled = false; poles.add(ls);
    }
    group.add(poles);
    for (const sp of sprites) sp.position.set(sp.userData.c.x + sp.userData.c.sx * tickLen * 0.4, yw(sp.userData.e), sp.userData.c.z);
  }
  const cb = $('scaleOn');
  if (cb) cb.onchange = () => { group.visible = cb.checked; };
  return { update, group, show(on) { group.visible = on; } };
})();
