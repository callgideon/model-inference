# Step 4: open the maintenance window at the edge (apps/infrx-api/deploy/Caddyfile.maintenance).
# The live Caddy (caddy:2 = 2.11.4, host network) bind-mounts the single file
# /etc/caddy/Caddyfile read-only and has the default admin (localhost:2019), so: save the live
# file, validate the maintenance site with the running binary, write it IN PLACE (a
# single-file bind mount pins the inode), reload through the current admin. Any failure
# before the reload leaves the edge untouched; a failed reload restores the live file.
set -uo pipefail
: "${WUTC:?}"
L=/opt/dlami/nvme/w4-logs
LIVE=/etc/caddy/Caddyfile
EXPECT=ff47f706baaaba94ca43c42469f010d730b94da72d5ddc2ca1bcec3385e19706
M=/opt/dlami/nvme/w3-checkout/apps/infrx-api/deploy/Caddyfile.maintenance
stop() { echo "STOP: $*"; exit 3; }
edge() { curl -sS -m 10 --resolve marlin2b.callbill.ai:443:127.0.0.1 -o /dev/null -w "$1 %{http_code}\n" "https://marlin2b.callbill.ai$2"; }
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ "$(sha256sum < $LIVE | cut -d' ' -f1)" = "$EXPECT" ] || stop "the live Caddyfile is not the one inventoried in step 1"
cp -p $LIVE $L/Caddyfile.live-$WUTC && sha256sum $L/Caddyfile.live-$WUTC
cp $M $L/Caddyfile.maintenance-$WUTC && sha256sum $L/Caddyfile.maintenance-$WUTC
docker exec -i caddy caddy adapt --config /dev/stdin --adapter caddyfile --validate < $M > $L/maintenance-adapted-$WUTC.json \
  || stop "the maintenance site does not validate with the running Caddy; the edge is unchanged"
echo "validated maintenance ($(wc -c < $L/maintenance-adapted-$WUTC.json) bytes adapted)"
echo "before: gateway=$(curl -sS -m 5 http://127.0.0.1:8001/health) engine_running_waiting=$(curl -sS -m 5 http://127.0.0.1:8000/metrics | awk '/^vllm:num_requests_(running|waiting)[{ ]/{n+=$NF} END{print n+0}')"
edge "before edge /health" /health
inode=$(stat -c %i $LIVE)
cat $M > $LIVE
[ "$(stat -c %i $LIVE)" = "$inode" ] || echo "WARN: inode changed"
echo "container_sees=$(docker exec caddy sha256sum /etc/caddy/Caddyfile)"
if ! docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address localhost:2019; then
  cat $L/Caddyfile.live-$WUTC > $LIVE
  echo "live file restored: $(sha256sum < $LIVE)"
  stop "the reload into maintenance failed; the running config is unchanged (Caddy loads atomically)"
fi
echo "window_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 2
docker exec caddy ls -la /config/admin.sock
curl -sS -m 3 -o /dev/null -w 'admin_2019_http=%{http_code}\n' http://127.0.0.1:2019/config/ 2>&1 | tail -1
edge "after edge /health" /health
curl -sS -m 10 --resolve marlin2b.callbill.ai:443:127.0.0.1 -D - https://marlin2b.callbill.ai/v1/models; echo
echo "after: engine_health=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health) gateway=$(curl -sS -m 5 http://127.0.0.1:8001/health)"
echo "engine_args=$(docker inspect --format '{{json .Args}}' marlin2b-8000) engine_image=$(docker inspect --format '{{.Image}}' marlin2b-8000)"
