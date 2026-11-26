// ---------------------------------------------------------------- Ctrl+F place search
const placeSearch = PlaceSearch.create({ THREE, scene, root, camera, controls, CFG, input: $('find'), list: $('findList'),
  onFlying: () => { controls.autoRotate = false; } });
addEventListener('keydown', e => {
  if (!EMBED && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); placeSearch.open(); }
});
// WASD moves the view over the ground (relative to where the camera looks), Q lowers it, E raises it; Shift is faster.
const held = new Set(), fwd = new THREE.Vector3(), rgt = new THREE.Vector3(), mv = new THREE.Vector3();
const typing = e => /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
addEventListener('keydown', e => {
  if (!typing(e) && !e.ctrlKey && !e.metaKey && !e.altKey && 'wasdqe'.includes(e.key.toLowerCase()) && e.key.length === 1) {
    held.add(e.key.toLowerCase()); placeSearch.cancelFly && placeSearch.cancelFly();
  }
});
addEventListener('keyup', e => held.delete(e.key.toLowerCase()));
addEventListener('blur', () => held.clear());
const KEY_FLOOR_Y = CFG.zrange[0] - 6 * H;
// WASD speed is a fraction of the height above the ground under the camera, so it slows down near the terrain.
const KEY_SPEED_PER_HEIGHT = 0.25, KEY_MIN_SPEED = Math.max(T * 0.04, 40);   // floor: close to the terrain it must not crawl (about a fast run, scene metres/s)
const groundRay = new THREE.Raycaster(), DOWN = new THREE.Vector3(0, -1, 0);
// Only the level each nearby tile is showing is tested: hidden levels and far tiles are skipped.
function groundYBelow(p) {
  groundRay.set(p, DOWN);
  let best = null;
  for (const t of tileList) {
    const lv = t.shown >= 0 ? t.levels[t.shown] : null;
    if (!lv || !lv.obj) continue;
    if (Math.abs(t.center.x - p.x) > 0.5 * T || Math.abs(t.center.z - p.z) > 0.5 * T) continue;   // only the tile under the camera
    if (!lv.groundMeshes) {   // ground meshes only: ray-testing every building and tree mesh is what made WASD stutter
      lv.groundMeshes = [];
      lv.obj.traverse(o => { if (o.isMesh && PIVOT_ROLES.has(o.userData.role)) lv.groundMeshes.push(o); });
    }
    for (const m of lv.groundMeshes) {
      if (!m.visible) continue;
      for (const h of groundRay.intersectObject(m, false)) if (best === null || h.point.y > best) best = h.point.y;
    }
  }
  return best;
}
// The ground height is re-measured at most every 150 ms or after the camera has moved a little; it changes slowly.
let groundProbe = { y: null, t: -Infinity, x: 0, z: 0 };
function groundCached(p) {
  const now = performance.now();
  const moved = Math.hypot(p.x - groundProbe.x, p.z - groundProbe.z);
  if (now - groundProbe.t < 400 && moved < 0.05 * T) return groundProbe.y;
  groundProbe = { y: groundYBelow(p), t: now, x: p.x, z: p.z };
  return groundProbe.y;
}
function keyMove(dt) {
  if (!held.size) return;
  camera.getWorldDirection(fwd); fwd.y = 0;
  if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1);
  fwd.normalize(); rgt.set(-fwd.z, 0, fwd.x);
  mv.set(0, 0, 0);
  if (held.has('w')) mv.add(fwd);
  if (held.has('s')) mv.sub(fwd);
  if (held.has('d')) mv.add(rgt);
  if (held.has('a')) mv.sub(rgt);
  if (held.has('e')) mv.y += 1;
  if (held.has('q')) mv.y -= 1;
  if (mv.lengthSq() === 0) return;
  const g = groundCached(camera.position);
  const above = g === null ? controls.getDistance() : camera.position.y - g;   // no ground under the camera: use the orbit distance
  const speed = Math.max(above * KEY_SPEED_PER_HEIGHT, KEY_MIN_SPEED) * (shiftDown ? 3 : 1);
  mv.normalize().multiplyScalar(speed * dt);
  // Q goes well below the model's lowest ground (six half-widths down), not just to the datum at height 0
  if (controls.target.y + mv.y < KEY_FLOOR_Y && mv.y < 0) mv.y = Math.max(mv.y, KEY_FLOOR_Y - controls.target.y);
  camera.position.add(mv); controls.target.add(mv);
  controls.autoRotate = false;
}
let shiftDown = false;
addEventListener('keydown', e => { if (e.key === 'Shift') shiftDown = true; });
addEventListener('keyup', e => { if (e.key === 'Shift') shiftDown = false; });
addEventListener('message', e => { if (e.data && e.data.type === 'find') placeSearch.open(); });   // from the GUI shell

