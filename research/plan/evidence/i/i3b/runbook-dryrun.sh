#!/usr/bin/env bash
# I3B RS-8: infra/runbooks/restore.md A3-A9 executed from the runbook's OWN bash blocks
# (extracted at run time, never retyped) against a hosted-shaped stand-in database on an
# I3B-owned integration stack. Only A1/A2 are replaced: no AWS, no SSM, no hosted project -
# HOSTED, BACKUP, IMAGE and PGPASSWORD are the stand-in's local values.
#
#   STACK=<postgres container of the stack> PGPORT_STANDIN=<its published port> \
#   STANDIN_PASSWORD=<its local literal> research/plan/evidence/i/i3b/runbook-dryrun.sh
#
# The stand-in is I1B's hosted shape: migrations 0001-0002 plus bk02's HOSTED_SEED (four
# users, two keys, one usage row, no ledger). It is dropped on exit.
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
: "${STACK:?}" "${PGPORT_STANDIN:?}" "${STANDIN_PASSWORD:?}"
BOOK=infra/runbooks/restore.md
STANDIN=infrx_i3b_dry_hosted

admin() { docker exec -i "$STACK" psql -U supabase_admin -d template1 -v ON_ERROR_STOP=1 -q "$@"; }
cleanup() { admin -c "drop database if exists $STANDIN with (force)" >/dev/null 2>&1 || true; }
trap cleanup EXIT

block() {   # the ```bash blocks of one "### Ax" section of the runbook, in order
  awk -v want="### $1 " '
    index($0, "### ") == 1 { inside = (index($0, want) == 1) }
    inside && /^```bash$/ { code = 1; next }
    inside && /^```$/ && code { code = 0; next }
    inside && code { print }' "$BOOK"
}

echo "== stand-in: $STANDIN = 0001-0002 + HOSTED_SEED"
admin -c "drop database if exists $STANDIN with (force)" \
      -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'postgres' and pid <> pg_backend_pid()" \
      -c "create database $STANDIN template postgres owner postgres" >/dev/null
for f in apps/app/supabase/migrations/000[1-2]_*.sql; do
  docker exec -i "$STACK" psql -U postgres -d "$STANDIN" -v ON_ERROR_STOP=1 -q --single-transaction -f - < "$f"
done
python3 -c 'import re,sys; print(re.search(r"HOSTED_SEED = \"\"\"(.*?)\"\"\"", open(sys.argv[1]).read(), re.S).group(1))' \
  tests/integration/backend/recovery/test_restore.py |
  docker exec -i "$STACK" psql -U postgres -d "$STANDIN" -v ON_ERROR_STOP=1 -q

# A1/A2 stand-ins (local values only)
PY=apps/infrx-api/.venv/bin/python
HOSTED="host=127.0.0.1 port=$PGPORT_STANDIN user=postgres dbname=$STANDIN sslmode=disable"
BACKUP="$(mktemp -d)/hosted-$(date -u +%Y%m%dT%H%M%SZ)"
IMAGE=$($PY -c 'import runpy; print(runpy.run_path("infra/runbooks/pgrestore.py")["IMAGE"])')
export PGPASSWORD="$STANDIN_PASSWORD"

for section in A3 A4 A5 A6 A7 A9; do
  echo "== $section ($(date -u +%T)) - $(block "$section" | grep -vc '^\s*#') lines from $BOOK"
  started=$(date +%s.%N)
  # shellcheck disable=SC1090
  source <(block "$section")
  echo "-- $section done in $(echo "$(date +%s.%N) - $started" | bc) s"
done
docker ps -a --format '{{.Names}}' | grep -x infrx-i3b-restore && { echo "A9 left the container"; exit 1; }
[ -e "$BACKUP" ] && { echo "A9 left the backup"; exit 1; }
echo "== dry run complete: A3-A9 exit 0"
