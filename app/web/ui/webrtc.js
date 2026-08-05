/**
 * Connect browser video element to LiveTalking WebRTC offer/answer flow.
 *
 * AutoDL HTTPS 反代常拦 UDP/ICE，失败时前端会回退静止立绘。
 * 公网 demo 主路径是短 MP4 + SSE（av-sse.js），不是本模块。
 * WebRTC 仅适合内网直连或已部署 TURN；决策见 docs/webrtc与播放路径决策.md。
 */
export async function connectLiveTalking(videoEl, baseUrl, sessionId) {
  if (!baseUrl || !sessionId) {
    return { ok: false, reason: "no_livetalking" };
  }

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });

  let gotVideo = false;
  pc.ontrack = (ev) => {
    if (ev.track.kind === "video") {
      gotVideo = true;
      videoEl.srcObject = ev.streams[0];
      videoEl.muted = true; // 声音走浏览器 TTS；避免双声道
      videoEl.play().catch(() => {});
    }
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // 等 ICE gathering 完成再发 offer，提高穿越成功率
  await new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") {
      resolve();
      return;
    }
    const t = setTimeout(resolve, 2500);
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(t);
        resolve();
      }
    };
  });

  const url = `${baseUrl.replace(/\/$/, "")}/offer`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription?.sdp || offer.sdp,
        type: "offer",
        sessionid: sessionId,
      }),
    });
  } catch (err) {
    pc.close();
    return { ok: false, reason: `offer_fetch_${err.message || "error"}` };
  }

  if (!res.ok) {
    pc.close();
    return { ok: false, reason: `offer_http_${res.status}` };
  }

  const answer = await res.json();
  if (!answer?.sdp) {
    pc.close();
    return { ok: false, reason: "offer_bad_answer" };
  }
  await pc.setRemoteDescription(answer);

  // 等真正 ICE connected；不要仅凭 ontrack 就判定成功（AutoDL 上常假连接）
  const iceOk = await new Promise((resolve) => {
    const done = (v) => {
      clearTimeout(t);
      resolve(v);
    };
    const t = setTimeout(() => done(pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed"), 6000);
    pc.oniceconnectionstatechange = () => {
      const s = pc.iceConnectionState;
      if (s === "connected" || s === "completed") done(true);
      if (s === "failed" || s === "closed") done(false);
    };
  });

  if (!iceOk) {
    pc.close();
    return { ok: false, reason: `ice_${pc.iceConnectionState || "timeout"}` };
  }
  return { ok: true, pc };
}
