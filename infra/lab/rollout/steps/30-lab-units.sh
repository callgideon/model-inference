#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L4 (box): install every Lab unit from the checked-out RELEASE
# (apps/infrx-api/deploy/lab/*/) into systemd, and reload. Enables and starts NOTHING: each unit
# is conditioned on its switch (the control marker, a role's /etc/infrx-lab/<role>.env).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/30-lab-units.sh RELEASE=<40 hex>
# Idempotent. Exit 2 = not RELEASE. Rollback: 90-lab-revert.sh (the units stay, inert).
set -euo pipefail
STEP=30-lab-units
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
at_release
install -d -m 0755 "$UNIT_DIR"
for unit in "$LAB_UNITS"/*/infrx-lab-*.service "$LAB_UNITS"/*/infrx-lab-*.timer; do
  [ -f "$unit" ] || continue
  install -m 0644 "$unit" "$UNIT_DIR/$(basename "$unit")"
  say "installed $(basename "$unit")"
done
systemctl daemon-reload
say "units installed; none enabled (each waits for its switch)"
