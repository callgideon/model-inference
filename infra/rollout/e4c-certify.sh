#!/usr/bin/env bash
# E4C: the certificate run (models/marlin2b/results/E4C-runbook.md §4), on the box, as root:
#   infra/rollout/ssm.sh infra/rollout/e4c-certify.sh RELEASE=<sha>
# session-03's e4b-certify3.sh (kept there unchanged: run3's record) plus the E4C inputs: the
# filled box and edge profiles and the certify key inventory (runbook §3 and H6), and the ledger
# half's own login. Every flag after certify.py is one certify.py's parser defines
# (apps/infrx-api/tests/i/test_rollout.py pins the set). Prints names, paths and ids only.
set -euo pipefail
: "${RELEASE:?the release commit}"
nvme=/opt/dlami/nvme; e4b=$nvme/e4b; e4c=$e4b/e4c; env_file=/etc/marlin2b-gateway.env
test -d "$nvme/w3-corpus" || { echo "no corpus at $nvme/w3-corpus" >&2; exit 2; }
for f in "$e4b/key.env" "$e4b/inventory.txt" "$e4b/parity-e0.jsonl" \
         "$e4c/E4C-box.json" "$e4c/E4C-edge.json" "$e4c/keys-certify.json"; do
  test -s "$f" || { echo "missing $f (E4C-runbook §3 / H6)" >&2; exit 2; }
done
# rule 1: the box shell hands certify exactly one key, under INFRX_API_KEY (names only)
[ "$(cut -d= -f1 "$e4b/key.env")" = INFRX_API_KEY ] \
  || { echo "$e4b/key.env must name exactly INFRX_API_KEY" >&2; exit 2; }
if grep -q '^MARLIN_API_KEY=' "$env_file"; then echo "MARLIN_API_KEY is in $env_file" >&2; exit 2; fi
docker image inspect "infrx-certify:$RELEASE" >/dev/null
out=$e4b/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$out"; chmod 777 "$out"; chmod 644 "$e4b/inventory.txt" "$e4b/parity-e0.jsonl"
GW=$(docker inspect --format '{{.Image}}' infrx-gateway)
RI=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")   # the served-build cell compares the two image ids literally
[ "$GW" = "$RI" ] || echo "note: gateway image $GW != release image $RI"
# The ledger half must not use the session pooler (5432: the runtime holds all 15 slots,
# EMAXCONNSESSION): the env file's DATABASE_URL on the transaction port, as run3 did. Since W10b
# that DATABASE_URL is the dedicated infrx_runtime login, which the operator tool certify's
# ledger half runs (cli.build_operations) refuses; it reads OPERATIONS_DATABASE_URL first, so
# that is the owner login (SSM OPS_DSN_PARAM, preflight's pg_journal_url) on 6543. Both values
# reach docker in a 0600 file with the run's other settings, never on its command line.
DB6543=$(sed -n 's/^DATABASE_URL=//p' "$env_file" | tr -d '"' | sed 's/:5432\//:6543\//')
[ -n "$DB6543" ] || { echo "no DATABASE_URL in the env file" >&2; exit 2; }
OPS6543=$(aws ssm get-parameter --region us-east-1 --with-decryption \
            --name "${OPS_DSN_PARAM:-/model-inference/pg_journal_url}" \
            --query Parameter.Value --output text | sed 's/:5432\//:6543\//')
[[ $OPS6543 == *:6543/* ]] || { echo "the owner DSN is not on the pooler's :6543" >&2; exit 2; }
dsns=$(umask 077; mktemp)
trap 'rm -f "$dsns"' EXIT   # docker reads the env files when it starts the container
printf '%s=%s\n' DATABASE_URL "$DB6543" OPERATIONS_DATABASE_URL "$OPS6543" CORPUS_CACHE /corpus \
  E4B_WINDOW_OK 1 INFRX_CERTIFY_GATEWAY_IMAGE "$GW" INFRX_CERTIFY_RELEASE_IMAGE "$RI" > "$dsns"
unset DB6543 OPS6543
# stop a certify container of this release still running (a relaunch)
docker ps -q --filter "ancestor=infrx-certify:$RELEASE" | xargs -r docker kill >/dev/null 2>&1 || true
nohup docker run --rm --network host \
  -v "$nvme/w3-checkout:/repo:ro" -v "$nvme/w3-corpus:/corpus:ro" \
  -v "$e4b:/e4b:ro" -v "$out:/out" -w /repo \
  --env-file "$env_file" --env-file "$e4b/key.env" --env-file "$dsns" \
  "infrx-certify:$RELEASE" python tests/integration/backend/certify.py --no-stack --box \
    --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
    --metrics-url http://127.0.0.1:8001/metrics --worker-metrics-url http://127.0.0.1:8002/metrics \
    --inventory /e4b/inventory.txt --release-sha "$RELEASE" --parity-baseline /e4b/parity-e0.jsonl \
    --run-profile /e4b/e4c/E4C-box.json --key-inventory /e4b/e4c/keys-certify.json \
    --overload-profile /e4b/e4c/E4C-edge.json \
    --workdir /out/work --report /out/report.json \
  > "$out/certify.log" 2>&1 &
sleep 30
echo "out=$out"; grep -E '^\[(ok|FAIL|PEND|skip)' "$out/certify.log" | cut -c1-160 || true; tail -c 600 "$out/certify.log"
