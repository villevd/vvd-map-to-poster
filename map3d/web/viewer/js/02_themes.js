// ---------------------------------------------------------------- themes
const root = new THREE.Group();
scene.add(root);
let theme = CFG.themes[CFG.theme];
const impostorUniforms = [];
const placeholderMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.28, depthWrite: false });
function applyThemeTo(obj) {
  obj.traverse(o => {
    if (!o.material || !o.material.color || o.userData.impostor || o.userData.treeVar) return;
    if (o.userData.textured) { o.material.color.setRGB(1, 1, 1); return; }   // the photo carries the colour
    const c = o.userData.role && colorOf(o.userData.role);
    if (c) o.material.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
  });
}
function setThemeColors() {
  const c = treeBase();
  impostorUniforms.forEach(u => u.uColor.value.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace));
  const bg = new THREE.Color().setStyle(theme.bg, THREE.SRGBColorSpace);
  if (!REAL.on) { scene.background = skyTex || bg; if (scene.fog) scene.fog.color.copy(bg); }   // the realistic sky takes over otherwise
  placeholderMat.color.setStyle(theme.text, THREE.SRGBColorSpace);
  updateWaterEnv();
  document.documentElement.style.setProperty('--bg', theme.bg);
  document.documentElement.style.setProperty('--text', theme.text);
  paintTreeVariants(root);
  if (REAL.on || manualSnow > 0) paintSeason();
  if (precipRecolor) precipRecolor();
}

// ---- colours: per-role overrides on top of the theme, and a random shade per tree
const COLOUR_ROLES = [['ground', 'Ground'], ['water', 'Water'], ['park', 'Parks'], ['forest', 'Forest'], ['tree', 'Trees'],
  ['building', 'Buildings'], ['road_motorway', 'Motorways'], ['road_primary', 'Primary roads'],
  ['road_secondary', 'Secondary roads'], ['road_tertiary', 'Tertiary roads'], ['road_residential', 'Small roads'],
  ['rail', 'Railways'], ['bridge', 'Bridges']];
const colourOv = {};
function colorOf(role) {
  if (colourOv[role]) return colourOv[role];
  if (role === 'water') return lakeTint(theme.colors.water);   // lakes: a clear blue-teal, still lighter or darker with the theme
  return theme.colors[role];
}
const hexOf = c => '#' + c.slice(0, 3).map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
function syncPickers() {
  document.querySelectorAll('#colorPickers input[type=color]').forEach(inp => {
    const c = colorOf(inp.dataset.role); if (c) inp.value = hexOf(c);
  });
}
// Five shades of the tree colour: small hue, saturation and lightness shifts, so the trees stay one family
const TREE_SHIFTS = [[0, 0, 0], [0.015, 0.05, 0.05], [-0.02, -0.08, -0.04], [0.03, 0, -0.06], [-0.035, 0.1, 0.02]];
// "Match ground": same hue and saturation as the ground, only slightly darker or lighter, randomised per tree
const MATCH_SHIFTS = [[0, 0, 0], [0, 0, 0.05], [0, 0, -0.05], [0, 0, 0.09], [0, 0, -0.08]];
// Natural tree colours (sRGB 0-1) used when the ground is not green or brown: the nearest one is taken
const NATURAL_TREES = [[0.16, 0.33, 0.15], [0.25, 0.45, 0.20], [0.36, 0.50, 0.22], [0.42, 0.56, 0.27],
  [0.42, 0.31, 0.19], [0.32, 0.22, 0.14], [0.50, 0.38, 0.24]];
