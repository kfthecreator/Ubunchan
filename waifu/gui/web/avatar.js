// VRM avatar: loading, idle animation, emotions and audio-driven lip-sync.
// Python drives it through window.waifu.*; events go back via console.log("@@waifu {...}").
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

const params = new URLSearchParams(location.search);
if (params.get("theme") === "light") document.documentElement.dataset.theme = "light";

function emit(type, data = {}) {
  console.log("@@waifu " + JSON.stringify({ type, ...data }));
}

// ---------------------------------------------------------------- scene
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(26, window.innerWidth / window.innerHeight, 0.1, 20);
camera.position.set(0, 1.3, 1.6);

const key = new THREE.DirectionalLight(0xffffff, Math.PI * 0.9);
key.position.set(0.6, 1.5, 1.8);
scene.add(key);
scene.add(new THREE.AmbientLight(0xffffff, 0.6));

const controls = new OrbitControls(camera, renderer.domElement);
controls.enablePan = false;
controls.enableDamping = true;
controls.minDistance = 0.5;
controls.maxDistance = 3.5;
controls.minPolarAngle = Math.PI * 0.3;
controls.maxPolarAngle = Math.PI * 0.62;
controls.minAzimuthAngle = -Math.PI * 0.45;
controls.maxAzimuthAngle = Math.PI * 0.45;

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Eyes follow the mouse a little; otherwise they look at the camera.
const lookTarget = new THREE.Object3D();
scene.add(lookTarget);
const mouse = new THREE.Vector2(0, 0);
window.addEventListener("pointermove", (e) => {
  mouse.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
});
document.addEventListener("pointerleave", () => mouse.set(0, 0));

// ---------------------------------------------------------------- model
let vrm = null;
let rest = {};         // rest rotations of the bones we animate
let framingY = 1.3;    // height the camera looks at

const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));
loader.load(
  "/model.vrm",
  (gltf) => {
    vrm = gltf.userData.vrm;
    if (!vrm) { showError("This file is not a VRM model."); return; }
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => { o.frustumCulled = false; });
    scene.add(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.target = lookTarget;
    resolveExpressions();
    setRestPose();
    frameUpperBody();
    document.getElementById("status").remove();
    emit("loaded", { name: vrm.meta?.name || vrm.meta?.title || "model",
                     expressions: Object.keys(vrm.expressionManager?.expressionMap || {}) });
  },
  (p) => {
    if (p.total) document.getElementById("progress").style.width = `${(100 * p.loaded) / p.total}%`;
  },
  (err) => showError("Could not load the model: " + (err?.message || err)),
);

function showError(msg) {
  const s = document.getElementById("status");
  if (s) s.textContent = msg;
  emit("error", { message: msg });
}

let exprName = {};  // our preset name -> the model's actual expression name
function resolveExpressions() {
  const names = Object.keys(vrm.expressionManager?.expressionMap || {});
  for (const k of ["happy", "angry", "sad", "relaxed", "surprised", "blink", "blinkLeft",
                   "blinkRight", "aa", "ih", "ou", "ee", "oh"]) {
    exprName[k] = names.find((n) => n === k) || names.find((n) => n.toLowerCase() === k.toLowerCase()) || null;
  }
}
function setExpr(k, v) {
  const n = exprName[k];
  if (n) vrm.expressionManager.setValue(n, v);
}

const bone = (name) => vrm?.humanoid?.getNormalizedBoneNode(name) || null;

function setRestPose() {
  // Bring the arms down from the T-pose into a relaxed stance.
  const pose = {
    leftUpperArm: [0, 0, 1.2], rightUpperArm: [0, 0, -1.2],
    leftLowerArm: [0, 0.25, 0.12], rightLowerArm: [0, -0.25, -0.12],
    leftHand: [0, 0, 0.1], rightHand: [0, 0, -0.1],
  };
  // VRM 1.0 models face the opposite way from VRM 0.x, so the arm rotations mirror.
  const s = vrm.meta?.metaVersion === "0" ? 1 : -1;
  for (const [name, [x, y, z]] of Object.entries(pose)) {
    const b = bone(name);
    if (b) b.rotation.set(x, y * s, z * s);
  }
  for (const name of ["hips", "spine", "chest", "upperChest", "neck", "head",
                      "leftUpperArm", "rightUpperArm", "leftShoulder", "rightShoulder"]) {
    const b = bone(name);
    if (b) rest[name] = b.rotation.clone();
  }
}

function frameUpperBody() {
  vrm.scene.updateMatrixWorld(true);
  const head = new THREE.Vector3();
  (bone("head") || vrm.scene).getWorldPosition(head);
  framingY = head.y - 0.14;
  camera.position.set(0, head.y + 0.02, 1.75);
  controls.target.set(0, framingY, 0);
  controls.update();
}

