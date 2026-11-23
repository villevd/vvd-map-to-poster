// ---------------------------------------------------------------- tiles, lazily loaded detail levels
const layers = { building: true, tree: true, roads: true, markings: true, traffic: true };
const tiles = new Map();
for (const g of CFG.groups) {
  const key = g.chunk.join(',');
  if (!tiles.has(key)) tiles.set(key, { key, center: new THREE.Vector3(g.center[0], zc, g.center[1]), levels: {}, shown: -1, explored: false });
  tiles.get(key).levels[g.lod] = { cfg: g, obj: null, state: 'idle', tris: 0 };
}
const tileList = [...tiles.values()];
const maxLod = CFG.maxLod;
let loadedGroups = 0;

function setLayers(obj) {
  obj.traverse(o => { const g = o.userData.group; if (g && g in layers) o.visible = layers[g]; });
}

