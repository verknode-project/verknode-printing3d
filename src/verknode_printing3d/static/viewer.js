// verknode-printing3d: the 3D viewers of the canvas widgets and port views (three.js). Each page gets its
// data from Python (mesh.py): positions / indices / segments as base64 arrays, the build volume, options.
// Coordinates are the printer's: millimetres, Z up, the build plate from (0, 0) to (bed x, bed y).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { TransformControls } from 'three/addons/TransformControls.js';

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const decode = (b64, Type) => {
  const bin = atob(b64 || '');
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Type(bytes.buffer);
};

function stage(el, opts) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf6f7f9);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 10000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(1, -2, 3);
  scene.add(sun);
  const [bx, by, bz] = opts.bed || [220, 220, 250];
  if (opts.plate !== false) {
    const grid = new THREE.GridHelper(Math.max(bx, by), Math.max(bx, by) / 10, 0x9aa4b0, 0xd0d6dc);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(bx / 2, by / 2, 0);
    scene.add(grid);
    const box = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(bx, by, bz)), 0xc3cad2);
    scene.add(box);
  }
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  const resize = () => {
    const w = el.clientWidth || 300;
    const h = el.clientHeight || 200;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(el);
  resize();
  const frame = obj => {
    const b = new THREE.Box3().setFromObject(obj);
    const c = b.getCenter(new THREE.Vector3());
    const r = Math.max(b.getSize(new THREE.Vector3()).length() / 2, 10);
    camera.position.set(c.x + r * 1.6, c.y - r * 2.2, c.z + r * 1.4);
    controls.target.copy(c);
    controls.update();
  };
  const loop = () => {
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  return { renderer, scene, camera, controls, frame, bed: [bx, by, bz] };
}

function meshObject(data, color) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(decode(data.positions, Float32Array), 3));
  geo.setIndex(new THREE.BufferAttribute(decode(data.indices, Uint32Array), 1));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({ color: color || '#e07b00', roughness: 0.55, metalness: 0.05,
                                               side: THREE.DoubleSide });
  return new THREE.Mesh(geo, mat);
}

function label(el, text) {
  const d = document.createElement('div');
  d.style.cssText = 'position:absolute;left:6px;bottom:4px;color:#555;font-size:11px;pointer-events:none';
  d.textContent = text;
  el.appendChild(d);
  return d;
}

export function meshView(el, data, opts) {
  const s = stage(el, opts);
  const mesh = meshObject(data, opts.color);
  s.scene.add(mesh);
  s.frame(mesh);
  const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
  label(el, `${data.name}: ${size.x.toFixed(1)} x ${size.y.toFixed(1)} x ${size.z.toFixed(1)} mm` +
            (data.truncated ? ' (part of it shown)' : ''));
}

export function meshEditor(el, data, opts) {
  const s = stage(el, opts);
  const mesh = meshObject(data, opts.color);
  // the part turns about its centre: a pivot there, the mesh inside it
  const box = new THREE.Box3().setFromObject(mesh);
  const c = box.getCenter(new THREE.Vector3());
  mesh.position.sub(c);
  const pivot = new THREE.Group();
  pivot.add(mesh);
  pivot.position.copy(c);
  const v = window.verknode && window.verknode.value;
  if (v && v.position) {
    pivot.position.fromArray(v.position);
    pivot.rotation.set(...(v.rotation || [0, 0, 0]).map(THREE.MathUtils.degToRad), 'XYZ');
    pivot.scale.fromArray(v.scale || [1, 1, 1]);
  }
  s.scene.add(pivot);
  s.frame(pivot);
  const gizmo = new TransformControls(s.camera, s.renderer.domElement);
  gizmo.attach(pivot);
  s.scene.add(gizmo.getHelper ? gizmo.getHelper() : gizmo);
  gizmo.addEventListener('dragging-changed', e => {
    s.controls.enabled = !e.value;
    if (!e.value) send();
  });
  const drop = () => {
    pivot.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(mesh);
    pivot.position.z -= b.min.z;
  };
  const send = () => {
    drop();
    const r = pivot.rotation;
    const value = { position: pivot.position.toArray().map(x => +x.toFixed(4)),
                    rotation: [r.x, r.y, r.z].map(a => +THREE.MathUtils.radToDeg(a).toFixed(3)),
                    scale: pivot.scale.toArray().map(x => +x.toFixed(5)), drop: true };
    window.verknode && window.verknode.setValue && window.verknode.setValue(value);
  };
  const bar = document.createElement('div');
  bar.style.cssText = 'position:absolute;left:6px;top:6px;display:flex;gap:4px';
  const button = (text, title, fn) => {
    const b = document.createElement('button');
    b.textContent = text;
    b.title = title;
    b.style.cssText = 'font:11px system-ui;padding:2px 7px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer';
    b.onclick = fn;
    bar.appendChild(b);
    return b;
  };
  const modes = {};
  for (const [m, t, key] of [['translate', 'Move', 'G'], ['rotate', 'Rotate', 'R'], ['scale', 'Scale', 'S']]) {
    modes[m] = button(t, `${t} (${key})`, () => setMode(m));
  }
  const setMode = m => {
    gizmo.setMode(m);
    for (const [k, b] of Object.entries(modes)) b.style.background = k === m ? '#dceeff' : '#fff';
  };
  setMode('translate');
  button('Lay flat', 'No rotation, standing on the plate', () => { pivot.rotation.set(0, 0, 0); send(); });
  button('Center', 'The middle of the build plate', () => {
    pivot.position.x = s.bed[0] / 2;
    pivot.position.y = s.bed[1] / 2;
    send();
  });
  el.appendChild(bar);
  window.addEventListener('keydown', e => {
    const k = e.key.toLowerCase();
    if (k === 'g' || k === 't') setMode('translate');
    if (k === 'r') setMode('rotate');
    if (k === 's') setMode('scale');
  });
  label(el, `${data.name}: drag the handles; the placed mesh is the second output`);
}

