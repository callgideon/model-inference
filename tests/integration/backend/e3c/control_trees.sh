#!/usr/bin/env bash
# E3C revert-type negative controls: build the two scratch trees `runner.py --control` runs.
#   tests/integration/backend/e3c/control_trees.sh <sha> <outdir> [nc-id ...]
# Prints `--control NC=TREE` arguments. Each tree is `git archive <sha>` with ONE fix removed;
# nothing touches a ref, an index or a worktree. A patch that does not apply is a hard stop
# (exit 1), never a partially reverted tree.
#
# nc-admission-ready (ADMISSION-READY, s04): the readiness barrier's product commits
# reverse-applied newest first - the relay admitting through ReadinessStore.admit_ready
# (W5-ADMIT-WIRING d58139f3 + its pilot anchor f5784d0c), the worker composing the marker-gated
# door (W5 wiring 1 fa446ae7) and W5 item 1 itself (16ff5771, 245dcb75, 53dc95ae). The
# schema (D10 0019) stays: nothing calls it. Only apps/infrx-api/infrx is reverted.
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
ADMISSION_READY=(f5784d0c d58139f3 fa446ae7 16ff5771 245dcb75 53dc95ae)

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
if grep -rq "admit_ready" "$ready/apps/infrx-api/infrx/gateway"; then
  echo "nc-admission-ready: the gateway still admits through admit_ready" >&2; exit 1
fi
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