// ---------------------------------------------------------------- emotions
// Our emotion names -> VRM preset expression weights (+ a head pose offset).
const EMOTIONS = {
  neutral:     { w: {},                                     head: [0, 0, 0] },
  happy:       { w: { happy: 0.75 },                        head: [0, 0, 0.04] },
  excited:     { w: { happy: 1.0, surprised: 0.25 },        head: [-0.05, 0, 0.02] },
  thinking:    { w: { relaxed: 0.25 },                      head: [-0.06, 0.1, 0.13], look: [0.35, 0.3] },
  surprised:   { w: { surprised: 0.9 },                     head: [-0.08, 0, 0] },
  mischievous: { w: { happy: 0.45, relaxed: 0.35, blinkLeft: 0.6 }, head: [0.02, -0.08, -0.12] },
  sad:         { w: { sad: 0.85 },                          head: [0.12, 0, 0.03] },
  worried:     { w: { sad: 0.5, surprised: 0.2 },           head: [0.06, 0, -0.05] },
  embarrassed: { w: { happy: 0.35, relaxed: 0.45 },         head: [0.1, 0.12, 0.08], look: [-0.4, -0.2] },
  pouty:       { w: { angry: 0.65 },                        head: [0.03, -0.15, -0.06] },
  listening:   { w: { relaxed: 0.15 },                      head: [0, 0, 0.1] },
};
const MOOD_KEYS = ["happy", "angry", "sad", "relaxed", "surprised", "blinkLeft"];
const moodTarget = Object.fromEntries(MOOD_KEYS.map((k) => [k, 0]));
const moodNow = { ...moodTarget };
let headTarget = [0, 0, 0], headNow = [0, 0, 0];
let lookOffset = [0, 0];
let moodTimer = null;
let stateEmotion = null;   // "thinking" / "listening" while the app is busy

function setEmotion(name, holdSeconds = 10) {
  const e = EMOTIONS[name] || EMOTIONS.neutral;
  for (const k of MOOD_KEYS) moodTarget[k] = e.w[k] || 0;
  headTarget = e.head;
  lookOffset = e.look || [0, 0];
  clearTimeout(moodTimer);
  if (name !== "neutral" && holdSeconds > 0) {
    moodTimer = setTimeout(() => setEmotion(stateEmotion || "neutral", 0), holdSeconds * 1000);
  }
}

function setState(state) {
  // idle | listening | thinking | working
  stateEmotion = state === "listening" ? "listening"
               : state === "thinking" || state === "working" ? "thinking" : null;
  if (stateEmotion) setEmotion(stateEmotion, 0);
  else if (!speaking) setEmotion("neutral", 0);
}

// ---------------------------------------------------------------- speech & lip-sync
let audioCtx = null, analyser = null, timeBuf = null, freqBuf = null;
let currentSource = null;
let speaking = false;
let silentUntil = 0;
const queue = [];
const mouth = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
const bubble = document.getElementById("bubble");
let bubbleTimer = null;

function ensureAudio() {
  if (!audioCtx) {
    audioCtx = new AudioContext();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.5;
    analyser.connect(audioCtx.destination);
    timeBuf = new Float32Array(analyser.fftSize);
    freqBuf = new Uint8Array(analyser.frequencyBinCount);
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
}

function showBubble(text) {
  clearTimeout(bubbleTimer);
  bubble.textContent = text;
  bubble.classList.toggle("show", !!text);
}
function hideBubbleSoon(seconds = 4) {
  clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => bubble.classList.remove("show"), seconds * 1000);
}

// item: { url?: string, text: string, emotion?: string, pitch?: number }
function speak(item) {
  queue.push(item);
  if (!speaking) playNext();
}

async function playNext() {
  const item = queue.shift();
  if (!item) {
    speaking = false;
    hideBubbleSoon();
    emit("speechEnd");
    if (stateEmotion) setEmotion(stateEmotion, 0);
    return;
  }
  speaking = true;
  if (item.emotion) setEmotion(item.emotion, 0);
  showBubble(item.text || "");
  emit("speechStart", { text: item.text || "" });

  const afterClip = () => {
    if (item.emotion) setEmotion(item.emotion, 6);
    playNext();
  };

  if (!item.url) { // no TTS voice: animate the mouth from the text length
    const secs = Math.max(1.2, (item.text || "").length * 0.06);
    silentUntil = performance.now() + secs * 1000;
    setTimeout(afterClip, secs * 1000);
    return;
  }
  try {
    ensureAudio();
    const data = await (await fetch(item.url)).arrayBuffer();
    const buffer = await audioCtx.decodeAudioData(data);
    const src = audioCtx.createBufferSource();
    src.buffer = buffer;
    src.playbackRate.value = item.pitch || 1.0;
    src.connect(analyser);
    src.onended = () => { if (currentSource === src) currentSource = null; afterClip(); };
    currentSource = src;
    src.start();
  } catch (err) {
    emit("error", { message: "audio playback failed: " + err });
    afterClip();
  }
}

function stopSpeaking() {
  queue.length = 0;
  if (currentSource) { try { currentSource.stop(); } catch (_) {} }
  silentUntil = 0;
}

