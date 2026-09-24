#!/usr/bin/env bash
# Step 14 (coordinator, 2026-09-24): INTAKE-DRAIN - the gateway drains a bounded request body before a
# mid-body refusal so HTTP/1.1 clients read the typed 429 (E4B run2 overload: 14 of 24 typed 429s were lost
# as ReadError). Branch codex/intake-drain (base 4db74b6). Gated on DRAIN_VERIFY. Expected conflict: the
# rollout runbook (the lane adds a LARGE_BODY_LIMIT sizing row after W7e; ours added W7e + a log line) -
# resolved as in steps 8b/10: ours + every line the lane added since the merge base (a row after its unique
# predecessor, a log line appended). Any other conflict aborts.
set -euo pipefail
: "${DRAIN_VERIFY:?DRAIN_VERIFY must name the verifier verdict (pass at <sha>)}"
REF=${DRAIN_REF:-codex/intake-drain}
if git merge-base --is-ancestor "$REF" HEAD; then echo "intake-drain: nothing to merge"; exit 0; fi
BASE=$(git merge-base HEAD "$REF")
git merge --no-ff --no-edit -m "merge: INTAKE-DRAIN ($(git rev-parse --short "$REF")) - a bounded drain of the declared request body before a mid-body refusal (Connection: close kept), so the typed 429 + Retry-After reaches an uploading HTTP/1.1 client; LARGE_BODY_LIMIT sizing row; verifier $DRAIN_VERIFY" "$REF" || true
CONFLICTS=$(git diff --name-only --diff-filter=U)
for f in $CONFLICTS; do case "$f" in infra/runbooks/rollout.md) ;; *) echo "unexpected conflict: $f"; git merge --abort; exit 1;; esac; done
if [ -n "$CONFLICTS" ]; then
BASE=$BASE REF=$REF python3 - <<'PY'
import os, pathlib, subprocess
def show(rev, path): return subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=True).stdout
ref, base = os.environ["REF"], os.environ["BASE"]
path = "infra/runbooks/rollout.md"
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
  git add infra/runbooks/rollout.md && git commit -q --no-edit
fi
git log --oneline -1 | cat
