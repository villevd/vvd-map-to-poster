// ---------------------------------------------------------------- performance panel (top right), bottleneck probe and dynamic culling
// The panel shows the frame rate and the average frame time. Below START fps the viewer sheds work, along two axes:
//   aggr (0..1)  WHAT IS DRAWN: tiles, trees, markings, roads, buildings and cars beyond a cut-off distance. At 1 all trees,
//                markings and cars are gone and only a sliver of buildings is left. Cuts are fractions of the draw distance.
//   gfx  (0..1)  HOW MUCH EACH PIXEL COSTS: render resolution, shadow map size (then no shadows), satellite texture size
//                (then no textures at all), and the glossy water material.
// A bottleneck probe tells which of the two matters: the JavaScript time spent per frame (scene walk, draw-call submission)
// against the whole frame time. Mostly CPU: culling is pushed harder (fewer objects, fewer draw calls, traffic slowed down).
// Mostly GPU: the gfx axis is pushed harder (fewer pixels, no shadows, small textures).
// Hiding eases in quickly and out slowly with a hold-off, so a recovering frame rate does not flip it back and forth.
// The Quality "Potato" setting forces both axes up. "Culling floor" lets you force a minimum culling by hand.
const PERF = {
  START: 42, FULL: 18, AUTO_MAX: 0.7,
  fps: 60, ms: 1000 / 60, worst: 0, peak: 60,
  aggr: 0,                 // 0 = nothing hidden by distance, 1 = the strongest culling
  gfx: 0,                  // 0 = full quality, 1 = lowest resolution, no shadows, no textures
  on: true,                // the "Dynamic culling" checkbox
  floor: 0,                // the "Culling floor" slider: culling never drops below this (0..1)
  bound: 'ok',             // 'cpu' | 'gpu' | 'ok': what limits the frame rate right now
  cpuMs: 0,                // JavaScript time per frame (everything in the animation loop, render submission included)
  potato: false,           // Quality = Potato
  treeKeep: 1,             // share of trees drawn (1 = all)
  shadowRes: 4096, texScale: 1, lite: false, noTraffic: false, halfTraffic: false,
  // world distance from the camera beyond which each kind of object is hidden (Infinity = not culled)
  cuts: { tile: Infinity, tree: Infinity, markings: Infinity, roads: Infinity, building: Infinity, car: Infinity },
  _n: 0, _sum: 0, _max: 0, _cpu: 0, _last: null, _lastUp: 0, _fit: [], _slow: 0, _readyAt: 0, _idleSince: 0, _ref: null, _noGain: 0, _lockUntil: 0, _lockLen: 45000, futile: false,
  hist: [],                // recent fps samples, for the graph (one per measurement, about every half second)
  stats: { tris: 0, calls: 0, objects: 0, passes: 0 },
  cpuSample(ms) { this._cpu += ms; },
  frame(now) {
    if (this._last === null) { this._last = now; return; }
    const dt = now - this._last; this._last = now;
    if (dt > 0 && dt < 500) { this._n++; this._sum += dt; if (dt > this._max) this._max = dt; }   // skip gaps (hidden tab)
    if (this._sum >= 500 && this._n) {                  // measure over half a second
      this.fps = 1000 * this._n / this._sum;
      this.ms = this._sum / this._n;
      this.cpuMs = this._cpu / this._n;
      this.worst = this._max;
      this._n = this._sum = this._max = this._cpu = 0;
      this.peak = Math.max(this.peak * 0.997, this.fps);   // the display's own limit (60, 144...): "fast enough" is relative to it
      this.hist.push(this.fps); if (this.hist.length > 120) this.hist.shift();
      this.measureScene();
      this.control(now);
      this.draw();
    }
    this.pumpTextures();
    const far = +dd.value * T, a = this.aggr;
    // Two limits per kind, the nearer one wins:
    //  - distance: a share of the draw distance. cut = far * (floor + (1 - floor) * (1 - min(1, a * k))). Each kind goes at its
    //    own speed: trees, markings and cars first, buildings last.
    //  - size on screen: hidden once it is thinner than a few pixels. This is what matters for a model seen whole from
    //    a distance (a single tile never crosses a draw-distance cut, but its trees are two pixels tall).
    const cut = (floor, k) => (a > 0 ? far * (floor + (1 - floor) * (1 - Math.min(1, a * k))) : Infinity);
    const px = a > 0 ? innerHeight / (2 * Math.tan(camera.fov * Math.PI / 360)) : 0;   // pixels per metre at 1 m distance
    const a15 = Math.pow(a, 1.5);
    const size = (metres, base, span) => (a > 0 ? metres * px / (base + span * a15) : Infinity);   // distance at which it is thinner than that many pixels
    const m = Math.min;
    this.cuts = { tile: a > 0 ? far * (1 - 0.65 * a) : Infinity,
      tree: m(cut(0, 1.0), size(14, 0.4, 3.5)), markings: m(cut(0, 1.6), size(0.8, 0.5, 6)), car: m(cut(0, 1.5), size(2, 0.6, 8)),
      roads: m(cut(0.1, 1.0), size(6, 0.5, 3)), building: m(cut(0.05, 0.9), size(14, 0.5, 5)) };
    this.treeKeep = a > 0 ? Math.max(0.05, 1 - 0.95 * a) : 1;   // share of trees drawn: they thin out first and only vanish near full load
  },
  // The controller: the shortfall against the target frame rate drives both axes; the bottleneck decides which goes further.
  control(now) {
    const q = quality.value;
    this.potato = q === 'potato';
    const target = Math.min(this.START, this.peak * 0.92);      // a 30 Hz display is not "slow" at 30 fps
    const full = Math.min(this.FULL, target * 0.5);
    // Loading a tile or a picture stalls the page for a moment: ignore the first seconds after the model opens, and
    // only react to slowness that lasts (two readings in a row), so a hitch never makes the trees vanish.
    if (window.__ready && !this._readyAt) this._readyAt = now;
    if (typeof ACT !== 'undefined' && ACT.items.size) this._idleSince = now;   // downloads and tile decoding in progress
    const warm = !this._readyAt || now - this._readyAt < 5000 || now - this._idleSince < 3000;
    // Culling only makes sense if the frame rate answers to it. If several raises in a row bring no gain, the limit is
    // elsewhere (the browser, a throttled tab, a software renderer): put everything back and stop trying for a while,
    // rather than strip the scene for nothing.
    if (this._ref && now - this._ref.t >= 1200) {
      this._noGain = this.fps < this._ref.fps * 1.1 ? this._noGain + 1 : 0; this._ref = null;
      if (this._noGain >= 3 && !this.potato) {
        this.aggr = this.floor; this.gfx = q === 'low' ? 0.5 : 0; this.applyGfx();
        this._lockUntil = now + this._lockLen; this._lockLen = Math.min(this._lockLen * 2, 600000);
        this._noGain = 0; this.futile = true; return;
      }
    }
    if (now > this._lockUntil) this.futile = false;
    this._slow = this.fps < target ? this._slow + 1 : 0;
    const short = (this.on || this.potato) && !warm && now > this._lockUntil && this._slow >= 2 ? Math.max(0, Math.min(1, (target - this.fps) / (target - full))) : 0;
    // Probe: a frame time well above the JavaScript time means the GPU is what makes us wait.
    this.bound = this.fps >= target ? 'ok' : (this.cpuMs / this.ms > 0.6 ? 'cpu' : 'gpu');
    const cullK = this.bound === 'gpu' ? 0.9 : 1.4, gfxK = this.bound === 'cpu' ? 0.7 : 1.7;
    let wantA = Math.min(1, short * cullK), wantG = q === 'high' ? 0 : Math.min(1, short * gfxK);
    if (this.potato) { wantG = 1; wantA = Math.max(wantA, 0.6); }
    wantA = Math.max(Math.min(wantA, this.AUTO_MAX), this.floor);   // automatic culling never empties the scene; the floor slider can
    if (q === 'low') wantG = Math.max(wantG, 0.5);
    const ease = (cur, want) => {
      if (want > cur + 0.02) { this._lastUp = now; if (!this._ref) this._ref = { fps: this.fps, t: now }; return cur + (want - cur) * 0.6; }
      // ease back only after a quiet spell, and only while the frame rate has headroom, in small steps
      if (now - this._lastUp > 5000 && this.fps >= target * 0.9) return Math.max(want, cur - 0.08);
      return cur;
    };
    this.aggr = ease(this.aggr, wantA); if (this.aggr < 0.01 && wantA === 0) this.aggr = 0;
    this.gfx = ease(this.gfx, wantG);   if (this.gfx < 0.01 && wantG === 0) this.gfx = 0;
    this.applyGfx();
  },
  // Turn the gfx scalar into the actual settings, touching only what changed (each change can cost a re-allocation)
  applyGfx() {
    const g = this.gfx, q = quality.value;
    const lite = this.lite ? g > 0.3 : g > 0.45;
    if (lite !== this.lite) { this.lite = lite; this.applyLite(); }
    const res = g < 0.12 ? 4096 : g < 0.25 ? 2048 : g < 0.4 ? 1024 : 0;
    if (res !== this.shadowRes) { this.shadowRes = res; if (res) this.setShadowRes(res); }
    const ts = g < 0.3 ? 1 : g < 0.5 ? 0.5 : g < 0.7 ? 0.25 : 0;
    if (ts !== this.texScale) {
      const crossing = ts === 0 || this.texScale === 0;
      this.texScale = ts;
      this._fit = [...satTexs];
      if (crossing) tileList.forEach(t => syncImagery(t));
    }
    this.noTraffic = this.aggr > 0.9; this.halfTraffic = this.aggr > 0.45 || g > 0.5;
    // render resolution: quantised, and applied only on a real change (a resize reallocates the framebuffer)
    let r = q === 'high' ? dpr : dpr * (1 - 0.65 * g);
    if (q === 'low') r = Math.min(r, 0.75);
    if (this.potato) r = Math.min(r, 0.5);
    r = Math.max(0.35, Math.round(r * 20) / 20);
    if (Math.abs(r - ratio) >= 0.08 || (r === dpr && ratio !== dpr)) {
      ratio = r; renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight);
    }
  },
  setShadowRes(res) {
    if (sun.shadow.mapSize.x === res) return;
    sun.shadow.mapSize.set(res, res);
    sun.shadow.normalBias = Math.min(0.15, 2 * shadowRange / res * 0.5);
    if (sun.shadow.map) { sun.shadow.map.dispose(); sun.shadow.map = null; }   // reallocated at the new size on next use
  },
  // Glossy water (a physical material with a clear coat) is the most expensive fragment shader in the scene
  applyLite() {
    for (const m of waterMats) { m.clearcoat = this.lite ? 0 : 0.8; m.needsUpdate = true; }
  },
  // Satellite pictures are re-cut smaller (or dropped from the GPU) a couple per frame, so nothing hitches
  fitTexture(tex) {
    const u = tex.userData, full = u.full; if (!full) return;
    const s = this.texScale || 0.25;                       // no textures: the map is unplugged, keep a small copy
    if (u.scale === s) return;
    u.scale = s; u.resizing = true;
    tex.dispose();                                         // drops the GPU copy; the new size is uploaded on next use
    if (s >= 1) tex.image = full;
    else {
      const c = document.createElement('canvas');
      c.width = Math.max(64, Math.round(full.width * s)); c.height = Math.max(64, Math.round(full.height * s));
      const g = c.getContext('2d'); g.imageSmoothingQuality = 'high'; g.drawImage(full, 0, 0, c.width, c.height);
      tex.image = c;
    }
    tex.anisotropy = this.lite ? 1 : Math.min(16, renderer.capabilities.getMaxAnisotropy());
    tex.needsUpdate = true;
    u.resizing = false;
  },
  pumpTextures() {
    for (let i = 0; i < 2 && this._fit.length; i++) { const t = this._fit.pop(); if (satTexs.has(t)) this.fitTexture(t); }
  },
  // Scene numbers for the HUD. Tris, batches (draw calls) and set-pass count come from the renderer's frame info
  // (read before this frame's render, so they describe the previous frame). Objects are visible meshes; set passes
  // are approximated by the number of distinct materials drawn, since each new material means a new GPU state.
  measureScene() {
    const info = renderer.info.render;
    let objects = 0; const mats = new Set();
    root.traverseVisible(o => {
      if (!o.isMesh) return;
      objects++;
      const m = o.material;
      if (Array.isArray(m)) m.forEach(x => mats.add(x.uuid)); else if (m) mats.add(m.uuid);
    });
    this.stats = { tris: info.triangles, calls: info.calls, objects, passes: mats.size };
  },
  draw() {
    const fpsEl = document.getElementById('phFps'); if (!fpsEl) return;
    fpsEl.textContent = this.fps.toFixed(0);
    document.getElementById('phMs').textContent = this.ms.toFixed(1);
    const fmt = n => n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);
    document.getElementById('stTris').textContent = fmt(this.stats.tris);
    document.getElementById('stObj').textContent = fmt(this.stats.objects);
    document.getElementById('stBatch').textContent = fmt(this.stats.calls);
    document.getElementById('stPass').textContent = fmt(this.stats.passes);
    this.drawGraph();
    document.getElementById('perfHud').dataset.band = this.fps >= this.START ? 'good' : (this.fps >= this.FULL + 10 ? 'warn' : 'bad');
    const pct = Math.round(this.aggr * 100);
    document.getElementById('phCull').textContent = !this.on && !this.potato && !this.floor ? 'Culling off'
      : this.futile ? 'Culling paused · it made no difference, the limit is elsewhere'
      : pct === 0 && this.gfx === 0 ? 'Culling idle · full detail'
      : `Culling ${pct}% · hiding far trees, roads, buildings and cars`;
    const bEl = document.getElementById('phBound');
    if (bEl) bEl.textContent =
      (this.bound === 'ok' ? 'No bottleneck' : this.bound === 'cpu' ? 'Bottleneck: CPU (objects, draw calls)' : 'Bottleneck: GPU (pixels, shadows, textures)') +
      ` · JS ${this.cpuMs.toFixed(1)} ms\nRes ${Math.round(ratio * 100)}% · shadows ${this.shadowRes || 'off'} · textures ${this.texScale === 0 ? 'off' : Math.round(this.texScale * 100) + '%'}${this.lite ? ' · simple water' : ''}`;
  },
  // FPS over the last ~60 s, with 20 / 50 fps guide lines (the culling thresholds)
  drawGraph() {
    const cv = document.getElementById('phGraph'); if (!cv) return;
    const g = cv.getContext('2d'), W = cv.width, H = cv.height, top = 60;   // y-scale: 0..top fps
    g.clearRect(0, 0, W, H);
    const y = f => H - Math.min(f, top) / top * H;
    g.strokeStyle = 'rgba(128,128,128,0.35)'; g.lineWidth = 1; g.setLineDash([3, 3]);
    for (const f of [28, 55]) { g.beginPath(); g.moveTo(0, y(f)); g.lineTo(W, y(f)); g.stroke(); }
    g.setLineDash([]);
    g.strokeStyle = '#2ecc71'; g.lineWidth = 1.5; g.beginPath();
    this.hist.forEach((f, i) => {
      const x = (i + (120 - this.hist.length)) / 119 * W;
      i ? g.lineTo(x, y(f)) : g.moveTo(x, y(f));
    });
    g.stroke();
  },
};
$('cullOn').onchange = () => { PERF.on = $('cullOn').checked; };
PERF.on = $('cullOn').checked;
$('cullMin').oninput = () => {
  PERF.floor = +$('cullMin').value / 100; $('cullMinO').textContent = $('cullMin').value + '%';
  PERF._lastUp = performance.now(); if (PERF.floor > PERF.aggr) PERF.aggr = PERF.floor;
};
$('cullMin').oninput();
