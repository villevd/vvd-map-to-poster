// ---------------------------------------------------------------- renderer / scene
const dpr = Math.min(window.devicePixelRatio || 1, 2);
// MSAA is fixed when the context is made, so it is decided from the saved quality: weak machines (Low / Potato, or two
// cores or fewer) start without it. preserveDrawingBuffer stays off: it forces a copy every frame on many GPUs, and the
// PNG button renders right before it reads the canvas, which works without it.
let savedQuality = 'auto';
try { savedQuality = (JSON.parse(localStorage.getItem('map3d-view') || '{}') || {}).quality || 'auto'; } catch (e) {}
const weakDevice = savedQuality === 'low' || savedQuality === 'potato' || (navigator.hardwareConcurrency || 8) <= 2;
const renderer = new THREE.WebGLRenderer({ antialias: !weakDevice, powerPreference: 'high-performance', preserveDrawingBuffer: false });
renderer.setPixelRatio(dpr);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
let precipRecolor = null;   // set by the weather module once it exists (theme changes happen earlier)
let manualSnow = 0;   // 0..1: snow the user switched on; settles on roofs, ground and trees (see the Weather section)
const N = CFG.tiles, T = CFG.tileSize, H = CFG.half;
const zc = (CFG.zrange[0] + CFG.zrange[1]) * 0.25;
const camera = new THREE.PerspectiveCamera(32, window.innerWidth / window.innerHeight, T * 0.01, H * 60);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, maxPolarAngle: Math.PI / 2 - 0.03,
  minDistance: Math.min(T * 0.08, 25), maxDistance: H * 14 });
controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE };   // left drag moves, right drag orbits
controls.target.set(0, zc, 0);
controls.autoRotate = false;   // off until the Auto-rotate button is pressed

// Right-drag orbits around the terrain point under the cursor (the pivot), not the model's centre. The camera moves
// by the same amount as the pivot, so the view direction does not jump. Off the terrain, the pivot stays where it was.
const pivotRay = new THREE.Raycaster();
const pivotNdc = new THREE.Vector2();
const PIVOT_ROLES = new Set(['ground', 'park', 'forest', 'water']);   // still used by search for ground height
function terrainPointAt(clientX, clientY) {
  const r = renderer.domElement.getBoundingClientRect();
  pivotNdc.set((clientX - r.left) / r.width * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
  pivotRay.setFromCamera(pivotNdc, camera);
  for (const h of pivotRay.intersectObject(root, true)) {
    const o = h.object;
    if (o.visible) return h.point;   // any visible surface: buildings, roads, trees, water, ground
  }
  return null;
}
renderer.domElement.addEventListener('pointerdown', e => {
  if (e.button !== 2) return;   // right button only; OrbitControls still handles the drag itself
  const p = terrainPointAt(e.clientX, e.clientY);
  if (!p) return;
  // The camera stays exactly where it is (so WASD position and zoom survive the click): the pivot goes on the view
  // centre ray at the depth of the terrain point under the cursor. Nothing jumps, and the orbit radius is the real
  // distance to the terrain instead of the old orbit distance.
  const fwd = camera.getWorldDirection(new THREE.Vector3());
  const depth = Math.max(p.clone().sub(camera.position).dot(fwd), controls.minDistance);
  controls.target.copy(camera.position).addScaledVector(fwd, depth);
  controls.update();
});

function dirFromCompass(bearingDeg, elevDeg) {
  const b = bearingDeg * Math.PI / 180, e = elevDeg * Math.PI / 180;
  return new THREE.Vector3(Math.sin(b) * Math.cos(e), Math.sin(e), -Math.cos(b) * Math.cos(e));
}
const startDist = H * 4.6;
function resetView() {
  controls.target.set(0, zc, 0);
  camera.position.copy(controls.target).add(dirFromCompass(CFG.camera.azimuth, CFG.camera.elevation).multiplyScalar(startDist));
  controls.update();
}
resetView();
// Sky view: the camera sits just above the ground and tilts up, so the skybox fills the top of the frame.
// OrbitControls always aims at its target, so the tilt is applied after it each frame (14_settings.js) and
// ends as soon as the user drags or zooms.
let skyDir = null;   // world direction to look along, or null for the normal orbit view
function horizonView() {
  const off = camera.position.clone().sub(controls.target), r = off.length();
  const az = Math.atan2(off.x, off.z), el = 0.1;   // about 6 degrees above the centre of the model
  camera.position.set(controls.target.x + r * Math.cos(el) * Math.sin(az), controls.target.y + r * Math.sin(el),
    controls.target.z + r * Math.cos(el) * Math.cos(az));
  controls.update();
  const fwd = controls.target.clone().sub(camera.position), flat = new THREE.Vector3(fwd.x, 0, fwd.z).normalize();
  const up = 0.22;   // radians above the horizon: the frame's top is the blue upper sky, not the pale sun glow at the horizon
  skyDir = flat.multiplyScalar(Math.cos(up)).add(new THREE.Vector3(0, Math.sin(up), 0));
}
$('horizon').onclick = horizonView;
renderer.domElement.addEventListener('pointerdown', () => { skyDir = null; });
renderer.domElement.addEventListener('wheel', () => { skyDir = null; }, { passive: true });

const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 1.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.5);
sun.castShadow = true;
sun.shadow.mapSize.set(4096, 4096);
const shadowRange = N > 1 ? T * 0.95 : H * 1.45;
Object.assign(sun.shadow.camera, { left: -shadowRange, right: shadowRange, top: shadowRange, bottom: -shadowRange, near: 1, far: H * 12 });
// Keep the biases tiny (scaled to one shadow-map texel): large values detach shadows from the building base.
sun.shadow.bias = -0.00005;
sun.shadow.normalBias = Math.min(0.15, 2 * shadowRange / 4096 * 0.5);
scene.add(sun, sun.target);
let sunAz = CFG.sun.bearing, sunEl = CFG.sun.elevation;
function placeSun() {   // the shadow window follows what you are looking at, so shadows stay sharp on big models
  sun.target.position.set(controls.target.x, zc, controls.target.z);
  sun.position.copy(sun.target.position).add(dirFromCompass(sunAz, sunEl).multiplyScalar(H * 5));
}
placeSun();
$('az').value = ((sunAz % 360) + 360) % 360;
$('el').value = sunEl;
$('az').oninput = $('el').oninput = () => { sunAz = +$('az').value; sunEl = +$('el').value; placeSun(); };

