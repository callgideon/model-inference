#!/usr/bin/env bash
# Install/refresh the marlin2b stack on the box: vLLM (systemd, docker),
# gateway (systemd, /opt/pytorch), Caddy (docker, TLS). Idempotent; run as root.
#   sudo ./apps/infrx-api/deploy/install.sh
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGION=${AWS_REGION:-us-east-1}

# gateway secrets from SSM, never in the repo. A missing parameter is a warning,
# not an error: the gateway runs with the legacy key alone (no Supabase auth or
# usage rows) and with Supabase alone (no legacy key).
ssm() {
  aws ssm get-parameter --name "$1" --with-decryption --region "$REGION" \
      --query Parameter.Value --output text 2>/dev/null || {
    echo "warning: SSM $1 missing; leaving it out of /etc/marlin2b-gateway.env" >&2; }
}
key=$(ssm /model-inference/marlin2b_api_key)
supabase_url=$(ssm /model-inference/supabase_url)
supabase_key=$(ssm /model-inference/supabase_service_role_key)

install -m 600 -o ubuntu /dev/null /etc/marlin2b-gateway.env
{ printf 'MODEL_ID=nemostation/marlin-2b\nMAX_INFLIGHT=16\n'
  if [ -n "$key" ]; then printf 'GATEWAY_API_KEY=%s\n' "$key"; fi
  if [ -n "$supabase_url" ]; then printf 'SUPABASE_URL=%s\n' "$supabase_url"; fi
  if [ -n "$supabase_key" ]; then printf 'SUPABASE_SERVICE_ROLE_KEY=%s\n' "$supabase_key"; fi
} > /etc/marlin2b-gateway.env

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
