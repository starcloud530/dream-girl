#!/usr/bin/env bash
# Dream Girl — start Engine + Gateway + vLLM-Omni TTS + Orchestrator
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${ROOT}/app"
FH="${ROOT}/vendor/flashhead"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
[[ -x "${PYTHON_BIN}" ]] || PYTHON_BIN="$(command -v python3)"
MODELS_ROOT="${DREAM_GIRL_MODELS_ROOT:-/root/autodl-fs/models}"
LOG_DIR="${DREAM_GIRL_LOG_DIR:-/root/autodl-tmp/dream-girl-logs}"
mkdir -p "${LOG_DIR}"

export PATH="$(dirname "${PYTHON_BIN}"):/usr/local/cuda/bin:${PATH:-}"
export DREAM_GIRL_ROOT="${DREAM_GIRL_ROOT:-${ROOT}}"
export DREAM_GIRL_MODELS_ROOT="${MODELS_ROOT}"
export DREAM_GIRL_LOG_DIR="${LOG_DIR}"
export CYBER_GF_APP_CONFIG="${CYBER_GF_APP_CONFIG:-${APP}/config/app.yaml}"
export DREAM_GIRL_APP_CONFIG="${DREAM_GIRL_APP_CONFIG:-${CYBER_GF_APP_CONFIG}}"
export PYTHONPATH="${APP}"
export FLASHHEAD_CONFIG="${FLASHHEAD_CONFIG:-configs/t1_384_stack.yaml}"
export FACE_MSE_FORMAT="${FACE_MSE_FORMAT:-fmp4}"
export FACE_PREROLL_MS="${FACE_PREROLL_MS:-1400}"
export FACE_OUT_MS="${FACE_OUT_MS:-1400}"
export FACE_OUT_FOLLOW_MS="${FACE_OUT_FOLLOW_MS:-1400}"
export FACE_JOB_MS="${FACE_JOB_MS:-1400}"

# Load .env if present
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

echo "== FlashHead stack :6009/:6008 =="
export PYTHON_BIN
export AUTODL_TMP="${AUTODL_TMP:-$(dirname "${LOG_DIR}")}"
# Point flashhead logs into dream-girl log dir via existing scripts if possible
if [[ -x "${FH}/scripts/start_stack_autodl.sh" ]]; then
  if curl -sf -m 2 http://127.0.0.1:6009/v1/health | grep -q '"status":"ok"'; then
    echo "engine already up"
    (cd "${FH}" && bash scripts/start_gateway_autodl.sh)
  else
    (cd "${FH}" && bash scripts/start_stack_autodl.sh)
  fi
else
  echo "ERROR: missing ${FH}/scripts/start_stack_autodl.sh"; exit 1
fi

echo "== Qwen TTS vLLM-Omni :8091 =="
# Default config uses qwen → Omni must come up. Cloud/edge TTS can skip.
TTS_REQUIRED=1
CFG="${CYBER_GF_APP_CONFIG}"
if [[ -f "${CFG}" ]] && grep -E '^\s*provider:\s*"(minimax|edge|mock)"' "${CFG}" >/dev/null 2>&1; then
  TTS_REQUIRED=0
  echo "tts.provider is cloud/edge — Omni optional"
fi
if curl -sf -m 3 http://127.0.0.1:8091/v1/models >/dev/null 2>&1; then
  echo "tts already up"
else
  VENV="${VLLM_OMNI_VENV:-/root/autodl-tmp/venv-vllm-omni}"
  OMNI_SRC="${VLLM_OMNI_SRC:-/root/autodl-tmp/vllm-omni}"
  MODEL="${QWEN_TTS_MODEL:-${MODELS_ROOT}/qwen-tts/Qwen3-TTS-12Hz-0.6B-CustomVoice}"
  STAGE="${QWEN_TTS_STAGE:-${OMNI_SRC}/vllm_omni/deploy/qwen3_tts.yaml}"
  if [[ ! -x "${VENV}/bin/vllm" || ! -f "${STAGE}" ]]; then
    echo "ERROR: vLLM-Omni not installed (missing ${VENV}/bin/vllm or ${STAGE})"
    echo "Run: bash ${ROOT}/deploy/autodl/install.sh"
    echo "Or set tts.provider to minimax/edge in app/config/app.yaml"
    if [[ "${TTS_REQUIRED}" == "1" ]]; then
      exit 1
    fi
  else
    pkill -f "vllm serve" 2>/dev/null || true
    fuser -k 8091/tcp 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC1091
    source "${VENV}/bin/activate"
    export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-12.0}"
    export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
    export VLLM_ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND:-FLASH_ATTN}"
    nohup vllm serve "${MODEL}" \
      --omni \
      --port 8091 \
      --host 0.0.0.0 \
      --trust-remote-code \
      --stage-configs-path "${STAGE}" \
      >"${LOG_DIR}/vllm_omni_tts.log" 2>&1 &
    echo $! >"${LOG_DIR}/vllm_omni_tts.pid"
    echo "vllm pid=$(cat "${LOG_DIR}/vllm_omni_tts.pid")"
    ready=0
    for i in $(seq 1 90); do
      if curl -sf -m 3 http://127.0.0.1:8091/v1/models >/dev/null 2>&1; then
        echo "tts ready after ${i}x2s"
        ready=1
        break
      fi
      if ! kill -0 "$(cat "${LOG_DIR}/vllm_omni_tts.pid")" 2>/dev/null; then
        echo "ERROR: vllm died — see ${LOG_DIR}/vllm_omni_tts.log"
        tail -n 40 "${LOG_DIR}/vllm_omni_tts.log" || true
        [[ "${TTS_REQUIRED}" == "1" ]] && exit 1
        break
      fi
      sleep 2
    done
    if [[ "${ready}" != "1" && "${TTS_REQUIRED}" == "1" ]]; then
      echo "ERROR: TTS not ready in time"
      exit 1
    fi
  fi
fi

echo "== Orchestrator :6006 =="
pkill -f "services.orchestrator.main" 2>/dev/null || true
fuser -k 6006/tcp 2>/dev/null || true
sleep 1
# Ensure assets reachable from app
mkdir -p "${APP}/assets/character"
rsync -a "${ROOT}/assets/character/" "${APP}/assets/character/" 2>/dev/null || true
nohup "${PYTHON_BIN}" -u -m services.orchestrator.main \
  >"${LOG_DIR}/orchestrator.log" 2>&1 &
echo $! >"${LOG_DIR}/orchestrator.pid"

ok=0
for i in $(seq 1 40); do
  if curl -sf -m 2 http://127.0.0.1:6006/v1/health | grep -q '"status":"ok"'; then
    echo "orch health ok after ${i}s"
    curl -sS http://127.0.0.1:6006/v1/health; echo
    ok=1
    break
  fi
  sleep 1
done
[[ "${ok}" == "1" ]] || { echo "orch failed"; tail -n 40 "${LOG_DIR}/orchestrator.log"; exit 1; }

echo ""
echo "Dream Girl started."
echo "  Open AutoDL custom service → port 6006 (HTTPS portal)"
echo "  Logs: ${LOG_DIR}"
bash "${ROOT}/deploy/autodl/healthcheck.sh" || true
