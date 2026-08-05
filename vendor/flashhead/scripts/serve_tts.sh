#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"

export QWEN_TTS_CONFIG="${QWEN_TTS_CONFIG:-configs/qwen_tts.yaml}"
export QWEN_TTS_HOST="${QWEN_TTS_HOST:-0.0.0.0}"
export QWEN_TTS_PORT="${QWEN_TTS_PORT:-6010}"

"${PYTHON_BIN}" -u "${ROOT}/scripts/run_tts_server.py" \
  --config "${QWEN_TTS_CONFIG}" \
  --host "${QWEN_TTS_HOST}" --port "${QWEN_TTS_PORT}"
