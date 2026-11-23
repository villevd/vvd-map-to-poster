// ---------------------------------------------------------------- renderer / scene
const dpr = Math.min(window.devicePixelRatio || 1, 2);
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
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

