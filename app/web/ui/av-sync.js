/**
 * GPU 模式音画缓冲层：等嘴型帧到齐再放 PCM，帧按目标 fps 匀速出画，减轻音画不同步。
 */
import { setPcmDeferred, releasePcmPlayback } from "./audio-player.js";

export function createAvSync({
  imgEl,
  fps = 20,
  prerollFrames = 4,
  maxWaitMs = 2200,
  maxQueue = 48,
} = {}) {
  let frameQ = [];
  let started = false;
  let armed = false;
  let lastBlobUrl = null;
  let pacer = null;
  let waitTimer = null;
  let shown = 0;
  let received = 0;

  const clearWait = () => {
    if (waitTimer) {
      clearTimeout(waitTimer);
      waitTimer = null;
    }
  };

  const show = (buf) => {
    if (!imgEl || !buf) return;
    const blob = new Blob([buf], { type: "image/jpeg" });
    if (blob.size < 100) return;
    const obj = URL.createObjectURL(blob);
    imgEl.src = obj;
    if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
    lastBlobUrl = obj;
    shown += 1;
  };

  const stopPacer = () => {
    if (pacer) {
      clearInterval(pacer);
      pacer = null;
    }
  };

  const ensurePacer = () => {
    if (pacer || !armed) return;
    const interval = Math.max(20, Math.round(1000 / fps));
    pacer = setInterval(() => {
      if (!started) return;
      if (!frameQ.length) return;
      // 积压过多时丢旧帧，追上音频
      if (frameQ.length > maxQueue) {
        frameQ.splice(0, frameQ.length - Math.floor(fps));
      }
      show(frameQ.shift());
    }, interval);
  };

  const release = (reason = "release") => {
    if (started || !armed) return;
    started = true;
    clearWait();
    setPcmDeferred(false);
    releasePcmPlayback();
    ensurePacer();
    console.info("[av-sync] start", reason, { received, queued: frameQ.length });
  };

  return {
    arm() {
      this.reset();
      armed = true;
      started = false;
      setPcmDeferred(true);
      clearWait();
      waitTimer = setTimeout(() => release("max_wait"), maxWaitMs);
      ensurePacer();
      console.info("[av-sync] armed", { fps, prerollFrames, maxWaitMs });
    },

    pushFrame(buf) {
      if (!buf) return;
      received += 1;
      frameQ.push(buf);
      if (frameQ.length > maxQueue * 2) {
        frameQ.splice(0, frameQ.length - maxQueue);
      }
      if (!started && frameQ.length >= prerollFrames) {
        release("preroll");
      }
      ensurePacer();
    },

    /** 若仍未启动（无帧），在 TTS 结束时也可触发，避免一直静音 */
    onAudioDone() {
      if (armed && !started) release("audio_done");
    },

    reset() {
      armed = false;
      started = false;
      frameQ = [];
      received = 0;
      shown = 0;
      clearWait();
      stopPacer();
      setPcmDeferred(false);
      if (lastBlobUrl) {
        URL.revokeObjectURL(lastBlobUrl);
        lastBlobUrl = null;
      }
    },

    stop() {
      this.reset();
    },

    stats() {
      return { received, shown, queued: frameQ.length, started, armed };
    },
  };
}
