#!/usr/bin/env bash
# Step 8b (added 2026-09-23T23:5xZ): the worker composition root `python -m infrx.worker` (lane I2B-R4,
# branch codex/i2b-r4-worker, based on the phase-3 head + the cutover a1e88dc). Gated on WORKER_VERIFY
# (the single-verifier verdict). Its Makefile list line unions like the others; its rehearse.sh change
# (sandbox deps for the release tree) is I's file — keep it.
set -euo pipefail
: "${WORKER_VERIFY:?WORKER_VERIFY must name the verifier verdict (pass)}"
REF=${WORKER_REF:-codex/i2b-r4-worker}
if git merge-base --is-ancestor "$REF" HEAD; then echo "worker: nothing to merge"; exit 0; fi
BASE=$(git merge-base HEAD "$REF")
git merge --no-ff --no-edit -m "merge: I2B-R4 worker composition root ($(git rev-parse --short "$REF")) - python -m infrx.worker, unit/installer alignment, rehearse.sh sandbox deps; verifier $WORKER_VERIFY" "$REF" || true
# Expected conflicts (dry-run 2026-09-24 at 245f5af): Makefile (api-mutants line: ours is the canonical
# order from step 6(d); the worker adds tests/w/test_worker_main_mutants.py) and 08 (ours added R95-R103
# and two log lines; the worker adds a §5.1 row and a log line). Anything else conflicting stops here.
CONFLICTS=$(git diff --name-only --diff-filter=U)
for f in $CONFLICTS; do case "$f" in Makefile|research/plan/08-contracts-v1-encoding.md) ;; *) echo "unexpected conflict: $f"; git merge --abort; exit 1;; esac; done
BASE=$BASE REF=$REF python3 - <<'PY'
import os, pathlib, subprocess
def show(rev, path): return subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=True).stdout
ours_rev, ref, base = "HEAD", os.environ["REF"], os.environ["BASE"]
# Makefile: ours + the worker's list inserted after tests/w/test_w4_mutants.py
path = "Makefile"; ours = show(ours_rev, path); theirs = show(ref, path)
assert "tests/w/test_worker_main_mutants.py" in theirs and "tests/w/test_worker_main_mutants.py" not in ours
anchor = " tests/w/test_w4_mutants.py"
assert ours.count(anchor) == 1
pathlib.Path(path).write_text(ours.replace(anchor, anchor + " tests/w/test_worker_main_mutants.py"))
# 08: ours + every line the worker ADDED since the base: a table row goes before its successor line
# (their file's next line that also exists in ours); a log line is appended.
path = "research/plan/08-contracts-v1-encoding.md"
ours = show(ours_rev, path); theirs = show(ref, path); basef = show(base, path)
ours_lines, their_lines = ours.splitlines(keepends=True), theirs.splitlines(keepends=True)
base_set, ours_set = set(basef.splitlines(keepends=True)), set(ours_lines)
added = [(i, l) for i, l in enumerate(their_lines) if l not in base_set and l not in ours_set]
assert added and len(added) <= 4, [l[:60] for _, l in added]
for i, line in added:
    if line.startswith("- 20"):
        line = line.replace("Next free ruling still R95", "next free ruling R104 after the merge (R95-R103 numbered by the coordinator)")
        ours_lines.append(line if line.endswith("\n") else line + "\n"); continue
    # a row goes right after its predecessor in THEIR file - the nearest preceding line that
    # exists exactly once in ours (a table row is unique; a blank line is not)
    pred = next((l for l in reversed(their_lines[:i]) if ours_lines.count(l) == 1), None)
    assert pred is not None and pred.startswith("|"), line[:60]
    j = ours_lines.index(pred) + 1; ours_lines.insert(j, line)
pathlib.Path(path).write_text("".join(ours_lines))
PY
git add Makefile research/plan/08-contracts-v1-encoding.md
git -c core.editor=true commit -q --no-edit
test -z "$(git diff --name-only --diff-filter=U)"
python3 research/plan/scripts/validate_plan.py >/dev/null
(cd apps/infrx-api && uv run --frozen --offline pytest -q -p no:cacheprovider tests/i 2>&1 | tail -2)
