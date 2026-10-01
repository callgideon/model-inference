#!/usr/bin/env bash
set -euo pipefail; echo "launch-v1.sh is deprecated (removed next wave): use infra/lab/rollout/lab-release.sh (vercel is now web)" >&2
a=${1:-}; exec bash "$(dirname "$0")/lab-release.sh" "${a/#vercel/web}" "${@:2}"
