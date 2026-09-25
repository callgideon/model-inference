#!/usr/bin/env bash
# Certification run3: the corrected runner (CERTIFY-TREE + CERTIFY-POLISH) in the certify image
# (runtime image + git), reading the deployed cap from the gateway env file, the worker gauge,
# the release sha; bench runs bounded by their own schedules (the soak = 14,400 + 900 s).
set -euo pipefail
: "${RELEASE:?}"
test -d /opt/dlami/nvme/w3-corpus || { echo "no corpus at /opt/dlami/nvme/w3-corpus"; exit 2; }
test -s /opt/dlami/nvme/e4b/key.env && test -s /opt/dlami/nvme/e4b/inventory.txt && test -s /opt/dlami/nvme/e4b/parity-e0.jsonl
docker image inspect "infrx-certify:$RELEASE" >/dev/null
out=/opt/dlami/nvme/e4b/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$out"; chmod 777 "$out"; chmod 644 /opt/dlami/nvme/e4b/inventory.txt /opt/dlami/nvme/e4b/parity-e0.jsonl
GW=$(docker inspect --format '{{.Image}}' infrx-gateway)
RI=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")   # the served-build cell compares the two image ids literally
[ "$GW" = "$RI" ] || echo "note: gateway image $GW != release image $RI"
# the runner's ledger half must not use the session pooler (5432: the runtime holds all 15 slots, EMAXCONNSESSION);
# the same DSN on the transaction port, computed here and never printed
DB6543=$(sed -n 's/^DATABASE_URL=//p' /etc/marlin2b-gateway.env | tr -d '"' | sed 's/:5432\//:6543\//')
[ -n "$DB6543" ] || { echo "no DATABASE_URL in the env file"; exit 2; }
# stop a certify container of this release still running (a relaunch)
docker ps -q --filter "ancestor=infrx-certify:$RELEASE" | xargs -r docker kill >/dev/null 2>&1 || true
nohup docker run --rm --network host \
  -v /opt/dlami/nvme/w3-checkout:/repo:ro -v /opt/dlami/nvme/w3-corpus:/corpus:ro \
  -v /opt/dlami/nvme/e4b:/e4b:ro -v "$out":/out -w /repo \
  --env-file /etc/marlin2b-gateway.env --env-file /opt/dlami/nvme/e4b/key.env -e DATABASE_URL="$DB6543" \
  -e CORPUS_CACHE=/corpus -e E4B_WINDOW_OK=1 -e INFRX_CERTIFY_GATEWAY_IMAGE="$GW" -e INFRX_CERTIFY_RELEASE_IMAGE="$RI" \
  "infrx-certify:$RELEASE" python tests/integration/backend/certify.py --no-stack --box \
    --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
    --metrics-url http://127.0.0.1:8001/metrics --worker-metrics-url http://127.0.0.1:8002/metrics \
    --inventory /e4b/inventory.txt --release-sha "$RELEASE" \
    --parity-baseline /e4b/parity-e0.jsonl --workdir /out/work --report /out/report.json \
  > "$out/certify.log" 2>&1 &
sleep 30; echo "out=$out"; grep -E '^\[(ok|FAIL|PEND|skip)' "$out/certify.log" | cut -c1-160; tail -c 600 "$out/certify.log"
