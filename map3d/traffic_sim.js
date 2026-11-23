/*
 * Traffic simulation for the 3D viewer (plain JavaScript, no dependencies).
 *
 * Cars drive random routes over the street network built by map3d/traffic.py
 * and obey the traffic signals and stop signs found in OpenStreetMap. Signal
 * timings are not in OpenStreetMap, so each signal gets a plausible cycle.
 *
 * Units: seconds, metres, and three.js coordinates (x east, y up, z = -north).
 * Works in the browser (window.TrafficSim) and in Node (module.exports).
 */
(function (global, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else global.TrafficSim = api;
})(typeof window !== 'undefined' ? window : this, function () {
  'use strict';

  var ACCEL = 2.4;          // m/s^2, pulling away from rest
  var BRAKE = 4.5;          // m/s^2, comfortable braking profile
  var BRAKE_MAX = 9.0;      // m/s^2, hardest braking a driver will do
  var STOP_GAP = 2.5;       // m, the stop line sits this far before the junction
  var DWELL = [2.0, 3.5];   // s, pause at a stop sign
  var SPAWN_CLEAR = 35.0;   // m, cars are never spawned this close to a junction
  var YIELD_LOOK = 30.0;    // m, an entering car gives way to a car on the ring this close to its entry

  // Vehicle types. len is a multiple of the base car length, w of its width.
  var TYPES = [
    { name: 'small',  len: 0.82, w: 0.9,  accel: 2.6, vmax: 1.0 },
    { name: 'car',    len: 1.0,  w: 1.0,  accel: 2.4, vmax: 1.0 },
    { name: 'pickup', len: 1.3,  w: 1.1,  accel: 2.2, vmax: 0.97 },
    { name: 'lorry',  len: 2.3,  w: 1.15, accel: 1.1, vmax: 0.88 },
  ];
  // Share of [small, car, pickup, lorry] on side streets, on main roads, and on the heavy-traffic route (Route 117)
  var MIX_LOCAL = [0.30, 0.54, 0.14, 0.02];
  var MIX_MAJOR = [0.12, 0.46, 0.20, 0.22];
  var MIX_HEAVY = [0.04, 0.24, 0.30, 0.42];

  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Signal state of an approach group at time t: 'g' green, 'a' amber, 'r' red.
  // Group 0 is the north-south approaches, group 1 the east-west ones.
  function lightState(c, group, t) {
    var ph = ((((t + c.off) % c.cyc) + c.cyc) % c.cyc) / c.cyc;
    if (group === 0) return ph < 0.45 ? 'g' : (ph < 0.5 ? 'a' : 'r');
    return ph < 0.5 ? 'r' : (ph < 0.95 ? 'g' : 'a');
  }

  // Approach group of a car driving in direction (dx, dz): 0 for north-south, 1 for east-west.
  function groupOf(dx, dz) {
    var a = Math.atan2(dz, dx);
    a = ((a % Math.PI) + Math.PI) % Math.PI;
    return (a > Math.PI / 4 && a < 3 * Math.PI / 4) ? 0 : 1;
  }

  function createSim(cfg, opts) {
    opts = opts || {};
    var rand = rng(opts.seed || cfg.seed || 7);
    var edges = cfg.edges;              // [{a, b, v, p: [x,y,z, ...]}] undirected, p runs a -> b
    var nodes = cfg.nodes;              // flat [x,y,z, ...]
    var ctrls = cfg.ctrls || [];        // [{k: 'signal'|'stop', cyc, off}]
    var nodeCtrl = cfg.nodeCtrl;        // per node: index into ctrls, or -1
    var spacing = cfg.spacing || 9.0;   // m, front-bumper to front-bumper distance of two base cars
    var baseLen = cfg.car_len || 6.5;   // m, length of the base car
    var clearance = Math.max(spacing - baseLen, 0.2 * baseLen);   // m, gap kept between a car and the one ahead
    var nNodes = nodes.length / 3;

    // Cumulative distances and the arrival group of every directed edge
    var out = [];
    for (var n = 0; n < nNodes; n++) out.push([]);
    edges.forEach(function (e, id) {
      var p = e.p, count = p.length / 3, cum = new Array(count);
      cum[0] = 0;
      for (var i = 1; i < count; i++) {
        var dx = p[3 * i] - p[3 * i - 3], dy = p[3 * i + 1] - p[3 * i - 2], dz = p[3 * i + 2] - p[3 * i - 1];
        cum[i] = cum[i - 1] + Math.sqrt(dx * dx + dy * dy + dz * dz);
      }
      e.cum = cum;
      e.L = cum[count - 1];
      var last = count - 1;
      // dir 0 arrives at b (last segment), dir 1 arrives at a (first segment, reversed)
      e.dirGroup = [
        groupOf(p[3 * last] - p[3 * last - 3], p[3 * last + 2] - p[3 * last - 1]),
        groupOf(p[0] - p[3], p[2] - p[5]),
      ];
      if (e.d === undefined || e.d === 0) out[e.a].push([id, 0]);     // e.d: the only direction allowed, if any
      if (e.d === undefined || e.d === 1) out[e.b].push([id, 1]);
    });

    // Point at traversal distance s along a directed edge
    function pointAt(e, dir, s) {
      var p = e.p, cum = e.cum, count = cum.length;
      var sf = dir ? e.L - s : s;
      sf = Math.max(0, Math.min(e.L, sf));
      var lo = 0, hi = count - 1;
      while (hi - lo > 1) {
        var mid = (lo + hi) >> 1;
        if (cum[mid] <= sf) lo = mid; else hi = mid;
      }
      var seg = cum[hi] - cum[lo], f = seg > 0 ? (sf - cum[lo]) / seg : 0;
      var ax = p[3 * lo], ay = p[3 * lo + 1], az = p[3 * lo + 2];      // segment in point order: lo -> hi
      var bx = p[3 * hi], by = p[3 * hi + 1], bz = p[3 * hi + 2];
      var dx = bx - ax, dz = bz - az, len = Math.hypot(dx, dz) || 1;
      if (dir) { dx = -dx; dz = -dz; }                  // driving b -> a: same point, opposite heading
      return {
        x: ax + (bx - ax) * f, y: ay + (by - ay) * f, z: az + (bz - az) * f,
        dx: dx / len, dz: dz / len,
      };
    }

    // Traffic controls that cars approach, one entry per directed edge that ends at a controlled node
    var approaches = [];
    edges.forEach(function (e, id) {
      for (var dir = 0; dir < 2; dir++) {
        if (e.d !== undefined && e.d !== dir) continue;
        var ci = nodeCtrl[dir ? e.a : e.b];
        if (ci < 0) continue;
        var stopAt = pointAt(e, dir, Math.max(0, e.L - STOP_GAP));
        approaches.push({
          e: id, dir: dir, ci: ci, kind: ctrls[ci].k, group: e.dirGroup[dir],
          x: stopAt.x, y: stopAt.y, z: stopAt.z,
        });
      }
    });

    // Cars are spawned on an edge with probability proportional to its class weight times its length,
    // so highways carry more cars per kilometre than small roads
    var spawnCum = [], spawnTotal = 0;
    edges.forEach(function (e) {
      spawnTotal += (e.w === undefined ? 1 : e.w) * (e.h ? 2.2 : 1) * e.L;
      spawnCum.push(spawnTotal);
    });

    var t = 0;
    var cars = [];

    function decide(car) {
      var e = edges[car.e], rem = e.L - car.s;
      var ci = nodeCtrl[car.dir ? e.a : e.b];
      car.ci = ci;
      car.g = e.dirGroup[car.dir];
      car.stopping = false;
      car.dwell = 0;
      car.rolled = false;
      car.didStop = false;
      // Pick the next road now so the car can slow down for the turn
      car.next = chooseNext(car);
      car.enter = !!(edges[car.next[0]].o && !e.o);      // about to enter a roundabout: slow down and give way
      car.rbNode = car.dir ? e.a : e.b;
      var d0 = pointAt(e, car.dir, e.L), d1 = pointAt(edges[car.next[0]], car.next[1], 0);
      var turn = Math.abs(Math.atan2(d0.dx * d1.dz - d0.dz * d1.dx, d0.dx * d1.dx + d0.dz * d1.dz));
      car.cornerV = turn < 0.35 ? Infinity : Math.max(3.5, 11 * (1 - turn / (Math.PI * 0.75)));
      if (car.enter) car.cornerV = Math.min(car.cornerV, 5.5);
      if (ci < 0) return;
      var c = ctrls[ci];
      var mustStop = c.k === 'stop' || lightState(c, car.g, t + rem / Math.max(car.v, 3)) === 'r';
      if (!mustStop) return;
      if (rem - STOP_GAP >= car.v * car.v / (2 * BRAKE_MAX)) {
        car.stopping = true;
        car.dwell = c.k === 'stop' ? DWELL[0] + rand() * (DWELL[1] - DWELL[0]) : 0;
      } else {
        car.rolled = true;              // too close to stop in time: the car runs it
      }
    }

    function spawn() {
      var attempts = 0;
      while (attempts++ < 50) {
        var target = rand() * spawnTotal, lo = 0, hi = spawnCum.length - 1;
        while (lo < hi) { var mid = (lo + hi) >> 1; if (spawnCum[mid] < target) lo = mid + 1; else hi = mid; }
        var id = lo;
        var e = edges[id];
        if (e.L <= SPAWN_CLEAR * 2) continue;
        var s = SPAWN_CLEAR + rand() * (e.L - 2 * SPAWN_CLEAR);
        var mix = e.h ? MIX_HEAVY : (e.v >= 13 ? MIX_MAJOR : MIX_LOCAL), r = rand(), type = 0, acc = mix[0];
        while (r > acc && type < mix.length - 1) acc += mix[++type];
        var spec = TYPES[type];
        var dir0 = e.d !== undefined ? e.d : (rand() < 0.5 ? 0 : 1);
        var car = { e: id, dir: dir0, s: s, v: 0, k: (0.85 + 0.2 * rand()) * spec.vmax, leader: null,
                    type: type, len: spec.len * baseLen, accel: spec.accel };
        car.v = (edges[id].v * car.k) * 0.8;
        decide(car);
        return car;
      }
      return null;
    }

    for (var i = 0; i < (cfg.cars || 0); i++) {
      var c0 = spawn();
      if (c0) cars.push(c0);
    }

    function chooseNext(car) {
      var e = edges[car.e];
      var end = car.dir ? e.a : e.b;
      var all = out[end];
      var straight = all.filter(function (o) { return !(o[0] === car.e && o[1] !== car.dir); });
      var pool = straight.length ? straight : all;
      if (!pool.length) {                 // dead end of one-way roads: turn round rather than vanish
        var back = [car.e, car.dir ? 0 : 1];
        return back;
      }
      // Lorries keep to the main roads and Route 117, pickups lean that way, everybody uses the heavy route more
      var wts = pool.map(function (o) {
        var ed = edges[o[0]];
        if (car.type === 3) return ed.h ? 8 : (ed.v >= 11 ? 3 : 0.25);
        if (car.type === 2) return ed.h ? 3 : (ed.v >= 11 ? 1.4 : 0.8);
        return ed.h ? 1.6 : 1;
      });
      var total = wts.reduce(function (a, b) { return a + b; }, 0), pick = rand() * total;
      for (var i = 0; i < pool.length; i++) { pick -= wts[i]; if (pick <= 0) return pool[i]; }
      return pool[pool.length - 1];
    }

    function stepOnce(dt) {
      dt = Math.min(dt, 0.1);
      t += dt;

      // Leader of every car on the same directed edge
      var lanes = {};
      cars.forEach(function (c) {
        var key = c.e * 2 + c.dir;
        (lanes[key] = lanes[key] || []).push(c);
      });
      // How soon a car on a roundabout reaches each junction of the ring, following the ring round (arcs are short, so a
      // car two arcs upstream still counts). A car that has only just passed a junction still occupies it.
      var arrive = {};
      function note(node, d) { if (arrive[node] === undefined || d < arrive[node]) arrive[node] = d; }
      cars.forEach(function (c) {
        var ce = edges[c.e];
        if (!ce.o) return;
        var from = c.dir ? ce.b : ce.a;
        if (c.s < 8) note(from, 0);
        var node = c.dir ? ce.a : ce.b, d = ce.L - c.s;
        for (var hop = 0; hop < 5 && d < YIELD_LOOK; hop++) {
          note(node, d);
          var nxt = null, list = out[node];
          for (var q = 0; q < list.length; q++) if (edges[list[q][0]].o && !(list[q][0] === c.e && hop === 0)) { nxt = list[q]; break; }
          if (!nxt) break;
          var ne = edges[nxt[0]];
          d += ne.L;
          node = nxt[1] ? ne.a : ne.b;
        }
      });
      Object.keys(lanes).forEach(function (key) {
        var arr = lanes[key].sort(function (a, b) { return a.s - b.s; });
        for (var i = 0; i < arr.length; i++) arr[i].leader = arr[i + 1] || null;
      });

      cars.forEach(function (car) {
        var e = edges[car.e], rem = e.L - car.s;
        var target = e.v * car.k;

        // A light that turns red while the car is still approaching: stop if the car can, otherwise it runs it
        if (!car.stopping && !car.rolled && car.ci >= 0 && ctrls[car.ci].k === 'signal' &&
            lightState(ctrls[car.ci], car.g, t) === 'r') {
          var d = rem - STOP_GAP;
          if (d > 0.3 && d >= car.v * car.v / (2 * BRAKE_MAX)) car.stopping = true;
          else car.rolled = true;         // already too close to stop: the car goes through
        }

        if (car.stopping) {
          // ease to a stop 0.3 m short of the line, so no speed is ever cut off in one step
          target = Math.min(target, Math.sqrt(2 * BRAKE * Math.max(rem - STOP_GAP - 0.3, 0)));
        }
        if (car.cornerV < Infinity) {      // brake early for sharp turns, then pick up speed again
          target = Math.min(target, Math.sqrt(car.cornerV * car.cornerV + 2 * BRAKE * Math.max(rem, 0)));
        }
        if (car.enter && !car.stopping && arrive[car.rbNode] !== undefined && arrive[car.rbNode] < YIELD_LOOK) {
          // roundabout entry: wait at the line while a car on the ring is about to pass
          target = Math.min(target, Math.sqrt(2 * BRAKE * Math.max(rem - STOP_GAP - 0.3, 0)));
        }
        if (car.leader) {
          var gap = (car.leader.s - car.s) - (car.leader.len + clearance);
          target = Math.min(target, Math.sqrt(2 * BRAKE * Math.max(gap, 0)));
        }
        if (car.v > target) car.v = Math.max(target, car.v - BRAKE_MAX * dt);
        else car.v = Math.min(target, car.v + (car.accel || ACCEL) * dt);

        // At the stop line: wait for green, or for the sign's dwell time
        if (car.stopping && rem - STOP_GAP <= 0.4 && car.v <= 0.05) {
          car.v = 0;
          car.didStop = true;
          var c = ctrls[car.ci];
          if (c.k === 'stop') {
            car.dwell -= dt;
            if (car.dwell <= 0) car.stopping = false;
          } else if (lightState(c, car.g, t) !== 'r') {
            car.stopping = false;
          }
        }

        if (car.v < 0.05 && target < 0.2) car.v = 0;      // a stopped car stays put instead of creeping
        car.s += car.v * dt;
        if (car.s < e.L) return;

        // Reached the junction: report the crossing, then continue on a new edge
        if (car.ci >= 0 && typeof opts.onCross === 'function') {
          var cc = ctrls[car.ci];
          opts.onCross({
            ci: car.ci, kind: cc.k, red: cc.k === 'signal' && lightState(cc, car.g, t) === 'r',
            rolled: car.rolled, didStop: car.didStop, t: t,
          });
        }
        var next = car.next || chooseNext(car);
        var left = car.s - e.L;
        car.e = next[0];
        car.dir = next[1];
        car.s = left;
        decide(car);
      });
    }

    var FIXED = 1 / 60, acc = 0;
    function step(dt) {
      if (!(dt > 0)) return;
      acc += Math.min(dt, 0.25);
      var n = 0;
      while (acc >= FIXED && n < 30) { stepOnce(FIXED); acc -= FIXED; n++; }
      if (n >= 30) acc = 0;           // the tab was paused: do not try to catch up
    }

    return {
      step: step,   // advance by the real frame time, in fixed 1/60 s steps so the motion does not depend on the frame rate
      cars: cars,
      approaches: approaches,
      pose: function (car) { return pointAt(edges[car.e], car.dir, car.s); },
      // colour state of an approach: 'g' | 'a' | 'r'
      lightAt: function (ap) {
        return ap.kind === 'stop' ? 'r' : lightState(ctrls[ap.ci], ap.group, t);
      },
      time: function () { return t; },
    };
  }

  return { createSim: createSim, lightState: lightState, TYPES: TYPES };
});
