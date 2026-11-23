import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const CFG = JSON.parse(document.getElementById('cfg').textContent);
// When the GUI shows this page in a frame it moves the settings panel into its own sidebar; lookups then follow it there
let PARENT_DOC = null;
try { if (window.parent !== window) PARENT_DOC = window.parent.document; } catch (e) {}
const $ = id => document.getElementById(id) || (PARENT_DOC && PARENT_DOC.getElementById(id));
$('t').textContent = CFG.title || '3D city';
$('s').textContent = CFG.subtitle || '';
$('credits').textContent = CFG.credits || '';

async function inflate(b64) {
  // Decode base64 directly (fetching a huge data: URL can stall or fail in some browsers / frames)
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
  return await new Response(stream).arrayBuffer();
}

