// ---------------------------------------------------------------- rain and snow: GPU particles
// Every particle's position is computed in the vertex shader from a fixed random seed and a time uniform, so the CPU does
// nothing per frame but write a handful of uniforms. Intensity is the number of particles drawn (a draw range), so a low
// setting is genuinely cheaper. The volume follows what you look at and scales with the zoom, and fades at its edges,
// near the lens and at top and bottom, so nothing pops.
const PRECIP = (() => {
  const MAX = 30000, st = { rain: 0, snow: 0, autoRain: 0, autoSnow: 0, wind: 0.3, scale: 1, box: 0, hgt: 0 };
  const U = {
    uTime: { value: 0 }, uCenter: { value: new THREE.Vector3() }, uBox: { value: 1 }, uHeight: { value: 1 }, uBase: { value: CFG.zrange[0] },
    uWind: { value: new THREE.Vector2() }, uRes: { value: 1000 },
  };
  const seeds = new Float32Array(MAX * 4);
  for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
  const COMMON = `
    uniform float uTime, uBox, uHeight, uBase; uniform vec3 uCenter; uniform vec2 uWind;
    // world position of a particle (y01 in 0..1 up the volume), wrapped around the centre so it stays put as the volume moves
    vec3 place(vec4 s, float speed, float drift, out float y01) {
      y01 = fract(s.y - uTime * speed * (0.8 + 0.4 * s.w));
      float x = uCenter.x + (fract(s.x - uCenter.x / uBox + 0.5) - 0.5) * uBox;
      float z = uCenter.z + (fract(s.z - uCenter.z / uBox + 0.5) - 0.5) * uBox;
      float h = uHeight * drift * (1.0 - y01);
      return vec3(x + uWind.x * h, uBase + y01 * uHeight, z + uWind.y * h);
    }
    float fadeOf(vec3 p, float y01) {
      float d = length(vec2(p.x - uCenter.x, p.z - uCenter.z)) / (0.5 * uBox);
      float f = (1.0 - smoothstep(0.65, 1.0, d)) * smoothstep(0.0, 0.06, y01) * (1.0 - smoothstep(0.9, 1.0, y01));
      return f * smoothstep(0.0, 0.04 * uBox, distance(p, cameraPosition));
    }`;
  const mk = (vs, fs, extra) => new THREE.ShaderMaterial({ uniforms: Object.assign({ uColor: { value: new THREE.Color() }, uAlpha: { value: 1 } }, U, extra),
    vertexShader: COMMON + vs, fragmentShader: fs, transparent: true, depthWrite: false });
  const FS_LINE = `uniform vec3 uColor; uniform float uAlpha; varying float vA;
    void main() {
      gl_FragColor = vec4(uColor, vA * uAlpha);
      #include <colorspace_fragment>
    }`;
  const FS_POINT = `uniform vec3 uColor; uniform float uAlpha; varying float vA;
    void main() {
      float r = length(gl_PointCoord - 0.5) * 2.0;
      float a = (1.0 - smoothstep(0.2, 1.0, r)) * vA * uAlpha;
      if (a < 0.02) discard;
      gl_FragColor = vec4(uColor, a);
      #include <colorspace_fragment>
    }`;

  // rain: one line segment per drop (two vertices sharing a seed; aEnd = 1 on the trailing end)
  const rg = new THREE.BufferGeometry(), rs = new Float32Array(MAX * 8), re = new Float32Array(MAX * 2);
  for (let i = 0; i < MAX; i++) { rs.set(seeds.subarray(i * 4, i * 4 + 4), i * 8); rs.set(seeds.subarray(i * 4, i * 4 + 4), i * 8 + 4); re[i * 2 + 1] = 1; }
  rg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX * 6), 3));
  rg.setAttribute('aSeed', new THREE.BufferAttribute(rs, 4));
  rg.setAttribute('aEnd', new THREE.BufferAttribute(re, 1));
  const rainMat = mk(`attribute vec4 aSeed; attribute float aEnd; varying float vA;
    void main() {
      float y01; vec3 p = place(aSeed, 1.3, 0.28, y01);
      vec3 fall = normalize(vec3(uWind.x * 0.28, -1.0, uWind.y * 0.28));
      p -= fall * (0.03 * uHeight) * aEnd;                              // the trailing end of the streak
      vA = fadeOf(p, y01);
      gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
    }`, FS_LINE, {});
  const rain = new THREE.LineSegments(rg, rainMat);

  // snow: one soft round point per flake, drifting and swirling as it falls
  const sg = new THREE.BufferGeometry();
  sg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX * 3), 3));
  sg.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 4));
  const snowMat = mk(`attribute vec4 aSeed; varying float vA; uniform float uRes;
    void main() {
      float y01; vec3 p = place(aSeed, 0.06, 0.18, y01);
      float w = uTime * 0.55 + aSeed.w * 40.0;
      p.x += sin(w) * 0.012 * uBox; p.z += cos(w * 0.83) * 0.012 * uBox;
      vA = fadeOf(p, y01);
      vec4 mv = viewMatrix * vec4(p, 1.0);
      gl_Position = projectionMatrix * mv;
      float world = uBox * 0.0016 * (0.6 + 0.9 * aSeed.w);
      gl_PointSize = clamp(world * projectionMatrix[1][1] * uRes * 0.5 / max(-mv.z, 0.001), 1.2, 8.0);
    }`, FS_POINT, {});
  const snow = new THREE.Points(sg, snowMat);

  for (const o of [rain, snow]) { o.frustumCulled = false; o.visible = false; o.renderOrder = 20; scene.add(o); }

  const colour = (m, rgb) => m.uniforms.uColor.value.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace);
  const size = new THREE.Vector2();
  let lastBox = 0, lastSnow = -1;
  const api = {
    state: st,
    setAuto(r, s_) { st.autoRain = r; st.autoSnow = s_; },
    recolor() {      // flakes must read against the theme: white on dark scenes, a cool grey-blue on pale ones
      const bg = (theme && theme.bg) ? new THREE.Color().setStyle(theme.bg, THREE.SRGBColorSpace) : new THREE.Color(0, 0, 0);
      const light = (bg.r + bg.g + bg.b) / 3 > 0.55;
      colour(snowMat, light ? [0.70, 0.78, 0.9] : [1, 1, 1]);
      colour(rainMat, light ? [0.46, 0.58, 0.76] : [0.72, 0.82, 0.98]);
      rainMat.uniforms.uAlpha.value = light ? 0.7 : 0.55; snowMat.uniforms.uAlpha.value = light ? 1.0 : 0.95;
    },
    update(now) {
      const r = Math.max(st.rain, st.autoRain), s_ = Math.max(st.snow, st.autoSnow);
      rain.visible = r > 0; snow.visible = s_ > 0;
      if (!rain.visible && !snow.visible) return;
      const dist = camera.position.distanceTo(controls.target);
      const box = Math.min(Math.max(dist * 1.3, 100), H * 4);
      if (!lastBox || Math.abs(box - lastBox) > 0.06 * lastBox) lastBox = box;      // steps, so zooming does not shimmer the flakes
      U.uBox.value = lastBox; U.uHeight.value = Math.min(Math.max(lastBox * 0.5, 80), 1000);
      U.uCenter.value.copy(controls.target);
      U.uTime.value = now / 1000;
      U.uWind.value.set(st.wind * 0.9, st.wind * 0.25);
      renderer.getDrawingBufferSize(size); U.uRes.value = size.y;
      const q = (quality.value === 'low' ? 0.5 : 1) * st.scale;                    // quality setting and the adaptive controller
      rg.setDrawRange(0, Math.round(MAX * Math.pow(r, 1.4) * q) * 2);
      sg.setDrawRange(0, Math.round(MAX * Math.pow(s_, 1.4) * q));
    },
    tick(fps) {      // adaptive: thin the particles out when the frame rate drops, restore them when it recovers
      if (!(rain.visible || snow.visible) || quality.value !== 'auto') return;
      st.scale = fps < 26 ? Math.max(0.3, st.scale * 0.8) : (fps > 56 ? Math.min(1, st.scale * 1.06) : st.scale);
    },
  };
  api.recolor();
  precipRecolor = api.recolor;
  return api;
})();
function applySnowCover() {   // snow settles on roofs and ground in proportion to how hard it snows
  manualSnow = $('snowOn').checked ? +$('snowAmt').value : 0;
  winU.uSnow.value = Math.max(manualSnow * 0.9, REAL.on ? REAL.snowCover * 0.9 : 0);
  applyThemeTo(root); setThemeColors();
}
function syncWeather() {
  const rOn = $('rainOn').checked, sOn = $('snowOn').checked, wnd = +$('windAmt').value;
  $('rainAmt').disabled = !rOn; $('snowAmt').disabled = !sOn;
  PRECIP.state.rain = rOn ? +$('rainAmt').value : 0; PRECIP.state.snow = sOn ? +$('snowAmt').value : 0;
  PRECIP.state.wind = wnd; $('windO').textContent = wnd < 0.05 ? 'calm' : Math.round(wnd * 100) + '%';
}
let snowRaf = 0;
['rainOn', 'rainAmt', 'snowOn', 'snowAmt', 'windAmt'].forEach(id => $(id).addEventListener('input', () => {
  syncWeather();
  if (id.startsWith('snow')) { cancelAnimationFrame(snowRaf); snowRaf = requestAnimationFrame(applySnowCover); }   // one repaint per frame while dragging
}));
syncWeather();

