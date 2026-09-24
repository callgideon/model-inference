#!/usr/bin/env bash
# Step 10 (coordinator, 2026-09-24): the PREP-WORKER (I2B-R5) preparation loop - the pilot's blocker.
# Branch codex/prep-worker (base 01103c9 = 865d352 + the worker branch with the 8b resolutions, so
# its worker files merge clean). Gated on PREPW_VERIFY. Expected conflicts: the Makefile api-mutants
# line (ours canonical; the lane adds tests/w/test_prep_worker_mutants.py after test_worker_main_mutants.py)
# and 08 (the lane's §5.1 PREPARATION_CONCURRENCY row + log line vs ours) - resolved like 8b: ours +
# every line the lane added since the merge base (a row after its predecessor, a log line appended).
set -euo pipefail
: "${PREPW_VERIFY:?PREPW_VERIFY must name the verifier verdict (pass at <sha>)}"
REF=${PREPW_REF:-codex/prep-worker}
if git merge-base --is-ancestor "$REF" HEAD; then echo "prep-worker: nothing to merge"; exit 0; fi
BASE=$(git merge-base HEAD "$REF")
git merge --no-ff --no-edit -m "merge: PREP-WORKER preparation loop ($(git rev-parse --short "$REF")) - python -m infrx.worker prepares admitted jobs (outbox -> claim_preparation -> media -> engine-exact prompt_tokens -> prepared); the pilot box emulation retired; verifier $PREPW_VERIFY" "$REF" || true
CONFLICTS=$(git diff --name-only --diff-filter=U)
for f in $CONFLICTS; do case "$f" in Makefile|research/plan/08-contracts-v1-encoding.md) ;; *) echo "unexpected conflict: $f"; git merge --abort; exit 1;; esac; done
BASE=$BASE REF=$REF CONFLICTS="$CONFLICTS" python3 - <<'PY'
import os, pathlib, subprocess
def show(rev, path): return subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=True).stdout
ref, base, conflicts = os.environ["REF"], os.environ["BASE"], os.environ["CONFLICTS"].split()
if "Makefile" in conflicts:
    path = "Makefile"; ours = show("HEAD", path); theirs = show(ref, path)
    new = "tests/w/test_prep_worker_mutants.py"
    assert new in theirs and new not in ours
    anchor = " tests/w/test_worker_main_mutants.py"
    assert ours.count(anchor) == 1
    pathlib.Path(path).write_text(ours.replace(anchor, anchor + " " + new))
if "research/plan/08-contracts-v1-encoding.md" in conflicts:
    path = "research/plan/08-contracts-v1-encoding.md"
    ours = show("HEAD", path); theirs = show(ref, path); basef = show(base, path)
    ours_lines, their_lines = ours.splitlines(keepends=True), theirs.splitlines(keepends=True)
    base_set, ours_set = set(basef.splitlines(keepends=True)), set(ours_lines)
    added = [(i, l) for i, l in enumerate(their_lines) if l not in base_set and l not in ours_set]
    assert added and len(added) <= 6, [l[:60] for _, l in added]
    for i, line in added:
        if line.startswith("- 20"):
            ours_lines.append(line if line.endswith("\n") else line + "\n"); continue
        pred = next((l for l in reversed(their_lines[:i]) if ours_lines.count(l) == 1), None)
        assert pred is not None and pred.startswith("|"), line[:60]
        ours_lines.insert(ours_lines.index(pred) + 1, line)
    pathlib.Path(path).write_text("".join(ours_lines))
PY
[ -n "$CONFLICTS" ] && { git add $CONFLICTS; git -c core.editor=true commit -q --no-edit; }
test -z "$(git diff --name-only --diff-filter=U)"
python3 research/plan/scripts/validate_plan.py >/dev/null
echo "merged $(git rev-parse --short HEAD)"
