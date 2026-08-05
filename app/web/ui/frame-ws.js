/**
 * Receive JPEG frames over WebSocket.
 * Prefer same-origin orchestrator proxy (viaOrchestrator=true) so Mac 日志/连通更稳。
 *
 * @param {object} [opts]
 * @param {(buf: ArrayBuffer) => void} [opts.onFrame] 若提供则不再直接刷 img（交给 av-sync）
 */
export function startFrameWS(
  imgEl,
  baseUrl,
  id,
  { fps = 15, viaOrchestrator = false, onFrame = null } = {}
) {
  if (!imgEl || !baseUrl || !id) {
    return { stop() {}, ok: false, reason: "missing_args" };
  }

  let base = baseUrl.replace(/\/$/, "");
  if (base.startsWith("https://")) base = "wss://" + base.slice("https://".length);
  else if (base.startsWith("http://")) base = "ws://" + base.slice("http://".length);
  else if (base.startsWith("/")) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    base = `${proto}//${location.host}`;
  }

  const path = viaOrchestrator
    ? `/v1/session/${id}/frames/ws?fps=${fps}`
    : `/v1/avatar/${id}/frames/ws?fps=${fps}`;
  const url = `${base}${path}`;

  let ws = null;
  let lastBlobUrl = null;
  let okFrames = 0;
  let stopped = false;
  let retryTimer = null;

  const show = (buf) => {
    if (typeof onFrame === "function") {
      onFrame(buf);
      okFrames += 1;
      return;
    }
    const blob = new Blob([buf], { type: "image/jpeg" });
    if (blob.size < 100) return;
    const obj = URL.createObjectURL(blob);
    imgEl.src = obj;
    if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
    lastBlobUrl = obj;
    okFrames += 1;
  };

  const connect = () => {
    if (stopped) return;
    console.info("[frame-ws] connecting", url);
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => console.info("[frame-ws] open");
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          console.info("[frame-ws] status", JSON.parse(ev.data));
        } catch (_) {}
        return;
      }
      show(ev.data);
    };
    ws.onerror = () => console.warn("[frame-ws] error", url);
    ws.onclose = (ev) => {
      console.warn("[frame-ws] closed", ev.code, ev.reason || "");
      if (!stopped) retryTimer = setTimeout(connect, 1500);
    };
  };

  connect();

  return {
    ok: true,
    stop() {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        ws?.close();
      } catch (_) {}
      ws = null;
      if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
      lastBlobUrl = null;
    },
    frames() {
      return okFrames;
    },
  };
}
