// ---------------------------------------------------------------- Ctrl+F place search
const placeSearch = PlaceSearch.create({ THREE, scene, root, camera, controls, CFG, input: $('find'), list: $('findList'),
  onFlying: () => { controls.autoRotate = false; } });
addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); placeSearch.open(); }
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
  const speed = Math.max(controls.getDistance() * 0.8, T * 0.03) * (shiftDown ? 3 : 1);
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