// Building walls get lit windows and snowy roofs in the shader: a window grid in world space, lit per cell.
function addWindows(mat) {
  mat.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, winU);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWp;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvWp = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
varying vec3 vWp;
uniform float uNight; uniform float uLit; uniform float uSnow;
float hash21(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }`)
      .replace('#include <color_fragment>', `#include <color_fragment>
  vec3 fN = normalize(cross(dFdx(vWp), dFdy(vWp)));
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.94, 0.96, 0.98), uSnow * smoothstep(0.6, 0.85, fN.y));`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
  {
    vec3 tN = normalize(cross(fN, vec3(0.0, 1.0, 0.0)));
    float wall = 1.0 - smoothstep(0.45, 0.75, abs(fN.y));
    vec2 cell = vec2(dot(vWp, tN) / 3.2, vWp.y / 3.4);
    vec2 f = fract(cell);
    float inWin = step(0.28, f.x) * step(f.x, 0.72) * step(0.3, f.y) * step(f.y, 0.75);
    float lit = step(hash21(floor(cell)), uLit);
    totalEmissiveRadiance += vec3(1.0, 0.8, 0.5) * 1.1 * wall * inWin * lit * uNight;
  }`);
  };
  mat.customProgramCacheKey = () => 'windows';
}

// Sun altitude and compass bearing (degrees) for a UTC instant at a place: the standard low-precision solar formulas.
function sunPos(ms, latDeg, lonDeg) {
  const rad = Math.PI / 180, d = ms / 86400000 - 0.5 + 2440588 - 2451545;
  const M = rad * (357.5291 + 0.98560028 * d);
  const C = rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + rad * 102.9372 + Math.PI, e = rad * 23.4397;
  const ra = Math.atan2(Math.sin(L) * Math.cos(e), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const H = rad * (280.16 + 360.9856235 * d + lonDeg) - ra;
  const lat = rad * latDeg;
  const alt = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(lat) - Math.tan(dec) * Math.cos(lat));
  return { alt: alt / rad, bearing: (az / rad + 180) % 360 };
}

