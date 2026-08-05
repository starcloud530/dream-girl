#!/usr/bin/env bash
# Dream Girl — install deps + download weight hooks (AutoDL / Linux + NVIDIA)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${ROOT}/app"
FH="${ROOT}/vendor/flashhead"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MODELS_ROOT="${DREAM_GIRL_MODELS_ROOT:-/root/autodl-fs/models}"
LOG_DIR="${DREAM_GIRL_LOG_DIR:-/root/autodl-tmp/dream-girl-logs}"
VENV="${VLLM_OMNI_VENV:-/root/autodl-tmp/venv-vllm-omni}"
OMNI_SRC="${VLLM_OMNI_SRC:-/root/autodl-tmp/vllm-omni}"

mkdir -p "${LOG_DIR}" "${MODELS_ROOT}"

echo "== Dream Girl install =="
echo "ROOT=${ROOT}"
echo "MODELS_ROOT=${MODELS_ROOT}"

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L || true
else
  echo "WARN: nvidia-smi not found — talking-head requires an NVIDIA GPU"
fi

echo "== app pip =="
"${PYTHON_BIN}" -m pip install -U pip setuptools wheel
(cd "${ROOT}" && "${PYTHON_BIN}" -m pip install -e .)

echo "== config =="
mkdir -p "${APP}/config" "${APP}/assets/character"
sed "s|\${DREAM_GIRL_MODELS_ROOT}|${MODELS_ROOT}|g" \
  "${ROOT}/configs/app.example.yaml" >"${APP}/config/app.yaml"
cp -f "${ROOT}/configs/app.example.yaml" "${APP}/config/app.example.yaml"
rsync -a "${ROOT}/assets/character/" "${APP}/assets/character/"

if [[ ! -f "${ROOT}/.env" && -f "${ROOT}/.env.example" ]]; then
  cp -f "${ROOT}/.env.example" "${ROOT}/.env"
  echo "Created ${ROOT}/.env — fill DEEPSEEK_API_KEY before start"
fi

echo "== flashhead env =="
chmod +x "${FH}/scripts/"*.sh 2>/dev/null || true
if [[ -x "${FH}/scripts/setup_env.sh" ]]; then
  (cd "${FH}" && bash scripts/setup_env.sh) || {
    echo "WARN: setup_env.sh failed; install torch stack manually (see vendor/flashhead/README.md)"
  }
fi

echo "== download weights =="
export MODELS_ROOT DREAM_GIRL_MODELS_ROOT="${MODELS_ROOT}"
(cd "${FH}" && bash scripts/download_weights.sh) || echo "WARN: FlashHead weights download failed"
(cd "${FH}" && bash scripts/download_qwen_tts.sh) || echo "WARN: Qwen-TTS 0.6B download failed"

echo "== vLLM-Omni (Qwen TTS server) =="
if [[ "${SKIP_VLLM_OMNI:-0}" == "1" ]]; then
  echo "SKIP_VLLM_OMNI=1 — you must use tts.provider=minimax|edge or install Omni yourself"
else
  if [[ ! -x "${VENV}/bin/vllm" ]]; then
    echo "Creating ${VENV} and installing vllm==0.26.0 + vllm-omni …"
    "${PYTHON_BIN}" -m venv "${VENV}"
    # shellcheck disable=SC1091
    source "${VENV}/bin/activate"
    pip install -U pip setuptools wheel
    pip install "vllm==0.26.0"
    if [[ ! -d "${OMNI_SRC}/.git" && ! -d "${OMNI_SRC}/vllm_omni" ]]; then
      mkdir -p "$(dirname "${OMNI_SRC}")"
      git clone --depth 1 https://github.com/vllm-project/vllm-omni.git "${OMNI_SRC}" \
        || git clone --depth 1 https://gh-proxy.com/https://github.com/vllm-project/vllm-omni.git "${OMNI_SRC}"
    fi
    (cd "${OMNI_SRC}" && pip install -e .)
    deactivate || true
  else
    echo "vLLM already present at ${VENV}"
  fi
  if [[ ! -f "${OMNI_SRC}/vllm_omni/deploy/qwen3_tts.yaml" ]]; then
    echo "ERROR: missing ${OMNI_SRC}/vllm_omni/deploy/qwen3_tts.yaml"
    echo "Set VLLM_OMNI_SRC or re-run without a broken clone."
    exit 1
  fi
fi

chmod +x "${ROOT}/deploy/autodl/"*.sh 2>/dev/null || true

echo ""
echo "Install finished."
echo "  1) edit ${ROOT}/.env  (DEEPSEEK_API_KEY)"
echo "  2) bash ${ROOT}/deploy/autodl/start_all.sh"
echo "  3) bash ${ROOT}/deploy/autodl/healthcheck.sh"
