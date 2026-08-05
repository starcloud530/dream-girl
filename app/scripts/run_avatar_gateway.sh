#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
export PYTHONPATH="${ROOT}"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -e . -q
fi
exec .venv/bin/python -m services.avatar_gateway.main
