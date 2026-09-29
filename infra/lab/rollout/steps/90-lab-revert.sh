#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook R (box): take the whole Lab off this box, switches first.
#   infra/rollout/ssm.sh infra/lab/rollout/steps/90-lab-revert.sh
# In order: every role OFF (unit disabled and stopped, its env file - the switch - removed), the
# control service OFF (disabled, marker and env file removed), the Lab site removed from the
# edge and the edge reloaded, then the App's /readyz. Units and the Lab image stay, inert.
# Hosted Lab migrations are NOT reverted (additive, R151; rollback targets:
# infra/rollout/known-good.json). Idempotent. Exit 1 = the App does not answer ready afterwards
# (it never depended on the Lab: look at the App, `journalctl -u marlin2b-gateway -u infrx-worker`).
set -euo pipefail
STEP=90-lab-revert
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
for role in "${ROLES[@]}"; do
  systemctl disable --now "infrx-lab-$role.service" 2>/dev/null || true
  rm -f "${LAB_ETC:?}/${role:?}.env"
done
say "every Lab role OFF"
systemctl disable --now infrx-lab-control.service 2>/dev/null || true
rm -f "${MARKER:?}" "${CONTROL_ENV:?}"
say "Lab control OFF"
site=$R/etc/caddy/lab/lab-control.caddy
if [ -f "$site" ]; then
  rm -f "${site:?}"
  docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock
  say "Lab site removed; edge reloaded"
fi
say "hosted Lab migrations are not reverted (additive, R151): see the runbook's rollback notes"
READY_S=${READY_S:-30}
for port in 8001 8002; do
  ready "$port" || die 1 "the App :$port/readyz does not answer after the Lab revert"
done
say "Lab reverted; the App answers ready on :8001 and :8002"
