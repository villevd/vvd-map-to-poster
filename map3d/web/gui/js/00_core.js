const $ = s => document.querySelector(s);
const f = $('#f'), status = $('#status'), logEl = $('#log');
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const TABS = ['create', 'style', 'insights', 'library'];
let themes = [], selected = null, polling = null, jobParams = null, currentTab = 'create';
let lastModels = [], modelMeta = {}, modelTheme = {}, lastCfg = null;

async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function setStatus(text, cls) { status.textContent = text; status.className = cls || ''; }

function paramsFromForm() {
  const v = n => f.elements[n];
  const outputs = ['html'];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) if (v(box).checked) outputs.push(name);
  // Heights, buildings and trees are live controls in the viewer, so the build always bakes the neutral defaults.
  return { city: v('city').value, country: v('country').value, theme: v('theme').value, distance: +v('distance').value, tiles: +v('tiles').value,
    exaggeration: 1.5, building_exaggeration: 1.5, buildings: true, trees: true,
    terrain: v('terrain').checked, overture: v('overture').checked, latitude: v('latitude').value, longitude: v('longitude').value, outputs };
}
// Settings that need a rebuild are queued: compared against what the open model was built with.
const REBUILD = ['city', 'country', 'distance', 'tiles', 'terrain', 'overture', 'latitude', 'longitude', 'outputs'];
let baked = null, building = false;
const bakedStore = () => { try { return JSON.parse(localStorage.getItem('map3d-baked') || '{}'); } catch (e) { return {}; } };
function rememberBaked(file, p) { try { const s = bakedStore(); s[file] = p; localStorage.setItem('map3d-baked', JSON.stringify(s)); } catch (e) {} }
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function dirtyKeys() {
  if (!baked) return [];
  const p = paramsFromForm();
  return REBUILD.filter(k => !same(typeof p[k] === 'string' ? p[k].trim() : p[k], typeof baked[k] === 'string' ? baked[k].trim() : baked[k]));
}
function updateGo() {
  const go = $('#go'), d = dirtyKeys(), has = !!selected;
  for (const k of REBUILD) { const el = f.elements[k]; const lab = el && el.closest && el.closest('label'); if (lab) lab.classList.toggle('dirty', d.includes(k)); }
  $('#pend').textContent = building ? '' : d.length ? `${d.length} change${d.length > 1 ? 's' : ''} queued - applied when you rebuild. Style changes apply instantly.` : '';
  if (building) { go.disabled = true; go.textContent = 'Building…'; return; }
  if (!has) { go.disabled = false; go.textContent = 'Generate 3D model'; }
  else if (!baked) { go.disabled = false; go.textContent = 'Rebuild this area'; }
  else if (d.length) { go.disabled = false; go.textContent = `Rebuild · apply ${d.length} change${d.length > 1 ? 's' : ''}`; }
  else { go.disabled = true; go.textContent = 'Up to date'; }
}
// Show an opened model's own build settings in the form, so form and view always describe the same thing.
function loadFormFrom(p) {
  const v = n => f.elements[n];
  for (const k of ['city', 'country', 'latitude', 'longitude']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['distance', 'tiles']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['terrain', 'overture']) if (p[k] !== undefined) v(k).checked = p[k];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) v(box).checked = (p.outputs || []).includes(name);
  if (p.theme && [...v('theme').options].some(o => o.value === p.theme)) v('theme').value = p.theme;
}
function saveForm() { store.set('map3d-gui', paramsFromForm()); }
function restoreForm() {
  const p = store.get('map3d-gui', null); if (!p) return;
  const v = n => f.elements[n];
  for (const k of ['city', 'country', 'theme', 'latitude', 'longitude']) if (p[k] !== undefined && v(k)) v(k).value = p[k];
  for (const k of ['distance', 'tiles']) if (p[k] !== undefined) v(k).value = p[k];
  for (const k of ['terrain', 'overture']) if (p[k] !== undefined) v(k).checked = p[k];
  for (const [box, name] of [['o_poster', 'poster'], ['o_glb', 'glb'], ['o_stl', 'stl']]) v(box).checked = (p.outputs || []).includes(name);
}

