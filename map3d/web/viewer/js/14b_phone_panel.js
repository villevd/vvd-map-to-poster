// ---------------------------------------------------------------- phone layout: the controls as one sheet with tabs
// The look is in style.css (max-width 720px). This part only switches the tab, opens and closes the sheet, and hides
// the Underground tab while the underground view is not available. Desktop ignores all of it, so the GUI's preview
// (which moves #ui into its own sidebar) keeps every pane stacked as before.
const uiPanel = $('ui');
const uiOpenBtn = $('uiOpen');
const uiTabBtns = uiPanel ? [...uiPanel.querySelectorAll('#uiTabs button')] : [];
function uiShowTab(name) {
  if (!uiPanel) return;
  uiPanel.dataset.tab = name;
  for (const b of uiTabBtns) b.classList.toggle('on', b.dataset.tab === name);
}
function uiSetOpen(on) {
  if (uiPanel) uiPanel.classList.toggle('open', on);
  if (uiOpenBtn) uiOpenBtn.setAttribute('aria-expanded', String(on));
}
for (const b of uiTabBtns) b.addEventListener('click', () => uiShowTab(b.dataset.tab));
if (uiOpenBtn) uiOpenBtn.addEventListener('click', () => uiSetOpen(true));
if ($('uiClose')) $('uiClose').addEventListener('click', () => uiSetOpen(false));

const ugPane = $('ugBox'), uiTabUnder = $('uiTabUnder');
function uiSyncUnderTab() {
  if (!uiTabUnder || !ugPane) return;
  uiTabUnder.hidden = ugPane.hidden;
  if (ugPane.hidden && uiPanel && uiPanel.dataset.tab === 'under') uiShowTab('map');
}
if (ugPane) new MutationObserver(uiSyncUnderTab).observe(ugPane, { attributes: true, attributeFilter: ['hidden'] });
uiSyncUnderTab();
