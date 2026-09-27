#!/usr/bin/env bash
# Operator-run (terminal, repo root of the claude/consumer-v1 checkout): the remaining go-live steps
# after W7 + W7f H1/H2 (done by the coordinator 2026-09-27 06:53–06:56Z). Every step logs to
# $LOGDIR/<step>.log and the script stops at the first failure naming the rollout.md §3 row.
#   infra/rollout/go-live-remaining.sh            # runs H3, W8, W9, W10, W10b, W11, main push, W12, test user
#   STEP=w10 infra/rollout/go-live-remaining.sh   # resume from a step: h3 w8 w9 w10 w10b w11 main w12 user
# Secrets never print: keys are read from SSM by name into the environment; the test-user password is
# generated and written 0600 to $LOGDIR/test-user.password (delete it after use).
set -euo pipefail
RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
TIP=$(git rev-parse HEAD)   # the checkout: must contain RELEASE with identical App product files (asserted below)
MIGRATION_DIGEST=4a6bffd9fd4112f793facdac79e58eaeadbd2681850f4978c546a52025f74909   # W7's COPY_DIGEST
INSTALL_ARGS=(RELEASE="$RELEASE" ENGINE_MAX_NUM_SEQS=8
  INFRX_SET="S3_MEDIA_BUCKET=llm-bootcamp-641134885443 MAX_VIDEO_SECONDS=82 WORKER_CONCURRENCY=8 LARGE_BODY_LIMIT=8 DATABASE_POOL_MAX_SIZE=6 ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch")
