// ---------------------------------------------------------------- activity: what the page is loading, bottom right
// Every network request the page makes (through fetch) and every model tile it decodes is listed here while it
// runs, with a bar for the current batch. The first part of the script, so it also catches the requests made
// while the rest of the page starts. actStart / actEnd are used by the other parts for work that is not a request.
const ACT = { items: new Map(), seq: 0, recent: [], total: 0, done: 0, failed: 0, session: 0, drawQueued: false, idleTimer: 0 };

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
  ACT.total++; ACT.session++;
  clearTimeout(ACT.idleTimer);
  actDraw();
  return id;
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

function actDraw() {
  if (ACT.drawQueued) return;
  ACT.drawQueued = true;
  requestAnimationFrame(() => {
    ACT.drawQueued = false;
    const box = document.getElementById('activity'); if (!box) return;
    const running = [...ACT.items.values()];
    const title = document.getElementById('actTitle'), count = document.getElementById('actCount');
    const fill = document.getElementById('actFill'), list = document.getElementById('actList');
    box.classList.toggle('busy', running.length > 0);
    title.textContent = running.length ? `Loading · ${running.length} running` : (ACT.total ? 'Done' : 'Idle');
    count.textContent = ACT.total ? `${ACT.done}/${ACT.total}${ACT.failed ? ` · ${ACT.failed} failed` : ''}` : `${ACT.session} this session`;
    fill.style.width = ACT.total ? `${Math.round(100 * ACT.done / ACT.total)}%` : '0%';
    const rows = running.map(it => ({ label: it.label, detail: it.detail, now: true, ms: performance.now() - it.t0 }))
      .concat(ACT.recent.map(r => ({ label: r.label, detail: r.detail, now: false, ok: r.ok, ms: r.ms })));
    list.replaceChildren(...rows.slice(0, 10).map(r => {
      const li = document.createElement('li');
      li.className = r.now ? 'now' : (r.ok ? 'ok' : 'bad');
      const name = document.createElement('span'); name.textContent = r.label;
      const det = document.createElement('span'); det.className = 'det'; det.textContent = r.detail || '';
      const ms = document.createElement('span'); ms.className = 'ms';
      ms.textContent = r.now ? '…' : `${Math.round(r.ms)} ms`;
      li.append(name, det, ms);
      return li;
    }));
  });
}

(function actBuild() {
  const box = document.createElement('div');
  box.id = 'activity';
  box.setAttribute('aria-live', 'polite');
  box.innerHTML = '<div class="act-head"><span id="actTitle">Idle</span><span id="actCount"></span></div>' +
    '<div class="act-bar"><div id="actFill"></div></div><ul id="actList"></ul>';
  document.body.appendChild(box);
})();
