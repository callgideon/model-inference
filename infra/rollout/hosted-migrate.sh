#!/usr/bin/env bash
# W6 + W6b + W7 of infra/runbooks/rollout.md as one guarded run (coordinator host, a checkout at RELEASE):
#   W6  fresh hosted dump, local restore into a throwaway PostgreSQL, equality check ("equal": true)
#   W6b the copy is migrated first: COPY_DIGEST = the plan digest on the copy; 0019–0026 applied there;
#       flags credit_admission f / legacy_usd_admission t / signup_grant t; drift_rows 0 — all asserted
#   W7  hosted `plan` must show the same applied list and the same digest, then `apply --expect`,
#       then `plan` = 0001–0026 with nothing pending and the same flags/drift as the copy
#
#   RELEASE=<40 hex> infra/rollout/hosted-migrate.sh --through w6b             # no hosted write (default)
#   RELEASE=<40 hex> infra/rollout/hosted-migrate.sh --through w7 --expect <COPY_DIGEST from a w6b run>
#   BACKUP_ROOT (default ~/infrx-backups, 0700), PGPORT_LOCAL (default 55697)
#
# Both forms rerun W6 and W6b in full. Exit codes: 2 usage / precondition, 3 host environment,
# 10 stopped BEFORE any hosted write (rollout.md §3 "W6 check not equal" / "W7 plan digest ≠" rows:
# 91-abort.sh, 93-restore-edge.sh), 20 stopped AFTER a hosted write may have happened (rollout.md §3
# "W7 apply exit 4, or anything wrong after it committed": maintenance stays, restore.md A8).
# Secrets: PGPASSWORD comes from SSM /INFRX-SUPABASE-PROD/db_password into the environment and is never
# printed; the local copy's password is a throwaway literal (restore.md A4's documented exception).
set -euo pipefail
THROUGH=w6b; EXPECT=
while [ $# -gt 0 ]; do case "$1" in --through) THROUGH=$2; shift 2;; --expect) EXPECT=$2; shift 2;; *) echo "unknown argument $1" >&2; exit 2;; esac; done
case "$THROUGH" in w6b|w7) ;; *) echo "--through must be w6b or w7" >&2; exit 2;; esac
if [ "$THROUGH" = w7 ]; then [[ $EXPECT =~ ^[0-9a-f]{64}$ ]] || { echo "--through w7 needs --expect <the COPY_DIGEST of a green w6b run>" >&2; exit 2; }; fi
: "${RELEASE:?export RELEASE=<the release commit, 40 hex> (rollout.md preamble)}"
[[ $RELEASE =~ ^[0-9a-f]{40}$ ]] || { echo "RELEASE is not a full commit id" >&2; exit 2; }
[ "$(git rev-parse HEAD)" = "$RELEASE" ] || { echo "HEAD $(git rev-parse --short HEAD) is not RELEASE=$RELEASE" >&2; exit 2; }
[ -z "$(git status --porcelain -- apps/app/supabase/migrations apps/infrx-api/deploy/migrate.py apps/infrx-api/infrx/state/migrations.py)" ] || { echo "uncommitted or untracked changes in the migration inputs" >&2; exit 2; }   # pgrestore.py is the dump tool, not a migration input
[ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
PY=apps/infrx-api/.venv/bin/python
HOSTED="host=aws-0-us-east-2.pooler.supabase.com port=5432 user=postgres.fcbnscgsymzdykendbrc dbname=postgres sslmode=require"
EXPECTED_PENDING="0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026"   # rollout.md W7: 0001-0018 -> 0001-0026
EXPECTED_FLAGS="credit_admission=false legacy_usd_admission=true signup_grant=true"
PORT=${PGPORT_LOCAL:-55697}
BACKUP_ROOT=${BACKUP_ROOT:-$HOME/infrx-backups}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$BACKUP_ROOT/hosted-$STAMP"
LOG="$BACKUP_ROOT/migrate-$STAMP.log"       # outside restore.md's hosted-* prune glob
CONTAINER=infrx-rollout-restore
LOCALPW=infrx-rollout-local
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
OWN=; VERIFIED=; STARTED=; WROTE=
# errexit-safe: local cleanup always completes; implicit set -e exits are normalized to 10 (before any
# hosted write) or 20 (after one), the documented codes 0/2/3/10/20 pass through unchanged
cleanup() { rc=$?; set +e; [ -z "$OWN" ] || docker rm -f "$CONTAINER" >/dev/null 2>&1; [ -n "$VERIFIED" ] || rm -rf -- "$BACKUP"; unset PGPASSWORD MIGRATE_DATABASE_URL; [ -n "$STARTED" ] || exit $rc; case $rc in 0|2|3|10|20) exit $rc;; esac; [ -n "$WROTE" ] && exit 20; exit 10; }
trap cleanup EXIT
umask 077; mkdir -p "$BACKUP_ROOT"; chmod 700 "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/.hosted-migrate.lock"; flock -n 9 || { echo "stop: another hosted-migrate run holds the lock" >&2; exit 3; }
say "hosted-migrate --through $THROUGH at RELEASE $RELEASE; backup $BACKUP; log $LOG"
if [ "$THROUGH" = w7 ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://marlin2b.callbill.ai/health || true)
  [ "$code" = 503 ] || { say "stop: public /health is '$code', not 503: W5 (maintenance + drain) has not run"; exit 2; }
fi
command -v ss >/dev/null && docker info >/dev/null 2>&1 || { say "stop: ss or docker unavailable"; exit 3; }
[ -z "$(ss -Hltn "sport = :$PORT")" ] || { say "stop: port $PORT is busy"; exit 3; }
docker container inspect "$CONTAINER" >/dev/null 2>&1 && { say "stop: container $CONTAINER exists (left as is)"; exit 3; }
PGPASSWORD=$(aws ssm get-parameter --with-decryption --name /INFRX-SUPABASE-PROD/db_password --query Parameter.Value --output text)
[ -n "$PGPASSWORD" ] || { say "stop: empty db password from SSM"; exit 3; }
export PGPASSWORD

# ---- W6: dump, local restore, equality ------------------------------------------------------------
STARTED=1
say "W6 dump -> $BACKUP"
$PY infra/runbooks/pgrestore.py dump --conninfo "$HOSTED" --out "$BACKUP" 2>&1 | tee -a "$LOG"
IMAGE=$($PY -c 'import runpy; print(runpy.run_path("infra/runbooks/pgrestore.py")["IMAGE"])')
GOTRUE=supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b
say "W6 local restore container ($IMAGE) on 127.0.0.1:$PORT"
OWN=1
docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:5432" -e POSTGRES_PASSWORD="$LOCALPW" "$IMAGE" >/dev/null
LOCAL_PG="host=127.0.0.1 port=$PORT user=postgres password=$LOCALPW dbname=postgres connect_timeout=3"
for _ in $(seq 1 90); do $PY -c "import psycopg; psycopg.connect('$LOCAL_PG').close()" 2>/dev/null && break; sleep 1; done
$PY -c "import psycopg; psycopg.connect('$LOCAL_PG').close()" || { say "stop: local PostgreSQL not ready on $PORT"; exit 10; }
sb() { docker exec "$CONTAINER" psql -q -U supabase_admin -d "$1" -v ON_ERROR_STOP=1 -c "$2"; }
sb postgres "alter role supabase_auth_admin with password '$LOCALPW'" >/dev/null
docker run --rm --network host -e GOTRUE_DB_DRIVER=postgres -e DATABASE_URL="postgres://supabase_auth_admin:$LOCALPW@127.0.0.1:$PORT/postgres?sslmode=disable" -e GOTRUE_JWT_SECRET=rehearsal-only-literal-not-a-secret-0000 -e GOTRUE_SITE_URL=http://localhost -e API_EXTERNAL_URL=http://localhost "$GOTRUE" auth migrate 2>&1 | tail -1 | tee -a "$LOG"
for _ in 1 2 3 4 5; do sb template1 "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'postgres' and pid <> pg_backend_pid()" >/dev/null; sb template1 "create database infrx_rollout_copy template postgres owner postgres" >/dev/null && break; sleep 0.3; done
sb infrx_rollout_copy "select 1" >/dev/null || { say "stop: the copy database was not created"; exit 10; }
LOCAL="host=127.0.0.1 port=$PORT user=postgres password=$LOCALPW dbname=infrx_rollout_copy sslmode=disable"
say "W6 restore into the copy"
$PY infra/runbooks/pgrestore.py restore --conninfo "$LOCAL" --from "$BACKUP" 2>&1 | tee -a "$LOG"
say "W6 equality check"
CHECK=$($PY infra/runbooks/pgrestore.py check --source "$HOSTED" --target "$LOCAL" 2>&1) && rc=0 || rc=$?
printf '%s\n' "$CHECK" | tee -a "$LOG"
if [ "$rc" -ne 0 ] || ! grep -qx '  "equal": true,' <<<"$CHECK"; then say "stop: check exit $rc / not equal — the dump is not a recovery point (removed). rollout.md §3 'W6 check not equal': 91-abort.sh, 93-restore-edge.sh"; exit 10; fi
VERIFIED=1
say "W6 PASS: verified dump $BACKUP ($(wc -l < "$BACKUP/SHA256SUMS") checksummed files)"
ls -1d "$BACKUP_ROOT"/hosted-*/ 2>/dev/null | head -n -7 | while read -r old; do rm -rf -- "$old"; say "pruned $old (restore.md P-25: keep the 7 newest verified dumps)"; done

# ---- W6b: migrate the copy, COPY_DIGEST -------------------------------------------------------------
export MIGRATE_DATABASE_URL="$HOSTED"
HOSTED_PLAN=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG") || { say "stop: hosted plan refused (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
printf '%s\n' "$HOSTED_PLAN" | tee -a "$LOG"
HOSTED_APPLIED=$(sed -n 's/^applied: //p' <<<"$HOSTED_PLAN")
case "$HOSTED_APPLIED" in *"0018 terminal_settlement") ;; *) say "stop: hosted applied list does not end at 0018 terminal_settlement (unrecorded hosted change)"; exit 10;; esac
SEED=$(sed 's/, /\n/g' <<<"$HOSTED_APPLIED" | sed "s/'/''/g" | sed -E "s/^([0-9]{4}) ?(.*)$/('\1', '\2')/" | paste -sd, -)
docker exec "$CONTAINER" psql -q -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 -c "create schema supabase_migrations" -c "create table supabase_migrations.schema_migrations (version text primary key, statements text[], name text)" -c "insert into supabase_migrations.schema_migrations (version, name) values $SEED" >/dev/null
export MIGRATE_DATABASE_URL="postgresql://postgres:$LOCALPW@127.0.0.1:$PORT/infrx_rollout_copy"
COPY_DIGEST=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG" | sed -n 's/^plan digest: //p')
[[ $COPY_DIGEST =~ ^[0-9a-f]{64}$ ]] || { say "stop: COPY_DIGEST is not 64 hex: '$COPY_DIGEST'"; exit 10; }
[ "$(sed -n 's/^plan digest: //p' <<<"$HOSTED_PLAN")" = "$COPY_DIGEST" ] || { say "stop: the hosted plan digest differs from COPY_DIGEST (different pending set)"; exit 10; }
say "W6b copy apply --expect $COPY_DIGEST"
COPY_OUT=$($PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST" 2>>"$LOG") || { say "stop: the copy's apply failed"; exit 10; }
printf '%s\n' "$COPY_OUT" | tee -a "$LOG"
[ "$(sed -n 's/^applied: //p' <<<"$COPY_OUT")" = "$EXPECTED_PENDING" ] || { say "stop: the copy applied '$(sed -n 's/^applied: //p' <<<"$COPY_OUT")', not $EXPECTED_PENDING"; exit 10; }
q() { docker exec "$CONTAINER" psql -At -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 -c "$1"; }
FLAGS=$(q "select string_agg(name || '=' || enabled, ' ' order by name) from infrx.feature_flags where name in ('credit_admission','legacy_usd_admission','signup_grant')")
DRIFT=$(q "select count(*) from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0")
say "copy after apply: $FLAGS; drift_rows $DRIFT"
[ "$FLAGS" = "$EXPECTED_FLAGS" ] && [ "$DRIFT" = 0 ] || { say "stop: copy flags/drift differ from rollout.md W6 Pass"; exit 10; }
printf '%s\n' "$COPY_DIGEST" > "$BACKUP/COPY_DIGEST"
say "W6b PASS: COPY_DIGEST=$COPY_DIGEST (also in $BACKUP/COPY_DIGEST; W10's MIGRATION_DIGEST)"
unset MIGRATE_DATABASE_URL
[ "$THROUGH" = w7 ] || { say "stopping before any hosted write (--through w6b)"; exit 0; }
[ "$EXPECT" = "$COPY_DIGEST" ] || { say "stop: --expect $EXPECT is not this run's COPY_DIGEST $COPY_DIGEST"; exit 10; }

# ---- W7: the hosted apply ----------------------------------------------------------------------------
export MIGRATE_DATABASE_URL="$HOSTED"
PLAN=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG") || { say "stop: hosted plan refused before the apply (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
printf '%s\n' "$PLAN" | tee -a "$LOG"
[ "$(sed -n 's/^applied: //p' <<<"$PLAN")" = "$HOSTED_APPLIED" ] || { say "stop: hosted applied list changed since W6b (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
[ "$(sed -n 's/^plan digest: //p' <<<"$PLAN")" = "$COPY_DIGEST" ] || { say "stop: hosted plan digest ≠ COPY_DIGEST (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://marlin2b.callbill.ai/health || true)
[ "$code" = 503 ] || { say "stop: public /health is '$code', not 503 just before the hosted apply (nothing changed)"; exit 10; }
say "W7 hosted apply --expect $COPY_DIGEST (0019–0026; never reverted afterwards)"
WROTE=1
APPLY_OUT=$($PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST" 2>>"$LOG") && rc=0 || rc=$?
printf '%s\n' "$APPLY_OUT" | tee -a "$LOG"
case $rc in
  0) ;;
  2|3) say "W7 apply exit $rc: nothing changed. rollout.md §3 'W7 plan digest ≠ COPY_DIGEST, or apply exit 2/3': 91-abort.sh, 93-restore-edge.sh"; exit 10;;
  *)   say "W7 apply exit $rc: hosted MAY be changed. rollout.md §3 'W7 apply exit 4, or anything wrong after it committed': maintenance stays, restore.md A8"; exit 20;;
esac
[ "$(sed -n 's/^applied: //p' <<<"$APPLY_OUT")" = "$EXPECTED_PENDING" ] || { say "W7: the apply printed '$APPLY_OUT' (a concurrent migrator?). restore.md A8"; exit 20; }
POST=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG") || { say "W7: hosted plan failed after the apply. restore.md A8, maintenance stays"; exit 20; }
printf '%s\n' "$POST" | tee -a "$LOG"
case "$POST" in *"0026 fenced_result"$'\n'"nothing pending") ;; *) say "W7: hosted is not 0001-0026 with nothing pending. restore.md A8"; exit 20;; esac
HSTATE=$($PY - 2>>"$LOG" <<'PY'
import os, psycopg
with psycopg.connect(os.environ["MIGRATE_DATABASE_URL"]) as c:
    c.execute("set transaction_read_only = on")
    f = c.execute("select string_agg(name || '=' || enabled, ' ' order by name) from infrx.feature_flags where name in ('credit_admission','legacy_usd_admission','signup_grant')").fetchone()[0]
    d = c.execute("select count(*) from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0").fetchone()[0]
    print(f"{f}; drift_rows {d}")
PY
) || HSTATE="read failed"
say "hosted after apply: $HSTATE"
[ "$HSTATE" = "$EXPECTED_FLAGS; drift_rows 0" ] || { say "W7: hosted flags/drift differ from the copy. restore.md A8"; exit 20; }
say "W7 PASS: hosted 0001-0026, nothing pending; MIGRATION_DIGEST=$COPY_DIGEST"
