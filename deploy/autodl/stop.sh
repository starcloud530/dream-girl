#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="${DREAM_GIRL_LOG_DIR:-/root/autodl-tmp/dream-girl-logs}"

pkill -f "services.orchestrator.main" 2>/dev/null || true
pkill -f "run_gateway.py" 2>/dev/null || true
# Do not kill engine/tts by default (slow to reload). Use STOP_ALL=1.
if [[ "${STOP_ALL:-0}" == "1" ]]; then
  pkill -f "run_engine.py" 2>/dev/null || true
  pkill -f "vllm serve" 2>/dev/null || true
  fuser -k 6006/tcp 6008/tcp 6009/tcp 8091/tcp 2>/dev/null || true
else
  fuser -k 6006/tcp 6008/tcp 2>/dev/null || true
fi
echo "stopped (STOP_ALL=${STOP_ALL:-0}). logs in ${LOG_DIR}"
