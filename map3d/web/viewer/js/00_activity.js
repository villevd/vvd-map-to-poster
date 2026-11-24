// ---------------------------------------------------------------- activity: what the page is loading, bottom right
// Every network request the page makes (through fetch) and every model tile it decodes is listed here while it
// runs, with a bar for the current batch. The first part of the script, so it also catches the requests made
// while the rest of the page starts. actStart / actEnd are used by the other parts for work that is not a request.
const ACT = { items: new Map(), seq: 0, recent: [], total: 0, done: 0, failed: 0, session: 0, drawQueued: false, idleTimer: 0,
  info: null,                      // set later: returns extra lines (queue, workers, data received) for the panel
  stalls: [], longest: 0 };        // main-thread stalls (long tasks) seen in the last 10 s

function actLabel(url) {
  const u = String(url);
  let m;
  if (/arcgisonline\.com/.test(u)) {
    m = /\/tile\/(\d+)\/(\d+)\/(\d+)/.exec(u);
    return ['Satellite tile', m ? `z${m[1]} · ${m[3]}/${m[2]}` : ''];
  }
  if (/\/api\/chunk/.test(u)) {
    m = /[?&]i=(-?\d+)&j=(-?\d+)/.exec(u);
    return ['Edge chunk (local server)', m ? `cell ${m[1]}, ${m[2]}` : ''];
  }
  if (/open-meteo|weather/i.test(u)) return ['Weather', 'current conditions at the model centre'];
  if (/nominatim|geocod|search/i.test(u)) return ['Place search', ''];
  try { const x = new URL(u, location.href); return ['Request', x.host + x.pathname.slice(0, 40)]; } catch (e) { return ['Request', u.slice(0, 48)]; }
}

function actStart(label, detail = '') {
  const id = ++ACT.seq;
  ACT.items.set(id, { label, detail, t0: performance.now() });
  actTick(true);
  ACT.total++; ACT.session++;
  clearTimeout(ACT.idleTimer);
  actDraw();
  return id;
}

/** Changes what a running item says it is doing now (e.g. "unpacking in worker 2"). */
function actUpdate(id, detail) {
  const it = ACT.items.get(id);
  if (it && it.detail !== detail) { it.detail = detail; actDraw(); }
}

function actEnd(id, ok = true) {
  const it = ACT.items.get(id);
  if (!it) return;
  ACT.items.delete(id);
  ACT.done++; if (!ok) ACT.failed++;
  ACT.recent.unshift({ label: it.label, detail: it.detail, ms: performance.now() - it.t0, ok });
  if (ACT.recent.length > 7) ACT.recent.length = 7;
  if (!ACT.items.size) ACT.idleTimer = setTimeout(() => { if (!ACT.items.size) { ACT.total = ACT.done = ACT.failed = 0; actDraw(); } }, 2500);
  actDraw();
}

// Requests made with fetch are listed by what they are for; the page's own code does not need to change
(function actWatchFetch() {
  const orig = window.fetch;
  if (typeof orig !== 'function') return;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const [label, detail] = actLabel(url);
    const id = actStart(label, detail);
    return orig.call(this, input, init).then(
      r => { actEnd(id, r.ok); return r; },
      e => { actEnd(id, false); throw e; });
  };
})();

// While anything runs the panel refreshes four times a second, so the spinners' elapsed times and the stats keep moving
let actTimer = 0;
function actTick(on) {
  if (on && !actTimer) actTimer = setInterval(() => { if (ACT.items.size || ACT.stalls.length) actDraw(); else { clearInterval(actTimer); actTimer = 0; actDraw(); } }, 250);
}
// Main-thread stalls: a long task is a moment where the page could not react. Shown so a hang is visible, not mysterious.
try {
  new PerformanceObserver(list => {
    const now = performance.now();
    for (const e of list.getEntries()) { ACT.stalls.push({ t: now, ms: e.duration }); ACT.longest = Math.max(ACT.longest, e.duration); }
    actTick(true); actDraw();
  }).observe({ type: 'longtask', buffered: true });
} catch (e) { /* not supported: the panel simply has no stall line */ }

