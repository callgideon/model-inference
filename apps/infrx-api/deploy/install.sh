#!/usr/bin/env bash
# Install/refresh the marlin2b stack on the box: vLLM (systemd, docker),
# gateway (systemd, /opt/pytorch), Caddy (docker, TLS). Idempotent; run as root.
#   sudo ./apps/infrx-api/deploy/install.sh
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGION=${AWS_REGION:-us-east-1}

# gateway secrets from SSM, never in the repo
key=$(aws ssm get-parameter --name /model-inference/marlin2b_api_key --with-decryption --region "$REGION" --query Parameter.Value --output text)
install -m 600 -o ubuntu /dev/null /etc/marlin2b-gateway.env
printf 'GATEWAY_API_KEY=%s\nMODEL_ID=nemostation/marlin-2b\nMAX_INFLIGHT=16\n' "$key" > /etc/marlin2b-gateway.env

/opt/pytorch/bin/pip install -q fastapi uvicorn httpx
command -v ffprobe >/dev/null || apt-get install -y -qq ffmpeg

install -m 644 "$here/marlin2b-vllm.service" "$here/marlin2b-gateway.service" /etc/systemd/system/
mkdir -p /etc/caddy && install -m 644 "$here/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now marlin2b-vllm marlin2b-gateway
systemctl restart marlin2b-gateway

docker rm -f caddy >/dev/null 2>&1 || true
docker run -d --name caddy --restart unless-stopped --network host \
  -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile:ro -v caddy_data:/data -v caddy_config:/config \
  caddy:2 >/dev/null
echo "installed; check: systemctl status marlin2b-vllm marlin2b-gateway; docker logs caddy"
