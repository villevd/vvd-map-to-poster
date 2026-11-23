// ---------------------------------------------------------------- billboard impostors (instanced, GPU-side facing)
// Billboard for a far tree: a trunk and a crown of overlapping, lit clumps (a leafy canopy, not a cone).
// Drawn in greys: the theme's tree colour and the per-tree tint are multiplied in by the shader.
function makeTreeTexture() {
  const W = 96, H = 160;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d');
  let seed = 7;                                             // fixed seed: every tree uses the same picture
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  // trunk (opaque, so the alpha test keeps it)
  g.fillStyle = '#9a9a9a';
  g.fillRect(W / 2 - 3, H * 0.6, 6, H * 0.4);
  // clumps of foliage, drawn bottom to top so the upper ones catch the light
  const clumps = [];
  for (let i = 0; i < 30; i++) {
    const a = rnd() * Math.PI * 2, d = Math.sqrt(rnd());
    clumps.push([W / 2 + Math.cos(a) * d * W * 0.36, H * 0.4 + Math.sin(a) * d * H * 0.24, 9 + rnd() * 11]);
  }
  clumps.sort((p, q) => q[1] - p[1]);
  for (const [x, y, r] of clumps) {
    const gr = g.createRadialGradient(x - r * 0.3, y - r * 0.4, r * 0.1, x, y, r);
    gr.addColorStop(0, '#ffffff'); gr.addColorStop(0.55, '#d4d4d4'); gr.addColorStop(1, '#7a7a7a');
    g.fillStyle = gr;
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
  }
  // the underside of the crown is in shadow
  g.globalCompositeOperation = 'source-atop';
  const shade = g.createLinearGradient(0, H * 0.1, 0, H * 0.7);
  shade.addColorStop(0, 'rgba(0,0,0,0)'); shade.addColorStop(1, 'rgba(0,0,0,0.4)');
  g.fillStyle = shade; g.fillRect(0, 0, W, H);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.NoColorSpace; return tex;
}
let treeTex = null;
function makeImpostors(arr, n) {
  treeTex = treeTex || makeTreeTexture();
  const geo = new THREE.InstancedBufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute([-0.5, 0, 0, 0.5, 0, 0, 0.5, 1, 0, -0.5, 1, 0], 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  geo.setIndex([0, 1, 2, 0, 2, 3]);
  const ib = new THREE.InstancedInterleavedBuffer(arr, 6);
  geo.setAttribute('iPos', new THREE.InterleavedBufferAttribute(ib, 3, 0));
  geo.setAttribute('iSize', new THREE.InterleavedBufferAttribute(ib, 2, 3));
  geo.setAttribute('iTint', new THREE.InterleavedBufferAttribute(ib, 1, 5));
  geo.instanceCount = n;
  const uniforms = THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uTex: { value: treeTex }, uColor: { value: new THREE.Color() } }]);
  uniforms.uBF = heightU.uBF;
  impostorUniforms.push(uniforms);
  const mat = new THREE.ShaderMaterial({
    uniforms, fog: true, side: THREE.DoubleSide,
    vertexShader: `
      attribute vec3 iPos; attribute vec2 iSize; attribute float iTint; uniform float uBF;
      varying vec2 vUv; varying float vTint;
      #include <fog_pars_vertex>
      void main() {
        vec3 right = normalize(vec3(viewMatrix[0][0], 0.0, viewMatrix[2][0]));
        vec3 wp = iPos + right * position.x * iSize.x + vec3(0.0, 1.0, 0.0) * position.y * iSize.y * uBF;
        vec4 mvPosition = viewMatrix * modelMatrix * vec4(wp, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        vUv = uv; vTint = iTint;
        #include <fog_vertex>
      }`,
    fragmentShader: `
      uniform sampler2D uTex; uniform vec3 uColor; varying vec2 vUv; varying float vTint;
      #include <fog_pars_fragment>
      void main() {
        vec4 t = texture2D(uTex, vUv);
        if (t.a < 0.5) discard;
        gl_FragColor = vec4(uColor * vTint * t.rgb * 0.85, 1.0);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
  });
  const m = new THREE.Mesh(geo, mat);
  m.frustumCulled = false; m.userData.impostor = true; m.userData.role = 'tree'; m.userData.group = 'tree';
  return m;
}