// 0 = deep winter, 1 = midsummer, from the day of the year and the hemisphere.
function seasonK(now) {
  const doy = (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(now.getFullYear(), 0, 0)) / 86400000;
  const s = CFG.geo.lat >= 0 ? 1 : -1;
  return (s * Math.cos(2 * Math.PI * (doy - 172) / 365) + 1) / 2;
}

// Relative traffic by hour: morning and evening peaks, quiet nights; weekends are about 40% quieter.
function trafficAt(h, weekend) {
  let v = TRAFFIC_HOURS[TRAFFIC_HOURS.length - 1][1];
  for (let i = 0; i < TRAFFIC_HOURS.length - 1; i++) {
    const [h0, v0] = TRAFFIC_HOURS[i], [h1, v1] = TRAFFIC_HOURS[i + 1];
    if (h >= h0 && h <= h1) { v = v0 + (v1 - v0) * (h - h0) / (h1 - h0); break; }
  }
  return weekend ? v * 0.6 : v;
}

function conditions(w) {   // WMO weather codes from Open-Meteo, backed by the precipitation amounts
  const c = w ? w.code : 0;
  const snowing = !!w && (w.snow > 0 || (c >= 71 && c <= 77) || c === 85 || c === 86);
  const raining = !!w && !snowing && (w.rain > 0 || (c >= 51 && c <= 67) || (c >= 80 && c <= 82) || c >= 95);
  return { snowing, raining, foggy: !!w && (c === 45 || c === 48), storm: !!w && c >= 95 };
}

