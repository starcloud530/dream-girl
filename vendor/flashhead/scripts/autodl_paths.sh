# AutoDL path conventions (Dream Girl / FlashHead)
#
#   autodl-tmp  — code, venv, logs
#   autodl-fs   — model weights only

export AUTODL_TMP="${AUTODL_TMP:-/root/autodl-tmp}"
export AUTODL_FS="${AUTODL_FS:-/root/autodl-fs}"
export AUTODL_FS_MODELS="${AUTODL_FS_MODELS:-${AUTODL_FS}/models}"

# Prefer Dream Girl vendor path when present
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
_FH_DEFAULT="$(cd "${_SCRIPT_DIR}/.." && pwd)"
export LIGHTNING_DIR="${LIGHTNING_DIR:-${_FH_DEFAULT}}"
export MODELS_ROOT="${MODELS_ROOT:-${DREAM_GIRL_MODELS_ROOT:-${AUTODL_FS_MODELS}}/flashhead}"
export CYBER_GF_DATA_ROOT="${CYBER_GF_DATA_ROOT:-${DREAM_GIRL_ROOT:-${AUTODL_TMP}/dream-girl}}"
export CYBERVERSE_MODELS_DIR="${CYBERVERSE_MODELS_DIR:-${LIGHTNING_DIR}/vendor/flash_head_models}"
export CYBERVERSE_STUB_DIR="${CYBERVERSE_STUB_DIR:-${LIGHTNING_DIR}/stubs/cyberverse}"
export PYTHONPATH="${LIGHTNING_DIR}:${CYBERVERSE_MODELS_DIR}:${CYBERVERSE_STUB_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

export CONDA_PY="${CONDA_PY:-/root/miniconda3/bin/python}"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "${CONDA_PY}" ]]; then
    PYTHON_BIN="${CONDA_PY}"
  else
    PYTHON_BIN="${LIGHTNING_DIR}/.venv/bin/python"
  fi
fi
export PYTHON_BIN
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export PATH="/root/miniconda3/bin:${CUDA_HOME}/bin:${PATH:-}"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

export FLASHHEAD_GATEWAY_HOST="${FLASHHEAD_GATEWAY_HOST:-0.0.0.0}"
export FLASHHEAD_GATEWAY_PORT="${FLASHHEAD_GATEWAY_PORT:-6008}"
export FLASHHEAD_ENGINE_HOST="${FLASHHEAD_ENGINE_HOST:-127.0.0.1}"
export FLASHHEAD_ENGINE_PORT="${FLASHHEAD_ENGINE_PORT:-6009}"

export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
