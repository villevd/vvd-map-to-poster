// ---- tabs: keyboard-friendly (arrow keys, Home, End), remembered between visits ------------------
function showTab(name) {
  if (!TABS.includes(name)) name = 'create';
  currentTab = name;
  document.querySelectorAll('[data-tab]').forEach(b => {
    const on = b.dataset.tab === name; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.tabpanel').forEach(p => { p.hidden = p.id !== 'tab-' + name; });
  store.set('map3d-tab', name); syncMini();
}
document.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });
$('.tabs').addEventListener('keydown', e => {
  const i = TABS.indexOf(currentTab);
  let j = null;
  if (e.key === 'ArrowRight') j = (i + 1) % TABS.length;
  else if (e.key === 'ArrowLeft') j = (i + TABS.length - 1) % TABS.length;
  else if (e.key === 'Home') j = 0;
  else if (e.key === 'End') j = TABS.length - 1;
  if (j === null) return;
  e.preventDefault(); showTab(TABS[j]); document.querySelector(`[data-tab="${TABS[j]}"]`).focus();
});

