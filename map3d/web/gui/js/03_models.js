// ---- models, library and insights ----------------------------------------------------------------
function timeAgo(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 30 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function openModel(file) {
  selected = file;
  store.set('map3d-open', file);            // reopened after a reload
  baked = bakedStore()[file] || null;
  if (baked) loadFormFrom(baked); else if (modelTheme[file]) loadFormFrom({ theme: modelTheme[file] });
  refreshLabels(); updateGo();
  const m = modelMeta[file] || {};
  $('#modelNow').textContent = m.city ? `${m.city} · ${m.theme}` : 'Model open';
  $('#empty').hidden = true; const v = $('#view'); v.hidden = false; v.src = '/files/' + encodeURIComponent(file);
  $('#previewMount').innerHTML = '<p class="none">Loading this model’s live controls…</p>';
  $('#insights').innerHTML = '<p class="none">Reading this model…</p>';
  v.onload = () => { v.contentWindow.focus(); mountPreviewPanel(v); lastCfg = readCfg(v); renderInsights(); };
  document.querySelectorAll('li.m').forEach(li => { const on = li.dataset.file === file; li.classList.toggle('sel', on); li.setAttribute('aria-current', on ? 'true' : 'false'); });
}
function stat(k, v, s) {
  return `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>${s ? `<div class="s">${esc(s)}</div>` : ''}</div>`;
}
const num = n => (typeof n === 'number' ? n.toLocaleString('en-US') : '—');
function renderInsights() {
  const box = $('#insights');
  if (!selected) {
    box.innerHTML = `<div class="empty-state"><b>Nothing open yet</b>Open a model from the Library tab, or build one in Create.<br>
      Its facts (area, buildings, relief, build settings) show up here.</div>`;
    return;
  }
  const m = modelMeta[selected] || {}, c = lastCfg;
  if (!c) { box.innerHTML = '<p class="none">This model’s details could not be read.</p>'; return; }
  const facts = c.facts || {}, b = facts.buildings || null, tiles = c.tiles || 1;
  const side = (c.tileSize || 0) * tiles / 1000, km2 = side * side;
  const lat = c.geo ? c.geo.lat : null, lon = c.geo ? c.geo.lon : null;
  const hasCoords = lat != null && lon != null;
  const coords = hasCoords ? `${(+lat).toFixed(4)}, ${(+lon).toFixed(4)}` : '';
  const themeName = c.themes && c.themes[c.theme] ? c.themes[c.theme].name : (c.theme || m.theme || '');
  const created = m.mtime ? new Date(m.mtime * 1000).toLocaleString() : '';
  const hm = b ? Math.round(100 * b.height_known / Math.max(1, b.count)) : null;
  const cards = [
    stat('Area', `${side.toFixed(1)} km`, `${km2.toFixed(1)} km² · ${tiles}×${tiles} tile${tiles > 1 ? 's' : ''}`),
    stat('Buildings', b ? num(b.count) : '—', b ? `${num(b.gabled)} with pitched roofs` : 'rebuild to see this'),
    stat('Trees', num(facts.trees), ''),
    stat('Bridges', num(facts.bridges), ''),
    stat('Relief', facts.relief_m != null ? `${facts.relief_m} m` : '—', facts.dem_min_m != null ? `lowest point ${facts.dem_min_m} m` : ''),
    stat('Traffic', c.traffic ? 'On' : 'Off', c.traffic ? 'stops at real lights and signs' : ''),
  ].join('');
  const heights = b ? `<div class="card" style="padding:12px 14px;margin-bottom:10px">
      <div class="rowk" style="border:0;padding:0"><span>Building heights from map data</span><b>${hm}%</b></div>
      <div class="meter"><i style="width:${hm}%"></i></div>
      <div class="dim">${num(b.height_known)} mapped · ${num(b.height_estimated)} estimated from building type</div></div>` : '';
  const hero = `<div class="hero">
      <div class="kicker">${esc(c.subtitle || 'Model')}</div>
      <div class="t">${esc(c.title || m.city || 'Untitled')}</div>
      <div class="dim">${esc(themeName)}${m.size_mb ? ' · ' + esc(m.size_mb) + ' MB' : ''}${created ? ' · built ' + esc(created) : ''}</div>
      ${hasCoords ? `<div class="acts"><button type="button" class="mini-btn" id="copyCoords">Copy coordinates ${esc(coords)}</button>
        <a class="mini-btn" target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${+lat}&amp;mlon=${+lon}#map=13/${+lat}/${+lon}">Map ↗</a></div>` : ''}
    </div>`;
  const note = c.facts ? '' : `<p class="dim" style="margin:0 0 10px">This model was built before these details were recorded. Rebuild it to see buildings, trees and relief.</p>`;
  box.innerHTML = `${hero}${note}<div class="stats">${cards}</div>${heights}
    <details class="card" open><summary><span>Build settings</span><span class="chev">›</span></summary>
      <div class="cbody">
        <div class="rowk"><span>Terrain exaggeration</span><b>×${esc(c.exag ? c.exag.terrain : '—')}</b></div>
        <div class="rowk"><span>Building height</span><b>×${esc(c.exag ? c.exag.building : '—')}</b></div>
        <div class="rowk"><span>Tiles</span><b>${tiles} × ${tiles}</b></div>
        <div class="rowk"><span>Map centre</span><b>${hasCoords ? esc(coords) : '—'}</b></div>
      </div></details>
    <details class="card"><summary><span>Viewer controls</span><span class="chev">›</span></summary>
      <div class="cbody"><ul class="tips">
        <li><kbd>Drag</kbd> orbit the model</li>
        <li><kbd>Scroll</kbd> zoom in and out</li>
        <li><kbd>Right-drag</kbd> pan across the city</li>
        <li><kbd>Ctrl</kbd> + <kbd>F</kbd> find a place</li>
      </ul></div></details>`;
  const cb = $('#copyCoords');
  if (cb) cb.onclick = () => { try { navigator.clipboard.writeText(coords).then(() => { cb.textContent = 'Copied ✓'; }, () => {}); } catch (e) {} };
}

function renderLibrary() {
  const ul = $('#models'), q = $('#libSearch').value.trim().toLowerCase();
  $('#libCount').textContent = lastModels.length ? String(lastModels.length) : '';
  ul.innerHTML = '';
  if (!lastModels.length) { ul.innerHTML = '<li class="empty">No models yet. Build one in the Create tab.</li>'; return; }
  const list = lastModels.filter(m => !q || `${m.city} ${m.theme} ${new Date(m.mtime * 1000).toLocaleDateString()}`.toLowerCase().includes(q));
  if (!list.length) { ul.innerHTML = `<li class="empty">Nothing matches “${esc(q)}”.</li>`; return; }
  for (const m of list) {
    const li = document.createElement('li');
    li.className = 'm' + (m.file === selected ? ' sel' : ''); li.dataset.file = m.file;
    li.tabIndex = 0; li.setAttribute('role', 'button'); li.setAttribute('aria-label', `Open ${m.city}`);
    const png = m.extras.includes('png') ? m.file.replace(/\.html$/, '.png') : null;
    li.innerHTML = `${png ? `<img class="thumb" loading="lazy" alt="" src="/files/${encodeURIComponent(png)}">` : '<div class="thumb ph" aria-hidden="true">◈</div>'}
      <div class="mbody"><b></b><span class="meta"></span><div class="links"></div></div>`;
    li.querySelector('b').textContent = m.city;
    li.querySelector('.meta').textContent = `${m.theme} · ${timeAgo(m.mtime)} · ${m.size_mb} MB`;
    const links = li.querySelector('.links');
    const a = (href, text, dl) => {
      const e = document.createElement('a'); e.href = href; e.textContent = text; e.target = '_blank'; e.rel = 'noopener';
      if (dl) e.download = '';
      e.onclick = ev => ev.stopPropagation(); e.onkeydown = ev => ev.stopPropagation(); links.appendChild(e);
    };
    a('/files/' + encodeURIComponent(m.file), 'Open in tab ↗');
    for (const ext of m.extras) a('/files/' + encodeURIComponent(m.file.replace(/\.html$/, '.' + ext)), ext.toUpperCase(), ext !== 'png');
    li.onclick = () => openModel(m.file);
    li.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openModel(m.file); } };
    ul.appendChild(li);
  }
}
async function loadModels(openFirst) {
  const models = await api('/api/models');
  lastModels = models;
  for (const m of models) { modelTheme[m.file] = m.theme; modelMeta[m.file] = m; }
  renderLibrary();
  if (!models.length) { renderInsights(); return; }
  const last = store.get('map3d-open', null);            // the model that was open last time, if it still exists
  const first = models.some(m => m.file === last) ? last : models[0].file;
  if (openFirst && !selected) openModel(first); else openModel(selected || first);
}
$('#libSearch').addEventListener('input', renderLibrary);

