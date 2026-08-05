const POEMS = [
  "草色青青忽自怜，浮生如梦亦如烟。",
  "鳥啼月落知多少，只記花開不記年。",
  "清晨簾幕捲輕霜，呵手試梅妝。",
];

export function randomPoem() {
  return POEMS[Math.floor(Math.random() * POEMS.length)];
}

export function appendMessage(container, role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

export function appendOrUpdateAssistant(container, el, delta) {
  if (!el) {
    el = document.createElement("div");
    el.className = "msg assistant";
    el.textContent = delta;
    container.appendChild(el);
  } else {
    el.textContent += delta;
  }
  container.scrollTop = container.scrollHeight;
  return el;
}
