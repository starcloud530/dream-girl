#!/usr/bin/env bash
# Download Qwen3-TTS 0.6B CustomVoice into MODELS_ROOT/qwen-tts/ (Dream Girl default)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"

# Align with configs/app.example.yaml + deploy/autodl/start_all.sh
BASE_MODELS="${DREAM_GIRL_MODELS_ROOT:-${AUTODL_FS_MODELS}}"
DEST="${BASE_MODELS}/qwen-tts/Qwen3-TTS-12Hz-0.6B-CustomVoice"
mkdir -p "$(dirname "${DEST}")"
PY="${PYTHON_BIN:-python3}"

echo "DEST=${DEST}"
echo "PYTHON=${PY}"

if [[ -f "${DEST}/config.json" ]] && [[ -f "${DEST}/model.safetensors" || -f "${DEST}/pytorch_model.bin" || -d "${DEST}" ]]; then
  if [[ -f "${DEST}/config.json" ]]; then
    echo "skip qwen-tts 0.6B (present)"
    exit 0
  fi
fi

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export DEST

if "${PY}" -c "import modelscope" 2>/dev/null; then
  echo "downloading 0.6B via ModelScope …"
  modelscope download \
    --model Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice \
    --local_dir "${DEST}" && { echo OK; du -sh "${DEST}" || true; exit 0; }
fi

echo "downloading 0.6B via huggingface_hub …"
"${PY}" - <<'PY'
from huggingface_hub import snapshot_download
import os

snapshot_download(
    repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    local_dir=os.environ["DEST"],
)
print("done qwen-tts 0.6B")
PY

echo "OK"
du -sh "${DEST}" 2>/dev/null || true
