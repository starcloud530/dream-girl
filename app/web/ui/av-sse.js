/**
 * HTTP/SSE 音画同流（无隧道、无 WebSocket）：
 * EventSource 收 av_mp4 元数据 → GET 短 MP4 → 双 video 预缓冲播放。
 * 首段约 1.4s；PREROLL=1 ≈ 收到首段即开播。
 */
const PREROLL = 1;

function ensureVideoPair(imgEl, { faceStack = true } = {}) {
  const parent = imgEl.parentElement;
  if (!parent) return { a: null, b: null };
  // faceStack：顶 1:1 叠层；否则后端已贴回 2:3，video 铺满
  parent.classList.toggle("face-stack", !!faceStack);
  let a = parent.querySelector("video.av-a");
  let b = parent.querySelector("video.av-b");
  if (!a) {
    a = document.createElement("video");
    a.className = "av-a";
    a.playsInline = true;
    a.preload = "auto";
    a.setAttribute("playsinline", "");
    parent.appendChild(a);
  }
  if (!b) {
    b = document.createElement("video");
    b.className = "av-b";
    b.playsInline = true;
    b.preload = "auto";
    b.setAttribute("playsinline", "");
    parent.appendChild(b);
  }
  // 闲置只看立绘；开播后 face-stack 盖顶，或全幅盖住静图
  a.hidden = true;
  b.hidden = true;
  a.style.opacity = "0";
  b.style.opacity = "0";
  imgEl.hidden = false;
  imgEl.style.opacity = "1";
  imgEl.style.zIndex = "1";
  return { a, b };
}

export function startAvSSE(imgEl, sessionId, { onStats, faceStack = true } = {}) {
  if (!imgEl || !sessionId || typeof EventSource === "undefined") {
    return { stop() {}, ok: false };
  }

  const { a: vidA, b: vidB } = ensureVideoPair(imgEl, { faceStack });
  const sseUrl = `/v1/session/${sessionId}/av/sse`;

  let es = null;
  let stopped = false;
  let retryTimer = null;
  const mp4Q = [];
  /** @type {Map<number, ArrayBuffer>} 已下载但未到播放序号的段 */
  const pending = new Map();
  /** 下一个应入队播放的 chunk；null=尚未锁定首段 */
  let nextChunk = null;
  let started = false;
  let playing = false;
  let front = vidA;
  let back = vidB;
  let chunksPlayed = 0;
  let underruns = 0;
  const objectUrls = [];
  const inflight = new Set();

  const drainPending = () => {
    if (nextChunk == null) {
      if (!pending.size) return;
      // 首段以当前已到的最小 chunk 为准（FlashHead 一般从 1 起）
      nextChunk = Math.min(...pending.keys());
    }
    while (pending.has(nextChunk)) {
      mp4Q.push(pending.get(nextChunk));
      pending.delete(nextChunk);
      nextChunk += 1;
    }
  };

  const report = () => {
    onStats?.({
      chunksPlayed,
      queued: mp4Q.length,
      preroll: !started,
      underruns,
      mode: "av_sse",
    });
  };

  const revokeLater = (u) => {
    objectUrls.push(u);
    while (objectUrls.length > 8) {
      const old = objectUrls.shift();
      try {
        URL.revokeObjectURL(old);
      } catch (_) {}
    }
  };

  const waitEnded = (v) =>
    new Promise((resolve) => {
      if (!v) return resolve();
      const done = () => {
        v.removeEventListener("ended", done);
        v.removeEventListener("error", done);
        resolve();
      };
      v.addEventListener("ended", done, { once: true });
      v.addEventListener("error", done, { once: true });
    });

  const playLoop = async () => {
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
        const nextEl = back;
        const curEl = front;
        nextEl.src = urlObj;
        nextEl.load();
        try {
          nextEl.muted = false;
          await nextEl.play();
        } catch (_) {
          try {
            nextEl.muted = true;
            await nextEl.play();
          } catch (err) {
            console.warn("[av-sse] play failed", err);
          }
        }
        nextEl.hidden = false;
        nextEl.style.opacity = "1";
        nextEl.style.zIndex = "2";
        // 立绘垫底作 poster，避免段间隙黑屏
        imgEl.hidden = false;
        imgEl.style.opacity = "1";
        curEl.hidden = true;
        curEl.style.opacity = "0";
        try {
          curEl.pause();
          curEl.removeAttribute("src");
          curEl.load();
        } catch (_) {}
        front = nextEl;
        back = curEl;
        if (mp4Q.length) {
          const peek = URL.createObjectURL(new Blob([mp4Q[0]], { type: "video/mp4" }));
          revokeLater(peek);
          back.src = peek;
          back.load();
        }
        chunksPlayed += 1;
        report();
        await waitEnded(front);
      }
    } finally {
      playing = false;
      if (!stopped && mp4Q.length && (started || mp4Q.length >= PREROLL)) {
        void playLoop();
      }
    }
  };

  const fetchChunk = async (url, chunk) => {
    const idx = Number(chunk);
    if (!Number.isFinite(idx) || inflight.has(idx) || pending.has(idx)) return;
    // 已播过/已入队的旧序号直接丢（重连重复推送）
    if (nextChunk != null && idx < nextChunk) return;
    inflight.add(idx);
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`mp4 HTTP ${r.status}`);
      const buf = await r.arrayBuffer();
      pending.set(idx, buf);
      const before = mp4Q.length;
      drainPending();
      console.info("[av-sse] mp4", idx, buf.byteLength, {
        queued: mp4Q.length,
        pending: pending.size,
        nextChunk,
        drained: mp4Q.length - before,
      });
      report();
      void playLoop();
    } catch (err) {
      console.warn("[av-sse] fetch mp4 failed", idx, err);
    } finally {
      inflight.delete(idx);
    }
  };

  const connect = () => {
    if (stopped) return;
    console.info("[av-sse] connecting", sseUrl);
    es = new EventSource(sseUrl);
    es.onopen = () => console.info("[av-sse] open");
    es.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (_) {
        return;
      }
      if (msg.type === "status") {
        console.info("[av-sse] status", msg);
        if (msg.event === "session_expired") {
          stopped = true;
          onStats?.({ preroll: true, queued: 0, error: "会话已过期，请刷新页面" });
          return;
        }
        if (msg.event === "error") {
          onStats?.({ preroll: true, queued: 0, error: msg.message });
        }
        return;
      }
      if (msg.type === "av_mp4") {
        const path = msg.url || `/v1/session/${sessionId}/mp4/${msg.chunk}`;
        void fetchChunk(path, msg.chunk);
      }
    };
    es.onerror = () => {
      console.warn("[av-sse] error/retry");
      try {
        es.close();
      } catch (_) {}
      es = null;
      if (!stopped) retryTimer = setTimeout(connect, 3000);
    };
  };

  connect();

  return {
    ok: true,
    stop() {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        es?.close();
      } catch (_) {}
      es = null;
      mp4Q.length = 0;
      pending.clear();
      nextChunk = null;
      for (const u of objectUrls) {
        try {
          URL.revokeObjectURL(u);
        } catch (_) {}
      }
      try {
        front?.pause();
        back?.pause();
      } catch (_) {}
      if (vidA) vidA.hidden = true;
      if (vidB) vidB.hidden = true;
      imgEl.hidden = false;
    },
  };
}
