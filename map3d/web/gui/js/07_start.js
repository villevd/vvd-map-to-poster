// ---- start -----------------------------------------------------------------------------------------
(async () => {
  themes = await api('/api/themes');
  const sel = $('#theme');
  for (const t of themes) {
    const o = document.createElement('option'); o.value = t.id; o.textContent = t.name; o.title = t.description; sel.appendChild(o);
  }
  sel.value = themes.some(t => t.id === 'midnight_blue_contrast') ? 'midnight_blue_contrast' : themes[0].id;
  renderSwatches();
  restoreForm(); refreshLabels(); updateGo();
  await loadModels(true);
  const cur = await api('/api/current'); if (cur.id) poll(cur.id);
})();
setCollapsed(!!store.get('map3d-collapsed', false));
showTab(store.get('map3d-tab', 'create'));

// Ctrl+F while a model is showing searches the model (a place), not the page
addEventListener('keydown', e => {
  const v = $('#view');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && !v.hidden && v.contentWindow) {
    e.preventDefault(); v.contentWindow.focus(); v.contentWindow.postMessage({ type: 'find' }, '*');
  }
});
