#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"
CONFIG="${1:-configs/t1_compile.yaml}"
"${PYTHON_BIN}" -m lightning.benchmark --config "${CONFIG}"