function updateMouth(dt, t) {
  let level = 0, shape = { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 };
  if (currentSource && analyser) {
    analyser.getFloatTimeDomainData(timeBuf);
    let sum = 0;
    for (let i = 0; i < timeBuf.length; i++) sum += timeBuf[i] * timeBuf[i];
    const rms = Math.sqrt(sum / timeBuf.length);
    level = Math.min(1, Math.max(0, (rms - 0.012) * 9));

    // Rough vowel guess from where the energy sits in the spectrum.
    analyser.getByteFrequencyData(freqBuf);
    const hz = audioCtx.sampleRate / analyser.fftSize;
    const band = (lo, hi) => {
      let s = 0; const a = Math.floor(lo / hz), b = Math.floor(hi / hz);
      for (let i = a; i < b; i++) s += freqBuf[i];
      return s / Math.max(1, b - a);
    };
    const low = band(150, 700), mid = band(700, 1800), high = band(1800, 4000);
    const total = low + mid + high + 1e-3;
    shape = { aa: mid / total + 0.2, oh: low / total * 0.9, ou: low / total * 0.4,
              ee: high / total * 0.8, ih: high / total * 0.5 };
  } else if (performance.now() < silentUntil) {
    level = 0.35 + 0.35 * Math.abs(Math.sin(t * 13)) * (0.6 + 0.4 * Math.sin(t * 5.3));
  }
  const norm = Object.values(shape).reduce((a, b) => a + b, 0) || 1;
  for (const k of Object.keys(mouth)) {
    const target = level * (shape[k] / norm) * (k === "aa" ? 1.3 : 1.0);
    const speed = target > mouth[k] ? 30 : 14;  // open fast, close a bit slower
    mouth[k] += (Math.min(1, target) - mouth[k]) * Math.min(1, dt * speed);
  }
}

// ---------------------------------------------------------------- animation loop
let lastFrame = performance.now(), elapsed = 0;
let nextBlink = 2, blinkT = -1;

function animate() {
  requestAnimationFrame(animate);
  const now = performance.now();
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  const t = (elapsed += dt);
  controls.update();

  if (vrm) {
    // Idle: breathing, gentle sway, head motion.
    const breathe = Math.sin(t * 1.7);
    const set = (name, dx = 0, dy = 0, dz = 0) => {
      const b = bone(name), r = rest[name];
      if (b && r) b.rotation.set(r.x + dx, r.y + dy, r.z + dz);
    };
    for (let i = 0; i < 3; i++) headNow[i] += (headTarget[i] - headNow[i]) * Math.min(1, dt * 4);
    const talk = speaking ? mouth.aa * 0.04 : 0;
    set("hips", 0, Math.sin(t * 0.45) * 0.03, Math.sin(t * 0.6) * 0.012);
    set("spine", breathe * 0.012, 0, -Math.sin(t * 0.6) * 0.01);
    set("chest", breathe * 0.018, 0, 0);
    set("upperChest", breathe * 0.012, 0, 0);
    set("neck", headNow[0] * 0.4, Math.sin(t * 0.5) * 0.03 + headNow[1] * 0.4, headNow[2] * 0.4);
    set("head", headNow[0] * 0.6 + Math.sin(t * 0.8) * 0.015 - talk,
        Math.sin(t * 0.37) * 0.05 + headNow[1] * 0.6,
        Math.sin(t * 0.53) * 0.025 + headNow[2] * 0.6);
    set("leftShoulder", 0, 0, -breathe * 0.012);
    set("rightShoulder", 0, 0, breathe * 0.012);
    set("leftUpperArm", 0, 0, Math.sin(t * 0.9) * 0.02);
    set("rightUpperArm", 0, 0, -Math.sin(t * 0.9 + 1) * 0.02);

    // Eyes: follow mouse around the camera, plus emotion offset.
    const eye = new THREE.Vector3(mouse.x * 0.35 + lookOffset[0], mouse.y * 0.25 + lookOffset[1], 0);
    eye.applyQuaternion(camera.quaternion).add(camera.position);
    lookTarget.position.lerp(eye, Math.min(1, dt * 6));

    // Expressions.
    const em = vrm.expressionManager;
    if (em) {
      for (const k of MOOD_KEYS) {
        moodNow[k] += (moodTarget[k] - moodNow[k]) * Math.min(1, dt * 5);
        setExpr(k, moodNow[k]);
      }
      updateMouth(dt, t);
      // Happy/relaxed already narrow the eyes and mouth; tone the lip-sync down.
      const damp = 1 - 0.5 * Math.max(moodNow.happy, moodNow.relaxed);
      for (const [k, v] of Object.entries(mouth)) setExpr(k, v * damp);

      nextBlink -= dt;
      if (nextBlink <= 0 && blinkT < 0) { blinkT = 0; nextBlink = 2 + Math.random() * 4; }
      let blink = 0;
      if (blinkT >= 0) {
        blinkT += dt;
        blink = Math.sin(Math.min(1, blinkT / 0.16) * Math.PI);
        if (blinkT >= 0.16) blinkT = -1;
      }
      setExpr("blink", blink * (1 - Math.max(moodNow.happy, moodNow.relaxed) * 0.9));
    }
    vrm.update(dt);
  }
  renderer.render(scene, camera);
}
animate();

window.waifu = { speak, stopSpeaking, setEmotion, setState, emotions: Object.keys(EMOTIONS) };
emit("ready");