function describeWeather(w) {
  if (!w) return 'weather unavailable (offline): clear sky assumed';
  const cd = conditions(w);
  const sky = cd.foggy ? 'fog' : cd.storm ? 'thunderstorm' : cd.snowing ? 'snow' : cd.raining ? 'rain'
    : w.cloud > 0.8 ? 'overcast' : w.cloud > 0.4 ? 'partly cloudy' : 'clear';
  return (w.temp == null ? '' : Math.round(w.temp) + ' °C · ') + sky;
}

async function fetchWeather() {   // current conditions at the model's centre; only the coordinates are sent
  if (REAL.manual) { REAL.weather = REAL.manual; if (REAL.on) applyRealistic(); return; }   // set by hand: nothing to fetch
  REAL.weather = null;
  try {
    const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + CFG.geo.lat.toFixed(3) + '&longitude=' + CFG.geo.lon.toFixed(3)
      + '&current=temperature_2m,cloud_cover,rain,snowfall,weather_code&timezone=auto';
    const c = (await (await fetch(url)).json()).current;
    REAL.weather = { temp: c.temperature_2m, cloud: c.cloud_cover / 100, rain: c.rain, snow: c.snowfall, code: c.weather_code };
  } catch (e) { REAL.weather = null; }
  if (REAL.on) applyRealistic();
}

function realDate() {
  const d = new Date();
  if (REAL.override !== null) d.setHours(Math.floor(REAL.override), Math.round((REAL.override % 1) * 60), 0, 0);
  return d;
}

// Road centre lines as arrays of points with cumulative lengths, for cars.
function buildRoads() {
  if (REAL.roads) return REAL.roads;
  REAL.roads = [];
  for (const flat of CFG.roads || []) {
    const pts = new Float32Array(flat), n = pts.length / 3, cum = new Float64Array(n);
    for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + Math.hypot(pts[i * 3] - pts[(i - 1) * 3], pts[i * 3 + 2] - pts[(i - 1) * 3 + 2]);
    if (cum[n - 1] > 1) REAL.roads.push({ pts, cum, len: cum[n - 1] });
  }
  return REAL.roads;
}
const P_AT = { x: 0, y: 0, z: 0, tx: 0, tz: 1 };
function pointAt(road, s) {   // position and unit horizontal tangent at distance s along a road
  const cum = road.cum, pts = road.pts;
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
  const t = clamp01((s - cum[lo]) / Math.max(1e-6, cum[hi] - cum[lo])), a = lo * 3, b = hi * 3;
  P_AT.x = pts[a] + (pts[b] - pts[a]) * t; P_AT.y = pts[a + 1] + (pts[b + 1] - pts[a + 1]) * t;
  P_AT.z = pts[a + 2] + (pts[b + 2] - pts[a + 2]) * t;
  const dx = pts[b] - pts[a], dz = pts[b + 2] - pts[a + 2], L = Math.hypot(dx, dz) || 1;
  P_AT.tx = dx / L; P_AT.tz = dz / L;
  return P_AT;
}
function pickRoad(roads, total) {   // a road, chosen in proportion to its length
  let r = Math.random() * total;
  for (const road of roads) { r -= road.len; if (r <= 0) return road; }
  return roads[roads.length - 1];
}