TEST_USER_EMAIL=${TEST_USER_EMAIL:-rey+infrx-test1@callsofia.co}
LOGDIR=${LOGDIR:-$HOME/infrx-go-live/$(date -u +%Y%m%dT%H%M%SZ)}
STEP=${STEP:-h3}
ORDER=(h3 w8 w9 w10 w10b w11 main w12 user)
umask 077; mkdir -p "$LOGDIR"
[ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
[ "$(git rev-parse --abbrev-ref HEAD)" = claude/consumer-v1 ] || { echo "check out claude/consumer-v1 first" >&2; exit 2; }
git merge-base --is-ancestor "$RELEASE" HEAD || { echo "HEAD does not contain RELEASE $RELEASE" >&2; exit 2; }
[ -z "$(git status --porcelain -- apps infra)" ] || { echo "uncommitted changes under apps/ or infra/" >&2; exit 2; }
APP_DIFF=$(git diff --name-only "$RELEASE" HEAD -- apps/app apps/infrx-api/infrx apps/infrx-api/deploy | grep -vE "/tests?/|\.test\.|README|\.md$" || true)
[ -z "$APP_DIFF" ] || { echo "product files differ from RELEASE (not a fast-forward of the release): $APP_DIFF" >&2; exit 2; }
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOGDIR/go-live.log"; }
ssm() { infra/rollout/ssm.sh "$@"; }
cli() { infra/rollout/operator-cli.sh "$@"; }
run() { local name=$1; shift; say "== $name"; if "$@" > "$LOGDIR/$name.log" 2>&1; then say "$name PASS (log $LOGDIR/$name.log)"; else local rc=$?; say "$name FAIL exit $rc — see $LOGDIR/$name.log"; return $rc; fi; }
from() { local i; for i in "${!ORDER[@]}"; do [ "${ORDER[$i]}" = "$STEP" ] && { START=$i; return; }; done; echo "unknown STEP $STEP" >&2; exit 2; }
from; say "go-live-remaining from step $STEP; logs $LOGDIR"
do_h3() {
  run h3-revoke-b5b74b8e cli revoke-key --org a349a382-bc66-4f28-b9b7-df866af42a33 --key-id b5b74b8e-bdbd-4865-97f1-38d2fa493f79 --idempotency-key revoke-precutover-b5b74b8e-20260927 --reason "H3: pre-cutover consumer key (2026-09-20) revoked at the CREDIT cutover"
  run h3-revoke-04af08ec cli revoke-key --org ddf4e0b9-83e3-4c5d-9da2-6b2435158f61 --key-id 04af08ec-f884-4a25-bd6a-af8d643020f6 --idempotency-key revoke-precutover-04af08ec-20260927 --reason "H3: pre-cutover consumer key (2026-09-20) revoked at the CREDIT cutover"
}
do_w8()  { run w8-checkout ssm infra/rollout/steps/40-checkout.sh RELEASE="$RELEASE"; grep -q "checked out $RELEASE" "$LOGDIR/w8-checkout.log" || { say "W8: no 'checked out' line — rollback: 91-abort.sh then 93-restore-edge.sh"; return 1; }; }
do_w9()  { run w9-s3-check ssm infra/rollout/steps/45-s3-check.sh RELEASE="$RELEASE"; grep -q "passed" "$LOGDIR/w9-s3-check.log" || { say "W9: not 'passed' — RB-W7f then RB-A"; return 1; }; }
do_w10() { run w10-install env TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh "${INSTALL_ARGS[@]}" MIGRATION_DIGEST="$MIGRATION_DIGEST"; grep -E "deployed $RELEASE|rollback:" "$LOGDIR/w10-install.log" | tee -a "$LOGDIR/go-live.log" >/dev/null; grep -q "deployed $RELEASE" "$LOGDIR/w10-install.log" || { say "W10: no 'deployed' line — exit 2: R1; exit 4: R2(b) (after the W7f reversal)"; return 1; }; }
do_w10b() {
  run w10b-maintenance ssm infra/rollout/steps/95-maintenance.sh RELEASE="$RELEASE"
  run w10b-runtime-login ssm infra/rollout/steps/55-runtime-login.sh
  run w10b-resume ssm infra/rollout/steps/56-resume.sh RELEASE="$RELEASE"
  grep -q "resumed" "$LOGDIR/w10b-resume.log" || { say "W10b: not resumed — rerun 56-resume.sh or the R paths (never 91-abort here)"; return 1; }
  local code; code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://marlin2b.callbill.ai/health || true); say "public /health after resume: $code"; [ "$code" = 200 ] || return 1
}
do_w11() { run w11-verify-local ssm infra/rollout/steps/60-verify-local.sh; }
do_main() {
  git fetch -q origin
  git merge-base --is-ancestor origin/main "$TIP" || { say "main: origin/main moved; stop"; return 1; }
  run main-push git push origin "$TIP:refs/heads/main"
  say "main = $TIP; Vercel builds production from main (project infrx-app): watch https://vercel.com/callgideon/infrx-app"
}
do_w12() {
  INFRX_TEST_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/e4b_api_key --query Parameter.Value --output text)
  [ -n "$INFRX_TEST_KEY" ] || { say "W12: e4b_api_key read failed"; return 1; }
  LEGACY_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/marlin2b_api_key --query Parameter.Value --output text 2>/dev/null || true)
  # the revoked key: a fresh consumer key for the test user (created in step 'user' if absent), revoked at once
  [ -f "$LOGDIR/test-user.json" ] || do_user
  local uid; uid=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['user_id'])" "$LOGDIR/test-user.json")
  run w12-issue-revoked cli issue-key --user "$uid" --name w12-revoked-20260927 --secret-file "$LOGDIR/w12-revoked.key" --idempotency-key key-w12-revoked-20260927 --reason "W12 revoked-key check"
  local kid org
  kid=$(python3 -c "import json,sys; d=[json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')][-1]; print(d['key_id'])" "$LOGDIR/w12-issue-revoked.log")
  org=$(python3 -c "import json,sys; d=[json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')][-1]; print(d['org_id'])" "$LOGDIR/w12-issue-revoked.log")
  run w12-revoke cli revoke-key --org "$org" --key-id "$kid" --idempotency-key revoke-w12-20260927 --reason "W12 revoked-key check"
  INFRX_REVOKED_KEY=$(tr -d '\n' < "$LOGDIR/w12-revoked.key"); rm -f "$LOGDIR/w12-revoked.key"
  export INFRX_TEST_KEY INFRX_REVOKED_KEY LEGACY_KEY; unset MARLIN_API_KEY
  run w12-verify-external infra/rollout/verify-external.sh
  unset INFRX_TEST_KEY INFRX_REVOKED_KEY LEGACY_KEY
  grep -c "^PASS" "$LOGDIR/w12-verify-external.log" | xargs -I{} say "W12: {} PASS lines (16 expected)"
}
do_user() {
  export SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD
  SUPABASE_URL=$(aws ssm get-parameter --name /model-inference/supabase_url --query Parameter.Value --output text)
  SUPABASE_SERVICE_ROLE_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/supabase_service_role_key --query Parameter.Value --output text)
  [ -f "$LOGDIR/test-user.password" ] || { openssl rand -base64 24 | tr -d '\n' > "$LOGDIR/test-user.password"; }
  INFRX_TEST_USER_PASSWORD=$(cat "$LOGDIR/test-user.password")
  apps/infrx-api/.venv/bin/python infra/app/create-test-user.py --email "$TEST_USER_EMAIL" --json > "$LOGDIR/test-user.json" 2> "$LOGDIR/test-user.err" || { say "test user: create-test-user.py failed ($LOGDIR/test-user.err)"; unset SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD; return 1; }
  unset SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD
  say "test user: $(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('email'), 'confirmed', d.get('confirmed'), 'grant', d.get('grant'), 'available', d.get('available'), 'CREDIT')" "$LOGDIR/test-user.json"); password in $LOGDIR/test-user.password (0600; delete after the first login)"
}
for ((i=START; i<${#ORDER[@]}; i++)); do "do_${ORDER[$i]}"; done
say "ALL DONE — W13 record: RELEASE $RELEASE, main $TIP, MIGRATION_DIGEST $MIGRATION_DIGEST, logs $LOGDIR (send the coordinator the go-live.log)"
