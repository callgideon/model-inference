#!/usr/bin/env bash
# Install/refresh the marlin2b stack on the box: vLLM (systemd, docker),
# gateway (systemd, /opt/pytorch), Caddy (docker, TLS). Idempotent; run as root.
#   sudo INFRX_MODE=dev ./apps/infrx-api/deploy/install.sh
#
# INFRX_MODE has no default (infra/README.md §5): a default is how an unmetered pilot
# happens by accident. `preflight.py apply` reads and validates every required SSM
# parameter before it replaces /etc/marlin2b-gateway.env, and a failed read, a bad
# value or an unmet runtime prerequisite leaves the previous file byte-identical and
# restarts nothing (evidence row `O-FAILOPEN`, matrix row `M-FAILCLOSED`).
#
# Order note: pip, the unit files and the Caddyfile are installed before the env file.
# They are idempotent and inert - systemd keeps serving the old configuration until a
# restart, and the restart only happens after a successful install - so the two
# operations a failed run must never perform, replacing the env file and restarting,
# are both inside preflight.py. Running this against the pilot host needs the
# deployment lock of infra/README.md §1.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGION=${AWS_REGION:-us-east-1}
# Before pip, apt or any unit file: an unusable mode must not leave half a deploy
# behind. `${VAR:?}` only catches unset and empty, so a typo would have run everything
# below and been refused at the very end.
case "${INFRX_MODE:-}" in
  dev|test|pilot) ;;
  *) echo "INFRX_MODE must be dev, test or pilot (no default); got '${INFRX_MODE:-}'" >&2
     exit 2 ;;
esac
ENV_FILE=${ENV_FILE:-/etc/marlin2b-gateway.env}
RUNTIME_PYTHON=${RUNTIME_PYTHON:-/opt/pytorch/bin/python}
SERVE_SCRIPT=${SERVE_SCRIPT:-$here/../../../models/marlin2b/serve.sh}

"$RUNTIME_PYTHON" -m pip install -q fastapi uvicorn httpx
command -v ffprobe >/dev/null || apt-get install -y -qq ffmpeg

install -m 644 "$here/marlin2b-vllm.service" "$here/marlin2b-gateway.service" /etc/systemd/system/
systemctl daemon-reload
# `enable`, not `enable --now`: the gateway must not start before its env file exists,
# which is the fail-open case this script is being fixed for.
systemctl enable marlin2b-vllm marlin2b-gateway
# The engine reads no env file and takes ~minutes to load weights, so it is *started*
# (idempotent: a no-op if it is already up) rather than restarted. Restarting it on
# every install would kill in-flight generation for a change it cannot even see.
# This blocks until the unit is active - up to the unit's TimeoutStartSec=900 on a cold
# start - and `set -e` stops the install if the engine cannot come up, which is the
# order infra/README.md §7 asks for: each step waits for the previous readiness signal.
systemctl start marlin2b-vllm

# Only the gateway reads the env file, so only the gateway is restarted - and only
# after a validated file is in place.
"$RUNTIME_PYTHON" "$here/preflight.py" apply \
  --mode "$INFRX_MODE" --env-file "$ENV_FILE" --owner ubuntu --region "$REGION" \
  --runtime-python "$RUNTIME_PYTHON" --serve-script "$SERVE_SCRIPT" \
  --restart marlin2b-gateway

if [ "$INFRX_MODE" = pilot ]; then
  # Two statements, not `mkdir … && install …`: a command that fails on the left of
  # `&&` is a tested condition, so `set -e` does not stop the script there.
  mkdir -p /etc/caddy
  install -m 644 "$here/Caddyfile" /etc/caddy/Caddyfile
  docker rm -f caddy >/dev/null 2>&1 || true
  docker run -d --name caddy --restart unless-stopped --network host \
    -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile:ro -v caddy_data:/data -v caddy_config:/config \
    caddy@sha256:14a9c00d4e833ebc2b65d36515b37bde3b73f0b323a2663aaafc88953d8c4e3f >/dev/null  # 2.11.4
  echo "installed; check: systemctl status marlin2b-vllm marlin2b-gateway; docker logs caddy"
else
  # infra/README.md §5: a dev host answering on the pilot's DNS name is the same
  # failure as an unmetered pilot, so no public listener and no ACME account.
  echo "INFRX_MODE=$INFRX_MODE: the public Caddy site is not installed" >&2
  echo "installed; check: systemctl status marlin2b-vllm marlin2b-gateway"
fi
