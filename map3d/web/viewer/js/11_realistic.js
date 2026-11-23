// ---------------------------------------------------------------- realistic view
// Sun, sky, weather, season, night lights and traffic, from the model's location and the current date and time.
const WHITE = new THREE.Color(1, 1, 1);
const SNOW = new THREE.Color(0.93, 0.95, 0.97), WINTER_TREE = new THREE.Color(0.5, 0.42, 0.33), WINTER_GROUND = new THREE.Color(0.72, 0.66, 0.52);
const SKY_DAY = new THREE.Color(0.5, 0.7, 0.92), SKY_CLOUD = new THREE.Color(0.62, 0.66, 0.7);
const SKY_DUSK = new THREE.Color(0.9, 0.5, 0.32), SKY_NIGHT = new THREE.Color(0.015, 0.025, 0.06);
const SUN_LOW = new THREE.Color(1, 0.6, 0.35), SUN_HIGH = new THREE.Color(1, 0.97, 0.92);
const HEMI_DAY = new THREE.Color(0.78, 0.86, 1), HEMI_NIGHT = new THREE.Color(0.1, 0.13, 0.26);
const TRAFFIC_HOURS = [[0, 0.06], [5, 0.2], [6.5, 0.8], [8, 1], [9.5, 0.6], [12, 0.5], [15, 0.65], [17, 0.95], [18.5, 0.75], [20, 0.4], [22, 0.2], [24, 0.06]];
const MAX_CARS = 900, STARS = 1400, LAMP_SPACING = 22;
const REAL = { on: false, weather: null, override: null, roads: null, cars: null, lamps: null, stars: null,
  rain: null, snow: null, night: 0, traffic: 0, season: 1, snowCover: 0, cloud: 0, cond: {}, saved: null, t: 0 };
const winU = { uNight: { value: 0 }, uLit: { value: 0.5 }, uSnow: { value: 0 } };
const clamp01 = v => Math.min(1, Math.max(0, v));
const rnd = (a, b) => a + Math.random() * (b - a);

