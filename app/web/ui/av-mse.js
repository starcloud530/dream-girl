/**
 * MSE + fMP4 连续播放（公网主路径，唯一音画轨）。
 * 首段约 1.4s；PREROLL=1 ≈ 收到 chunk1 即开播（降首动画）。
 * 严格从 chunk=1 起播，避免后到的大首段被跳过。
 */
const PREROLL = 1;

const FMP4_MIMES = [
  'video/mp4; codecs="avc1.42E01E,mp4a.40.2"',
  'video/mp4; codecs="avc1.42001E,mp4a.40.2"',
  'video/mp4; codecs="avc1.4D401E,mp4a.40.2"',
  'video/mp4; codecs="avc1.64001E,mp4a.40.2"',
  'video/mp4; codecs="avc1.640028,mp4a.40.2"',
];

export function mseSupported() {
  const MS = window.MediaSource || window.WebKitMediaSource;
  if (!MS || typeof MS.isTypeSupported !== "function") return false;
  return FMP4_MIMES.some((c) => MS.isTypeSupported(c));
}

function pickFmp4Mime() {
  const MS = window.MediaSource || window.WebKitMediaSource;
  for (const c of FMP4_MIMES) {
    if (MS.isTypeSupported(c)) return c;
  }
  return null;
}

/** 解析 ISO-BMFF 顶层 box → [{type, start, end}] */
function listBoxes(u8) {
  const out = [];
  let off = 0;
  const n = u8.byteLength;
  const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  while (off + 8 <= n) {
    let size = view.getUint32(off);
    const typ = String.fromCharCode(
      u8[off + 4],
      u8[off + 5],
      u8[off + 6],
      u8[off + 7]
    );
    let header = 8;
    if (size === 1) {
      if (off + 16 > n) break;
      const hi = view.getUint32(off + 8);
      const lo = view.getUint32(off + 12);
      size = hi * 0x100000000 + lo;
      header = 16;
    } else if (size === 0) {
      size = n - off;
    }
    if (size < 8 || off + size > n) break;
    out.push({ type: typ, start: off, end: off + size });
    off += size;
  }
  return out;
}

/**
 * 首段：整段 fMP4（含 init）。
 * 后续：只取 moof+mdat（及可能的 sidx），丢掉 ftyp/moov，配合 sequence 续时间轴。
 */
function mediaPayload(u8, { first }) {
  if (first) return u8;
  const boxes = listBoxes(u8);
  const keep = boxes.filter((b) =>
    ["moof", "mdat", "sidx", "ssix"].includes(b.type)
  );
  if (!keep.length) return u8;
  const total = keep.reduce((s, b) => s + (b.end - b.start), 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const b of keep) {
    out.set(u8.subarray(b.start, b.end), o);
    o += b.end - b.start;
  }
  return out;
}

function ensureMseVideo(imgEl, { faceStack = false } = {}) {
  const parent = imgEl.parentElement;
  if (!parent) return null;
  parent.classList.toggle("face-stack", !!faceStack);
  let v = parent.querySelector("video.av-mse");
  if (!v) {
    v = document.createElement("video");
    v.className = "av-mse";
    v.playsInline = true;
    v.autoplay = true;
    v.muted = true;
    v.preload = "auto";
    v.setAttribute("playsinline", "");
    parent.appendChild(v);
  }
  parent.querySelectorAll("video.av-a, video.av-b, video#avatar").forEach((el) => {
    el.hidden = true;
    el.style.opacity = "0";
  });
  v.hidden = false;
  v.style.opacity = "0";
  v.style.zIndex = "2";
  imgEl.hidden = false;
  imgEl.style.opacity = "1";
  imgEl.style.zIndex = "1";
  return v;
}

