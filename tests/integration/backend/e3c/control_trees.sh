#!/usr/bin/env bash
# E3C revert-type negative controls: build the scratch trees `runner.py --control` runs.
#   tests/integration/backend/e3c/control_trees.sh <sha> <outdir> [nc-id ...]
# Prints `--control NC=TREE` arguments. Each tree is `git archive <sha>` with ONE fix removed;
# nothing touches a ref, an index or a worktree. A patch that does not apply is a hard stop
# (exit 1), never a partially reverted tree. Default: all five controls below.
#
# nc-admission-ready (ADMISSION-READY, s04): the readiness barrier's product commits
# reverse-applied newest first - W5-F5B's post-marker attach (0a230353), W5-F5's
# no-recheck-after-the-marker (e592c6d0), the relay admitting through
# ReadinessStore.admit_ready (W5-ADMIT-WIRING d58139f3 + its pilot anchor f5784d0c) and W5
# item 1 itself (16ff5771,
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
#
# nc-dur-fence (DUR-FENCE, s14), nc-dur-cap (DUR-CAP, s15), nc-credit-rate (CREDIT-RATE, s16):
# one check removed from one SQL function by a last migration holding that function's latest
# definition in the tree with the check changed - fence_lease's generation check,
# admission_checks's three active-job cap comparisons off by one, terminalize's debit read at
# the alias's current listing instead of the admitted card. `reverts.py` holds each anchor
# and refuses (exit 1) a tree where it does not occur exactly as often as written.
set -euo pipefail
sha=${1:?sha}; out=${2:?outdir}; shift 2
want=" ${*:-nc-admission-ready nc-retention-durable nc-dur-fence nc-dur-cap nc-credit-rate} "
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
args=()
repo=$(git rev-parse --show-toplevel)
short=$(git -C "$repo" rev-parse --short=8 "$sha")
ADMISSION_READY=(0a230353 e592c6d0 f5784d0c d58139f3 16ff5771 245dcb75 53dc95ae)

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
  # d58139f3's pilot hunk is rebased over WR-P25-1 (2e6931ea), which changed only the
  # argument of the call it moves: `_pg_lifecycle(connect, settings.pilot)` -> `settings`.
  git -C "$repo" diff "$commit^" "$commit" -- apps/infrx-api/infrx \
    | sed 's/_pg_lifecycle(connect, settings\.pilot)/_pg_lifecycle(connect, settings)/' \
    | patch -R -p1 -d "$ready" --no-backup-if-mismatch --forward --silent || {
      echo "nc-admission-ready: reverting $commit does not apply on $sha" >&2; exit 1; }
done
main="$ready/apps/infrx-api/infrx/worker/__main__.py"
python3 - "$main" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
# W5 wiring 1's argument, in either spelling it has had (fa446ae7; W5-F5B 6dbaae64 shares
# M6's lifecycle): exactly once, or the tree is not the one this control was written for.
text, n = re.subn(r",\s*readiness=(?:lifecycle|PgLifecycle\(connect, limits=limits\))", "", text)
assert n == 1, f"W5 wiring 1's readiness argument found {n} times in {path}"
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

for nc in nc-dur-fence nc-dur-cap nc-credit-rate; do
  [[ $want == *" $nc "* ]] || continue
  dir=$(tree "$nc")
  python3 "$here/reverts.py" "$dir" "$nc" || {
    echo "$nc: the revert does not apply on $sha" >&2; exit 1; }
  args+=(--control "$nc=$dir")
done

echo "${args[*]}"
