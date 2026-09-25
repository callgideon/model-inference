#!/usr/bin/env bash
# E3C revert-type negative controls: build the two scratch trees `runner.py --control` runs.
#   tests/integration/backend/e3c/control_trees.sh <sha> <outdir> [nc-id ...]
# Prints `--control NC=TREE` arguments. Each tree is `git archive <sha>` with ONE fix removed;
# nothing touches a ref, an index or a worktree. A patch that does not apply is a hard stop
# (exit 1), never a partially reverted tree.
#
# nc-admission-ready (ADMISSION-READY, s04): the readiness barrier's product commits
# reverse-applied newest first - the relay admitting through ReadinessStore.admit_ready
# (W5-ADMIT-WIRING d58139f3 + its pilot anchor f5784d0c) and W5 item 1 itself (16ff5771,
# 245dcb75, 53dc95ae) - then the worker's `readiness=PgLifecycle(...)` argument (W5 wiring 1,
# fa446ae7) removed by hand: that commit also composed the reconciliation gauges and the
# `PgLifecycle` import M6-WIRING now shares, so reverting it whole kills the worker (E3C
# early run on 9d61d1e1). The schema (D10 0019) stays: nothing calls it. Only
# apps/infrx-api/infrx changes; the tree must pass ruff's undefined-name check (F821).
#
# nc-retention-durable (RETENTION-DURABLE, s06): D10.b's durable liveness rule is ONE
# function, `infrx.content_referenced` (0020, 13aeb6d3, which also creates everything M5,
# G7 and M6 stand on, so the commit cannot be reverted whole). The tree adds a last
# migration that redefines it as "nothing is referenced": the pre-D10 world, where no durable
# record kept a live job's media. M6's collector then deletes whatever is past its grace.
set -euo pipefail
sha=${1:?sha}; out=${2:?outdir}; shift 2
want=" ${*:-nc-admission-ready nc-retention-durable} "
args=()
repo=$(git rev-parse --show-toplevel)
short=$(git -C "$repo" rev-parse --short=8 "$sha")
ADMISSION_READY=(f5784d0c d58139f3 16ff5771 245dcb75 53dc95ae)
WIRING_1="readiness=PgLifecycle(connect, limits=limits)"

tree() {                                  # tree <name>: a fresh archive of <sha>
  local dir="$out/$1-$short"
  rm -rf "$dir" && mkdir -p "$dir"
  git -C "$repo" archive "$sha" | tar -x -C "$dir"
  echo "$dir"
}

if [[ $want == *" nc-admission-ready "* ]]; then
ready=$(tree nc-admission-ready)
for commit in "${ADMISSION_READY[@]}"; do
  git -C "$repo" merge-base --is-ancestor "$commit" "$sha" || {
    echo "nc-admission-ready: $commit is not in $sha" >&2; exit 1; }
  git -C "$repo" diff "$commit^" "$commit" -- apps/infrx-api/infrx \
    | patch -R -p1 -d "$ready" --no-backup-if-mismatch --forward --silent || {
      echo "nc-admission-ready: reverting $commit does not apply on $sha" >&2; exit 1; }
done
main="$ready/apps/infrx-api/infrx/worker/__main__.py"
[[ $(grep -c "$WIRING_1" "$main") == 1 ]] || {
  echo "nc-admission-ready: W5 wiring 1's argument is not exactly once in $main" >&2; exit 1; }
python3 - "$main" "$WIRING_1" <<'PY'
import re, sys
path, arg = sys.argv[1], sys.argv[2]
text = open(path).read()
text, n = re.subn(r",\s*" + re.escape(arg), "", text)
assert n == 1, n
open(path, "w").write(text)
PY
if grep -rq "admit_ready\|readiness=" "$ready/apps/infrx-api/infrx/gateway" \
     "$ready/apps/infrx-api/infrx/worker"; then
  echo "nc-admission-ready: the tree still composes the readiness barrier" >&2; exit 1
fi
command -v ruff >/dev/null || { echo "nc-admission-ready: ruff is needed for F821" >&2; exit 1; }
ruff check --no-cache --quiet --select F821 "$ready/apps/infrx-api/infrx" >&2 || {
  echo "nc-admission-ready: the reverted tree has undefined names" >&2; exit 1; }
args+=(--control "nc-admission-ready=$ready")
fi

if [[ $want == *" nc-retention-durable "* ]]; then
durable=$(tree nc-retention-durable)
cat > "$durable/apps/app/supabase/migrations/9999_e3c_nc_retention_durable.sql" <<'SQL'
-- E3C nc-retention-durable (a scratch tree only, never a real migration): D10.b's durable
-- liveness rule removed - no reference keeps content.
create or replace function infrx.content_referenced(c infrx.content_objects, p_now timestamptz)
returns text language sql stable security definer set search_path = infrx, public, pg_temp
as $$ select null::text $$;
SQL
args+=(--control "nc-retention-durable=$durable")
fi

echo "${args[*]}"
