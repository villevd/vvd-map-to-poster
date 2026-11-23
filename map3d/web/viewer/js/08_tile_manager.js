// ---------------------------------------------------------------- per-frame tile management
let qualityScale = 1, ratio = dpr, lastFpsT = performance.now(), frames = 0, fps = 60;
function lodFor(d) {
  if (maxLod === 0) return 0;
  const s = T * qualityScale;
  return d < 3.5 * s ? 0 : (d < 7 * s ? Math.min(1, maxLod) : maxLod);
}
function nearestReady(tile, wantLod) {
  const order = [];
  for (let l = 0; l <= maxLod; l++) order.push([Math.abs(l - wantLod) + (l < wantLod ? 0.1 : 0), l]);
  order.sort((a, b) => a[0] - b[0]);
  for (const [, l] of order) if (tile.levels[l] && tile.levels[l].state === 'ready') return l;
  return -1;
}
const cam = new THREE.Vector3();
function updateTiles() {
  cam.copy(camera.position);
  const far = +dd.value * T;
  const wantShadow = shadowsOn.checked && quality.value !== 'low';
  for (const t of tileList) {
    const d = cam.distanceTo(t.center);
    if (controls.target.distanceTo(t.center) < 1.2 * T) t.explored = true;
    // Hidden tiles (past the draw distance, or unexplored under the fog of war) cost nothing: they are not even loaded
    const hidden = (fogOn.checked && d > far * 1.15) || (fowOn.checked && !t.explored);
    const wantLod = lodFor(d);
    if (!hidden) {
      want(t, maxLod, 0);
      if (wantLod !== maxLod) want(t, wantLod, 1 + d / T);
    }
    const show = hidden ? -1 : nearestReady(t, wantLod);
    if (!hidden && imgOn.sat && d < 2 * T) upgradeSat(t);     // zoomed in: sharper ground picture for this tile
    for (const [l, lv] of Object.entries(t.levels)) if (lv.obj) lv.obj.visible = (+l === show);
    t.shown = show;
    t.placeholder.visible = fowOn.checked && !t.explored;
    if (show >= 0) {
      const casts = wantShadow && show === 0 && d < 2.2 * T;
      t.levels[show].obj.traverse(o => {
        if (o.isMesh && !o.userData.impostor) { o.castShadow = casts && o.userData.cast; o.receiveShadow = wantShadow && o.userData.receive && show === 0; }
      });
    }
  }
  renderer.shadowMap.enabled = wantShadow; sun.castShadow = wantShadow;
  requestChunks();
}
quality.onchange = () => {
  qualityScale = { auto: 1, high: 1.8, low: 0.5 }[quality.value];
  ratio = quality.value === 'low' ? Math.min(dpr, 0.75) : dpr; renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight);
};
shadowsOn.onchange = updateTiles;

