#!/usr/bin/env bash
# Operator-run (terminal, repo root of the claude/consumer-v1 checkout): the remaining go-live steps after
# W7 + W7f H1/H2 (done by the coordinator 2026-09-27 06:53–06:56Z). Order: h3 w8 w9 w10 w10b w11 user w12 main.
# Every step logs to $LOGDIR/<step>.log and the script stops at the first failure naming the rollout.md §3 row.
#   infra/rollout/go-live-remaining.sh                                   # full run, new LOGDIR
#   infra/rollout/go-live-remaining.sh --step <step> --logdir <the first run's dir>   # resume (reuse the LOGDIR)
# main receives RELEASE itself (the App release identity; S1/C1 compare the commit with RELEASE); the later
# test/doc commits on claude/consumer-v1 follow in the next release. H4–H6 (tenant-2, +40,000 CREDIT, the
# keys-certify inventory) remain for the coordinator after this script.
# Secrets never print: keys are read from SSM by name into the environment; the issued W12 key and the
# test-user password are 0600 files inside the 0700 LOGDIR — shred both after W13.
set -euo pipefail
RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
MIGRATION_DIGEST=4a6bffd9fd4112f793facdac79e58eaeadbd2681850f4978c546a52025f74909   # W7's COPY_DIGEST
INSTALL_ARGS=(RELEASE="$RELEASE" ENGINE_MAX_NUM_SEQS=8
  INFRX_SET="S3_MEDIA_BUCKET=llm-bootcamp-641134885443 MAX_VIDEO_SECONDS=82 WORKER_CONCURRENCY=8 LARGE_BODY_LIMIT=8 DATABASE_POOL_MAX_SIZE=6 ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch")
