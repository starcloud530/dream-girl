#!/usr/bin/env bash
# 常驻 FlashHead Engine :6009（占 GPU / 权重）；Gateway 可单独重启
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"

export FLASHHEAD_CONFIG="${FLASHHEAD_CONFIG:-configs/t1_384_stack.yaml}"
export FLASHHEAD_ENGINE_HOST="${FLASHHEAD_ENGINE_HOST:-127.0.0.1}"
export FLASHHEAD_ENGINE_PORT="${FLASHHEAD_ENGINE_PORT:-6009}"
LOG_DIR="${DREAM_GIRL_LOG_DIR:-${AUTODL_TMP}/dream-girl-logs}"
mkdir -p "${LOG_DIR}"

pkill -f "run_engine.py" 2>/dev/null || true
pkill -f "serve.engine" 2>/dev/null || true
fuser -k "${FLASHHEAD_ENGINE_PORT}/tcp" 2>/dev/null || true
sleep 1

export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1
# 512@1.4s ≈ 100KB/段（原质量）
export FLASHHEAD_MP4_CRF="${FLASHHEAD_MP4_CRF:-28}"
export FLASHHEAD_AAC_BITRATE="${FLASHHEAD_AAC_BITRATE:-64k}"
nohup "${PYTHON_BIN}" -u "${ROOT}/scripts/run_engine.py" \
  --config "${FLASHHEAD_CONFIG}" \
  --host "${FLASHHEAD_ENGINE_HOST}" --port "${FLASHHEAD_ENGINE_PORT}" \
  >"${LOG_DIR}/flashhead_engine.log" 2>&1 &
echo $! >"${LOG_DIR}/flashhead_engine.pid"
echo "FlashHead Engine pid=$(cat ${LOG_DIR}/flashhead_engine.pid) :${FLASHHEAD_ENGINE_PORT}"

ok=0
for i in $(seq 1 120); do
  if curl -sf -m 2 "http://127.0.0.1:${FLASHHEAD_ENGINE_PORT}/v1/health" | grep -q '"status":"ok"'; then
    echo "engine health ok after ${i}s"
    curl -sS -m 5 "http://127.0.0.1:${FLASHHEAD_ENGINE_PORT}/v1/health"; echo
    ok=1
    break
  fi
  if ! kill -0 "$(cat ${LOG_DIR}/flashhead_engine.pid)" 2>/dev/null; then
    echo "engine died"; tail -n 50 "${LOG_DIR}/flashhead_engine.log" || true; exit 1
  fi
  sleep 5
done
[[ "${ok}" == "1" ]] || { echo "timeout waiting engine"; tail -n 50 "${LOG_DIR}/flashhead_engine.log" || true; exit 1; }
