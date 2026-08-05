#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for c in t0_baseline t1_compile t2_512; do
  echo "======== ${c} ========"
  bash "${SCRIPT_DIR}/run_bench.sh" "configs/${c}.yaml" || echo "WARN: ${c} failed"
done
