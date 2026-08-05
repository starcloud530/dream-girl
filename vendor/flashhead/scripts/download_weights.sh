#!/usr/bin/env bash
# 下载 FlashHead Pro 必需权重到 autodl-fs/models/flashhead（跳过 Lite）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"

mkdir -p "${MODELS_ROOT}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
PY="${PYTHON_BIN:-python3}"

echo "MODELS_ROOT=${MODELS_ROOT}"
echo "PYTHON=${PY}"

PRO_DIR="${MODELS_ROOT}/SoulX-FlashHead-1_3B"
W2V_DIR="${MODELS_ROOT}/wav2vec2-base-960h"
PRO_WEIGHT="${PRO_DIR}/Model_Pro/diffusion_pytorch_model.safetensors"
VAE_WEIGHT="${PRO_DIR}/VAE_Wan/Wan2.1_VAE.pth"

need_flashhead=0
if [[ ! -f "${PRO_WEIGHT}" ]] || [[ "$(stat -c%s "${PRO_WEIGHT}" 2>/dev/null || echo 0)" -lt 5000000000 ]]; then
  need_flashhead=1
fi
if [[ ! -f "${VAE_WEIGHT}" ]]; then
  need_flashhead=1
fi

export PRO_DIR W2V_DIR
if [[ "${need_flashhead}" -eq 1 ]]; then
  echo "downloading SoulX-FlashHead-1_3B (Pro + VAE_Wan only)"
  "${PY}" - <<'PY'
from huggingface_hub import snapshot_download
import os
dest = os.environ["PRO_DIR"]
snapshot_download(
    repo_id="Soul-AILab/SoulX-FlashHead-1_3B",
    local_dir=dest,
    allow_patterns=[
        "Model_Pro/*",
        "VAE_Wan/*",
        "README.md",
        "config.json",
        "*.json",
    ],
    ignore_patterns=["Model_Lite/*", "VAE_LTX/*", "assets/*"],
)
print("done flashhead Pro")
PY
  touch "${PRO_DIR}/.download_ok"
else
  echo "skip flashhead (Pro+VAE present)"
  touch "${PRO_DIR}/.download_ok"
fi

if [[ ! -f "${W2V_DIR}/pytorch_model.bin" ]] && [[ ! -f "${W2V_DIR}/model.safetensors" ]]; then
  echo "downloading wav2vec2-base-960h"
  "${PY}" - <<'PY'
from huggingface_hub import snapshot_download
import os
snapshot_download(repo_id="facebook/wav2vec2-base-960h", local_dir=os.environ["W2V_DIR"])
print("done wav2vec")
PY
  touch "${W2V_DIR}/.download_ok"
else
  echo "skip wav2vec (present)"
  touch "${W2V_DIR}/.download_ok"
fi

echo "OK"
du -sh "${MODELS_ROOT}"/* 2>/dev/null || true
ls -lh "${PRO_WEIGHT}" "${VAE_WEIGHT}" 2>/dev/null || true
