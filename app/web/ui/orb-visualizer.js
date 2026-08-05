const LABELS = {
  idle: "点击发送开始",
  thinking: "思考中…",
  speaking: "说话中…",
};

export function setOrbState(orbEl, labelEl, state) {
  orbEl.classList.remove("idle", "thinking", "speaking");
  orbEl.classList.add(state === "thinking" || state === "speaking" ? state : "idle");
  if (labelEl) {
    labelEl.textContent = LABELS[state] || LABELS.idle;
  }
}
