// ---------------------------------------------------------------- worker pool: heavy work runs off the main thread
// Unpacking a tile (inflate), its normals, UVs and tint colours, and parsing the big chunk JSON all happen in Web Workers,
// so drawing and the controls keep running while a model loads. The worker code is one function turned into a Blob URL
// (this page is a single file), and the same function runs on the main thread when workers are not available.

/** Everything a tile needs that is plain number crunching. ``d`` carries the bytes and the mesh table; ``stage`` reports progress. */
async function tileParseCore(d, stage) {
  stage('unpacking');
  let bytes;
  if (typeof d.src === 'string') {
    const bin = atob(d.src); bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  } else bytes = new Uint8Array(d.src);
  const buf = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer();
  stage('computing normals and colours');
  const outs = [], transfer = [buf];
  for (const m of d.meshes) {
    const o = {}, pos = new Float32Array(buf, m.pos, m.nv * 3);
    if (d.uvRoles[m.role]) {          // planar UVs: the tile square maps onto [0, 1] (u east, v north)
      const uv = new Float32Array(m.nv * 2);
      for (let i = 0; i < m.nv; i++) {
        uv[i * 2] = (pos[i * 3] - d.x0) / d.T;
        uv[i * 2 + 1] = (d.zN + d.T - pos[i * 3 + 2]) / d.T;
      }
      o.uv = uv; transfer.push(uv.buffer);
    }
    if (m.tint !== undefined) {
      const tb = new Uint8Array(buf, m.tint, m.nv), col = new Float32Array(m.nv * 3);
      const grey = m.variant !== undefined ? new Float32Array(m.nv) : null;
      for (let i = 0; i < m.nv; i++) {
        const v = tb[i] / 127.5; col[i * 3] = col[i * 3 + 1] = col[i * 3 + 2] = v;
        if (grey) grey[i] = v;
      }
      o.color = col; transfer.push(col.buffer);
      if (grey) { o.grey = grey; transfer.push(grey.buffer); }
    }
    if (m.nrm === undefined && !m.flat) {   // same result as BufferGeometry.computeVertexNormals for an indexed mesh
      const idx = m.idx16 ? new Uint16Array(buf, m.idx, m.nf * 3) : new Uint32Array(buf, m.idx, m.nf * 3);
      const nrm = new Float32Array(m.nv * 3);
      for (let f = 0; f < m.nf * 3; f += 3) {
        const a = idx[f] * 3, b = idx[f + 1] * 3, c = idx[f + 2] * 3;
        const abx = pos[b] - pos[a], aby = pos[b + 1] - pos[a + 1], abz = pos[b + 2] - pos[a + 2];
        const acx = pos[c] - pos[a], acy = pos[c + 1] - pos[a + 1], acz = pos[c + 2] - pos[a + 2];
        const nx = aby * acz - abz * acy, ny = abz * acx - abx * acz, nz = abx * acy - aby * acx;
        nrm[a] += nx; nrm[a + 1] += ny; nrm[a + 2] += nz;
        nrm[b] += nx; nrm[b + 1] += ny; nrm[b + 2] += nz;
        nrm[c] += nx; nrm[c + 1] += ny; nrm[c + 2] += nz;
      }
      for (let i = 0; i < m.nv * 3; i += 3) {
        const l = Math.hypot(nrm[i], nrm[i + 1], nrm[i + 2]) || 1;
        nrm[i] /= l; nrm[i + 1] /= l; nrm[i + 2] /= l;
      }
      o.normal = nrm; transfer.push(nrm.buffer);
    }
    outs.push(o);
  }
  return { res: { buf, meshes: outs }, transfer };
}
/** A request made and parsed in the worker: the main thread never sees the (large) JSON text. */
async function jsonParseCore(d, stage) {
  stage('downloading');
  const r = await fetch(d.url, d.init);
  if (!r.ok) return { res: { status: r.status, text: (await r.text()).slice(0, 200) }, transfer: [] };
  stage('parsing');
  return { res: { status: r.status, json: await r.json() }, transfer: [] };
}

