// Runs the browser traffic simulation headless for a config written by test_traffic.py.
// Usage: node traffic_sim_check.js <config.json> <simulated seconds>
// Prints one JSON line with what the cars did at the controls.
const fs = require('fs');
const path = require('path');
const TrafficSim = require(path.join(__dirname, '..', 'map3d', 'traffic_sim.js'));

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const seconds = Number(process.argv[3] || 1800);
const stats = { crossings: 0, redRuns: 0, stopSignSkips: 0, rolled: 0, signalStops: 0, nonFinite: 0 };

const sim = TrafficSim.createSim(cfg, {
  seed: 3,
  onCross: function (ev) {
    stats.crossings++;
    if (ev.rolled) stats.rolled++;
    if (ev.kind === 'signal' && ev.red && !ev.rolled) stats.redRuns++;
    if (ev.kind === 'stop' && !ev.didStop && !ev.rolled) stats.stopSignSkips++;
    if (ev.kind === 'signal' && ev.didStop) stats.signalStops++;
  },
});

const dt = 1 / 30;
for (let t = 0; t < seconds; t += dt) {
  sim.step(dt);
  if (t % 10 < dt) {
    for (const c of sim.cars) if (!Number.isFinite(c.s) || !Number.isFinite(c.v)) stats.nonFinite++;
  }
}
stats.cars = sim.cars.length;
stats.approaches = sim.approaches.length;
console.log(JSON.stringify(stats));
