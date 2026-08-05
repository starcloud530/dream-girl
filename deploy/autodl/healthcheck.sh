#!/usr/bin/env bash
# Dream Girl — health summary
set -euo pipefail

check() {
  local name="$1" url="$2"
  local code
  code=$(curl -sf -m 3 -o /tmp/dg-health.body -w "%{http_code}" "$url" 2>/dev/null || echo "down")
  echo "${name}: ${code}"
  if [[ "${code}" == "200" ]]; then
    head -c 240 /tmp/dg-health.body 2>/dev/null; echo
  fi
}

echo "== Dream Girl health =="
check "orchestrator :6006" "http://127.0.0.1:6006/v1/health"
check "flashhead_gw :6008" "http://127.0.0.1:6008/v1/health"
check "flashhead_eng :6009" "http://127.0.0.1:6009/v1/health"
check "qwen_tts :8091" "http://127.0.0.1:8091/v1/models"

echo ""
echo "Public URL (AutoDL template):"
echo "  https://u<uid>-<instance>.<region>.seetacloud.com:8443/"
echo "  (custom service must map to container port 6006)"