const WORK = {
  size: Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 4) - 1)),
  workers: [], queue: [], seq: 0, jobs: new Map(), done: 0, ms: 0, failed: false, fallbackBusy: 0,
};
(function workStart() {
  try {
    const src = `${tileParseCore}\n${jsonParseCore}\n` +
      `self.onmessage = async e => { const d = e.data, t0 = performance.now();\n` +
      `  const stage = s => self.postMessage({ id: d.id, stage: s });\n` +
      `  try { const o = await (d.kind === 'json' ? jsonParseCore : tileParseCore)(d, stage);\n` +
      `    self.postMessage({ id: d.id, res: o.res, ms: performance.now() - t0 }, o.transfer); }\n` +
      `  catch (err) { self.postMessage({ id: d.id, err: String(err && err.message || err), name: err && err.name }); } };`;
    const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    for (let i = 0; i < WORK.size; i++) {
      const w = new Worker(url), slot = { w, job: null, n: i + 1 };
      w.onmessage = e => workDone(slot, e.data);
      w.onerror = e => { e.preventDefault(); workFail(slot, e.message || 'worker error'); };
      WORK.workers.push(slot);
    }
  } catch (e) { WORK.workers.length = 0; WORK.failed = true; console.warn('workers unavailable, running on the main thread', e); }
})();

function workFail(slot, msg) {   // a crashed worker: its job is run again on the main thread, the pool stops being used
  const job = slot.job; slot.job = null;
  WORK.failed = true; WORK.workers.forEach(s => s.w.terminate()); WORK.workers.length = 0;
  if (job) workMain(job);
  while (WORK.queue.length) workMain(WORK.queue.shift());
}
function workDone(slot, m) {
  if (m.stage !== undefined) { const j = WORK.jobs.get(m.id); if (j && j.onStage) j.onStage(m.stage, slot.n); return; }
  const job = WORK.jobs.get(m.id); if (!job) return;
  WORK.jobs.delete(m.id); slot.job = null;
  WORK.done++; WORK.ms += m.ms || 0;
  if (m.err !== undefined) { const e = m.name === 'TypeError' ? new TypeError(m.err) : new Error(m.err); job.reject(e); } else job.resolve(m.res);
  workPump();
}
function workPump() {
  for (const slot of WORK.workers) {
    if (slot.job || !WORK.queue.length) continue;
    const job = WORK.queue.shift(); slot.job = job;
    if (job.onStage) job.onStage('starting', slot.n);
    slot.w.postMessage(job.data, job.transfer);
  }
}
async function workMain(job) {   // fallback: same code, on the main thread (one at a time, yielding between jobs)
  WORK.fallbackBusy++;
  try {
    await nextTask();
    const t0 = performance.now();
    const o = await (job.data.kind === 'json' ? jsonParseCore : tileParseCore)(job.data, s => job.onStage && job.onStage(s, 0));
    WORK.done++; WORK.ms += performance.now() - t0;
    job.resolve(o.res);
  } catch (e) { job.reject(e); } finally { WORK.fallbackBusy--; }
}
/** Runs ``kind`` ('tile' | 'json') in a worker. ``transfer`` lists buffers handed over (not copied). ``onStage(text, workerNo)``. */
function workRun(kind, data, transfer = [], onStage = null) {
  return new Promise((resolve, reject) => {
    const id = ++WORK.seq; data.kind = kind; data.id = id;
    const job = { id, data, transfer, onStage, resolve, reject };
    WORK.jobs.set(id, job);
    if (WORK.failed || !WORK.workers.length) { WORK.jobs.delete(id); workMain(job); return; }
    WORK.queue.push(job); workPump();
  });
}
WORK.info = () => {
  const busy = WORK.workers.filter(s => s.job).length;
  return WORK.workers.length
    ? `Workers: ${busy}/${WORK.workers.length} busy · ${WORK.queue.length} queued · ${WORK.done} jobs done` + (WORK.done ? ` (avg ${Math.round(WORK.ms / WORK.done)} ms)` : '')
    : 'Workers: unavailable, running on the main thread';
};

// A task boundary that is not delayed like setTimeout(0) is (browsers clamp nested timeouts to 4 ms)
const _taskChan = new MessageChannel(), _taskWaiters = [];
_taskChan.port1.onmessage = () => { const w = _taskWaiters.shift(); if (w) w(); };
const nextTask = () => new Promise(r => { _taskWaiters.push(r); _taskChan.port2.postMessage(0); });
/** Time-slicing for long main-thread loops: ``const y = sliceYielder(); ... await y();`` gives the page back every ~6 ms. */
function sliceYielder(budget = 6) {
  let t0 = performance.now();
  return async () => { if (performance.now() - t0 > budget) { await nextTask(); t0 = performance.now(); } };
}
