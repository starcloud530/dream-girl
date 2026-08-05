#!/usr/bin/env bash
# 分辨率梯子：384 / 320 / 256（需先停 Gateway 以免抢显存）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"
cd "${ROOT}"
mkdir -p results/v1
LOG_DIR="${DREAM_GIRL_LOG_DIR:-${AUTODL_TMP}/dream-girl-logs}"
mkdir -p "${LOG_DIR}"

echo "== stop gateway =="
pkill -f "run_gateway.py" 2>/dev/null || true
pkill -f "serve.gateway" 2>/dev/null || true
fuser -k "${FLASHHEAD_GATEWAY_PORT:-6008}/tcp" 2>/dev/null || true
sleep 2

for cfg in configs/t1_384.yaml configs/t1_320.yaml configs/t1_256.yaml; do
  echo ""
  echo "======== BENCH ${cfg} ========"
  "${PYTHON_BIN}" -m lightning.benchmark --config "${cfg}" \
    2>&1 | tee "${LOG_DIR}/bench_$(basename "${cfg}" .yaml).log"
done

echo ""
echo "== summary =="
"${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import yaml
rows=[]
for p in sorted(Path("results/v1").glob("t1_*.yaml")):
    d=yaml.safe_load(p.read_text())
    if not d: continue
    rows.append((d.get("tier"), d.get("height"), d.get("median_rtp"), d.get("median_eff_fps"), p.name))
print(f"{'tier':12} {'H':>4} {'RTP':>8} {'eff_fps':>8}  file")
for t,h,r,f,n in rows:
    print(f"{str(t):12} {h:>4} {float(r):>8.4f} {float(f):>8.2f}  {n}")
PY
