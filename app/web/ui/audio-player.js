let currentAudio = null;
let audioChunks = [];
let audioCtx = null;
let playMime = "audio/mpeg";
let pcmQueue = [];
let pcmPlaying = false;
/** GPU 模式：先积 PCM，等嘴型缓冲层 release 后再播 */
let pcmDeferred = false;
const PCM_RATE = 16000;

function ensureCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: PCM_RATE });
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

export function ensureAudioCtx() {
  return ensureCtx();
}

/** 按 AudioContext 时间轴播放一段 s16le PCM；when 为 ctx.currentTime 起点。 */
export function playPcmBytesLocked(bytes, { when = null } = {}) {
  if (!bytes || !bytes.byteLength) return Promise.resolve();
  const ctx = ensureCtx();
  const buffer = s16ToBuffer(bytes);
  return new Promise((resolve) => {
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    src.onended = () => resolve();
    const t0 = when == null ? ctx.currentTime : when;
    try {
      src.start(t0);
    } catch (_) {
      src.start();
    }
    currentAudio = {
      pause() {
        try {
          src.stop();
        } catch (_) {}
      },
    };
  });
}

export function setPcmDeferred(on) {
  pcmDeferred = !!on;
}

export function releasePcmPlayback() {
  pcmDeferred = false;
  void drainPcmQueue();
}

export function resetAudio() {
  audioChunks = [];
  pcmQueue = [];
  pcmPlaying = false;
  // 打断时不要长期卡在 deferred
  pcmDeferred = false;
  if (currentAudio) {
    try {
      currentAudio.pause();
    } catch (_) {}
    currentAudio = null;
  }
}

function b64ToBytes(base64) {
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function isPcm(mime) {
  const m = (mime || "").toLowerCase();
  return m.includes("l16") || m.includes("pcm") || m.includes("raw");
}

function s16ToBuffer(bytes) {
  const ctx = ensureCtx();
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const samples = Math.floor(u8.byteLength / 2);
  const buffer = ctx.createBuffer(1, samples, PCM_RATE);
  const data = buffer.getChannelData(0);
  const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  for (let i = 0; i < samples; i += 1) {
    data[i] = view.getInt16(i * 2, true) / 32768;
  }
  return buffer;
}

async function drainPcmQueue() {
  if (pcmPlaying) return;
  pcmPlaying = true;
  const ctx = ensureCtx();
  while (pcmQueue.length) {
    const bytes = pcmQueue.shift();
    const buffer = s16ToBuffer(bytes);
    await new Promise((resolve) => {
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      src.onended = resolve;
      src.start();
      currentAudio = {
        pause() {
          try {
            src.stop();
          } catch (_) {}
        },
      };
    });
  }
  pcmPlaying = false;
}

export function pushAudioChunk(base64, mime = "audio/mpeg") {
  playMime = mime || playMime;
  const bytes = b64ToBytes(base64);
  if (isPcm(playMime)) {
    pcmQueue.push(bytes);
    if (!pcmDeferred) void drainPcmQueue();
    return;
  }
  audioChunks.push(bytes);
}

export async function playBufferedAudio(mime = playMime) {
  // PCM already streamed via queue; wait until drained
  if (isPcm(mime || playMime)) {
    while (pcmPlaying || pcmQueue.length) {
      await new Promise((r) => setTimeout(r, 50));
    }
    return;
  }
  if (!audioChunks.length) return;
  const chunks = audioChunks;
  audioChunks = [];
  const blob = new Blob(chunks, { type: mime || "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  currentAudio = audio;
  try {
    await audio.play();
  } catch (err) {
    console.warn("audio play failed", err);
  }
  await new Promise((resolve) => {
    audio.onended = resolve;
    audio.onerror = resolve;
  });
  URL.revokeObjectURL(url);
}

export function stopAudio() {
  resetAudio();
}
