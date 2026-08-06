#!/usr/bin/env bash
# optional / Mac debug — NOT the production entry (use deploy/autodl/).
# SSH tunnel: local 6006/6008 → remote Orchestrator / Avatar Gateway
# Requires DREAM_GIRL_SSH_ENV with HOST/PORT[/PASS]. No instance defaults.
# CYBER_GF_SSH_ENV is a deprecated alias for the same env-file path.
set -euo pipefail
ENV_FILE="${DREAM_GIRL_SSH_ENV:-${CYBER_GF_SSH_ENV:-}}"
if [[ -z "${ENV_FILE}" || ! -f "${ENV_FILE}" ]]; then
  echo "missing SSH env. Export DREAM_GIRL_SSH_ENV=/path/to/ssh.env (HOST/PORT[/PASS])"
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a
: "${HOST:?HOST missing}"
: "${PORT:?PORT missing}"
export SSHPASS="${PASS:-${SSHPASS:-}}"

pkill -f "sshpass.*-L 6006:127.0.0.1:6006" 2>/dev/null || true
sleep 0.5

exec sshpass -e ssh -N \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -L 6006:127.0.0.1:6006 \
  -L 6008:127.0.0.1:6008 \
  -p "${PORT}" "root@${HOST}"
