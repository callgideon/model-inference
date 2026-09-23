#!/usr/bin/env bash
# OPTION B, only if D5's verdict arrives well before the cutover's and M1-L2's: merging the
# phase-3 branch also merges the cutover at 6cb8ebe and M1-L2 at e1bb54f (both pre-verdict), so
# step 2 cannot run early. Instead: merge D5 alone and apply its requests 1, 2 and 8 by
# cherry-picking the phase-3 branch's own commits (the identical text), plus request 4 (cli).
# tasks.json D5 stays "planned" until the phase-3 merge (stale_pending: the integration head's
# stack.PENDING still names D5). Later, at the phase-3 merge, Makefile and
# tests/integration/pgstate.py conflict (the phase-3 side = these cherry-picks + its later
# commits: M1-L2's s3 list, edd7a79's anchor fix): take the phase-3 side for both
#   git checkout --theirs Makefile tests/integration/pgstate.py && git add ... && git commit --no-edit
# - measured: the tree then equals option A's tree after step 2's merge (bf918d8^{tree}).
# Then run the rest of 2-e3b-phase3-bodies.sh from "# b" (q3rig), "# c", "# d" (skip "# a": done here).
# The intermediate tree is NOT a gate candidate: 0018 makes terminalize real, so the layer-3
# tripwires dr07c and rc04b fail until phase 3 brings their bodies (D5 review CF-5/H-B1);
# no layer-3 run and no ff of main between this and the phase-3 merge. Not tested here.
set -euo pipefail
bash "$(dirname "$0")/1-d5-terminal-transaction.sh"
git cherry-pick eb08901 8c00bb4 54d3955
export D5DIFFS=$(mktemp -d)
python3 - <<'PY'
import hashlib, os, pathlib
t = pathlib.Path("research/plan/evidence/d/D5-4bcac3b.md").read_text()
name, sha = "d5-cli-build-operations.diff", "d2a26348d34f61d9f54c13523c5b72c8e7437fc3be4ee1215d495af823f471ea"
body = t[t.index(f"### `{name}`"):].split("```diff\n", 1)[1].split("\n```\n", 1)[0] + "\n"
assert hashlib.sha256(body.encode()).hexdigest() == sha
pathlib.Path(os.environ["D5DIFFS"], name).write_text(body)
PY
git apply --index "$D5DIFFS/d5-cli-build-operations.diff" && rm -r "$D5DIFFS"
git commit -q -m "D5 integration request 4: cli.build_operations composes the PostgreSQL adapters from DATABASE_URL (refuses without it)"
