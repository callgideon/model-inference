#!/usr/bin/env bash
# Merge 2: codex/e3b-phase3-bodies (analysed at a4facba; replayed clean on a scratch clone at the
# final head b9529d1 on 2026-09-24 with D5 4bfdbf0, cutover 1bed457, M1-L2 ba26ca4), in the SAME step as merge 1.
# No conflict. The branch already carries, as its own commits, D5 integration requests
# 1 (Makefile d5 list, eb08901; harness list 0018, 54d3955), 2 (contracts retirement, 8c00bb4,
# +/- lines identical to D5's contracts-retire-0016-refusal.diff, sha256 a8e75c3f...) and 8
# (pgstate 0018 rows, 54d3955): the coordinator applies NONE of them again.
# It also carries cutover 6cb8ebe and M1-L2 e1bb54f (merges 3 and 4 add only their later commits).
# Coordinator edits after the merge, one commit each:
#   a. D5 request 4: cli.build_operations on the PostgreSQL adapters (d5-cli-build-operations.diff)
#   b. D5 request 6: q3rig INFRX_Q3_STORE switch (q3rig.diff; default "fake" = unchanged)
#   c. validate_plan: fenced code is not a link (D5 evidence's q3rig diff has `](org, ...)`);
#      research/plan/handoffs: links to files the cutover retired become plain text
#   d. tasks.json: D5 -> implemented, E3B disposition += phase 3; ledger regenerated
#   e. phase-3 IR3F-2(b): the gate's `make api-test` gets a D task of its own (contracts.tasklocal
#      "e3b2d" 55438/55468, outside the e3b2 block; run.make_env passes it with the lock/queue
#      Valkeys while the stack is up); the runner's make_env case pins the four keys
# Needs: D5_VERIFY (the D5 verifier reference, e.g. "pass at <sha> (evidence/d/D5-verify-<sha>.json)").
set -euo pipefail
: "${D5_VERIFY:?set D5_VERIFY to the D5 verifier verdict/reference}"
REF=${E3B3_REF:-codex/e3b-phase3-bodies}
git merge --no-ff --no-edit -m "merge: E3B phase 3 bodies ($(git rev-parse --short "$REF")) with D5" "$REF"

# a + b: the exact diffs D5 handed over, read from its evidence and checked by sha256.
export D5DIFFS=$(mktemp -d)
python3 - <<'PY'
import hashlib, os, pathlib
t = pathlib.Path("research/plan/evidence/d/D5-4bcac3b.md").read_text()
want = {"d5-cli-build-operations.diff": "d2a26348d34f61d9f54c13523c5b72c8e7437fc3be4ee1215d495af823f471ea",
        "q3rig.diff": "4b69278e91efdf4003d31daf64371645cc089c09dd099f5ca1a7200f9f281497"}
for name, sha in want.items():
    body = t[t.index(f"### `{name}`"):].split("```diff\n", 1)[1].split("\n```\n", 1)[0] + "\n"
    assert hashlib.sha256(body.encode()).hexdigest() == sha, name
    pathlib.Path(os.environ["D5DIFFS"], name).write_text(body)
PY
# 2(a) DROPPED 2026-09-23T23:5xZ (coordinator): D5 request 4 is superseded — the cutover's f7d9b03
# implements cli.build_operations from DATABASE_URL; D5's own re-check found the diff applies
# neither forward nor reverse on the phase-3 head. Guard: the diff must NOT apply forward.
if git apply --check "$D5DIFFS/d5-cli-build-operations.diff" 2>/dev/null; then
  echo "unexpected: the cli diff still applies — the cutover's build_operations is missing"; exit 1; fi
git apply --index "$D5DIFFS/q3rig.diff"
git commit -q -m "D5 integration request 6 (D3 request 5): q3rig INFRX_Q3_STORE=postgres runs the Q3 rig on the real store (default fake: unchanged; the 4 PostgreSQL rig failures are Q's)"
rm -r "$D5DIFFS"

# c: the plan validator and the handoff links.
python3 - <<'PY'
import pathlib, re
p = pathlib.Path("research/plan/scripts/validate_plan.py")
s = p.read_text()
old = '    """Read inline destinations including Next.js route names with parentheses."""\n'
new = old + "    source = re.sub(r'^```.*?^```', '', source, flags=re.M | re.S)   # code is not a link\n"
assert s.count(old) == 1 and "code is not a link" not in s
p.write_text(s.replace(old, new))
gone = {"apps/infrx-api/gateway.py": "retired at the cutover, 43fe900",
        "apps/infrx-api/tests/test_gateway_auth.py": "retired at the cutover, 88e1cfd",
        "apps/infrx-api/tests/test_inflight.py": "retired at the cutover, 88e1cfd",
        "apps/infrx-api/tests/test_media.py": "retired at the cutover, 88e1cfd"}
for doc in sorted(pathlib.Path("research/plan/handoffs").glob("*.md")):
    s = doc.read_text()
    for path, why in gone.items():
        s = s.replace(f"[{path}](../../../{path})", f"`{path}` ({why})")
    doc.write_text(s)
PY
git add research/plan/scripts/validate_plan.py research/plan/handoffs
git commit -q -m "plan: validate_plan skips fenced code (D5 evidence's q3rig diff); handoff links to the files the cutover retired become plain text"

