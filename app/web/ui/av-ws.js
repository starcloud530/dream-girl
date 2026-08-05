/**
 * AutoDL 音画同流（对齐 Soul 官方 Gradio streaming）：
 * JSON av_mp4 + 一段 H264/AAC MP4 binary；双 video 无缝接段；预缓冲 2 段再播。
 *
 * 仍兼容旧协议 av_chunk + JPEG（若 Gateway 回退）。
 */
import { playPcmBytesLocked, stopAudio, ensureAudioCtx } from "./audio-player.js";

const PREROLL = 2;
const PCM_RATE = 16000;

function b64ToBytes(base64) {
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureVideoPair(imgEl) {
  const parent = imgEl.parentElement;
  if (!parent) return { a: null, b: null };
  let a = parent.querySelector("video.av-a");
  let b = parent.querySelector("video.av-b");
  if (!a) {
    a = document.createElement("video");
    a.className = "av-a";
    a.playsInline = true;
    a.preload = "auto";
    a.setAttribute("playsinline", "");
    a.setAttribute("webkit-playsinline", "");
    parent.appendChild(a);
  }
  if (!b) {
    b = document.createElement("video");
    b.className = "av-b";
    b.playsInline = true;
    b.preload = "auto";
    b.setAttribute("playsinline", "");
    b.setAttribute("webkit-playsinline", "");
    parent.appendChild(b);
  }
  // 立绘先留着，等第一段 MP4 真正开播再藏，避免黑屏
  const canvas = parent.querySelector("canvas.av-canvas");
  if (canvas) canvas.hidden = true;
  a.hidden = true;
  b.hidden = true;
  imgEl.hidden = false;
  return { a, b };
}

export function startAvWS(imgEl, sessionId, { onStats } = {}) {
  if (!imgEl || !sessionId) {
    return { stop() {}, ok: false };
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/v1/session/${sessionId}/av/ws`;
  const { a: vidA, b: vidB } = ensureVideoPair(imgEl);

  let ws = null;
  let stopped = false;
  let retryTimer = null;
  let mode = "av_mp4"; // or av_chunk
  let pendingMeta = null;
  let pendingFrames = [];
  const mp4Q = [];
  const jpegQ = [];
  let started = false;
  let playing = false;
  let front = vidA;
  let back = vidB;
  let chunksPlayed = 0;
  let underruns = 0;
  let objectUrls = [];

  const report = () => {
    onStats?.({
      chunksPlayed,
      framesShown: chunksPlayed, // mp4 段计数
      queued: mode === "av_mp4" ? mp4Q.length : jpegQ.length,
      preroll: !started,
      underruns,
      mode,
    });
  };

  const revokeLater = (u) => {
    objectUrls.push(u);
    if (objectUrls.length > 6) {
      const old = objectUrls.shift();
      try {
        URL.revokeObjectURL(old);
      } catch (_) {}
    }
  };

  const waitEnded = (v) =>
    new Promise((resolve) => {
      if (!v) {
        resolve();
        return;
      }
      const done = () => {
        v.removeEventListener("ended", done);
        v.removeEventListener("error", done);
        resolve();
      };
      v.addEventListener("ended", done, { once: true });
      v.addEventListener("error", done, { once: true });
    });

  const playMp4Loop = async () => {
    if (playing || stopped) return;
    playing = true;
    try {
      while (!stopped) {
        if (!started) {
          if (mp4Q.length < PREROLL) {
            report();
            break;
          }
          started = true;
        }
        if (!mp4Q.length) {
          underruns += 1;
          report();
          break;
        }
        const buf = mp4Q.shift();
        const urlObj = URL.createObjectURL(new Blob([buf], { type: "video/mp4" }));
        revokeLater(urlObj);

        // 双缓冲：下一段进 back，当前 front 播完立刻切
        const nextEl = back;
        const curEl = front;
        nextEl.src = urlObj;
        nextEl.muted = false;
        nextEl.load();
        try {
          // 带音轨的 MP4 需用户手势后才能 unmute；失败则静音重试（画面优先）
          nextEl.muted = false;
          await nextEl.play();
        } catch (err) {
          console.warn("[av-ws] play with audio failed, retry muted", err);
          try {
            nextEl.muted = true;
            await nextEl.play();
          } catch (err2) {
            console.warn("[av-ws] play failed", err2);
          }
        }
        nextEl.hidden = false;
        imgEl.hidden = true;
        curEl.hidden = true;
        try {
          curEl.pause();
          curEl.removeAttribute("src");
          curEl.load();
        } catch (_) {}
        front = nextEl;
        back = curEl;

        // 预载再下一段到 back（不 shift）
        if (mp4Q.length) {
          const peekUrl = URL.createObjectURL(new Blob([mp4Q[0]], { type: "video/mp4" }));
          revokeLater(peekUrl);
          back.src = peekUrl;
          back.load();
        }

        chunksPlayed += 1;
        report();
        await waitEnded(front);
      }
      // 队列空了：冻结末帧（paused 停在最后一帧），不要卸 src，否则变黑屏
      if (!stopped && !mp4Q.length && front && !front.hidden) {
        try {
          front.pause();
        } catch (_) {}
        console.info("[av-ws] queue empty — freeze last frame (not black)");
        report();
      }
    } finally {
      playing = false;
      if (!stopped && mp4Q.length && (started || mp4Q.length >= PREROLL)) {
        void playMp4Loop();
      }
    }
  };

  // ---- legacy JPEG path (fallback) ----
  let canvas = null;
  let c2d = null;
  let audioCursor = 0;
  let frameTimer = null;
  let jpegScheduling = false;

  const ensureCanvas = () => {
    const parent = imgEl.parentElement;
    if (!parent) return;
    canvas = parent.querySelector("canvas.av-canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "av-canvas";
      parent.appendChild(canvas);
    }
    if (vidA) vidA.hidden = true;
    if (vidB) vidB.hidden = true;
    imgEl.hidden = true;
    canvas.hidden = false;
    c2d = canvas.getContext("2d", { alpha: false });
  };

  const paint = (bmp) => {
    if (!c2d || !canvas || !bmp) return;
    if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
      canvas.width = bmp.width;
      canvas.height = bmp.height;
    }
    c2d.drawImage(bmp, 0, 0);
  };

  const pumpJpeg = async () => {
    if (jpegScheduling || stopped) return;
    jpegScheduling = true;
    try {
      const ctx = ensureAudioCtx();
      while (!stopped && jpegQ.length) {
        if (!started) {
          if (jpegQ.length < PREROLL) {
            report();
            break;
          }
          started = true;
          audioCursor = ctx.currentTime + 0.05;
        }
        if (ctx.currentTime > audioCursor + 0.05) {
          audioCursor = ctx.currentTime + 0.08;
          underruns += 1;
        }
        const chunk = jpegQ.shift();
        const startAt = audioCursor;
        const fps = chunk.fps || 20;
        const dur =
          chunk.pcm?.byteLength > 0
            ? chunk.pcm.byteLength / 2 / (chunk.sample_rate || PCM_RATE)
            : (chunk.frames?.length || 0) / fps;
        const bitmaps = [];
        for (const buf of chunk.frames || []) {
          try {
            bitmaps.push(await createImageBitmap(new Blob([buf], { type: "image/jpeg" })));
          } catch (_) {}
        }
        if (chunk.pcm?.byteLength) void playPcmBytesLocked(chunk.pcm, { when: startAt });
        const frameDur = 1 / fps;
        let i = 0;
        const tick = () => {
          if (stopped) return;
          const elapsed = ensureAudioCtx().currentTime - startAt;
          const idx = Math.min(bitmaps.length - 1, Math.max(0, Math.floor(elapsed / frameDur)));
          while (i <= idx && i < bitmaps.length) {
            paint(bitmaps[i]);
            try {
              bitmaps[i].close?.();
            } catch (_) {}
            i += 1;
          }
          if (i < bitmaps.length) {
            frameTimer = setTimeout(tick, Math.max(0, (startAt + i * frameDur - ensureAudioCtx().currentTime) * 1000));
          }
        };
        if (frameTimer) clearTimeout(frameTimer);
        tick();
        audioCursor = startAt + dur;
        chunksPlayed += 1;
        report();
      }
    } finally {
      jpegScheduling = false;
      if (!stopped && jpegQ.length && (started || jpegQ.length >= PREROLL)) void pumpJpeg();
    }
  };

  const connect = () => {
    if (stopped) return;
    console.info("[av-ws] connecting", url);
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ensureAudioCtx();
      console.info("[av-ws] open");
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch (_) {
          return;
        }
        if (msg.type === "status") {
          if (msg.mode) mode = msg.mode;
          console.info("[av-ws] status", msg);
          return;
        }
        if (msg.type === "av_mp4") {
          mode = "av_mp4";
          pendingMeta = msg;
          return;
        }
        if (msg.type === "av_chunk") {
          mode = "av_chunk";
          ensureCanvas();
          pendingMeta = msg;
          pendingFrames = [];
          return;
        }
        return;
      }
      if (mode === "av_mp4" || pendingMeta?.type === "av_mp4") {
        mp4Q.push(ev.data);
        pendingMeta = null;
        void playMp4Loop();
        return;
      }
      if (!pendingMeta) return;
      pendingFrames.push(ev.data);
      if (pendingFrames.length >= (pendingMeta.n_frames || 0)) {
        jpegQ.push({
          pcm: b64ToBytes(pendingMeta.pcm_b64 || ""),
          frames: pendingFrames.slice(0, pendingMeta.n_frames),
          fps: pendingMeta.fps || 20,
          sample_rate: pendingMeta.sample_rate || PCM_RATE,
        });
        pendingMeta = null;
        pendingFrames = [];
        void pumpJpeg();
      }
    };
    ws.onerror = () => console.warn("[av-ws] error");
    ws.onclose = (ev) => {
      console.warn("[av-ws] closed", ev.code);
      if (!stopped) retryTimer = setTimeout(connect, 1500);
    };
  };

  connect();

  return {
    ok: true,
    stop() {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (frameTimer) clearTimeout(frameTimer);
      try {
        ws?.close();
      } catch (_) {}
      ws = null;
      mp4Q.length = 0;
      jpegQ.length = 0;
      stopAudio();
      for (const u of objectUrls) {
        try {
          URL.revokeObjectURL(u);
        } catch (_) {}
      }
      objectUrls = [];
      try {
        front?.pause();
        back?.pause();
      } catch (_) {}
      if (vidA) vidA.hidden = true;
      if (vidB) vidB.hidden = true;
      if (canvas) canvas.hidden = true;
      imgEl.hidden = false;
    },
    stats() {
      return { chunksPlayed, queued: mp4Q.length, underruns, started, mode };
    },
  };
}
