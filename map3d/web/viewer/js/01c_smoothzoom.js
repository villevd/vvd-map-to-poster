// ---------------------------------------------------------------- smooth mouse-wheel zoom
// OrbitControls applies each wheel notch at once, which feels like a jump. Here the wheel only adds to a pending
// zoom amount (in log distance), and the camera eases toward it, so zooming glides and stays proportional at any
// distance. The loop runs only while there is zoom left to apply.
controls.enableZoom = false;
const SZ = { left: 0, running: false, last: 0, speed: 0.0016, ease: 9 };
function smoothZoomStep(now) {
  const dt = Math.min((now - SZ.last) / 1000, 0.05); SZ.last = now;
  const d = SZ.left * (1 - Math.exp(-dt * SZ.ease));      // the share of the remaining zoom applied this frame
  SZ.left -= d;
  const off = camera.position.clone().sub(controls.target);
  const len = THREE.MathUtils.clamp(off.length() * Math.exp(d), controls.minDistance, controls.maxDistance);
  camera.position.copy(controls.target).add(off.setLength(len));
  if (Math.abs(SZ.left) < 0.002) { SZ.left = 0; SZ.running = false; if (typeof camSave === 'function') camSave(); return; }
  requestAnimationFrame(smoothZoomStep);
}
renderer.domElement.addEventListener('wheel', e => {
  e.preventDefault();
  const px = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 100 : e.deltaY;
  SZ.left = THREE.MathUtils.clamp(SZ.left + THREE.MathUtils.clamp(px, -240, 240) * SZ.speed, -1.2, 1.2);
  if (!SZ.running) { SZ.running = true; SZ.last = performance.now(); requestAnimationFrame(smoothZoomStep); }
}, { passive: false });