# d: the manifest.
D5M=$(git log -1 --merges --format=%h --grep="^merge: D5 terminal transaction")
E3M=$(git log -1 --merges --format=%h --grep="^merge: E3B phase 3 bodies")
DAY=$(date -u +%F)
D5M=$D5M E3M=$E3M DAY=$DAY python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path("research/plan/tasks.json")
raw = p.read_text(encoding="utf-8")
d = json.loads(raw)
assert json.dumps(d, indent=2, ensure_ascii=False) + "\n" == raw, "tasks.json would re-encode"
t = {x["id"]: x for x in d["tasks"]}
assert t["D5"]["status"] == "planned" and t["D5"]["disposition"] == "modify"
t["D5"]["status"] = "implemented"
t["D5"]["disposition"] = (
    f"modify. Merged --no-ff on claude/backend-impl at {os.environ['D5M']} ({os.environ['DAY']}) "
    f"together with E3B phase 3 ({os.environ['E3M']}) after independent review (fix_required at "
    f"c67e4f5 -> one fix round -> verifier {os.environ['D5_VERIFY']}); requests 1, 2 and 8 arrived "
    "applied on the phase-3 branch (eb08901, 54d3955, 8c00bb4), 4 (cli.build_operations) and 6 "
    "(q3rig) applied at the merge; 3, 5, 7 and the ruling candidates open "
    "(research/plan/evidence/d/D5-4bcac3b.md)")
t["E3B"]["disposition"] += (
    f"; phase 3 merged with D5 at {os.environ['E3M']} ({os.environ['DAY']}): every body real on the "
    "worker as its own process, G2-R1 and M3-U1/M3-U2 retired (M pilot-media 8b91648 on the branch), "
    "the video_upload journeys run; PENDING only I2B-R4 (fix_required at 4ac1419 -> one fix round "
    "-> verifier at b9529d1)")
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
python3 research/plan/scripts/validate_plan.py --write-ledger
git add research/plan/tasks.json research/plan/17-task-ledger.md
git commit -q -m "plan: manifest D5 implemented (merged with E3B phase 3); E3B phase 3 recorded; ledger regenerated"

# e: phase-3 IR3F-2(b). The canonical gate (make integration --layer 3) runs `make api-test` while the
# e3b2 stack holds 56732 (E2's PostgreSQL moved by +1200), so the D suites need a task of their own:
# "e3b2d" outside the block (P-21: the host's ephemeral range), passed only while the stack is up.
python3 - <<'PY'
import pathlib
p = pathlib.Path("apps/infrx-api/infrx/contracts/tasklocal.py")
s = p.read_text()
old = '    "e3b2": {"postgres": 56732},\n}\n'
new = ('    "e3b2": {"postgres": 56732},\n'
       '    # E3B phase 3 IR3F-2(b): the gate\'s `make api-test` runs the D suites while the e3b2 stack\n'
       '    # holds 56732, so they get a D task of their own outside the block (containers infrx-e3b2d-*)\n'
       '    "e3b2d": {"postgres": 55438, "valkey": 55468},\n}\n')
assert s.count(old) == 1 and "e3b2d" not in s
p.write_text(s.replace(old, new))
p = pathlib.Path("tests/integration/run.py")
s = p.read_text()
old = ('        env.update(INFRX_M_S3_ENDPOINT=harness.s3_endpoint(), INFRX_M_S3_LOCAL_CREDS="1")\n')
new = old + ('        # IR3F-2(b): the D suites inside `make api-test` get the gate\'s own D task (tasklocal\n'
             '        # "e3b2d") and lock/queue Valkeys, never the stack\'s PostgreSQL or another lane\'s\n'
             '        env.update(INFRX_D_TASK="e3b2d", INFRX_D2_VALKEY_PORT="55468",\n'
             '                   INFRX_D2_VALKEY_CONTAINER="infrx-e3b2d-valkey", INFRX_Q_VALKEY_PORT="55469")\n')
assert s.count(old) == 1 and "e3b2d" not in s
p.write_text(s.replace(old, new))
p = pathlib.Path("tests/integration/test_run.py")
s = p.read_text()
old = ('                                "INFRX_M_S3_ENDPOINT": harness.s3_endpoint(),\n'
       '                                "INFRX_M_S3_LOCAL_CREDS": "1"}, seen\n')
new = ('                                "INFRX_M_S3_ENDPOINT": harness.s3_endpoint(),\n'
       '                                "INFRX_M_S3_LOCAL_CREDS": "1",\n'
       '                                "INFRX_D_TASK": "e3b2d", "INFRX_D2_VALKEY_PORT": "55468",\n'
       '                                "INFRX_D2_VALKEY_CONTAINER": "infrx-e3b2d-valkey",\n'
       '                                "INFRX_Q_VALKEY_PORT": "55469"}, seen\n')
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
git add apps/infrx-api/infrx/contracts/tasklocal.py tests/integration/run.py tests/integration/test_run.py
git commit -q -m "E3B phase 3 IR3F-2(b): the gate's make api-test runs the D suites on a task of its own (tasklocal e3b2d 55438/55468, outside the e3b2 block) with lock/queue Valkeys while the stack is up; the make_env case pins the keys"
