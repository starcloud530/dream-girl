#!/usr/bin/env bash
# Ban real AutoDL/seetacloud fingerprints, absolute /Users/ paths, and sk- API keys.
# Template hosts like u<uid>-<instance>.<region>.seetacloud.com are allowed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

# Patterns aligned with AGENTS.md §4.3 (+ broader /Users/ and numeric seetacloud hosts).
# - sk-… : suspected API keys (length ≥20 after sk-)
# - 785020… / uuN-N. / uNNNN-N.region.seetacloud.com : real instance fingerprints
# - PORT=53xxx : fixed AutoDL SSH ports
# - /Users/ : local absolute paths (must not land in the public tree)
PATTERN='sk-[a-zA-Z0-9]{20,}|785020[0-9]+|uu[0-9]+-[0-9]+\.|u[0-9]+-[0-9]+\.[a-z0-9]+\.seetacloud\.com|PORT=53[0-9]{3}|/Users/'

# Self / docs that only *document* the ban rules (contain the regex text or /Users/ examples).
EXCLUDES=(
  --glob '!.git/**'
  --glob '!.github/**'
  --glob '!AGENTS.md'
)

if ! command -v rg >/dev/null 2>&1; then
  echo "ERROR: ripgrep (rg) is required" >&2
  exit 2
fi

set +e
HITS="$(rg -n --hidden "${EXCLUDES[@]}" -e "${PATTERN}" . 2>/dev/null)"
RC=$?
set -e

# rg: 0=match, 1=no match, 2=error
if [[ "${RC}" -eq 2 ]]; then
  echo "ERROR: ripgrep failed" >&2
  exit 2
fi

if [[ "${RC}" -eq 0 && -n "${HITS}" ]]; then
  echo "ERROR: forbidden secret / instance fingerprint / absolute path detected:" >&2
  echo "${HITS}" >&2
  exit 1
fi

echo "secret_scan: OK (no banned fingerprints)"
