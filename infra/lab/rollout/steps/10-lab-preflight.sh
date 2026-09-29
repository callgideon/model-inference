#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L1 (box, read-only): what of the Lab is on this box, by NAME.
#   infra/rollout/ssm.sh infra/lab/rollout/steps/10-lab-preflight.sh RELEASE=<40 hex>
# Exit 2 = the checkout is not RELEASE; 3 = BLOCKED, the release carries no Lab rollout files.
# Prints: the switch (marker), each Lab env file's names, each Lab unit's installed/enabled/
# active state, and the App's two /readyz (the Lab must never change them).
set -euo pipefail
STEP=10-lab-preflight
repo=${REPO:-/home/ubuntu/model-inference}
[ -f "$repo/infra/lab/rollout/lib.sh" ] || { echo "BLOCKED: $repo carries no infra/lab/rollout (a pre-Lab release)" >&2; exit 3; }
. "$repo/infra/lab/rollout/lib.sh"
at_release
say "release $RELEASE; Lab image: $(cat "$IMAGE_FILE" 2>/dev/null || echo none)"
say "control switch (marker): $([ -e "$MARKER" ] && echo ON || echo off)"
for file in "$CONTROL_ENV" "$LAB_ETC"/*.env; do
  [ -f "$file" ] && say "env $file: $(cut -d= -f1 "$file" | tr '\n' ' ')"
done
for role in control "${ROLES[@]}"; do
  unit=$(basename "$(unit_file "$role")")
  installed=$([ -f "$(unit_file "$role")" ] && echo installed || echo absent)
  say "$unit: $installed, $(systemctl is-enabled "$unit" 2>/dev/null || echo disabled), $(systemctl is-active "$unit" 2>/dev/null || echo inactive)"
done
for port in 8001 8002; do
  say "App :$port/readyz $(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$port/readyz" || echo down)"
done
