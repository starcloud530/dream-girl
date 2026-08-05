#!/usr/bin/env bash
# AutoDL 后台启动 Qwen3-TTS :6010
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"

export QWEN_TTS_CONFIG="${QWEN_TTS_CONFIG:-configs/qwen_tts.yaml}"
export QWEN_TTS_HOST="${QWEN_TTS_HOST:-0.0.0.0}"
export QWEN_TTS_PORT="${QWEN_TTS_PORT:-6010}"
LOG_DIR="${DREAM_GIRL_LOG_DIR:-${AUTODL_TMP}/dream-girl-logs}"
mkdir -p "${LOG_DIR}"

pkill -f "run_tts_server.py" 2>/dev/null || true
fuser -k "${QWEN_TTS_PORT}/tcp" 2>/dev/null || true
sleep 1

export PYTHONUNBUFFERED=1
nohup "${PYTHON_BIN}" -u "${ROOT}/scripts/run_tts_server.py" \
  --config "${QWEN_TTS_CONFIG}" \
  --host "${QWEN_TTS_HOST}" --port "${QWEN_TTS_PORT}" \
  >"${LOG_DIR}/qwen_tts.log" 2>&1 &
echo $! >"${LOG_DIR}/qwen_tts.pid"
echo "Qwen TTS pid=$(cat ${LOG_DIR}/qwen_tts.pid) :${QWEN_TTS_PORT}"

ok=0
for i in $(seq 1 60); do
  if curl -sf -m 2 "http://127.0.0.1:${QWEN_TTS_PORT}/v1/health" | grep -q '"status":"ok"'; then
    echo "health ok after ${i}s"
    curl -sS -m 5 "http://127.0.0.1:${QWEN_TTS_PORT}/v1/health"; echo
    ok=1
    break
  fi
  if ! kill -0 "$(cat "${LOG_DIR}/qwen_tts.pid")" 2>/dev/null; then
    echo "qwen tts died"; tail -n 40 "${LOG_DIR}/qwen_tts.log" || true; exit 1
  fi
  sleep 5
done
[[ "${ok}" == "1" ]] || { echo "timeout"; tail -n 40 "${LOG_DIR}/qwen_tts.log" || true; exit 1; }
