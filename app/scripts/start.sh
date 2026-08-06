#!/usr/bin/env bash
# optional / Mac debug — NOT the production entry (use deploy/autodl/).
# 启动可真实测试的产品（Mac orchestrator）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
export PYTHONPATH="${ROOT}"
unset CYBER_GF_USE_MOCK

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -e . -q
fi

# kill stale
if lsof -iTCP:8011 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8011 已被占用，先结束旧进程…"
  pkill -f "services.orchestrator.main" 2>/dev/null || true
  sleep 1
fi

# Portraits: prefer repo assets/character (synced by deploy scripts)
if [[ ! -f assets/character/xiaoya-v1-sit.jpg ]]; then
  ROOT_ASSETS="$(cd "${ROOT}/../assets/character" 2>/dev/null && pwd || true)"
  if [[ -n "${ROOT_ASSETS}" && -f "${ROOT_ASSETS}/xiaoya-v1-sit.jpg" ]]; then
    mkdir -p assets/character
    rsync -a "${ROOT_ASSETS}/" assets/character/
  else
    echo "WARN: missing assets/character/xiaoya-v1-sit.jpg"
  fi
fi

echo "启动编排器（见 config app.yaml 端口，默认 6006）"
echo "日志请自行重定向，例如: … > /tmp/dream-girl-orch.log 2>&1"
exec .venv/bin/python -m services.orchestrator.main
