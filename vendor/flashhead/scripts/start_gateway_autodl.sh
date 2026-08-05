#!/usr/bin/env bash
# FlashHead Gateway :6008（薄层）。默认连本机 Engine :6009，重启不卸模型。
# 单体模式：FLASHHEAD_ENGINE_URL= bash 本脚本（空字符串关闭双进程）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"
export FLASHHEAD_CONFIG="${FLASHHEAD_CONFIG:-configs/t1_384_stack.yaml}"
export FLASHHEAD_ENGINE_PORT="${FLASHHEAD_ENGINE_PORT:-6009}"
# 默认双进程；显式 export FLASHHEAD_ENGINE_URL= 可退回嵌入式加载
if [[ -z "${FLASHHEAD_ENGINE_URL+x}" ]]; then
  export FLASHHEAD_ENGINE_URL="http://127.0.0.1:${FLASHHEAD_ENGINE_PORT}"
fi
LOG_DIR="${DREAM_GIRL_LOG_DIR:-${AUTODL_TMP}/dream-girl-logs}"
mkdir -p "${LOG_DIR}"

# 只杀 Gateway，不动 Engine / TTS
pkill -f "run_gateway.py" 2>/dev/null || true
pkill -f "serve.gateway:app" 2>/dev/null || true
pkill -f "services.avatar_gateway" 2>/dev/null || true
fuser -k "${FLASHHEAD_GATEWAY_PORT:-6008}/tcp" 2>/dev/null || true
sleep 1

if [[ -n "${FLASHHEAD_ENGINE_URL}" ]]; then
  if ! curl -sf -m 2 "${FLASHHEAD_ENGINE_URL}/v1/health" | grep -q '"status":"ok"'; then
    echo "Engine not ready at ${FLASHHEAD_ENGINE_URL}; start it first:"
    echo "  bash ${SCRIPT_DIR}/start_engine_autodl.sh"
    exit 1
  fi
  echo "using Engine ${FLASHHEAD_ENGINE_URL}"
else
  echo "FLASHHEAD_ENGINE_URL empty → embedded model load in gateway"
fi

export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1
# 默认 preroll 1.4s 后开脸（降首动画）；整句串行：FACE_WAIT_TTS_END=1
export FACE_WAIT_TTS_END="${FACE_WAIT_TTS_END:-0}"
export FACE_PREROLL_MS="${FACE_PREROLL_MS:-1400}"
export FACE_FLUSH_MS="${FACE_FLUSH_MS:-250}"
export FACE_JOB_MS="${FACE_JOB_MS:-1400}"
# 首段/后续均按 ~1.4s 模型片直出
export FACE_OUT_MS="${FACE_OUT_MS:-1400}"
export FACE_OUT_FOLLOW_MS="${FACE_OUT_FOLLOW_MS:-1400}"
# 公网主路径：fMP4 → 前端 MSE sequence（消双 video 段缝）
export FACE_MSE_FORMAT="${FACE_MSE_FORMAT:-fmp4}"

GW_ARGS=(
  --config "${FLASHHEAD_CONFIG}"
  --host "${FLASHHEAD_GATEWAY_HOST}"
  --port "${FLASHHEAD_GATEWAY_PORT}"
)
if [[ -n "${FLASHHEAD_ENGINE_URL}" ]]; then
  GW_ARGS+=(--engine-url "${FLASHHEAD_ENGINE_URL}")
fi

nohup "${PYTHON_BIN}" -u "${ROOT}/scripts/run_gateway.py" \
  "${GW_ARGS[@]}" \
  >"${LOG_DIR}/flashhead_gateway.log" 2>&1 &
echo $! >"${LOG_DIR}/flashhead_gateway.pid"
echo "FlashHead Gateway pid=$(cat ${LOG_DIR}/flashhead_gateway.pid) :${FLASHHEAD_GATEWAY_PORT}"

ok=0
# 双进程时 Gateway 秒起；单体仍可能要等模型
MAX_WAIT="${GATEWAY_HEALTH_WAIT:-90}"
for i in $(seq 1 "${MAX_WAIT}"); do
  if curl -sf -m 2 "http://127.0.0.1:${FLASHHEAD_GATEWAY_PORT}/v1/health" | grep -q '"status":"ok"'; then
    echo "health ok after ${i}s"
    curl -sS -m 5 "http://127.0.0.1:${FLASHHEAD_GATEWAY_PORT}/v1/health"; echo
    curl -sS -m 5 -o /dev/null -w "assets:%{http_code}\n" \
      "http://127.0.0.1:${FLASHHEAD_GATEWAY_PORT}/assets/character/xiaoya-v1.jpg" || true
    ok=1
    break
  fi
  if ! kill -0 "$(cat ${LOG_DIR}/flashhead_gateway.pid)" 2>/dev/null; then
    echo "gateway died"; tail -n 40 "${LOG_DIR}/flashhead_gateway.log" || true; exit 1
  fi
  sleep 1
done
[[ "${ok}" == "1" ]] || { echo "timeout waiting health"; tail -n 40 "${LOG_DIR}/flashhead_gateway.log" || true; exit 1; }
tail -n 20 "${LOG_DIR}/flashhead_gateway.log" || true