function toHsl(c) {
  const hsl = {}; new THREE.Color().setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace).getHSL(hsl, THREE.SRGBColorSpace);
  return hsl;
}
/** The base tree colour: the theme's tree colour, or with "match ground" the ground colour when it is green or brown. */
const SAT_TREE = [0.11, 0.25, 0.10];   // over the aerial photo: the dark green of real boreal conifers
/** Keeps a candidate tree colour a believable vegetation shade: never pale or grey, whatever the ground looks like. */
function vegetation(c) {
  const hsl = toHsl(c);
  return new THREE.Color().setHSL(hsl.h, Math.max(hsl.s, 0.32), Math.min(hsl.l, 0.30), THREE.SRGBColorSpace)
    .getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);
}
function treeBase() {
  if (!$('treeMatch').checked) return colorOf('tree');
  if ($('satOn') && $('satOn').checked) return SAT_TREE;          // the ground colour means nothing under a photo
  const g = colorOf('ground').slice(0, 3), hsl = toHsl(g);
  const natural = hsl.h * 360 >= 20 && hsl.h * 360 <= 160 && hsl.s >= 0.12 && hsl.l >= 0.12 && hsl.l <= 0.6;
  const pick = natural ? g : NATURAL_TREES.reduce((best, c) => {
    const d2 = k => (k[0] - g[0]) ** 2 + (k[1] - g[1]) ** 2 + (k[2] - g[2]) ** 2;
    return d2(c) < d2(best) ? c : best;
  });
  const v = vegetation(pick);
  return [v.r, v.g, v.b];
}
function treePalette() {
  const base = treeBase(), hsl = toHsl(base), match = $('treeMatch').checked;
  return (match ? MATCH_SHIFTS : TREE_SHIFTS).map(([dh, ds, dl]) => new THREE.Color().setHSL(
    (hsl.h + dh + 1) % 1, Math.min(1, Math.max(0, hsl.s + ds)), Math.min(1, Math.max(0, hsl.l + dl)), THREE.SRGBColorSpace));
}
// Trees with a baked shade: each tree's colour is written per vertex (shade x tint), and the material is
// white, or the season's tint when snow is on.
function paintTreeVariants(obj = root) {
  const pal = treePalette(), match = $('treeMatch').checked, random = match || $('treeRandom').checked;
  const mul = REAL.on ? new THREE.Color().lerpColors(WINTER_TREE, new THREE.Color(1, 1, 1), REAL.season) : new THREE.Color(1, 1, 1);
  obj.traverse(o => {
    const tv = o.userData.treeVar; if (!tv || !o.isMesh) return;
    const attr = o.geometry.getAttribute('color'), arr = attr.array;
    for (let i = 0; i < tv.n; i++) {
      const c = pal[random ? tv.variant[i] : 0], g = tv.grey[i];
      arr[i * 3] = c.r * g; arr[i * 3 + 1] = c.g * g; arr[i * 3 + 2] = c.b * g;
    }
    attr.needsUpdate = true;
    o.material.color.copy(mul);
  });
}
{
  const box = $('colorPickers');
  for (const [role, label] of COLOUR_ROLES) {
    const row = document.createElement('label'); row.className = 'cpick';
    const inp = document.createElement('input'); inp.type = 'color'; inp.dataset.role = role;
    inp.oninput = () => { colourOv[role] = rgbOf(inp.value); applyThemeTo(root); setThemeColors(); };
    row.append(label, inp); box.append(row);
  }
  $('colorReset').onclick = () => { for (const k in colourOv) delete colourOv[k]; applyThemeTo(root); setThemeColors(); syncPickers(); };
  $('treeRandom').onchange = () => paintTreeVariants(root);
  $('treeMatch').onchange = () => { paintTreeVariants(root); setThemeColors(); };
  if ($('satOn')) $('satOn').addEventListener('change', () => { paintTreeVariants(root); setThemeColors(); });
}
function applyTheme(id) {
  theme = CFG.themes[id]; if (!theme) return;
  for (const k in colourOv) delete colourOv[k];
  applyThemeTo(root); setThemeColors(); $('themeSel').value = id; syncPickers();
}
for (const [id, th] of Object.entries(CFG.themes)) {
  const o = document.createElement('option'); o.value = id; o.textContent = th.name; $('themeSel').appendChild(o);
}
$('themeSel').onchange = () => applyTheme($('themeSel').value);
window.setViewerTheme = id => { if (!CFG.themes[id]) return false; applyTheme(id); return true; };   // used by the GUI shell

