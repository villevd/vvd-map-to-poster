// ---- theme: swatch cards (the hidden select stays the form value and the viewer's source) -----
function renderSwatches() {
  const box = $('#swatches'); box.innerHTML = '';
  for (const t of themes) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'sw'; b.dataset.id = t.id; b.title = t.description || t.name;
    b.setAttribute('aria-label', `Theme ${t.name}`);
    b.style.background = `linear-gradient(135deg, ${t.bg} 55%, ${t.text} 55%)`;
    b.innerHTML = '<span></span>'; b.querySelector('span').textContent = t.name;
    b.onclick = () => setTheme(t.id);
    box.appendChild(b);
  }
}
function setTheme(id) {
  const sel = $('#theme'); if (sel.value === id) return;
  sel.value = id; sel.dispatchEvent(new Event('change'));
}
function syncSwatches() {
  const cur = $('#theme').value; let desc = '';
  document.querySelectorAll('#swatches .sw').forEach(b => {
    const on = b.dataset.id === cur; b.setAttribute('aria-pressed', String(on)); if (on) desc = b.title;
  });
  $('#themeDesc').textContent = desc;
}
// Changing the theme recolours the open model right away (no rebuild, no reload).
$('#theme').addEventListener('change', () => {
  refreshLabels(); saveForm();
  // The viewer's own (hidden) theme picker now lives in this page: drive it, which also works for older exports.
  const vs = document.getElementById('themeSel');
  if (vs && [...vs.options].some(o => o.value === $('#theme').value)) { vs.value = $('#theme').value; vs.dispatchEvent(new Event('change')); }
});

// One sidebar: the model viewer's settings panel is moved out of the frame into the Style tab (same origin),
// together with the viewer's own panel styles. Its event handlers keep working; the viewer looks its controls up here.
function mountPreviewPanel(frame) {
  const mount = $('#previewMount');
  try {
    const d = frame.contentDocument, ui = d.getElementById('ui');
    if (!ui) return;
    let css = '';
    for (const sheet of d.styleSheets) for (const r of sheet.cssRules) {
      if (r.selectorText && /^#(ui|stats|realBox|perf)\b/.test(r.selectorText)) css += r.cssText + '\n';
    }
    let st = $('#previewCss'); if (!st) { st = document.createElement('style'); st.id = 'previewCss'; document.head.appendChild(st); }
    st.textContent = css;
    mount.textContent = '';
    mount.appendChild(document.adoptNode(ui));
  } catch (e) { mount.innerHTML = '<p class="none">Preview settings are shown on the model itself.</p>'; }
}
// The viewer's build facts (buildings, trees, relief...) are in its config block; read them without executing anything.
function readCfg(frame) {
  try { return JSON.parse(frame.contentDocument.getElementById('cfg').textContent); } catch (e) { return null; }
}