// Created on first use: cars, street lights, rain, snow and stars. Everything on the road network is in the root group,
// so it follows the vertical exaggeration like the buildings do.
function ensureRealistic() {
  if (REAL.cars) return;
  const roads = buildRoads(), H = CFG.half, yLo = CFG.zrange[0], yHi = CFG.zrange[1] + H * 0.08;
  const total = roads.reduce((a, r) => a + r.len, 0);
  const body = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshLambertMaterial({ color: 0xffffff }), MAX_CARS);
  body.frustumCulled = false; body.count = 0;
  const palette = [0xf2f2f2, 0x25282d, 0x9aa1aa, 0x8c1c1c, 0x1f3a5f, 0xd9d4c7, 0x4a4f55, 0xe0b040];
  const lightPos = new Float32Array(MAX_CARS * 12), lightCol = new Float32Array(MAX_CARS * 12), state = [];
  const col = new THREE.Color();
  for (let i = 0; roads.length && i < MAX_CARS; i++) {
    col.setHex(palette[Math.floor(Math.random() * palette.length)]);
    body.setColorAt(i, col);
    const road = pickRoad(roads, total);
    state.push({ road, s: Math.random() * road.len, dir: Math.random() < 0.5 ? 1 : -1, v: 8 + Math.random() * 6 });
    for (let j = 0; j < 4; j++) lightCol.set(j < 2 ? [1, 0.95, 0.8] : [1, 0.12, 0.06], i * 12 + j * 3);
  }
  const lightGeo = new THREE.BufferGeometry();
  lightGeo.setAttribute('position', new THREE.BufferAttribute(lightPos, 3));
  lightGeo.setAttribute('color', new THREE.BufferAttribute(lightCol, 3));
  lightGeo.setDrawRange(0, 0);
  const lights = new THREE.Points(lightGeo, new THREE.PointsMaterial({ size: H * 0.005, sizeAttenuation: true, vertexColors: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  lights.frustumCulled = false;
  REAL.cars = { body, lights, lightGeo, lightPos, state, dummy: new THREE.Object3D() };
  root.add(body, lights);

  const lampPos = [];
  for (const r of roads) for (let s = LAMP_SPACING / 2; s < r.len && lampPos.length < 90000; s += LAMP_SPACING) {
    const p = pointAt(r, s); lampPos.push(p.x, p.y + 6.5, p.z);
  }
  const lampGeo = new THREE.BufferGeometry();
  lampGeo.setAttribute('position', new THREE.Float32BufferAttribute(lampPos, 3));
  REAL.lamps = new THREE.Points(lampGeo, new THREE.PointsMaterial({ color: 0xffd08a, size: H * 0.006, sizeAttenuation: true,
    transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
  REAL.lamps.frustumCulled = false;
  root.add(REAL.lamps);


  const starPos = new Float32Array(STARS * 3), R = H * 30;
  for (let i = 0; i < STARS; i++) {   // sky dome, upper hemisphere
    const u = rnd(-1, 1), a = rnd(0, Math.PI * 2), q = Math.sqrt(1 - u * u);
    starPos.set([R * q * Math.cos(a), R * Math.abs(u), R * q * Math.sin(a)], i * 3);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  REAL.stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false,
    transparent: true, opacity: 0, depthWrite: false, fog: false }));
  REAL.stars.visible = false;
  scene.add(REAL.stars);
}

function realisticObjects() {
  if (!REAL.cars) return [];
  return [REAL.cars.body, REAL.cars.lights, REAL.lamps, REAL.stars];
}

// Recomputes the whole environment from the date, time, place and weather. Cheap; called on changes and every 30 s.
function applyRealistic() {
  if (!REAL.on) return;
  ensureRealistic();
  const now = realDate(), hour = now.getHours() + now.getMinutes() / 60, w = REAL.weather, cd = conditions(w);
  const sp = sunPos(now.getTime(), CFG.geo.lat, CFG.geo.lon), elev = sp.alt;
  const cloud = w ? clamp01(w.cloud) : 0.1;
  const up = clamp01((elev + 2) / 8), day = clamp01((elev + 6) / 12), night = clamp01((-elev - 1) / 5);
  const warm = 1 - clamp01(elev / 30), dusk = clamp01(1 - Math.abs(elev) / 10);
  const season = seasonK(now), winterness = 1 - season;
  const weekend = now.getDay() === 0 || now.getDay() === 6;
  const traffic = trafficAt(hour, weekend);
  const snowCover = cd.snowing ? 0.9 : (w && w.temp != null && w.temp < -2 && winterness > 0.5 ? 0.6 : 0);
  const fogAmt = cd.foggy ? 1 : cd.storm ? 0.5 : cd.raining ? 0.4 : cd.snowing ? 0.3 : 0;
  Object.assign(REAL, { night, traffic, season, snowCover, cloud, cond: cd, elev });

  // sun and sky
  sunAz = sp.bearing; sunEl = Math.max(elev, 1);
  sun.visible = up > 0.01;
  sun.intensity = 2.6 * up * (1 - 0.65 * cloud);
  sun.color.copy(SUN_HIGH).lerp(SUN_LOW, warm);
  hemi.intensity = (0.3 + 1.25 * day) * (1 - 0.3 * cloud);
  hemi.color.copy(HEMI_NIGHT).lerp(HEMI_DAY, day);
  placeSun();
  $('az').value = ((sunAz % 360) + 360) % 360; $('el').value = Math.max(8, sunEl);
  const sky = new THREE.Color().copy(SKY_NIGHT).lerp(SKY_DAY, day).lerp(SKY_CLOUD, cloud * 0.8 * day).lerp(SKY_DUSK, 0.75 * dusk);
  scene.background = sky;
  updateWaterEnv();
  const far = (+dd.value) * T * (1 - 0.7 * fogAmt);
  scene.fog = new THREE.Fog(sky, far * 0.55, far);

  // weather
  if (REAL.manual) PRECIP.setAuto(0, 0);   // the Rain and Snow sliders already drive the particles exactly
  else PRECIP.setAuto(cd.raining ? clamp01(0.25 + (w.rain || 0) / 2) : 0, cd.snowing ? clamp01(0.3 + (w.snow || 0)) : 0);

  // night: lit windows, street lights, stars and car lights
  winU.uNight.value = night;
  winU.uLit.value = hour >= 17 && hour < 23 ? 0.7 : (hour >= 23 || hour < 5 ? 0.3 : 0.5);
  winU.uSnow.value = Math.max(snowCover * 0.9, manualSnow * 0.9);
  REAL.stars.visible = night > 0.02; REAL.stars.material.opacity = night;
  REAL.lamps.visible = night > 0.02; REAL.lamps.material.opacity = 0.9 * night;
  REAL.cars.lights.material.opacity = night;
  if (trafficLights) trafficLights.material.opacity = night;

  setThemeColors();       // paints season and snow on the terrain and trees
  if (REAL.override === null) $('tod').value = hour;
  const tr = traffic < 0.3 ? 'quiet' : traffic < 0.7 ? 'moderate' : 'busy';
  $('realInfo').textContent = [now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }),
    'sun ' + Math.round(elev) + '°', describeWeather(w) + (REAL.manual ? ' (set by you)' : ''), 'traffic ' + tr].join(' · ');
}

// Season and snow colours on top of the theme: multiplied per role, so switching the theme or chunks is always consistent.
function paintSeason() {
  const k = REAL.season, snowC = winU.uSnow.value;
  const treeMul = new THREE.Color().lerpColors(WINTER_TREE, WHITE, k), groundMul = new THREE.Color().lerpColors(WINTER_GROUND, WHITE, k);
  const tc = treeBase();
  impostorUniforms.forEach(u => u.uColor.value.setRGB(tc[0], tc[1], tc[2], THREE.SRGBColorSpace).multiply(treeMul));
  root.traverse(o => {
    const role = o.userData.role, c = role && colorOf(role);
    if (!c || !o.isMesh || o.userData.impostor || o.userData.treeVar || !o.material || !o.material.color) return;
    const col = o.material.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
    if (role === 'tree') col.multiply(treeMul);
    else if (role === 'ground' || role === 'park' || role === 'forest') { col.multiply(groundMul); col.lerp(SNOW, snowC * 0.85); }
  });
}

// Cars drive along the road centre lines (ping-pong at the ends), with headlights and tail lights.
function stepRealistic(now) {
  const dt = Math.min(0.1, Math.max(0, (now - REAL.t) / 1000)); REAL.t = now;
  if (!REAL.on || !REAL.cars) return;
  const C = REAL.cars, n = trafficUpdate ? 0 : Math.min(Math.round(MAX_CARS * REAL.traffic), C.state.length), dummy = C.dummy, P = C.lightPos;
  for (let i = 0; i < n; i++) {
    const st = C.state[i];
    st.s += st.dir * st.v * dt;
    const L = st.road.len;
    if (st.s > L) { st.s = 2 * L - st.s; st.dir = -1; }
    if (st.s < 0) { st.s = -st.s; st.dir = 1; }
    const p = pointAt(st.road, st.s);
    const x = p.x, y = p.y, z = p.z, tx = p.tx * st.dir, tz = p.tz * st.dir;
    dummy.position.set(x, y + 0.75, z); dummy.rotation.set(0, Math.atan2(tx, tz), 0); dummy.scale.set(1.8, 1.5, 4.4);
    dummy.updateMatrix(); C.body.setMatrixAt(i, dummy.matrix);
    const px = -tz * 0.7, pz = tx * 0.7, hy = y + 0.6;
    P.set([x + tx * 2.3 + px, hy, z + tz * 2.3 + pz, x + tx * 2.3 - px, hy, z + tz * 2.3 - pz,
           x - tx * 2.3 + px, hy, z - tz * 2.3 + pz, x - tx * 2.3 - px, hy, z - tz * 2.3 - pz], i * 12);
  }
  C.body.count = n; C.body.instanceMatrix.needsUpdate = true;
  C.lightGeo.setDrawRange(0, n * 4); C.lightGeo.attributes.position.needsUpdate = true;
}

function setRealistic(on) {
  REAL.on = on;
  $('realBox').hidden = !on;
  renderer.toneMapping = on ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
  if (on) {
    REAL.saved = { az: sunAz, el: sunEl };
    ensureRealistic(); fetchWeather(); applyRealistic();
    return;
  }
  for (const o of realisticObjects()) o.visible = false;
  PRECIP.setAuto(0, 0);
  sunAz = REAL.saved ? REAL.saved.az : CFG.sun.bearing; sunEl = REAL.saved ? REAL.saved.el : CFG.sun.elevation;
  sun.visible = true; sun.intensity = 2.5; sun.color.set(0xffffff);
  hemi.intensity = 1.55; hemi.color.set(0xffffff);
  winU.uNight.value = 0; winU.uSnow.value = manualSnow * 0.9;
  scene.fog = null;
  $('az').value = ((sunAz % 360) + 360) % 360; $('el').value = sunEl;
  placeSun();
  applyTheme($('themeSel').value);
  applyFog();
}
$('real').onchange = () => setRealistic($('real').checked);
$('tod').oninput = () => { REAL.override = +$('tod').value; applyRealistic(); };
$('nowBtn').onclick = () => { REAL.override = null; applyRealistic(); };
setInterval(() => { if (REAL.on && REAL.override === null) applyRealistic(); }, 30000);
setInterval(() => { if (REAL.on) fetchWeather(); }, 10 * 60000);

applyTheme(CFG.theme); applyFog();
tileList.forEach(t => { if (t.center.distanceTo(controls.target) < 0.5 * T) t.explored = true; });
let tick = 0;
controls.addEventListener('change', placeSun);
window.__map3d = { updateTiles, tileList, explore: k => { const t = tiles.get(k); if (t) t.explored = true; } };

// ---------------------------------------------------------------- weather set by hand
// A preset (or the temperature / cloud sliders plus the Rain and Snow controls) replaces the live weather: in the realistic view
// it drives the sky, cloud, fog, snow cover and particles; without it, it drives the particles and the snow cover.
REAL.manual = null;
const WX_PRESETS = {
  clear:     { cloud: 0.05, temp: 18,  rain: 0,    snow: 0,    wind: 0.15, code: 0 },
  partly:    { cloud: 0.5,  temp: 16,  rain: 0,    snow: 0,    wind: 0.25, code: 0 },
  overcast:  { cloud: 0.95, temp: 10,  rain: 0,    snow: 0,    wind: 0.3,  code: 0 },
  fog:       { cloud: 0.9,  temp: 6,   rain: 0,    snow: 0,    wind: 0.05, code: 45 },
  lightrain: { cloud: 0.85, temp: 11,  rain: 0.35, snow: 0,    wind: 0.3,  code: 61 },
  heavyrain: { cloud: 1,    temp: 12,  rain: 0.95, snow: 0,    wind: 0.6,  code: 65 },
  snow:      { cloud: 0.9,  temp: -6,  rain: 0,    snow: 0.5,  wind: 0.2,  code: 73 },
  blizzard:  { cloud: 1,    temp: -15, rain: 0,    snow: 1,    wind: 0.9,  code: 75 },
  storm:     { cloud: 1,    temp: 20,  rain: 0.9,  snow: 0,    wind: 0.7,  code: 95 },
};
let wxCode = 0;   // WMO code that carries fog and thunderstorm, which have no slider of their own
function manualWeather() {
  const rainOn = $('rainOn').checked, snowOn = $('snowOn').checked;
  return {
    temp: +$('wxTemp').value, cloud: +$('wxCloud').value,
    rain: rainOn ? Math.max(0, (+$('rainAmt').value - 0.25) * 2) : 0, snow: snowOn ? Math.max(0, +$('snowAmt').value - 0.3) : 0,
    code: wxCode || (snowOn ? 73 : rainOn ? 61 : 0),
  };
}
function applyManualWeather() {
  if (!REAL.manual) return;
  REAL.manual = manualWeather(); REAL.weather = REAL.manual;
  $('wxTempO').textContent = $('wxTemp').value + ' °C'; $('wxCloudO').textContent = Math.round($('wxCloud').value * 100) + '%';
  if (REAL.on) applyRealistic();
}
function setWeatherControls(rain, snow, wind) {
  $('rainOn').checked = rain > 0; if (rain > 0) $('rainAmt').value = rain;
  $('snowOn').checked = snow > 0; if (snow > 0) $('snowAmt').value = snow;
  $('windAmt').value = wind;
  syncWeather(); applySnowCover();
}
$('wxPreset').onchange = () => {
  const v = $('wxPreset').value;
  if (v === 'live') {
    REAL.manual = null; wxCode = 0; $('wxManual').hidden = true;
    setWeatherControls(0, 0, +$('windAmt').value);
    fetchWeather();
    return;
  }
  const p = WX_PRESETS[v];
  wxCode = p.code; $('wxTemp').value = p.temp; $('wxCloud').value = p.cloud; $('wxManual').hidden = false;
  REAL.manual = {};
  setWeatherControls(p.rain, p.snow, p.wind);
  applyManualWeather();
};
['wxTemp', 'wxCloud'].forEach(id => $(id).addEventListener('input', () => {
  const sel = $('wxPreset'); const custom = sel.querySelector('option[value=custom]'); custom.hidden = false; sel.value = 'custom';
  applyManualWeather();
}));
['rainOn', 'rainAmt', 'snowOn', 'snowAmt'].forEach(id => $(id).addEventListener('input', () => {
  if (!REAL.manual) return;
  const sel = $('wxPreset'); sel.querySelector('option[value=custom]').hidden = false; sel.value = 'custom';
  if (wxCode && wxCode !== 45 && wxCode !== 95) wxCode = 0;   // a hand-set rain or snow level replaces the preset's own precipitation code
  applyManualWeather();
}));
