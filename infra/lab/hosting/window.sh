#!/usr/bin/env bash
# AP-05 box step (a COORDINATOR WINDOW only; infra/lab/hosting/README.md): open or close the
# window in which a private candidate engine may hold the pilot's one L40S.
#   infra/rollout/ssm.sh infra/lab/hosting/window.sh STATE=open RELEASE=<40 hex> PORT=8100
#   infra/rollout/ssm.sh infra/lab/hosting/window.sh STATE=close RELEASE=<40 hex>
# open  refuses (exit 3, nothing changed) unless the edge is in maintenance (95-maintenance:
#       the gateway unit is inactive) and PORT is a candidate port (8100-8199, never 8000);
#       then stops marlin2b-vllm.service (serve.sh pins --gpu-memory-utilization 0.90: two
#       engines cannot share the GPU), waits for the GPU to hold no process, installs the
#       infrx-candidate@.service template from RELEASE and the controller's directories
#       (/etc/infrx-lab/hosting for the per-port env files, $HOSTING_ROOT for installed
#       models), all owned by ubuntu.
# close stops every infrx-candidate@<port> unit and its marlin2b-<port> container (never
#       port 8000's), removes the env files and installed models, starts
#       marlin2b-vllm.service and waits for its /health; the edge reopens with 56-resume.
# Idempotent. Names only in output; no secret is read.
set -euo pipefail
STEP=hosting-window
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/rollout/box-lib.sh"
etc=$R/etc/infrx-lab/hosting
units=$R/etc/systemd/system
models=${HOSTING_ROOT:-$R/opt/dlami/nvme/hosting}
at_release
case "${STATE:-}" in
  open)
    port=${PORT:-}
    [[ $port =~ ^81[0-9][0-9]$ ]] || die 3 "PORT must be a candidate port 8100-8199 (8000 is the serving engine's)"
    if systemctl is-active --quiet marlin2b-gateway.service; then
      die 3 "the gateway is serving: open the maintenance window first (95-maintenance)"
    fi
    systemctl stop marlin2b-vllm.service
    for _ in $(seq "${GPU_FREE_S:-120}"); do
      [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && break
      sleep 1
    done
    [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] \
      || die 4 "the GPU still holds a process after marlin2b-vllm stopped"
    install -m 0644 "$repo/infra/lab/hosting/infrx-candidate@.service" "$units/infrx-candidate@.service"
    install -d -o ubuntu -g ubuntu -m 0750 "$etc" "$models"
    systemctl daemon-reload
    say "window open: serving engine stopped, GPU free, candidate port $port; run the hosting controller (README step 3)"
    ;;
  close)
    for env in "$etc"/*.env; do
      [ -e "$env" ] || continue
      port=$(basename "$env" .env)
      [ "$port" = 8000 ] && continue
      systemctl stop "infrx-candidate@$port.service" || true
      docker rm -f "marlin2b-$port" >/dev/null 2>&1 || true
      rm -f "$env"
      say "candidate $port stopped"
    done
    rm -rf "${models:?}"/*
    systemctl start marlin2b-vllm.service
    for _ in $(seq "${READY_S:-900}"); do
      curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health && break
      sleep "${READY_SLEEP_S:-1}"
    done
    curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health \
      || die 4 "marlin2b-vllm did not answer /health: do not reopen the edge"
    say "window closed: serving engine healthy; reopen the edge with 56-resume"
    ;;
  *) die 2 "STATE must be open or close" ;;
esac
