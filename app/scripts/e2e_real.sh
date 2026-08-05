#!/usr/bin/env bash
# 真实 API 冒烟（DeepSeek + Edge TTS，无 mock）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
export PYTHONPATH="${ROOT}"
unset CYBER_GF_USE_MOCK

PORT="${ORCH_PORT:-8011}"
BASE="http://127.0.0.1:${PORT}"
LOG="/tmp/dream-girl-real-$$.log"

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    wait "${PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

.venv/bin/python -m services.orchestrator.main >"${LOG}" 2>&1 &
PID=$!
sleep 3

echo "==> health"
curl -sf "${BASE}/v1/health" | python3 -m json.tool

SID=$(curl -sf -X POST "${BASE}/v1/session" -H "Content-Type: application/json" -d '{}' | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
echo "session_id=${SID}"

curl -sf -X POST "${BASE}/v1/session/${SID}/message" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，一句话介绍你自己"}' >/dev/null

.venv/bin/python - <<PY
import asyncio, json, websockets, sys

async def main():
    uri = "ws://127.0.0.1:${PORT}/v1/session/${SID}/events"
    got_delta = got_audio = got_done = False
    async with websockets.connect(uri) as ws:
        for _ in range(120):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(msg)
            t = ev.get("type")
            if t == "assistant_delta":
                got_delta = True
            if t == "assistant_audio":
                got_audio = True
            if t == "assistant_done":
                got_done = True
                break
            if t == "error":
                print("ERROR", ev.get("payload"), file=sys.stderr)
                sys.exit(1)
    print("delta", got_delta, "audio", got_audio, "done", got_done)
    if not (got_delta and got_audio and got_done):
        sys.exit(1)

asyncio.run(main())
PY

echo "==> real API smoke passed"
