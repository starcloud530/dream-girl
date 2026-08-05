#!/usr/bin/env bash
# 在 CUDA >=12.8 机器上源码编译 SageAttention 2.x（API: from sageattention import sageattn）
# SageAttention3（sageattn3_blackwell）可选第二步，API 不同需改 flash_head。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"

export PATH="/usr/local/cuda/bin:${PATH:-}"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-12.0}"
export MAX_JOBS="${MAX_JOBS:-4}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  # standalone venv
  PYTHON_BIN="${ROOT}/.venv/bin/python"
fi
: "${PYTHON_BIN:?PYTHON_BIN missing — run setup_env.sh first}"

NVCC_VER="$("${CUDA_HOME}/bin/nvcc" --version | grep -oE 'release [0-9.]+' | awk '{print $2}')"
echo "nvcc ${NVCC_VER} CUDA_HOME=${CUDA_HOME} arch=${TORCH_CUDA_ARCH_LIST}"
python_major_minor="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
echo "python ${python_major_minor} $($("${PYTHON_BIN}" -c 'import torch; print(torch.__version__, torch.version.cuda)'))"

SRC="${SAGE_SRC:-/root/autodl-tmp/src/SageAttention}"
if [[ ! -f "${SRC}/setup.py" && ! -f "${SRC}/pyproject.toml" ]]; then
  echo "ERROR: SageAttention source missing at ${SRC}" >&2
  echo "  本机下载后 rsync 到该目录（AutoDL 常无法访问 GitHub）" >&2
  exit 1
fi

"${PYTHON_BIN}" -m pip uninstall -y sageattention 2>/dev/null || true
echo "==> build SageAttention 2.x from ${SRC}"
cd "${SRC}"
"${PYTHON_BIN}" -m pip install --no-build-isolation -v . 2>&1 | tee /tmp/sage2_build.log | tail -50

# 必须离开源码目录，否则会 import 到本地包导致 circular import
cd /tmp
"${PYTHON_BIN}" - <<'PY'
import importlib.metadata as m
import torch
from sageattention import sageattn
print("sageattention", m.version("sageattention"))
q = k = v = torch.randn(1, 12, 256, 64, device="cuda", dtype=torch.bfloat16)
o = sageattn(q, k, v)
print("sageattn OK", tuple(o.shape), o.dtype)
PY

# 可选：SageAttention3（Blackwell FP4）— 失败不阻断
if [[ "${BUILD_SAGE3:-0}" == "1" ]]; then
  echo "==> build SageAttention3 blackwell"
  cd "${SRC}/sageattention3_blackwell"
  "${PYTHON_BIN}" -m pip install --no-build-isolation -v . 2>&1 | tee /tmp/sage3_build.log | tail -40 || {
    echo "WARN: SageAttention3 build failed (see /tmp/sage3_build.log)"
  }
fi

echo "OK SageAttention built"
