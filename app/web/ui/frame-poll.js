/**
 * Poll LiveTalking /frame.jpg over HTTPS — works behind AutoDL reverse proxy
 * where WebRTC/UDP ICE usually fails.
 */
export function startFramePoll(imgEl, baseUrl, sessionId, { fps = 15 } = {}) {
  if (!imgEl || !baseUrl || !sessionId) {
    return { stop() {}, ok: false, reason: "missing_args" };
  }
  const interval = Math.max(40, Math.floor(1000 / fps));
  let timer = null;
  let stopped = false;
  let lastBlobUrl = null;
  let inflight = false;
  let okFrames = 0;

  const tick = async () => {
    if (stopped || inflight) return;
    inflight = true;
    try {
      const url = `${baseUrl.replace(/\/$/, "")}/frame.jpg?sessionid=${encodeURIComponent(
        sessionId
      )}&t=${Date.now()}`;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) return;
      if (res.status === 204) return;
      const blob = await res.blob();
      if (!blob || blob.size < 100) return;
      const obj = URL.createObjectURL(blob);
      imgEl.src = obj;
      if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
      lastBlobUrl = obj;
      okFrames += 1;
    } catch (_) {
      /* ignore transient */
    } finally {
      inflight = false;
    }
  };

  timer = setInterval(tick, interval);
  void tick();

  return {
    ok: true,
    stop() {
      stopped = true;
      if (timer) clearInterval(timer);
      timer = null;
      if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
      lastBlobUrl = null;
    },
    frames() {
      return okFrames;
    },
  };
}
