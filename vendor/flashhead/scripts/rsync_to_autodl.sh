#!/usr/bin/env bash
# Sync vendor/flashhead to a remote AutoDL box.
# Requires SSH target via env file or HOST+PORT (no baked-in instance defaults).
set -euo pipefail
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${LOCAL_ROOT}/scripts/autodl_paths.sh"

ENV_FILE="${DREAM_GIRL_SSH_ENV:-${CYBER_GF_SSH_ENV:-}}"
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
fi
: "${HOST:?Set HOST (or source DREAM_GIRL_SSH_ENV with HOST/PORT[/PASS])}"
: "${PORT:?Set PORT (SSH port of your AutoDL instance)}"
REMOTE_DIR="${REMOTE_LIGHTNING_DIR:-${LIGHTNING_DIR}}"
export SSHPASS="${PASS:-${SSHPASS:-}}"

DG_ROOT="$(cd "${LOCAL_ROOT}/../.." && pwd)"
ASSETS_SRC="${DREAM_GIRL_ASSETS:-${DG_ROOT}/assets/character}"

RSH_FILE="/tmp/rsync-sshpass-flashhead-$$.sh"
if [[ -n "${SSHPASS}" ]] && command -v sshpass >/dev/null 2>&1; then
  cat > "${RSH_FILE}" <<EOF
#!/bin/bash
exec sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -p ${PORT} "\$@"
EOF
else
  cat > "${RSH_FILE}" <<EOF
#!/bin/bash
exec ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -p ${PORT} "\$@"
EOF
fi
chmod +x "${RSH_FILE}"
trap 'rm -f "${RSH_FILE}"' EXIT

echo "rsync -> root@${HOST}:${PORT}:${REMOTE_DIR}"
"${RSH_FILE}" "root@${HOST}" "mkdir -p ${REMOTE_DIR} ${MODELS_ROOT}"

rsync -az --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'results/' \
  --exclude '.git' \
  --exclude 'vendor' \
  -e "${RSH_FILE}" \
  "${LOCAL_ROOT}/" "root@${HOST}:${REMOTE_DIR}/"

if [[ -d "${ASSETS_SRC}" ]]; then
  REMOTE_ASSETS="${DREAM_GIRL_ROOT:-/root/autodl-tmp/dream-girl}/assets/character"
  "${RSH_FILE}" "root@${HOST}" "mkdir -p ${REMOTE_ASSETS}"
  rsync -az -e "${RSH_FILE}" "${ASSETS_SRC}/" "root@${HOST}:${REMOTE_ASSETS}/" || true
fi

echo "OK  remote=${REMOTE_DIR}  weights=${MODELS_ROOT}"