TEST_USER_EMAIL=${TEST_USER_EMAIL:-rey+infrx-test1@callsofia.co}
LOGDIR=${LOGDIR:-$HOME/infrx-go-live/$(date -u +%Y%m%dT%H%M%SZ)}
STEP=${STEP:-h3}
while [ $# -gt 0 ]; do case "$1" in --step) STEP=$2; shift 2;; --logdir) LOGDIR=$2; shift 2;; *) echo "unknown argument $1 (use --step <step> --logdir <dir>)" >&2; exit 2;; esac; done
ORDER=(h3 w8 w9 w10 w10b w11 user w12 main)
umask 077; mkdir -p "$LOGDIR"; chmod 700 "$LOGDIR"
TAG=$(basename "$LOGDIR")
[ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
git rev-parse --verify -q "$RELEASE^{commit}" >/dev/null || { echo "RELEASE $RELEASE is not in this repository: git fetch origin" >&2; exit 2; }
[ -z "$(git status --porcelain -- infra apps/infrx-api/deploy)" ] || { echo "uncommitted changes under infra/ or apps/infrx-api/deploy" >&2; exit 2; }
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOGDIR/go-live.log"; }
fail() { say "$1 RED — $2"; exit 1; }
ssm() { infra/rollout/ssm.sh "$@"; }
cli() { infra/rollout/operator-cli.sh "$@"; }
run() { local name=$1; shift; say "== $name"; if "$@" > "$LOGDIR/$name.log" 2>&1; then say "$name PASS (log $LOGDIR/$name.log)"; else local rc=$?; say "$name FAIL exit $rc — see $LOGDIR/$name.log"; return $rc; fi; }
from() { local i; for i in "${!ORDER[@]}"; do [ "${ORDER[$i]}" = "$STEP" ] && { START=$i; return; }; done; echo "unknown STEP $STEP (one of ${ORDER[*]})" >&2; exit 2; }
from; say "go-live-remaining from step $STEP; LOGDIR=$LOGDIR (reuse it with STEP=… LOGDIR=… to resume)"
W7F="the W7f reversal first (credit-transition --to legacy_usd, rollout.md §3)"
do_h3() {
  run h3-revoke-b5b74b8e cli revoke-key --org a349a382-bc66-4f28-b9b7-df866af42a33 --key-id b5b74b8e-bdbd-4865-97f1-38d2fa493f79 --idempotency-key revoke-precutover-b5b74b8e-20260927 --reason "H3: pre-cutover consumer key (2026-09-20) revoked at the CREDIT cutover" || fail H3 "revoke b5b74b8e failed (nothing else changed): read the log, rerun with the same idempotency key"
  run h3-revoke-04af08ec cli revoke-key --org ddf4e0b9-83e3-4c5d-9da2-6b2435158f61 --key-id 04af08ec-f884-4a25-bd6a-af8d643020f6 --idempotency-key revoke-precutover-04af08ec-20260927 --reason "H3: pre-cutover consumer key (2026-09-20) revoked at the CREDIT cutover" || fail H3 "revoke 04af08ec failed (nothing else changed): read the log, rerun with the same idempotency key"
}
do_w8() {
  run w8-checkout ssm infra/rollout/steps/40-checkout.sh RELEASE="$RELEASE" || fail W8 "$W7F, then ssm 91-abort.sh RELEASE=$RELEASE, then ssm 93-restore-edge.sh SAVED=<W4 path> SAVED_SHA256=<W4 sha> (exit 2 'dirty' = move untracked files aside on the box and rerun)"
  grep -q "checked out $RELEASE" "$LOGDIR/w8-checkout.log" || fail W8 "no 'checked out $RELEASE' line: $W7F, then 91-abort.sh + 93-restore-edge.sh"
  say "W8 done — run W9 and W10 right after (no engine restart or reboot until W10)"
}
do_w9() {
  run w9-s3-check ssm infra/rollout/steps/45-s3-check.sh RELEASE="$RELEASE" || fail W9 "$W7F, then RB-A (91-abort.sh + 93-restore-edge.sh); nothing installed"
  grep -q "real-bucket conformance passed for $RELEASE" "$LOGDIR/w9-s3-check.log" || fail W9 "no 'real-bucket conformance passed' line: $W7F, then RB-A"
  ! grep -qE '[0-9]+ skipped' "$LOGDIR/w9-s3-check.log" || fail W9 "the S3 conformance cases were skipped, not run: $W7F, then RB-A"
}
do_w10() {
  run w10-install env TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh "${INSTALL_ARGS[@]}" MIGRATION_DIGEST="$MIGRATION_DIGEST" || fail W10 "exact code: aws ssm get-command-invocation --command-id <the 'command …' id in the log> --instance-id i-0e8449a4ffca29bab --query ResponseCode (2 = preflight refused → $W7F, then R1; 4 = not ready → $W7F, then R2(b) with the 'backup' path in the log)"
  grep -E "deployed $RELEASE|rollback:|backup" "$LOGDIR/w10-install.log" | tee -a "$LOGDIR/go-live.log" >/dev/null || true
  grep -q "deployed $RELEASE" "$LOGDIR/w10-install.log" || fail W10 "status Success but no 'deployed $RELEASE' line (SSM truncates long output): the edge may be LIVE — check public /health before any rollback"
}
do_w10b() {
  run w10b-maintenance ssm infra/rollout/steps/95-maintenance.sh RELEASE="$RELEASE" || fail W10b "95-maintenance: 'still OPEN and admitting' → stop here (the edge is open; do not run 55); any other failure → the edge may already serve 503 — rerun 95"
  run w10b-runtime-login ssm infra/rollout/steps/55-runtime-login.sh || fail W10b "55-runtime-login: the edge is in maintenance — either ssm 56-resume.sh RELEASE=$RELEASE on the owner login (record RV-09 not met) or $W7F then R2; never 91-abort"
  run w10b-resume ssm infra/rollout/steps/56-resume.sh RELEASE="$RELEASE" || fail W10b "56-resume: maintenance stays — journalctl -u marlin2b-gateway -u infrx-worker on the box, rerun 56, or $W7F then R2(a); never 91-abort"
  grep -q "resumed" "$LOGDIR/w10b-resume.log" || fail W10b "no 'resumed' line: maintenance stays — rerun 56-resume.sh or $W7F then R2(a); never 91-abort"
  local code=000 i
  for i in 1 2 3 4 5 6; do code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://marlin2b.callbill.ai/health || true); [ "$code" = 200 ] && break; sleep 5; done
  say "public /health after resume: $code"; [ "$code" = 200 ] || fail W10b "public /health is $code, not 200: journalctl on the box, rerun 56-resume.sh, or $W7F then R2(a)"
}
do_w11() {
  run w11-verify-local ssm infra/rollout/steps/60-verify-local.sh || fail W11 "$W7F, then R2(a) (95-maintenance first if 90-revert exits 4; zero-count of pilot jobs first)"
  say "review $LOGDIR/w11-verify-local.log before going public: listeners only :80 :443 sshd; INFRX_MODE=pilot; no GATEWAY_API_KEY; ro/capdrop/user as applied"
}
do_user() {
  [ -s "$LOGDIR/test-user.json" ] && { say "test user already recorded in $LOGDIR/test-user.json"; return 0; }
  export SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD
  SUPABASE_URL=$(aws ssm get-parameter --name /model-inference/supabase_url --query Parameter.Value --output text) && [ -n "$SUPABASE_URL" ] || fail USER "supabase_url read failed"
  SUPABASE_SERVICE_ROLE_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/supabase_service_role_key --query Parameter.Value --output text) && [ -n "$SUPABASE_SERVICE_ROLE_KEY" ] || fail USER "supabase_service_role_key read failed"
  [ -s "$LOGDIR/test-user.password" ] || openssl rand -base64 24 | tr -d '\n' > "$LOGDIR/test-user.password"
  INFRX_TEST_USER_PASSWORD=$(cat "$LOGDIR/test-user.password")
  if apps/infrx-api/.venv/bin/python infra/app/create-test-user.py --email "$TEST_USER_EMAIL" --reset-existing --json > "$LOGDIR/test-user.json.tmp" 2> "$LOGDIR/test-user.err"; then
    mv "$LOGDIR/test-user.json.tmp" "$LOGDIR/test-user.json"
  else
    unset SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD; rm -f "$LOGDIR/test-user.json.tmp"; fail USER "create-test-user.py failed (see $LOGDIR/test-user.err; exit 2 = env/input, 3 = a refused call)"
  fi
  unset SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD
  say "test user: $(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('email'), 'confirmed', d.get('confirmed'), 'grant', d.get('grant'), 'available', d.get('available'), 'CREDIT')" "$LOGDIR/test-user.json"); password in $LOGDIR/test-user.password (0600; shred after the first login)"
}
do_w12() {
  [ -s "$LOGDIR/test-user.json" ] || do_user
  local uid kid org n
  uid=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['user_id'])" "$LOGDIR/test-user.json")
  if [ ! -s "$LOGDIR/w12-revoked.key" ]; then
    run w12-issue-revoked cli issue-key --user "$uid" --name "w12-revoked-$TAG" --secret-file "$LOGDIR/w12-revoked.key" --idempotency-key "key-w12-revoked-$TAG" --reason "W12 revoked-key check" || fail W12 "issue-key failed (no key issued)"
    [ -s "$LOGDIR/w12-revoked.key" ] || fail W12 "issue-key wrote no secret (a replay?): remove $LOGDIR/w12-revoked.key and rerun with a new LOGDIR"
  fi
  # always revoke (a replay under the same idempotency key is harmless): a rerun after a failed revoke must never smoke with an ACTIVE key
  kid=$(python3 -c "import json,sys; d=[json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')][-1]; print(d['key_id'])" "$LOGDIR/w12-issue-revoked.log")
  org=$(python3 -c "import json,sys; d=[json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')][-1]; print(d['org_id'])" "$LOGDIR/w12-issue-revoked.log")
  run w12-revoke cli revoke-key --org "$org" --key-id "$kid" --idempotency-key "revoke-w12-$TAG" --reason "W12 revoked-key check" || fail W12 "key $kid is ACTIVE: revoke it (revoke-key --org $org --key-id $kid) before any rerun of W12"
  INFRX_TEST_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/e4b_api_key --query Parameter.Value --output text) && [ -n "$INFRX_TEST_KEY" ] || fail W12 "e4b_api_key read failed"
  LEGACY_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/marlin2b_api_key --query Parameter.Value --output text) && [ -n "$LEGACY_KEY" ] || fail W12 "marlin2b_api_key read failed: stop (an empty LEGACY_KEY skips the R51 check silently)"
  INFRX_REVOKED_KEY=$(tr -d '\n' < "$LOGDIR/w12-revoked.key")
  export INFRX_TEST_KEY INFRX_REVOKED_KEY LEGACY_KEY; unset MARLIN_API_KEY
  if [ -z "${IP+x}" ]; then
    [ "$(aws ec2 describe-addresses --public-ips 100.57.145.167 --query 'Addresses[0].InstanceId' --output text 2>/dev/null)" = i-0e8449a4ffca29bab ] || fail W12 "the Elastic IP 100.57.145.167 is not on the box: export IP=<current> (or IP= to skip the port checks)"
  fi
  run w12-verify-external infra/rollout/verify-external.sh || { unset INFRX_TEST_KEY INFRX_REVOKED_KEY LEGACY_KEY; fail W12 "smoke red: before any pilot request was accepted → $W7F then R2(a); after → R3"; }
  unset INFRX_TEST_KEY INFRX_REVOKED_KEY LEGACY_KEY
  n=$(grep -c '^PASS' "$LOGDIR/w12-verify-external.log" || true); say "W12: $n PASS lines (16 expected)"
  [ "$n" = 16 ] || fail W12 "$n PASS lines, not 16: a check was skipped — read $LOGDIR/w12-verify-external.log; $W7F then R2(a) before any pilot request, R3 after"
}
do_main() {
  say "main = the App production deploy (Vercel builds from main). Preconditions: X3 Vercel variables (done 06:54Z); X4 production auth settings (signup OFF, or accepted ON for internal testing — signup_grant is live); local gates at RELEASE; W12 16/16 green."
  local ok; read -rp "X3/X4 checked and W12 16/16 green? type yes: " ok; [ "$ok" = yes ] || fail MAIN "not confirmed; nothing pushed"
  git fetch -q origin
  git merge-base --is-ancestor origin/main "$RELEASE" || fail MAIN "origin/main is not an ancestor of RELEASE (someone moved main): stop, nothing pushed"
  run main-push git push origin "$RELEASE:refs/heads/main" || fail MAIN "push refused: nothing deployed"
  say "main = RELEASE $RELEASE; Vercel builds production (project callgideon/infrx-app): https://vercel.com/callgideon/infrx-app — then smoke S1–S6 (curl -sS -D - https://app.callbill.ai/api/version must report commit=$RELEASE)"
}
for ((i=START; i<${#ORDER[@]}; i++)); do "do_${ORDER[$i]}"; done
say "ALL DONE — W13 record for the coordinator: RELEASE $RELEASE, main $RELEASE, MIGRATION_DIGEST $MIGRATION_DIGEST, logs $LOGDIR (send go-live.log + w10-install.log's 'backup' line). Then: shred -u $LOGDIR/w12-revoked.key after H6; shred -u $LOGDIR/test-user.password after the first login. H4–H6 remain (coordinator)."
