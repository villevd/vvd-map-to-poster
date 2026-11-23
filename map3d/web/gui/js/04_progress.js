// ---- progress: the build prints a known sequence of lines; each one moves the bar to its milestone ----
const STAGES = [
  [/Collecting data/, 4, 'Planning the area'],
  [/Step 1\/3/, 6, 'Downloading map data'],
  [/building outlines/, 10, 'Downloading buildings'],
  [/water, parks, forests/, 20, 'Downloading land cover'],
  [/railway lines/, 28, 'Downloading railways'],
  [/traffic lights and stop signs/, 33, 'Downloading traffic lights & stop signs'],
  [/street network/, 38, 'Downloading the street network'],
  [/Converting everything/, 46, 'Projecting to a local grid'],
  [/OpenStreetMap gave/, 50, 'Map data received'],
  [/Overture Maps: filling/, 54, 'Merging Overture Maps data'],
  [/Downloading elevation/, 62, 'Downloading terrain heights'],
  [/Step 3\/3/, 66, 'Building the 3D model'],
  [/Shaping the terrain/, 69, 'Shaping the terrain'],
  [/Extruding buildings/, 73, 'Extruding buildings'],
  [/Planting trees/, 79, 'Planting trees'],
  [/Casting building shadows/, 83, 'Meshing terrain and shadows'],
  [/Laying roads/, 88, 'Laying roads and bridges'],
  [/Preparing the traffic/, 92, 'Preparing traffic'],
  [/Adding the base plinth/, 95, 'Finishing the model'],
  [/built before|Reusing it|loaded from the cache/, 60, 'Reusing cached data'],
  [/Writing the interactive/, 97, 'Writing the 3D page'],
  [/generation complete/, 100, 'Done'],
];
let progPct = 0, progCap = 0;
function setProgress(pct, label) {
  progPct = Math.max(progPct, pct);
  const txt = Math.round(progPct) + '%';
  $('#fill').style.width = progPct + '%'; $('#progPct').textContent = txt;
  $('#miniFill').style.width = progPct + '%'; $('#miniPct').textContent = txt;
  if (label) { $('#progStage').textContent = label; $('#miniStage').textContent = label; }
}
function progressFromLines(lines) {
  for (const line of lines) {
    for (const [re, pct, label] of STAGES) if (re.test(line) && pct >= progPct) { setProgress(pct, label); progCap = pct; }
    const m = /tile (\d+)\/(\d+) built/.exec(line);   // big areas build tile by tile between 66% and 95%
    if (m) setProgress(66 + 29 * (+m[1] / +m[2]), `Building tile ${m[1]} of ${m[2]}`);
  }
}
setInterval(() => {   // slow downloads: creep towards the next milestone so the bar never looks frozen
  if (!$('#prog').hidden && !$('#prog').classList.contains('idle') && progPct < 96) setProgress(progPct + 0.15);
}, 500);
function syncMini() { $('#miniProg').hidden = !(building && currentTab !== 'create'); }
function busy(on) {
  const pr = $('#prog');
  if (on) { progPct = 0; pr.hidden = false; pr.className = ''; setProgress(1, 'Starting…'); }
  building = on; $('#stop').hidden = !on; updateGo(); syncMini();
}
async function poll(id) {
  let since = 0; busy(true); logEl.textContent = ''; $('#logCard').hidden = true;
  clearInterval(polling);
  polling = setInterval(async () => {
    let j; try { j = await api(`/api/job?id=${id}&since=${since}`); } catch (e) { return; }
    if (j.lines.length) {
      logEl.textContent += (logEl.textContent ? '\n' : '') + j.lines.join('\n'); logEl.scrollTop = logEl.scrollHeight;
      $('#logCard').hidden = false; since = j.next; progressFromLines(j.lines);
    }
    if (j.state === 'running') { setStatus(`Working… ${Math.round(j.elapsed)} s`); return; }
    clearInterval(polling); busy(false);
    $('#prog').classList.add('idle');
    if (j.state === 'done') {
      setProgress(100, 'Done');
      setStatus('Done ✓', 'ok'); const html = j.files.find(n => n.endsWith('.html'));
      if (html) rememberBaked(html, jobParams || paramsFromForm());
      selected = null; await loadModels(false); if (html) openModel(html);
    } else if (j.state === 'cancelled') { setStatus('Cancelled'); $('#progStage').textContent = 'Cancelled'; $('#miniStage').textContent = 'Cancelled'; }
    else {
      if (!logEl.textContent) logEl.textContent = 'The build stopped without printing anything.';
      $('#logCard').hidden = false; $('#logCard').open = true;
      setStatus('Failed - see the log below.', 'err'); $('#progStage').textContent = 'Failed'; $('#miniStage').textContent = 'Failed';
    }
  }, 700);
}
f.addEventListener('submit', async e => {
  e.preventDefault(); setStatus('Starting…');
  try { jobParams = paramsFromForm(); const { id } = await api('/api/generate', jobParams); poll(id); }
  catch (err) { setStatus(err.message, 'err'); }
});
$('#stop').onclick = () => api('/api/cancel', {}).catch(() => {});

