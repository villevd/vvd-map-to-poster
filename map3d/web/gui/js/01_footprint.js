// ---- footprint: the area as a live picture of tiles, with a plain-language size ----------------
function refreshLabels() {
  const d = +f.elements.distance.value, n = +f.elements.tiles.value;
  $('#distO').textContent = d + ' m';
  $('#tilesO').textContent = n + ' × ' + n;
  const side = 2 * d * n / 1000, km2 = side * side;
  $('#footBig').textContent = `${side.toFixed(1)} × ${side.toFixed(1)} km`;
  const load = km2 <= 9 ? ['Small', 'Small area, light on memory and data'] : km2 <= 36 ? ['Medium', 'A moderate amount of map data'] :
    km2 <= 100 ? ['Large', 'Large area: more data to download and more memory'] : ['Very large', 'Built tile by tile; expect a long build'];
  $('#footSub').innerHTML = `${n * n} tile${n * n > 1 ? 's' : ''} · ${km2.toFixed(1)} km² <span class="pill" title="${esc(load[1])}">${load[0]}</span>`;
  const g = $('#grid');
  if (g.dataset.n !== String(n)) { g.dataset.n = n; g.style.gridTemplateColumns = `repeat(${n}, 1fr)`; g.innerHTML = '<i></i>'.repeat(n * n); }
  $('#hint').textContent = (n > 1 ? `Each tile is ${(2 * d / 1000).toFixed(1)} km across. ` : '') +
    (km2 > 36 ? 'Large areas take much longer and need more memory.' : '');
  syncSwatches();
}
f.addEventListener('input', () => { refreshLabels(); saveForm(); updateGo(); });

