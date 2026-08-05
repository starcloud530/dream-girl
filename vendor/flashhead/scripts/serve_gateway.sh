#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"
export PYTHONPATH="${ROOT}:${CYBERVERSE_MODELS_DIR}:${PYTHONPATH:-}"
CONFIG="${FLASHHEAD_CONFIG:-configs/t_v2_face256.yaml}"
exec "${PYTHON_BIN}" -m serve.gateway --config "${CONFIG}" \
  --host "${FLASHHEAD_GATEWAY_HOST}" \
  --port "${FLASHHEAD_GATEWAY_PORT}"
