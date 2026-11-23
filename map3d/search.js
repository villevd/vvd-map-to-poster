/* Ctrl+F place search for the 3D viewer. Classic script, embedded into the exported .html (no dependencies).
 *
 *   PlaceSearch.create({ THREE, scene, root, camera, controls, CFG, input, list, onFlying })
 *     -> { open(), close(), update(nowMs) }
 *
 * Places are looked up with OpenStreetMap Nominatim, restricted to the area of the model, then the camera flies
 * there and the place is highlighted (pulsing ring, light beam and - when OSM has one - the building outline).
 * Model axes: x = east, z = -north, y = up (before the viewer's vertical exaggeration, which lives on `root`).
 */
(function (global) {
  'use strict';

  // lon/lat -> local metres of the model (equirectangular; the model is a few km wide)
  function toLocal(geo, lon, lat) {
    const kLat = 111132.0, kLon = 111320.0 * Math.cos(geo.lat * Math.PI / 180);
    return { x: (lon - geo.lon) * kLon, north: (lat - geo.lat) * kLat };
  }
  function viewbox(geo) {            // west,north,east,south
    const kLat = 111132.0, kLon = 111320.0 * Math.cos(geo.lat * Math.PI / 180), h = geo.half;
    return [geo.lon - h / kLon, geo.lat + h / kLat, geo.lon + h / kLon, geo.lat - h / kLat];
  }

  async function geocode(geo, q, signal) {
    const vb = viewbox(geo);
    const url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&polygon_geojson=1&accept-language=fr,en'
      + '&viewbox=' + vb.map(v => v.toFixed(6)).join(',') + '&bounded=1&q=' + encodeURIComponent(q);
    const res = await fetch(url, { signal, headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('search service answered ' + res.status);
    const hits = await res.json();
    return hits.map(h => ({
      name: (h.name || h.display_name.split(',')[0]),
      detail: h.display_name.split(',').slice(1, 3).join(',').trim(),
      lon: +h.lon, lat: +h.lat, geojson: h.geojson || null,
      box: h.boundingbox ? h.boundingbox.map(Number) : null,       // south, north, west, east
    }));
  }

  function create(ctx) {
    const { THREE, scene, root, camera, controls, CFG, input, list } = ctx;
    const geo = CFG.geo;
    const group = new THREE.Group(); group.visible = false; root.add(group);
    let fly = null, abort = null, results = [], sel = 0, pulse = 0;
    const glow = new THREE.Color(0xffd23f);

    function mat(opacity) {
      return new THREE.MeshBasicMaterial({ color: glow, transparent: true, opacity, depthTest: false, depthWrite: false,
        side: THREE.DoubleSide });
    }
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 64).rotateX(-Math.PI / 2), mat(0.9));
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 1, 12, 1, true), mat(0.5));
    ring.renderOrder = beam.renderOrder = 50;
    group.add(ring, beam);
    let outline = null;

    // Terrain height under a point (root-local x, z). Only the ground meshes count: buildings, roads, trees and
    // bridges are skipped, so the beacon and the footprint sit on the land and not on a roof. The terrain is put
    // into a 2-D grid once per search, so each lookup only tests a few triangles.
    let tIndex = null;
    function buildTerrainIndex() {
      root.updateMatrixWorld(true);
      const inv = new THREE.Matrix4().copy(root.matrixWorld).invert();
      const cell = 20, cells = new Map(), tris = [], v = new THREE.Vector3();
      root.traverse(o => {
        if (!o.isMesh || !o.userData || o.userData.group !== 'terrain' || !o.geometry) return;
        const pos = o.geometry.attributes.position, idx = o.geometry.index;
        if (!pos) return;
        const M = new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld);
        const P = new Float64Array(pos.count * 3);
        for (let i = 0; i < pos.count; i++) {
          v.fromBufferAttribute(pos, i).applyMatrix4(M);
          P[3 * i] = v.x; P[3 * i + 1] = v.y; P[3 * i + 2] = v.z;
        }
        const n = idx ? idx.count : pos.count;
        for (let t = 0; t + 2 < n; t += 3) {
          const ids = idx ? [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)] : [t, t + 1, t + 2];
          const T = [];
          for (const id of ids) T.push(P[3 * id], P[3 * id + 1], P[3 * id + 2]);
          const k = tris.length; tris.push(T);
          const x0 = Math.min(T[0], T[3], T[6]), x1 = Math.max(T[0], T[3], T[6]);
          const z0 = Math.min(T[2], T[5], T[8]), z1 = Math.max(T[2], T[5], T[8]);
          for (let ix = Math.floor(x0 / cell); ix <= Math.floor(x1 / cell); ix++) {
            for (let iz = Math.floor(z0 / cell); iz <= Math.floor(z1 / cell); iz++) {
              const key = ix + ',' + iz;
              if (cells.has(key)) cells.get(key).push(k); else cells.set(key, [k]);
            }
          }
        }
      });
      return { cell, cells, tris };
    }
    // height of triangle T above (x, z), or null when the point is outside it
    function heightIn(T, x, z) {
      const x0 = T[0], z0 = T[2], x1 = T[3], z1 = T[5], x2 = T[6], z2 = T[8];
      const d = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2);
      if (Math.abs(d) < 1e-12) return null;
      const a = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / d;
      const b = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / d;
      const c = 1 - a - b, e = -1e-6;
      if (a < e || b < e || c < e) return null;
      return a * T[1] + b * T[4] + c * T[7];
    }
    function groundY(x, z) {
      if (!tIndex) tIndex = buildTerrainIndex();
      const ix = Math.floor(x / tIndex.cell), iz = Math.floor(z / tIndex.cell);
      let near = null, nearD = Infinity;
      for (let dx = -1; dx <= 1; dx++) {
        for (let dz = -1; dz <= 1; dz++) {
          const list = tIndex.cells.get((ix + dx) + ',' + (iz + dz));
          if (!list) continue;
          for (const k of list) {
            const T = tIndex.tris[k], h = heightIn(T, x, z);
            if (h !== null) return h;
            const cx = (T[0] + T[3] + T[6]) / 3 - x, cz = (T[2] + T[5] + T[8]) / 3 - z, d = cx * cx + cz * cz;
            if (d < nearD) { nearD = d; near = (T[1] + T[4] + T[7]) / 3; }
          }
        }
      }
      return near !== null ? near : (CFG.zrange[0] + CFG.zrange[1]) * 0.25;
    }
    // where the highlight lies: just above the terrain at that point
    function drape(x, z) { return groundY(x, z) + 0.6; }
    // split a triangle until its edges are short, so the fill follows the slopes
    function split(a, b, c, depth, out) {
      const longest = Math.max(a.distanceTo(b), b.distanceTo(c), c.distanceTo(a));
      if (depth < 6 && longest > 3) {
        const ab = a.clone().add(b).multiplyScalar(0.5), bc = b.clone().add(c).multiplyScalar(0.5);
        const ca = c.clone().add(a).multiplyScalar(0.5);
        split(a, ab, ca, depth + 1, out); split(ab, b, bc, depth + 1, out);
        split(ca, bc, c, depth + 1, out); split(ab, bc, ca, depth + 1, out);
        return;
      }
      for (const p of [a, b, c]) out.push(p.x, drape(p.x, p.y), p.y);
    }
    function clearOutline() {
      if (!outline) return;
      group.remove(outline);
      outline.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
      outline = null;
    }
    function ringsOf(g) {
      if (!g) return [];
      if (g.type === 'Polygon') return [g.coordinates[0]];
      if (g.type === 'MultiPolygon') return g.coordinates.map(p => p[0]);
      return [];
    }

    function show(hit) {
      tIndex = null;                       // the terrain may have streamed in since the last search
      const p = toLocal(geo, hit.lon, hit.lat), x = p.x, z = -p.north, y = groundY(x, z);
      let r = 14;
      clearOutline();
      const rings = ringsOf(hit.geojson);
      if (rings.length) {
        outline = new THREE.Group();
        const edges = [], fills = [];
        for (const rg of rings) {
          const closed = rg.length > 1 && rg[0][0] === rg[rg.length - 1][0] && rg[0][1] === rg[rg.length - 1][1];
          const P = rg.slice(0, closed ? rg.length - 1 : rg.length).map(q => {
            const l = toLocal(geo, q[0], q[1]); return new THREE.Vector2(l.x, -l.north);
          });
          if (P.length < 3) continue;
          // outline: every ~2 m along the edge, each point on the terrain
          for (let i = 0; i < P.length; i++) {
            const a = P[i], b = P[(i + 1) % P.length], k = Math.max(1, Math.ceil(a.distanceTo(b) / 2));
            for (let s = 0; s < k; s++) {
              const p0 = a.clone().lerp(b, s / k), p1 = a.clone().lerp(b, (s + 1) / k);
              edges.push(p0.x, drape(p0.x, p0.y), p0.y, p1.x, drape(p1.x, p1.y), p1.y);
            }
          }
          // fill: triangulate the footprint, then drape the triangles onto the terrain
          if (THREE.ShapeUtils) {
            for (const [ia, ib, ic] of THREE.ShapeUtils.triangulateShape(P, [])) split(P[ia], P[ib], P[ic], 0, fills);
          }
        }
        if (edges.length) {
          const lines = new THREE.LineSegments(
            new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(edges, 3)),
            new THREE.LineBasicMaterial({ color: glow, depthTest: false, transparent: true }));
          lines.renderOrder = 51; outline.add(lines);
        }
        if (fills.length) {
          const area = new THREE.Mesh(
            new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(fills, 3)),
            new THREE.MeshBasicMaterial({ color: glow, transparent: true, opacity: 0.28, depthTest: false,
              depthWrite: false, side: THREE.DoubleSide }));
          area.renderOrder = 49; outline.add(area);
        }
        group.add(outline);
      }
      if (hit.box) {
        const w = toLocal(geo, hit.box[2], hit.box[0]), e = toLocal(geo, hit.box[3], hit.box[1]);
        r = Math.min(Math.max(Math.hypot(e.x - w.x, e.north - w.north) * 0.6, 10), 120);
      }
      ring.position.set(x, y + 0.8, z); ring.scale.setScalar(r);
      beam.position.set(x, y + 90, z); beam.scale.set(r * 0.12 + 0.5, 180, r * 0.12 + 0.5);
      group.visible = true; pulse = performance.now();

      // fly: look at the place from the current compass direction, close enough to read the building
      const world = new THREE.Vector3(x, y, z).applyMatrix4(root.matrixWorld);
      const dist = Math.max(r * 4.5, 160);
      const dir = camera.position.clone().sub(controls.target); dir.y = 0;
      if (dir.lengthSq() < 1e-6) dir.set(1, 0, 1);
      dir.normalize().multiplyScalar(Math.cos(0.7)).y = Math.sin(0.7);
      fly = { t0: performance.now(), dur: 1400, fromT: controls.target.clone(), fromP: camera.position.clone(),
        toT: world, toP: world.clone().add(dir.multiplyScalar(dist)) };
      if (ctx.onFlying) ctx.onFlying(world, hit);
    }

    function render() {
      list.innerHTML = '';
      results.forEach((h, i) => {
        const li = document.createElement('li');
        li.className = i === sel ? 'sel' : '';
        li.textContent = h.name + (h.detail ? ' — ' + h.detail : '');
        li.onclick = () => { sel = i; show(h); render(); };
        list.appendChild(li);
      });
    }
    function message(text) { list.innerHTML = ''; const li = document.createElement('li'); li.className = 'msg'; li.textContent = text; list.appendChild(li); }

    async function run() {
      const q = input.value.trim(); if (!q) return;
      if (abort) abort.abort(); abort = new AbortController();
      message('Searching…');
      try {
        results = await geocode(geo, q, abort.signal); sel = 0;
        if (!results.length) { message('Nothing called “' + q + '” in this area.'); return; }
        render(); show(results[0]);
      } catch (e) { if (e.name !== 'AbortError') message('Search unavailable: ' + e.message); }
    }

    input.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Enter') { if (results.length && e.shiftKey) { sel = (sel + 1) % results.length; show(results[sel]); render(); } else run(); }
      else if (e.key === 'ArrowDown' && results.length) { e.preventDefault(); sel = (sel + 1) % results.length; show(results[sel]); render(); }
      else if (e.key === 'ArrowUp' && results.length) { e.preventDefault(); sel = (sel + results.length - 1) % results.length; show(results[sel]); render(); }
      else if (e.key === 'Escape') close();
    });

    const box = input.closest('#findBox') || input.parentElement;
    function open() { box.hidden = false; input.focus(); input.select(); }
    function close() {
      box.hidden = true; input.blur(); group.visible = false; clearOutline(); fly = null; results = []; list.innerHTML = '';
    }

    function update(now) {
      if (fly) {
        const k = Math.min(1, (now - fly.t0) / fly.dur), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
        controls.target.lerpVectors(fly.fromT, fly.toT, e);
        camera.position.lerpVectors(fly.fromP, fly.toP, e);
        if (k >= 1) fly = null;
      }
      if (group.visible) {
        const s = 0.5 + 0.5 * Math.sin((now - pulse) / 260);
        ring.material.opacity = 0.45 + 0.5 * s; beam.material.opacity = 0.25 + 0.3 * s;
      }
    }
    return { open, close, update, isOpen: () => !box.hidden };
  }

  global.PlaceSearch = { create, toLocal, viewbox };
})(typeof window !== 'undefined' ? window : globalThis);
