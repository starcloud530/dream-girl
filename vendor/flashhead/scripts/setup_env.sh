#!/usr/bin/env bash
# FlashHead 环境：优先用镜像 conda base（已带 torch cu128 / 5090），只补缺包
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"

cd "${ROOT}"
bash "${SCRIPT_DIR}/link_vendor.sh" || true

export PATH="/root/miniconda3/bin:/usr/local/cuda/bin:${PATH:-}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: PYTHON_BIN 不可用: ${PYTHON_BIN}" >&2
  exit 1
fi

# 若指向本仓库空 .venv，改回 conda
if ! "${PYTHON_BIN}" -c "import torch" 2>/dev/null; then
  if [[ -x "${CONDA_PY}" ]] && "${CONDA_PY}" -c "import torch" 2>/dev/null; then
    echo "WARN: ${PYTHON_BIN} 无 torch，改用 conda: ${CONDA_PY}"
    PYTHON_BIN="${CONDA_PY}"
    export PYTHON_BIN
  fi
fi

echo "==> Python: $("${PYTHON_BIN}" --version) @ ${PYTHON_BIN}"
"${PYTHON_BIN}" -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"

"${PYTHON_BIN}" -m pip install -U pip wheel -q
"${PYTHON_BIN}" -m pip install -r "${ROOT}/requirements.txt" -q
"${PYTHON_BIN}" -m pip install loguru einops diffusers transformers accelerate omegaconf mediapipe -q || true

"${PYTHON_BIN}" -c "import xfuser" 2>/dev/null || \
  "${PYTHON_BIN}" -m pip install xfuser -q || echo "WARN: xfuser install failed"

if ! "${PYTHON_BIN}" -c "from sageattention import sageattn" 2>/dev/null; then
  echo "WARN: sageattention 未就绪。CUDA 12.8+ 请："
  echo "  bash scripts/build_sageattention.sh"
fi

# 方便本仓库脚本：.venv → 当前 PYTHON（conda 时做 symlink）
if [[ ! -e "${ROOT}/.venv" ]]; then
  ln -sfn "$(dirname "$(dirname "${PYTHON_BIN}")")" "${ROOT}/.venv"
  echo "linked ${ROOT}/.venv -> $(readlink "${ROOT}/.venv")"
fi

echo "OK env ${PYTHON_BIN}"