function actDraw() {
  if (ACT.drawQueued) return;
  ACT.drawQueued = true;
  requestAnimationFrame(() => {
    ACT.drawQueued = false;
    const box = document.getElementById('activity'); if (!box) return;
    const now = performance.now();
    while (ACT.stalls.length && now - ACT.stalls[0].t > 10000) ACT.stalls.shift();
    const running = [...ACT.items.values()];
    const title = document.getElementById('actTitle'), count = document.getElementById('actCount');
    const fill = document.getElementById('actFill'), list = document.getElementById('actList'), info = document.getElementById('actInfo');
    box.classList.toggle('busy', running.length > 0);
    title.textContent = running.length ? `Loading · ${running.length} running` : (ACT.total ? 'Done' : 'Idle');
    count.textContent = ACT.total ? `${ACT.done}/${ACT.total}${ACT.failed ? ` · ${ACT.failed} failed` : ''}` : `${ACT.session} this session`;
    fill.style.width = ACT.total ? `${Math.round(100 * ACT.done / ACT.total)}%` : '0%';
    // Running items of the same kind (e.g. 40 satellite pictures) are one row: "name x40", newest detail, oldest age
    const groups = new Map();
    for (const it of running) {
      const g = groups.get(it.label) || { label: it.label, n: 0, detail: '', t0: it.t0 };
      g.n++; g.detail = it.detail; g.t0 = Math.min(g.t0, it.t0); groups.set(it.label, g);
    }
    const rows = [...groups.values()].map(g => ({ label: g.n > 1 ? `${g.label} ×${g.n}` : g.label, detail: g.detail, now: true, ms: now - g.t0 }))
      .concat(ACT.recent.map(r => ({ label: r.label, detail: r.detail, now: false, ok: r.ok, ms: r.ms })));
    list.replaceChildren(...rows.slice(0, 9).map(r => {
      const li = document.createElement('li');
      li.className = r.now ? 'now' : (r.ok ? 'ok' : 'bad');
      const mark = document.createElement('i'); mark.className = r.now ? 'spin' : 'tick'; mark.textContent = r.now ? '' : (r.ok ? '✓' : '✗');
      const name = document.createElement('span'); name.textContent = r.label;
      const det = document.createElement('span'); det.className = 'det'; det.textContent = r.detail || '';
      const ms = document.createElement('span'); ms.className = 'ms';
      ms.textContent = r.now ? `${(r.ms / 1000).toFixed(1)} s` : `${Math.round(r.ms)} ms`;
      li.append(mark, name, det, ms);
      return li;
    }));
    // Stats lines: what the loader is doing overall, and whether the page is staying responsive
    const lines = [];
    try { if (ACT.info) lines.push(...ACT.info()); } catch (e) { /* the loader is not set up yet */ }
    if (ACT.stalls.length) {
      const worst = Math.max(...ACT.stalls.map(x => x.ms));
      lines.push(`Main thread: ${ACT.stalls.length} stall${ACT.stalls.length > 1 ? 's' : ''} in 10 s, worst ${Math.round(worst)} ms`);
    } else lines.push('Main thread: responsive');
    info.replaceChildren(...lines.map(t => { const d = document.createElement('div'); d.textContent = t; return d; }));
    document.getElementById('actSpin').hidden = !running.length;
  });
}

(function actBuild() {
  const box = document.createElement('div');
  box.id = 'activity';
  box.setAttribute('aria-live', 'polite');
  box.innerHTML = '<div class="act-head"><span class="act-t"><i id="actSpin" class="spin" hidden></i><span id="actTitle">Idle</span></span><span id="actCount"></span></div>' +
    '<div class="act-bar"><div id="actFill"></div></div><ul id="actList"></ul><div id="actInfo"></div>';
  document.body.appendChild(box);
})();
