#!/usr/bin/env bash
# optional / Mac debug — NOT the production entry (use deploy/autodl/).
# Sync app/ to a remote AutoDL box (prefer git pull on GPU).
# Requires DREAM_GIRL_SSH_ENV with HOST/PORT[/PASS].
# CYBER_GF_SSH_ENV is a deprecated alias for the same env-file path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${DREAM_GIRL_SSH_ENV:-${CYBER_GF_SSH_ENV:-}}"
[[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]] || {
  echo "missing SSH env file. Export DREAM_GIRL_SSH_ENV=/path/to/ssh.env"
  echo "  (file must define HOST= PORT= and optionally PASS=)"
  exit 1
}
# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a
: "${HOST:?HOST missing in ${ENV_FILE}}"
: "${PORT:?PORT missing in ${ENV_FILE}}"
export SSHPASS="${PASS:-${SSHPASS:-}}"
RSH="/tmp/rsync-sshpass-dream-girl-$$.sh"
printf '%s\n' '#!/bin/bash' "exec sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -p ${PORT} \"\$@\"" > "${RSH}"
chmod +x "${RSH}"
trap 'rm -f "${RSH}"' EXIT

REMOTE_DIR="${REMOTE_DIR:-/root/autodl-tmp/dream-girl/app}"
echo "rsync ${ROOT}/ -> root@${HOST}:${REMOTE_DIR}/"
rsync -az --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.egg-info' --exclude 'out.pcm' --exclude '.pytest_cache' \
  --exclude 'tmp/' --exclude 'config/app.yaml' \
  -e "${RSH}" \
  "${ROOT}/" "root@${HOST}:${REMOTE_DIR}/"

sshpass -e ssh -p "${PORT}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
  "root@${HOST}" "bash -s" <<REMOTE
set -euo pipefail
cd "${REMOTE_DIR}"
if [[ -f config/app.autodl.yaml ]]; then
  cp -f config/app.autodl.yaml config/app.yaml
  echo "applied config/app.autodl.yaml -> app.yaml"
fi
REMOTE

echo "OK synced"