const FEATURE_COLORS = { 'wall-outer': 0xe0533d, 'wall-inner': 0xf0a030, fill: 0x3d9be9, skin: 0x8e5bd0,
                         skirt: 0x7a8a99, 'support': 0x4caf50 };
const PALETTE = [0xe0533d, 0xf0a030, 0x3d9be9, 0x8e5bd0, 0x4caf50, 0x00897b, 0xc2185b, 0x795548];

export function gcodeView(el, data, opts) {
  const s = stage(el, opts);
  const seg = decode(data.segments, Float32Array);
  const layers = decode(data.layers, Uint16Array);
  const feats = decode(data.features, Uint8Array);
  const kinds = decode(data.kinds, Uint8Array);
  const n = layers.length;
  const colorOf = f => {
    if (!f) return 0x9aa4b0;
    const name = data.names[f - 1];
    return FEATURE_COLORS[name] ?? PALETTE[(f - 1) % PALETTE.length];
  };
  const positions = new Float32Array(n * 6);
  const colors = new Float32Array(n * 6);
  const col = new THREE.Color();
  let shown = 0;
  const build = maxLayer => {
    shown = 0;
    for (let i = 0; i < n; i++) {
      if (layers[i] > maxLayer || (!kinds[i] && !opts.travel)) continue;
      positions.set(seg.subarray(i * 6, i * 6 + 6), shown * 6);
      col.setHex(kinds[i] ? colorOf(feats[i]) : 0xc7ced6);
      if (kinds[i] && layers[i] === maxLayer) col.offsetHSL(0, 0, 0.12);
      colors.set([col.r, col.g, col.b, col.r, col.g, col.b], shown * 6);
      shown++;
    }
    geo.setDrawRange(0, shown * 2);
    geo.attributes.position.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
  };
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true }));
  s.scene.add(lines);
  build(data.maxLayer);
  geo.computeBoundingBox();
  s.frame(lines);
  const bar = document.createElement('div');
  bar.style.cssText = 'position:absolute;left:6px;right:6px;top:6px;display:flex;gap:6px;align-items:center;font-size:11px';
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = 1;
  slider.max = Math.max(1, data.maxLayer);
  slider.value = data.maxLayer;
  slider.style.flex = '1';
  const text = document.createElement('span');
  const show = () => {
    text.textContent = `layer ${slider.value} / ${data.maxLayer}`;
    build(+slider.value);
  };
  slider.oninput = show;
  bar.append(slider, text);
  el.appendChild(bar);
  show();
  const legend = data.names.map((nm, i) => `<span style="color:#${colorOf(i + 1).toString(16).padStart(6, '0')}">■</span> ${nm}`);
  const l = label(el, '');
  l.innerHTML = legend.join(' &nbsp;') + (data.truncated ? ' (part of it shown)' : '');
}
