// ---------------------------------------------------------------- per-frame tile management
let qualityScale = 1, ratio = dpr, lastFpsT = performance.now(), frames = 0, fps = 60;
function lodFor(d) {
  if (maxLod === 0) return 0;
  const s = T * qualityScale * (1 - 0.85 * PERF.aggr);   // under load, detailed models stop much sooner (the coarse level takes over)
  return d < 3.5 * s ? 0 : (d < 7 * s ? Math.min(1, maxLod) : maxLod);
}
function nearestReady(tile, wantLod) {
  const order = [];
  for (let l = 0; l <= maxLod; l++) order.push([Math.abs(l - wantLod) + (l < wantLod ? 0.1 : 0), l]);
  order.sort((a, b) => a[0] - b[0]);
  for (const [, l] of order) if (tile.levels[l] && tile.levels[l].state === 'ready') return l;
  return -1;
}
const cam = new THREE.Vector3(), _cellPos = new THREE.Vector3();
// Trees are whole models laid out one after another in the index buffer (and instances in the impostor buffer), so
// drawing only the first share of them thins the forest evenly. Trees are the bulk of the triangles in most models.
function thinTrees(o, keep) {
  const g = o.geometry;
  if (o.userData.impostor) {
    if (o.userData.n === undefined) o.userData.n = g.instanceCount;
    g.instanceCount = Math.ceil(o.userData.n * keep);
  } else if (g.index) {
    g.setDrawRange(0, Math.ceil(g.index.count * keep / 3) * 3);
  }
}
function updateTiles() {
  cam.copy(camera.position);
  const far = +dd.value * T;
  const cuts = PERF.cuts;      // dynamic culling: distances beyond which each kind of object is hidden (Infinity = off)
  const wantShadow = shadowsOn.checked && quality.value !== 'low' && !PERF.potato && PERF.shadowRes > 0;
  // The tile nearest the camera is never hidden by distance culling: if every tile were culled while the model is still
  // loading, nothing would be requested or shown, and the opening screen would wait forever.
  let nearest = null, nearestD = Infinity;
  for (const t of tileList) { const d = cam.distanceTo(t.center); if (d < nearestD) { nearestD = d; nearest = t; } }
  const cutTile = window.__ready ? cuts.tile : Infinity;   // no tile culling before the model first appears
  for (const t of tileList) {
    const d = cam.distanceTo(t.center);
    if (controls.target.distanceTo(t.center) < 1.2 * T) t.explored = true;
    const keep = t === nearest;
    // Hidden tiles (past the draw distance, or unexplored under the fog of war) cost nothing: they are not even loaded
    const hidden = (fogOn.checked && d > far * 1.15 && !keep) || (fowOn.checked && !t.explored) || (d > cutTile && !keep) || (UG_STATE.on && t.extra);
    const wantLod = lodFor(d);
    if (!hidden) {
      want(t, maxLod, 0);
      if (wantLod !== maxLod) want(t, wantLod, 1 + d / T);
    }
    const show = hidden ? -1 : nearestReady(t, wantLod);
    if (!hidden && imgOn.sat && d < 2 * T && PERF.texScale === 1) upgradeSat(t);     // zoomed in: sharper ground picture for this tile
    for (const [l, lv] of Object.entries(t.levels)) if (lv.obj) lv.obj.visible = (+l === show);
    t.shown = show;
    t.placeholder.visible = fowOn.checked && !t.explored;
    if (show >= 0) {
      const casts = wantShadow && show === 0 && d < 2.2 * T;
      // far trees, roads, markings and buildings are hidden under load (layer checkboxes still apply)
      const gHide = { tree: d > cuts.tree, markings: d > cuts.markings, roads: d > cuts.roads, building: d > cuts.building };
      const lvObj = t.levels[show].obj;
      lvObj.traverse(o => {
        const g = o.userData.group;
        if (o.userData.cellC) {   // tree cell: culled by its own distance, not the tile's
          _cellPos.copy(o.userData.cellC).add(lvObj.position);
          o.visible = layers.tree && (cuts.tree === Infinity || cam.distanceTo(_cellPos) <= cuts.tree);
        } else if (g && g in layers) o.visible = layers[g] && !gHide[g];
        if (o.userData.role === 'tree' && o.geometry) thinTrees(o, PERF.treeKeep);   // under load, draw only a share of the trees
        if (o.isMesh && !o.userData.impostor) { o.castShadow = casts && o.userData.cast; o.receiveShadow = wantShadow && o.userData.receive && show === 0; }
      });
    }
  }
  renderer.shadowMap.enabled = wantShadow; sun.castShadow = wantShadow;
  requestChunks();
}
quality.onchange = () => {
  qualityScale = { auto: 1, high: 1.8, low: 0.5, potato: 0.3 }[quality.value];
  // resolution, shadows and textures follow from PERF.gfx; give the controller a clean start at the new setting
  PERF.gfx = quality.value === 'potato' ? 1 : (quality.value === 'low' ? 0.5 : 0); PERF.applyGfx();
  // the ground picture is cut at a different resolution for High: re-apply it to the tiles that show one
  if (imgOn.sat || imgOn.bld) tileList.forEach(t => syncImagery(t));
};
shadowsOn.onchange = updateTiles;

