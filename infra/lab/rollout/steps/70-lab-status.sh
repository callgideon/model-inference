#!/usr/bin/env bash
# LAB-DEPLOY-PREP step L5d (box, read-only): why a Lab unit is not ready — its unit state, the
# container, and the last journal lines (lines that could carry a value are dropped by NAME).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/70-lab-status.sh RELEASE=<40 hex> [UNIT=infrx-lab-control]
set -euo pipefail
STEP=70-lab-status
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
at_release
unit=${UNIT:-infrx-lab-control}
say "$unit: $(systemctl is-enabled "$unit" 2>/dev/null || true) / $(systemctl is-active "$unit" 2>/dev/null || true)"
systemctl show "$unit" -p ExecMainStatus -p NRestarts -p ActiveEnterTimestamp --no-pager || true
docker ps -a --filter "name=$unit" --format '{{.Names}} {{.Status}} {{.Image}}' || true
say "journal (last 60 lines; value-bearing lines dropped)"
journalctl -u "$unit" -n 60 --no-pager -o short-iso 2>/dev/null | grep -viE 'password|secret|anon_key|bearer|DATABASE_URL=|postgres(ql)?://' || true
