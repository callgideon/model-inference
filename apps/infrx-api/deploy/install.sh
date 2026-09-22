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
: "${INFRX_MODE:?INFRX_MODE must be set explicitly: dev, test or pilot (no default)}"
ENV_FILE=${ENV_FILE:-/etc/marlin2b-gateway.env}
RUNTIME_PYTHON=${RUNTIME_PYTHON:-/opt/pytorch/bin/python}
SERVE_SCRIPT=${SERVE_SCRIPT:-$here/../../../models/marlin2b/serve.sh}

"$RUNTIME_PYTHON" -m pip install -q fastapi uvicorn httpx
command -v ffprobe >/dev/null || apt-get install -y -qq ffmpeg

install -m 644 "$here/marlin2b-vllm.service" "$here/marlin2b-gateway.service" /etc/systemd/system/
# `enable`, not `enable --now`: a unit started before the env file exists is the
# fail-open case this script is being fixed for. preflight.py restarts both units
# after it has installed a validated file.
systemctl daemon-reload
systemctl enable marlin2b-vllm marlin2b-gateway

"$RUNTIME_PYTHON" "$here/preflight.py" apply \
  --mode "$INFRX_MODE" --env-file "$ENV_FILE" --owner ubuntu --region "$REGION" \
  --runtime-python "$RUNTIME_PYTHON" --serve-script "$SERVE_SCRIPT" \
  --restart marlin2b-vllm --restart marlin2b-gateway

if [ "$INFRX_MODE" = pilot ]; then
  mkdir -p /etc/caddy && install -m 644 "$here/Caddyfile" /etc/caddy/Caddyfile
  docker rm -f caddy >/dev/null 2>&1 || true
  docker run -d --name caddy --restart unless-stopped --network host \
    -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile:ro -v caddy_data:/data -v caddy_config:/config \
    caddy:2 >/dev/null
  echo "installed; check: systemctl status marlin2b-vllm marlin2b-gateway; docker logs caddy"
else
  # infra/README.md §5: a dev host answering on the pilot's DNS name is the same
  # failure as an unmetered pilot, so no public listener and no ACME account.
  echo "INFRX_MODE=$INFRX_MODE: the public Caddy site is not installed" >&2
  echo "installed; check: systemctl status marlin2b-vllm marlin2b-gateway"
fi
