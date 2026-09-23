# Step 7: close the maintenance window - restore the live Caddyfile saved in step 4 and
# reload through the maintenance site's admin socket. The edge opens only onto an engine
# that is healthy with the inventoried args and image; otherwise it stays in maintenance.
set -uo pipefail
: "${WUTC:?}"
L=/opt/dlami/nvme/w4-logs
LIVE=/etc/caddy/Caddyfile
EXPECT=ff47f706baaaba94ca43c42469f010d730b94da72d5ddc2ca1bcec3385e19706
ARGS='["serve","/model","--served-model-name","marlin2b","--hf-overrides","{\"architectures\":[\"Qwen3_5ForConditionalGeneration\"]}","--max-model-len","32768","--gpu-memory-utilization","0.90","--limit-mm-per-prompt","{\"video\":1,\"image\":4}","--dtype","bfloat16","--max-num-seqs","32"]'
IMAGE=sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42
S=$L/Caddyfile.live-$WUTC
stop() { echo "STOP (edge stays in maintenance): $*"; exit 3; }
edge() { curl -sS -m 10 --resolve marlin2b.callbill.ai:443:127.0.0.1 -o /dev/null -w "$1 %{http_code}\n" "https://marlin2b.callbill.ai$2"; }
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ "$(systemctl is-active marlin2b-vllm)" = active ] || stop "marlin2b-vllm is not active"
[ "$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)" = 200 ] || stop "engine /health is not 200"
[ "$(docker inspect --format '{{json .Args}}' marlin2b-8000)" = "$ARGS" ] || stop "engine args differ from the step-1 inventory"
[ "$(docker inspect --format '{{.Image}}' marlin2b-8000)" = "$IMAGE" ] || stop "engine image differs from the step-1 inventory"
echo "engine: healthy, args and image = step 1; started=$(docker inspect --format '{{.State.StartedAt}}' marlin2b-8000)"
[ "$(sha256sum < "$S" | cut -d' ' -f1)" = "$EXPECT" ] || stop "the saved live copy does not hash to $EXPECT"
cat "$S" > $LIVE
echo "restored_file=$(sha256sum < $LIVE | cut -d' ' -f1) container_sees=$(docker exec caddy sha256sum /etc/caddy/Caddyfile | cut -d' ' -f1)"
[ "$(sha256sum < $LIVE | cut -d' ' -f1)" = "$EXPECT" ] || stop "restored file hash mismatch"
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock \
  || stop "the reload of the live site failed; the running config is still maintenance"
echo "window_end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 2
curl -sS -m 3 -o /dev/null -w 'admin_2019_http=%{http_code}\n' http://127.0.0.1:2019/config/
edge "edge /health" /health
curl -sS -m 10 --resolve marlin2b.callbill.ai:443:127.0.0.1 https://marlin2b.callbill.ai/health; echo
echo "gateway=$(curl -sS -m 5 http://127.0.0.1:8001/health) gateway_unit=$(systemctl is-active marlin2b-gateway)"
echo "engine_args=$(docker inspect --format '{{json .Args}}' marlin2b-8000)"
echo "engine_image=$(docker inspect --format '{{.Image}}' marlin2b-8000)"
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader
