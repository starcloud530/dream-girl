#!/usr/bin/env bash
# 从 ModelScope 下载 RealESRGAN x2（国内快）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/autodl_paths.sh"

OUT="${AUTODL_FS_MODELS}/upscale_models/RealESRGAN_x2.pth"
CACHE="${AUTODL_TMP}/modelscope_cache"
mkdir -p "$(dirname "$OUT")" "${CACHE}"
export MODELSCOPE_CACHE="${CACHE}"

"${PYTHON_BIN}" <<'PY'
import glob, shutil
from pathlib import Path
from modelscope.hub.snapshot_download import snapshot_download

dst = Path("/root/autodl-fs/models/upscale_models/RealESRGAN_x2.pth")
cache = snapshot_download("AI-ModelScope/Real-ESRGAN", allow_file_pattern="RealESRGAN_x2.pth")
src = next(iter(glob.glob(cache + "/**/RealESRGAN_x2.pth", recursive=True)))
shutil.copy2(src, dst)
print(f"OK {dst} ({dst.stat().st_size} bytes)")
PY

ls -lh "${OUT}"
