#!/usr/bin/env bash
# 将 CyberVerse models/ 链到 vendor/flash_head_models（含 flash_head 包）
# 远端若已 rsync 过 vendor/flash_head_models/flash_head，则跳过
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${FLASHHEAD_SRC:-${ROOT}/../github/CyberVerse/models}"
DST="${ROOT}/vendor/flash_head_models"

if [[ -d "${DST}/flash_head" ]]; then
  echo "vendor already present: ${DST}/flash_head"
  exit 0
fi
if [[ ! -d "${SRC}/flash_head" ]]; then
  echo "ERROR: flash_head not found at ${SRC}/flash_head (and no vendor copy)" >&2
  exit 1
fi
mkdir -p "${ROOT}/vendor"
rm -rf "${DST}"
ln -sfn "$(cd "${SRC}" && pwd)" "${DST}"
echo "linked ${DST} -> $(readlink "${DST}")"
