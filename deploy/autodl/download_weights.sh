#!/usr/bin/env bash
# Dream Girl — download FlashHead + Qwen3-TTS 0.6B weights (re-runnable)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FH="${ROOT}/vendor/flashhead"

# Respect DREAM_GIRL_MODELS_ROOT / MODELS_ROOT (weights stay off git)
MODELS_BASE="${DREAM_GIRL_MODELS_ROOT:-${MODELS_ROOT:-/root/autodl-fs/models}}"
# If MODELS_ROOT was already the flashhead subdir, prefer DREAM_GIRL_* / strip later via vendor autodl_paths
export DREAM_GIRL_MODELS_ROOT="${DREAM_GIRL_MODELS_ROOT:-${MODELS_BASE}}"
export MODELS_ROOT="${DREAM_GIRL_MODELS_ROOT}"

if [[ "${SKIP_DOWNLOAD:-0}" == "1" ]]; then
  echo "SKIP_DOWNLOAD=1 — skipping FlashHead + Qwen-TTS weight download"
  exit 0
fi

mkdir -p "${DREAM_GIRL_MODELS_ROOT}"
chmod +x "${FH}/scripts/"*.sh 2>/dev/null || true

echo "== Dream Girl download weights =="
echo "DREAM_GIRL_MODELS_ROOT=${DREAM_GIRL_MODELS_ROOT}"
echo "HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com} (override for HF mirror)"

(cd "${FH}" && bash scripts/download_weights.sh)
(cd "${FH}" && bash scripts/download_qwen_tts.sh)

echo "Download finished. Re-run this script after a network drop to resume/skip-present."
