#!/usr/bin/env bash
# Engine(:6009) + Gateway(:6008) 一键拉起
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "${SCRIPT_DIR}/start_engine_autodl.sh"
bash "${SCRIPT_DIR}/start_gateway_autodl.sh"
echo "stack ready: engine=:6009 gateway=:6008"
