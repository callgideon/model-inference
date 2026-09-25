#!/usr/bin/env bash
# Read-only: the hosted pooler's connection budget for what is INSTALLED on the box
# (infra/runbooks/pool_budget.py). The env file reaches the runtime image on stdin (no mount,
# no argument), and the script prints knob names, numbers and the DSN's pooler mode only.
# Exit 1 = the installed configuration can exhaust the pooler (the EMAXCONNSESSION of
# 2026-09-24). Optional: SET="DATABASE_POOL_MAX_SIZE=6 ..." evaluates a proposed change.
set -euo pipefail
repo=/home/ubuntu/model-inference
for need in infra/runbooks/pool_budget.py; do  # absent at the pre-I8 known-good targets (bda1586, 4226315)
  [ -e "$repo/$need" ] || { echo "BLOCKED: this step needs an I8+ checkout (missing $need)" >&2; exit 3; }
done
env_file=/etc/marlin2b-gateway.env
image=$(sed -n 's/^INFRX_IMAGE=//p' "$env_file")
[[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "no INFRX_IMAGE in $env_file" >&2; exit 2; }
sets=()
for pair in ${SET:-}; do sets+=(--set "$pair"); done
docker run --rm -i --network none -v "$repo:/repo:ro" "$image" \
  python /repo/infra/runbooks/pool_budget.py --env-file /dev/stdin \
  --units /repo/apps/infrx-api/deploy "${sets[@]}" < "$env_file"
