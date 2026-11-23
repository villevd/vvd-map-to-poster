// Runs the browser traffic simulation headless and reports how the vehicles moved.
// Usage: node traffic_motion_check.js <config.json> <simulated seconds>
// Prints one JSON line: teleports, roundabout direction and give-way, and the vehicle mix on heavy-route roads.
const fs = require('fs');
const path = require('path');
const TrafficSim = require(path.join(__dirname, '..', 'map3d', 'traffic_sim.js'));

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const seconds = Number(process.argv[3] || 600);
const sim = TrafficSim.createSim(cfg, { seed: 11 });
const dt = 1 / 30;

// roundabout centres: groups of ring edges that share a junction
const ringIdx = cfg.edges.map((e, i) => (e.o ? i : -1)).filter(i => i >= 0);
const parent = {};
const find = x => (parent[x] === undefined || parent[x] === x) ? (parent[x] = x) : (parent[x] = find(parent[x]));
ringIdx.forEach(i => { parent[find(cfg.edges[i].a)] = find(cfg.edges[i].b); });
const groups = {};
ringIdx.forEach(i => { const g = find(cfg.edges[i].a); (groups[g] = groups[g] || []).push(i); });
const centre = {};
Object.values(groups).forEach(members => {
  let sx = 0, sz = 0, n = 0;
  members.forEach(i => { const p = cfg.edges[i].p; for (let k = 0; k < p.length; k += 3) { sx += p[k]; sz += p[k + 2]; n++; } });
  members.forEach(i => { centre[i] = [sx / n, sz / n]; });
});

const names = TrafficSim.TYPES.map(t => t.name);
const share = { heavy: names.map(() => 0), other: names.map(() => 0) };
const initial = { heavy: names.map(() => 0), other: names.map(() => 0) };
sim.cars.forEach(c => { (cfg.edges[c.e].h ? initial.heavy : initial.other)[c.type]++; });
const out = { teleports: 0, ringCcw: 0, ringCw: 0, entries: 0, entriesTooClose: 0, ringEdges: ringIdx.length, cars: sim.cars.length };
const last = sim.cars.map(c => { const p = sim.pose(c); return [p.x, p.z]; });
const prevEdge = sim.cars.map(c => c.e);

for (let t = 0; t < seconds; t += dt) {
  sim.step(dt);
  sim.cars.forEach((c, k) => {
    const e = cfg.edges[c.e], p = sim.pose(c);
    if (Math.hypot(p.x - last[k][0], p.z - last[k][1]) > Math.max(1.0, c.v * dt * 3 + 0.3)) out.teleports++;
    last[k] = [p.x, p.z];
    (e.h ? share.heavy : share.other)[c.type]++;
    if (e.o) {
      const cen = centre[c.e];
      const rx = p.x - cen[0], rn = -(p.z - cen[1]), hx = p.dx, hn = -p.dz;      // x east, north = -z
      (rx * hn - rn * hx > 0) ? out.ringCcw++ : out.ringCw++;
    }
    if (c.e !== prevEdge[k] && e.o && !cfg.edges[prevEdge[k]].o) {              // just entered a roundabout
      out.entries++;
      let near = Infinity;
      sim.cars.forEach((o, j) => {
        if (j === k || !cfg.edges[o.e].o) return;
        const q = sim.pose(o); near = Math.min(near, Math.hypot(q.x - p.x, q.z - p.z));
      });
      if (near < 4) out.entriesTooClose++;
    }
    prevEdge[k] = c.e;
  });
}
const pct = a => { const s = a.reduce((x, y) => x + y, 0) || 1; return Object.fromEntries(a.map((v, i) => [names[i], v / s])); };
out.mixHeavy = pct(share.heavy);
out.mixOther = pct(share.other);
out.startHeavy = pct(initial.heavy);
out.startOther = pct(initial.other);
out.startHeavyCount = initial.heavy.reduce((x, y) => x + y, 0);
console.log(JSON.stringify(out));
