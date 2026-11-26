import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const CFG = JSON.parse(document.getElementById('cfg').textContent);
// Embedded on another site (an iframe, e.g. villevd.ca/carte): ?embed=1 hides the developer panels, the search (it would
// query the public Nominatim server from every visitor) and the cake; ?panel=0 also hides the settings panel.
const QS = new URLSearchParams(location.search);
const EMBED = QS.has('embed');
if (EMBED) document.body.classList.add('embed');
if (QS.get('panel') === '0') document.body.classList.add('nopanel');
// When the GUI shows this page in a frame it moves the settings panel into its own sidebar; lookups then follow it there
let PARENT_DOC = null;
try { if (window.parent !== window) PARENT_DOC = window.parent.document; } catch (e) {}
const $ = id => document.getElementById(id) || (PARENT_DOC && PARENT_DOC.getElementById(id));
$('t').textContent = CFG.title || '3D city';
$('s').textContent = CFG.subtitle || '';
$('credits').textContent = CFG.credits || '';

async function inflate(src) {
  // src: base64 text (standalone pages, chunks) or the raw compressed bytes (baked maps, fetched from the server)
  let bytes;
  if (typeof src === 'string') {
    // Decode base64 directly (fetching a huge data: URL can stall or fail in some browsers / frames)
    const bin = atob(src); bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  } else bytes = new Uint8Array(src);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
  return await new Response(stream).arrayBuffer();
}

