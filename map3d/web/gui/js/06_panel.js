// ---- collapse to a slim rail, and drag to resize the panel ---------------------------------------
function setCollapsed(on) {
  document.body.classList.toggle('collapsed', on);
  const b = $('#collapse'); b.textContent = on ? '›' : '‹';
  b.title = on ? 'Expand panel' : 'Collapse panel'; b.setAttribute('aria-expanded', String(!on));
  store.set('map3d-collapsed', on);
}
$('#collapse').onclick = () => setCollapsed(!document.body.classList.contains('collapsed'));
(() => {
  const r = $('#resizer');
  const clamp = w => Math.max(280, Math.min(600, Math.round(w)));
  let cur = clamp(store.get('map3d-side', 360));
  const apply = w => { cur = clamp(w); document.documentElement.style.setProperty('--side', cur + 'px'); };
  apply(cur);
  r.addEventListener('pointerdown', e => { r.setPointerCapture(e.pointerId); e.preventDefault(); });
  r.addEventListener('pointermove', e => { if (r.hasPointerCapture(e.pointerId)) apply(e.clientX); });
  r.addEventListener('pointerup', e => {
    if (r.hasPointerCapture(e.pointerId)) { r.releasePointerCapture(e.pointerId); store.set('map3d-side', cur); }
  });
  r.addEventListener('dblclick', () => { apply(360); store.set('map3d-side', cur); });
  r.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault(); apply(cur + (e.key === 'ArrowRight' ? 16 : -16)); store.set('map3d-side', cur);
  });
})();

