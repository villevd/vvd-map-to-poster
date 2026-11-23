// ---------------------------------------------------------------- distance fog, draw distance, fog of war
const fogOn = $('fogOn'), dd = $('dd'), fowOn = $('fowOn'), shadowsOn = $('shadowsOn'), quality = $('quality');
fogOn.checked = N > 1;
dd.value = N > 1 ? Math.min(14, startDist / T + N * 0.9) : 14;
if (N <= 1) { $('fowRow').style.display = 'none'; }
const plane = new THREE.PlaneGeometry(T * 0.98, T * 0.98); plane.rotateX(-Math.PI / 2);
tileList.forEach(t => {
  t.placeholder = new THREE.Mesh(plane, placeholderMat);
  t.placeholder.position.set(t.center.x, CFG.zrange[0] + T * 0.002, t.center.z); t.placeholder.visible = false; scene.add(t.placeholder);
});
function applyFog() {
  $('ddO').textContent = (+dd.value).toFixed(1) + ' tiles';
  if (REAL.on) { applyRealistic(); return; }
  if (fogOn.checked) { const far = +dd.value * T; scene.fog = new THREE.Fog(scene.background, far * 0.55, far); }
  else scene.fog = null;
  setThemeColors();
  tileList.forEach(t => Object.values(t.levels).forEach(l => l.obj && l.obj.traverse(o => { if (o.material) o.material.needsUpdate = true; })));
}
fogOn.onchange = dd.oninput = applyFog;

