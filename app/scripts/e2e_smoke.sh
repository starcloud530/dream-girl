#!/usr/bin/env bash
# 本地冒烟：health → session → message → 等待 WS assistant_done
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
export PYTHONPATH="${ROOT}"
export CYBER_GF_USE_MOCK="${CYBER_GF_USE_MOCK:-1}"

PORT="${ORCH_PORT:-8011}"
BASE="http://127.0.0.1:${PORT}"
LOG="/tmp/dream-girl-orch-$$.log"

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    wait "${PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -e . -q
fi

.venv/bin/python -m services.orchestrator.main >"${LOG}" 2>&1 &
PID=$!
sleep 2

echo "==> health"
curl -sf "${BASE}/v1/health" | head -c 500
echo

echo "==> create session"
SESSION_JSON=$(curl -sf -X POST "${BASE}/v1/session" -H "Content-Type: application/json" -d '{}')
SID=$(python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])" <<<"${SESSION_JSON}")
echo "session_id=${SID}"

echo "==> send message (mock LLM/TTS if no .env)"
curl -sf -X POST "${BASE}/v1/session/${SID}/message" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好"}' >/dev/null

echo "==> wait for assistant_done via python ws client"
.venv/bin/python - <<PY
import asyncio, json, websockets, sys

async def main():
    uri = "ws://127.0.0.1:${PORT}/v1/session/${SID}/events"
    async with websockets.connect(uri) as ws:
        for _ in range(80):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(msg)
            print(ev.get("type"), ev.get("payload"))
            if ev.get("type") == "assistant_done":
                print("OK assistant_done")
                return
        print("TIMEOUT waiting assistant_done", file=sys.stderr)
        sys.exit(1)

asyncio.run(main())
PY

echo "==> e2e smoke passed"