export function startAvMSE(imgEl, sessionId, { onStats, faceStack = false } = {}) {
  const mime = pickFmp4Mime();
  if (!imgEl || !sessionId || typeof EventSource === "undefined" || !mime) {
    return { stop() {}, ok: false, reason: "mse_unsupported" };
  }

  const video = ensureMseVideo(imgEl, { faceStack });
  if (!video) return { stop() {}, ok: false, reason: "no_video" };

  const MS = window.MediaSource || window.WebKitMediaSource;
  const sseUrl = `/v1/session/${sessionId}/av/sse`;

  let es = null;
  let stopped = false;
  let retryTimer = null;
  let mediaSource = null;
  let sourceBuffer = null;
  let objectUrl = null;
  let sbReady = false;
  let started = false;
  let firstAppendDone = false;
  let chunksPlayed = 0;
  let underruns = 0;
  let nextChunk = null;
  const pending = new Map();
  const queue = [];
  const inflight = new Set();
  let appending = false;

  let lastStallAt = 0;
  const noteStall = (why) => {
    const now = performance.now();
    // 去抖：同一段卡顿不连加；轮与轮之间由 beginTurn 清零
    if (now - lastStallAt < 800) return;
    lastStallAt = now;
    underruns += 1;
    console.info("[av-mse] stall", why, { underruns, t: video.currentTime });
    report();
  };

  const report = () => {
    let buffered = null;
    try {
      if (video.buffered.length) {
        buffered = `${video.buffered.start(0).toFixed(2)}-${video.buffered
          .end(video.buffered.length - 1)
          .toFixed(2)}`;
      }
    } catch (_) {}
    onStats?.({
      chunksPlayed,
      queued: queue.length,
      preroll: !started,
      underruns,
      mode: "av_mse_fmp4",
      currentTime: video.currentTime,
      paused: video.paused,
      buffered,
    });
  };

  const showVideo = () => {
    video.hidden = false;
    video.style.opacity = "1";
    video.style.zIndex = "2";
  };

  const tryPlay = async () => {
    showVideo();
    try {
      await video.play();
    } catch (err) {
      console.warn("[av-mse] play", err);
    }
    if (video.muted) {
      try {
        video.muted = false;
      } catch (_) {}
    }
  };

  const waitUpdate = (sb) =>
    new Promise((resolve, reject) => {
      if (!sb.updating) return resolve();
      const ok = () => {
        cleanup();
        resolve();
      };
      const bad = () => {
        cleanup();
        reject(new Error("SourceBuffer error"));
      };
      const cleanup = () => {
        sb.removeEventListener("updateend", ok);
        sb.removeEventListener("error", bad);
      };
      sb.addEventListener("updateend", ok, { once: true });
      sb.addEventListener("error", bad, { once: true });
    });

  const trimBuffer = async () => {
    if (!sourceBuffer || sourceBuffer.updating || !video.buffered.length) return;
    try {
      const end = video.buffered.end(video.buffered.length - 1);
      const keepFrom = Math.max(0, video.currentTime - 2);
      if (end - keepFrom > 12 && video.currentTime > 4) {
        sourceBuffer.remove(0, keepFrom);
        await waitUpdate(sourceBuffer);
      }
    } catch (err) {
      console.warn("[av-mse] trim", err);
    }
  };

  const appendAb = async (ab) => {
    while (sourceBuffer.updating) {
      await waitUpdate(sourceBuffer).catch(() => {});
    }
    if (mediaSource?.readyState === "ended") {
      try {
        mediaSource.open?.();
      } catch (_) {
        // some browsers reopen on append
      }
    }
    sourceBuffer.appendBuffer(ab);
    await waitUpdate(sourceBuffer);
  };

  const drainPending = () => {
    // 必须从 chunk=1 起播。首段 ~2.8s 体积更大，常比 chunk2 后下完；
    // 若用 min(pending) 会从 2 起播，并因 idx<nextChunk 永久丢掉 chunk1 → 开头台词/口型消失。
    if (nextChunk == null) {
      if (!pending.has(1)) return;
      nextChunk = 1;
    }
    while (pending.has(nextChunk)) {
      queue.push({ buf: pending.get(nextChunk), chunk: nextChunk });
      pending.delete(nextChunk);
      nextChunk += 1;
    }
  };

  const pumpAppend = async () => {
    if (stopped || appending || !sbReady || !sourceBuffer) return;
    if (!started) {
      if (queue.length < PREROLL) {
        report();
        return;
      }
      started = true;
    }
    if (!queue.length) {
      try {
        if (
          video.buffered.length &&
          !video.paused &&
          video.currentTime >=
            video.buffered.end(video.buffered.length - 1) - 0.15
        ) {
          noteStall("queue_empty");
        }
      } catch (_) {}
      report();
      return;
    }

    appending = true;
    try {
      while (!stopped && queue.length && sourceBuffer) {
        const item = queue.shift();
        const u8 = new Uint8Array(item.buf);
        const payload = mediaPayload(u8, { first: !firstAppendDone });
        await appendAb(payload);
        firstAppendDone = true;
        chunksPlayed += 1;
        console.info("[av-mse] fmp4", item.chunk, {
          bytes: payload.byteLength,
          raw: item.buf.byteLength,
          t: video.currentTime,
          buf: video.buffered.length
            ? `${video.buffered.start(0).toFixed(2)}-${video.buffered
                .end(video.buffered.length - 1)
                .toFixed(2)}`
            : "none",
        });
        report();
        await tryPlay();
        await trimBuffer();
      }
    } catch (err) {
      console.warn("[av-mse] append failed", err);
      onStats?.({
        error: `MSE append 失败: ${err.message || err}`,
        queued: queue.length,
        chunksPlayed,
      });
    } finally {
      appending = false;
      if (!stopped && queue.length) void pumpAppend();
    }
  };

  const fetchChunk = async (url, chunk) => {
    const idx = Number(chunk);
    if (!Number.isFinite(idx) || inflight.has(idx) || pending.has(idx)) return;
    if (nextChunk != null && idx < nextChunk) return;
    inflight.add(idx);
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`media HTTP ${r.status}`);
      const buf = await r.arrayBuffer();
      pending.set(idx, buf);
      drainPending();
      console.info("[av-mse] got", idx, buf.byteLength);
      report();
      void pumpAppend();
    } catch (err) {
      console.warn("[av-mse] fetch failed", idx, err);
    } finally {
      inflight.delete(idx);
    }
  };

  const setupMediaSource = () =>
    new Promise((resolve, reject) => {
      mediaSource = new MS();
      objectUrl = URL.createObjectURL(mediaSource);
      video.src = objectUrl;
      video.addEventListener("error", () => {
        console.warn("[av-mse] video error", video.error);
      });
      video.addEventListener("waiting", () => {
        noteStall("waiting");
      });
      video.addEventListener("playing", () => showVideo());
      const onOpen = () => {
        try {
          sourceBuffer = mediaSource.addSourceBuffer(mime);
          try {
            sourceBuffer.mode = "sequence";
          } catch (_) {}
          sbReady = true;
          resolve();
        } catch (e) {
          reject(e);
        }
      };
      mediaSource.addEventListener("sourceopen", onOpen, { once: true });
      mediaSource.addEventListener(
        "error",
        () => reject(new Error("MediaSource error")),
        { once: true }
      );
    });

  const hardResetPlayback = async () => {
    // 每轮回归初始：新 MediaSource 时间轴 + 清空计数
    queue.length = 0;
    pending.clear();
    inflight.clear();
    nextChunk = null;
    started = false;
    firstAppendDone = false;
    chunksPlayed = 0;
    underruns = 0;
    lastStallAt = 0;
    appending = false;
    sbReady = false;
    sourceBuffer = null;
    try {
      video.pause();
    } catch (_) {}
    try {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    } catch (_) {}
    objectUrl = null;
    mediaSource = null;
    try {
      video.removeAttribute("src");
      video.load();
    } catch (_) {}
    await setupMediaSource();
    report();
    console.info("[av-mse] beginTurn reset");
  };

  const connect = () => {
    if (stopped) return;
    console.info("[av-mse] connecting fMP4", sseUrl, mime);
    es = new EventSource(sseUrl);
    es.onopen = () => console.info("[av-mse] open");
    es.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (_) {
        return;
      }
      if (msg.type === "status") {
        if (msg.event === "session_expired") {
          stopped = true;
          onStats?.({ preroll: true, queued: 0, error: "会话已过期，请刷新页面" });
          return;
        }
        if (msg.event === "turn_reset") {
          void hardResetPlayback();
          return;
        }
        return;
      }
      if (msg.type === "av_mp4") {
        if (msg.format && msg.format !== "fmp4" && msg.format !== "mp4") {
          console.warn("[av-mse] unexpected format", msg.format);
        }
        // 序号回绕（新一轮从 1 起）→ 先清空再收
        const idx = Number(msg.chunk);
        if (
          Number.isFinite(idx) &&
          nextChunk != null &&
          idx + 1 < nextChunk &&
          idx <= 2
        ) {
          void hardResetPlayback().then(() =>
            fetchChunk(
              msg.url || `/v1/session/${sessionId}/mp4/${msg.chunk}`,
              msg.chunk
            )
          );
          return;
        }
        const path = msg.url || `/v1/session/${sessionId}/mp4/${msg.chunk}`;
        void fetchChunk(path, msg.chunk);
      }
    };
    es.onerror = () => {
      try {
        es.close();
      } catch (_) {}
      es = null;
      if (!stopped) retryTimer = setTimeout(connect, 3000);
    };
  };

  void (async () => {
    try {
      await setupMediaSource();
      connect();
      report();
    } catch (err) {
      console.warn("[av-mse] setup failed", err);
      onStats?.({
        preroll: true,
        queued: 0,
        error: `MSE 初始化失败: ${err.message || err}`,
      });
    }
  })();

  return {
    ok: true,
    mode: "av_mse_fmp4",
    beginTurn() {
      return hardResetPlayback();
    },
    stop() {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        es?.close();
      } catch (_) {}
      es = null;
      queue.length = 0;
      pending.clear();
      try {
        video.pause();
      } catch (_) {}
      try {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      } catch (_) {}
      objectUrl = null;
      sourceBuffer = null;
      mediaSource = null;
      video.hidden = true;
      video.style.opacity = "0";
      try {
        video.removeAttribute("src");
        video.load();
      } catch (_) {}
      imgEl.hidden = false;
    },
  };
}
