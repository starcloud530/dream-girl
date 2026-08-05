import { randomPoem, appendMessage, appendOrUpdateAssistant } from "./chat.js";
import { setOrbState } from "./orb-visualizer.js";
import { pushAudioChunk, playBufferedAudio, stopAudio, resetAudio } from "./audio-player.js";
import { startAvMSE, mseSupported } from "./av-mse.js?v=12";
// 公网：MSE/fMP4；PREROLL=1 · Gateway 1.4s 出段；严格 chunk1 起播


// Unlock audio on first interaction (browser autoplay policy)
document.addEventListener(
  "click",
  () => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        const c = new Ctx();
        c.resume().catch(() => {});
      }
    } catch (_) {}
  },
  { once: true }
);

const els = {
  status: document.getElementById("conn-status"),
  poetry: document.getElementById("poetry"),
  chat: document.getElementById("chat"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  interrupt: document.getElementById("interrupt"),
  orb: document.getElementById("orb"),
  orbLabel: document.getElementById("orb-label"),
  video: document.getElementById("avatar"),
  portrait: document.getElementById("portrait"),
  avatarFrame: document.querySelector(".avatar-frame"),
  metrics: document.getElementById("metrics"),
};

let sessionId = null;
let ws = null;
let assistantEl = null;
let avatarMode = "browser";
let audioMime = "audio/mpeg";
let avStream = null;

function wsUrlFor(id) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/v1/session/${id}/events`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function setStatus(text) {
  els.status.textContent = text;
}

async function handleEvent(ev) {
  const { type, payload = {} } = ev;
  switch (type) {
    case "state":
      setOrbState(els.orb, els.orbLabel, payload.state || "idle");
      // GPU 音画同流时禁用 CSS breathe，避免和逐帧刷画面打架造成「一顿一顿」
      if (payload.state === "speaking" && avatarMode !== "gpu") {
        els.avatarFrame?.classList.add("speaking");
      } else {
        els.avatarFrame?.classList.remove("speaking");
      }
      // 每轮说话开始：前端也清一次（后端会再推 turn_reset）
      if (payload.state === "speaking" && avatarMode === "gpu") {
        try {
          avStream?.beginTurn?.();
        } catch (_) {}
      }
      break;
    case "assistant_delta":
      assistantEl = appendOrUpdateAssistant(els.chat, assistantEl, payload.text || "");
      break;
    case "assistant_done":
      assistantEl = null;
      els.avatarFrame?.classList.remove("speaking");
      if (payload.pipe_rtf != null) {
        const parts = [
          `pipe ${payload.pipe_rtf}`,
          payload.llm_rtf != null ? `llm ${payload.llm_rtf}` : null,
          payload.tts_rtf != null ? `tts ${payload.tts_rtf}` : null,
          payload.tail_ms != null ? `tail ${payload.tail_ms}ms` : null,
        ].filter(Boolean);
        els.metrics.textContent = `RTF · ${parts.join(" · ")}`;
      }
      // browser 模式才本地播 TTS；GPU 音画由 AutoDL av/ws 锁步播放
      if (avatarMode !== "gpu") {
        await playBufferedAudio(audioMime);
      }
      break;
    case "assistant_audio":
      // GPU：编排器不再下发（音频走 AutoDL）；若仍收到则忽略，避免抢跑
      if (avatarMode === "gpu") break;
      if (payload.format) audioMime = payload.format;
      pushAudioChunk(payload.data, payload.format || audioMime);
      break;
    case "metrics":
      if (payload.pcm_buffer_ms != null && payload.pcm_pacer_preroll) {
        els.metrics.textContent = `音频匀速缓冲中 ${payload.pcm_buffer_ms} ms…`;
      } else if (payload.first_audio_ms != null) {
        const pace = payload.pcm_pacer ? " · 匀速推流" : "";
        els.metrics.textContent = `首包音频 ~${payload.first_audio_ms} ms${pace}`;
      }
      break;
    case "error":
      appendMessage(els.chat, "assistant", `错误：${payload.message || payload.code}`);
      setOrbState(els.orb, els.orbLabel, "idle");
      els.avatarFrame?.classList.remove("speaking");
      break;
    default:
      break;
  }
}

function connectWs(id) {
  if (ws) {
    ws.close();
    ws = null;
  }
  ws = new WebSocket(wsUrlFor(id));
  ws.onopen = () => setStatus("已连接");
  ws.onclose = () => setStatus("连接断开");
  ws.onerror = () => setStatus("WebSocket 错误");
  ws.onmessage = (msg) => {
    try {
      void handleEvent(JSON.parse(msg.data));
    } catch (err) {
      console.warn("bad event", err);
    }
  };
}

async function bootstrap() {
  els.poetry.textContent = randomPoem();
  setStatus("初始化…");

  const health = await api("/v1/health");
  avatarMode = health.avatar_mode || "browser";
  setStatus(
    `编排器 OK · LLM=${health.llm} · TTS=${health.tts} · 模式=${avatarMode}`
  );

  const session = await api("/v1/session", { method: "POST", body: "{}" });
  sessionId = session.session_id;
  avatarMode = session.avatar_mode || avatarMode;
  // 闲置固定立绘：优先本机 /assets，避免远端 URL 失败变黑
  if (els.portrait) {
    const localPortrait = "/assets/character/xiaoya-v1-sit.jpg";
    const url = session.portrait_url || health.portrait_url || localPortrait;
    els.portrait.hidden = false;
    els.portrait.style.opacity = "1";
    els.portrait.src =
      url.startsWith("http://127.0.0.1") || url.startsWith("/")
        ? url
        : localPortrait;
    els.portrait.onerror = () => {
      els.portrait.src = localPortrait;
    };
  }
  connectWs(sessionId);

  if (avatarMode === "gpu") {
    const avatarSid = session.avatar_session_id;
    if (avatarSid && avatarSid !== "local-noop") {
      els.video.hidden = true;
      els.portrait.hidden = false;
      // backend_composite：后端已贴回 2:3；frontend_stack：顶 1:1 叠层
      const faceStack = session.layout !== "backend_composite";
      els.avatarFrame?.classList.toggle("face-stack", faceStack);
      const layoutLabel = faceStack ? "顶1:1叠层" : "2:3贴回";
      if (!mseSupported()) {
        els.metrics.textContent =
          "浏览器不支持 MSE/fMP4，无法播放连续音画（请换 Chrome/Edge）";
        return;
      }
      avStream = startAvMSE(els.portrait, sessionId, {
        faceStack,
        onStats: (st) => {
          if (st.error) {
            els.metrics.textContent = `音画 MSE：${st.error}`;
            return;
          }
          if (st.preroll) {
            els.metrics.textContent = `MSE 预缓冲 ${st.queued}/1 · ${layoutLabel}…`;
            return;
          }
          const u = st.underruns ? ` · 等缓冲 ${st.underruns}` : "";
          const t =
            typeof st.currentTime === "number"
              ? ` · t=${st.currentTime.toFixed(1)}`
              : "";
          const b = st.buffered ? ` · buf ${st.buffered}` : "";
          els.metrics.textContent = `MSE/fMP4 · 段 ${st.chunksPlayed} · 队列 ${st.queued}${t}${b}${u}`;
        },
      });
      els.metrics.textContent = avStream?.ok
        ? `MSE/fMP4 连续轨 · ${layoutLabel}…`
        : "MSE 启动失败（需支持 fMP4 的浏览器）";
    } else {
      els.metrics.textContent = "数字人未连上 GPU（已降级语音）— 请强制刷新重试";
    }
  } else {
    els.metrics.textContent = "浏览器立绘 + 语音模式（DeepSeek + Edge TTS）";
  }
}

async function sendMessage() {
  const text = els.input.value.trim();
  if (!text || !sessionId) return;
  els.input.value = "";
  appendMessage(els.chat, "user", text);
  assistantEl = null;
  resetAudio();
  await api(`/v1/session/${sessionId}/message`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

async function interrupt() {
  if (!sessionId) return;
  try {
    avStream?.beginTurn?.();
  } catch (_) {}
  await api(`/v1/session/${sessionId}/interrupt`, { method: "POST", body: "{}" });
  assistantEl = null;
  stopAudio();
  setOrbState(els.orb, els.orbLabel, "idle");
  els.avatarFrame?.classList.remove("speaking");
}

els.send.addEventListener("click", () => sendMessage().catch(console.error));
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage().catch(console.error);
});
els.interrupt.addEventListener("click", () => interrupt().catch(console.error));

bootstrap().catch((err) => {
  setStatus(`启动失败: ${err.message || err}`);
  console.error(err);
});
