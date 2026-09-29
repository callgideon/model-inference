#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L2 (coordinator host): the hosted apply of the Lab migrations,
# ONLY under R151/R201's three conditions, then exactly infra/rollout/hosted-migrate.sh.
#
#   infra/lab/rollout/lab-migrate.sh --release <40 hex> --hosted-at 0026 --window <ref> \
#       [--through w6b | --through w7 --expect <COPY_DIGEST of a green w6b run>]
#
# Refused (exit 2, nothing dialled) unless all three hold:
#   1. the known-good re-proof: `infra/rollout/known-good.py --list --applied <newest>` finds a
#      rollback target KNOWN-GOOD at the release's newest migration (a schema_proof through it,
#      recorded in infra/rollout/known-good.json after infra/runbooks/schema_proof.py);
#   2. the reviewed EXPECTED_PENDING patch: hosted-migrate.sh's EXPECTED_PENDING is exactly the
#      release's migrations after --hosted-at, and its W7 post-check names the newest file;
#   3. an operator window tied to I2L/P-08: --window <ref> (logged with the run).
# --hosted-at is what `migrate.py plan` reported on hosted (never guessed). Then hosted-migrate.sh
# runs as it always does (its own exit codes: 10 before any hosted write, 20 after one).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
HOSTED_MIGRATE=${HOSTED_MIGRATE:-infra/rollout/hosted-migrate.sh}
KNOWN_GOOD=${KNOWN_GOOD:-infra/rollout/known-good.py}
PY=${PY:-apps/infrx-api/.venv/bin/python}
MIGRATIONS=${MIGRATIONS:-apps/app/supabase/migrations}
RELEASE=; HOSTED_AT=; WINDOW=; THROUGH=w6b; EXPECT=
while [ $# -gt 0 ]; do case "$1" in
  --release) RELEASE=$2; shift 2 ;; --hosted-at) HOSTED_AT=$2; shift 2 ;;
  --window) WINDOW=$2; shift 2 ;; --through) THROUGH=$2; shift 2 ;; --expect) EXPECT=$2; shift 2 ;;
  *) echo "unknown argument $1" >&2; exit 2 ;; esac; done
stop() { echo "lab-migrate: STOP (R151): $*" >&2; exit 2; }
[[ $RELEASE =~ ^[0-9a-f]{40}$ ]] || stop "--release <the release commit, 40 hex>"
[[ $HOSTED_AT =~ ^[0-9]{4}$ ]] || stop "--hosted-at NNNN: hosted's newest applied migration, from migrate.py plan"
files=$(cd "$MIGRATIONS" && ls [0-9][0-9][0-9][0-9]_*.sql | sort)
newest=$(tail -1 <<<"$files")
pending=$(awk -v at="$HOSTED_AT" 'substr($0, 1, 4) > at { printf "%s%s", sep, substr($0, 1, 4); sep = ", " }' <<<"$files")
[ -n "$pending" ] || stop "nothing is pending after $HOSTED_AT: there is no Lab apply to make"
# 3. the window
[[ $WINDOW =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{3,}$ ]] || stop "condition 3: --window <the operator window reference tied to I2L/P-08>"
# 2. the reviewed EXPECTED_PENDING patch
current=$(sed -n 's/^EXPECTED_PENDING="\([^"]*\)".*/\1/p' "$HOSTED_MIGRATE")
[ "$current" = "$pending" ] || stop "condition 2: $HOSTED_MIGRATE EXPECTED_PENDING is '$current', not '$pending': apply the reviewed patch (runbook §2)"
grep -q "${newest%.sql}" "$HOSTED_MIGRATE" || stop "condition 2: $HOSTED_MIGRATE's W7 post-check does not name ${newest%.sql} (runbook §2 patch)"
# 1. the known-good re-proof
"$PY" "$KNOWN_GOOD" --list --applied "${newest:0:4}" > /dev/null \
  || stop "condition 1: no rollback target is KNOWN-GOOD at ${newest:0:4}: rerun infra/runbooks/schema_proof.py and record its schema_proof (runbook §2)"
echo "lab-migrate: R151 conditions hold (window $WINDOW; pending $pending); running $HOSTED_MIGRATE --through $THROUGH"
exec "$HOSTED_MIGRATE" --release "$RELEASE" --through "$THROUGH" ${EXPECT:+--expect "$EXPECT"}
