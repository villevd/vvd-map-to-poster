// ---------------------------------------------------------------- the cut region (underground cutaway)
// One rectangle in the ground plane (world x/z). While the cutaway is on, every fragment inside it is discarded, so the
// earth under the city can be looked into. Materials opt in with cutMaterial(); the region and the on/off switch are
// shared uniforms, so nothing is rebuilt when the cut moves. The cutaway itself lives in 09b_underground.js.
const CUT = { on: { value: 0 }, box: { value: new THREE.Vector4(0, 0, 0, 0) } };   // box: minX, minZ, maxX, maxZ
const UG_STATE = { on: false };   // read by the tile manager: edge chunks stay out while the block of earth is shown
let ugTileReady = null, ugUpdate = null;   // set by 09b_underground.js
function cutMaterial(mat) {
  const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
  mat.onBeforeCompile = (sh, r) => {
    if (prev) prev(sh, r);
    sh.uniforms.uCutOn = CUT.on; sh.uniforms.uCutBox = CUT.box;
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying vec3 vCutW;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvCutW = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', '#include <common>\nvarying vec3 vCutW; uniform float uCutOn; uniform vec4 uCutBox;')
      .replace('void main() {', 'void main() {\n  if (uCutOn > 0.5 && vCutW.x > uCutBox.x && vCutW.x < uCutBox.z && vCutW.z > uCutBox.y && vCutW.z < uCutBox.w) discard;');
  };
  mat.customProgramCacheKey = () => (prevKey ? prevKey.call(mat) : '') + 'cut';
  mat.needsUpdate = true;
  return mat;
}
