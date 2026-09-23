#!/usr/bin/env bash
# Merge 6: codex/e4b-certify (analysed at 5afe3d9; base 7c52627, i.e. before D5/phase 3).
# ONE textual conflict:
#   Makefile api-mutants - keep the integration side's lists and comment, add E4B's two comment
#   lines and its second, root-run command (tests/integration/backend/test_e4b_mutants.py).
# ONE semantic conflict (merges clean, test red):
#   certify.dataset_check pends the ledger half on owners=("D5",) when build_operations refuses;
#   the phase-3 merge removed D5 from the vocabulary (stack.PENDING), so Report.check turns it
#   into an untyped FAIL and test_e4b_the_dataset_drill_pends_on_the_owner_it_needs_... fails
#   ('FAIL' != 'PENDING'). With D5 request 4 applied (step 2) build_operations refuses only
#   without DATABASE_URL, i.e. off the box: the owner is BOX (E4B's own rule: "a check the local
#   target can never judge names BOX, not a task"). The mutant anchor is kept byte-identical.
# A second semantic conflict (E4B x cutover): E4B-endpoint.md is generated and went stale
#   when the cutover published W3's pins (351d084) - fixed in a2 below.
# Coordinator edits, one commit each:
#   a. the semantic fix above; a2. the endpoint document;
#   b. E4B request 1: namespace e4b (NAMESPACES +1300 -> 56800-56899, TASK_BLOCKS, TASK_PORTS pg 56832);
#   c. tasks.json E4B -> implemented (software half), ledger regenerated;
#   d. Makefile: api-mutants lists in one canonical order (track groups), comment names the lists.
# Needs: E4B_VERIFY (the E4B verifier verdict/reference).
set -euo pipefail
: "${E4B_VERIFY:?set E4B_VERIFY to the E4B verifier verdict/reference}"
REF=${E4B_REF:-codex/e4b-certify}
if ! git merge --no-ff --no-edit -m "merge: E4B certify, software half ($(git rev-parse --short "$REF"))" "$REF"; then
  test "$(git diff --name-only --diff-filter=U)" = "Makefile"
  python3 - <<'PY'
import subprocess
show = lambda stage: subprocess.run(["git", "show", f":{stage}:Makefile"], check=True,
                                    capture_output=True, text=True).stdout
ours, theirs = show(2), show(3)
comment = ("# E4B's list lives beside its runner in tests/integration/backend (outside apps/infrx-api), so it\n"
           "# runs from the root with the pinned interpreter, through the same shared R83 runner.\n")
command = ("\tINFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider "
           "tests/integration/backend/test_e4b_mutants.py\n")
assert comment in theirs and command in theirs and comment not in ours
lines = ours.splitlines(keepends=True)
head = next(i for i, l in enumerate(lines) if l.startswith("api-mutants:"))
assert lines[head + 1].startswith("\tcd $(API) && INFRX_MUTANTS=all")
lines[head + 1] += command
lines[head] = comment + lines[head]
open("Makefile", "w").write("".join(lines))
PY
  git add Makefile
  git commit --no-edit -q
fi

# a. the dataset drill's ledger half pends on BOX.
python3 - <<'PY'
import pathlib
edits = {
 "tests/integration/backend/certify.py": [
  ('    """The provisioned client\'s own view (G6B `Operations.tenant(secret)`), or None while no\n'
   '    PostgreSQL adapter is wired (`build_operations` refuses until D5)."""\n',
   '    """The provisioned client\'s own view (G6B `Operations.tenant(secret)`), or None where\n'
   '    `build_operations` refuses: without the deployment\'s DATABASE_URL (off the box)."""\n'),
  ('                     "client invariants hold; `infrx.operations.cli.build_operations` refuses "\n'
   '                     "(no PostgreSQL AccountView/Ledger adapter), so the tenant\'s ledger cannot "\n'
   '                     "be read", owners=("D5",), measured=measured, label=target["label"])\n',
   '                     "client invariants hold; `infrx.operations.cli.build_operations` refuses "\n'
   '                     "without the deployment\'s DATABASE_URL, so the tenant\'s ledger cannot be "\n'
   '                     "read here", owners=("BOX",), measured=measured, label=target["label"])\n')],
 "tests/integration/backend/test_certify.py": [
  ('    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,\n'
   '                                                                          ["D5"])\n',
   '    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,\n'
   '                                                                          ["BOX"])\n')],
 "tests/integration/backend/e4b_mutants.py": [
  ('"no ledger adapter is PENDING on D5, never PASS"',
   '"no ledger adapter is PENDING on the box, never PASS"')]}
for path, pairs in edits.items():
    p = pathlib.Path(path); s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:60])
        s = s.replace(old, new)
    p.write_text(s)
PY
git add tests/integration/backend/certify.py tests/integration/backend/test_certify.py tests/integration/backend/e4b_mutants.py
git commit -q -m "E4B x E3B phase 3: the dataset drill's ledger half pends on BOX (D5 left the pending vocabulary; build_operations refuses only without DATABASE_URL since D5 request 4)"

# a2. E4B x cutover (semantic): E4B-endpoint.md is generated from the code and was stale once
#     the cutover published W3's pins (test_endpoint_doc red; every E4B mutant broken_runner by
#     the pristine baseline). The generator's caveats ("fixture placeholder", "moving tag",
#     "held at this commit") are now false: reword, then regenerate.
python3 - <<'PY'
import pathlib
p = pathlib.Path("tests/integration/backend/endpoint_doc.py"); s = p.read_text()
pairs = [
 ('        "it is stale. It describes the metered endpoint **as the cutover mounts it** (G2-R1,",\n'
  '        "held at this commit): until then the deployed gateway is the legacy one. Nothing here",\n',
  '        "it is stale. It describes the metered endpoint **as the cutover mounts it** (G2-R1,",\n'
  '        "merged; the box serves the legacy gateway until the I2B rollout). Nothing here",\n'),
 ('             "⚠️ the fixture placeholder, not W3\'s measured pin (E4B config-pin finding)"),\n',
  '             "W3\'s measured pin (`models/marlin2b/serving-version.json`), published since the "\n'
  '             "cutover (E4B B1, 351d084)"),\n'),
 ('             "⚠️ a moving tag in the published record (same finding)"))),\n',
  '             "W3\'s digest-pinned image (same record; `runtime_image_digest` stays a new "\n'
  '             "serving version\'s, R76)"))),\n')]
for old, new in pairs:
    assert s.count(old) == 1, old[:70]
    s = s.replace(old, new)
p.write_text(s)
PY
apps/infrx-api/.venv/bin/python tests/integration/backend/endpoint_doc.py --write
git add tests/integration/backend/endpoint_doc.py research/plan/evidence/e/E4B-endpoint.md
git commit -q -m "E4B x cutover: the endpoint document's release rows name W3's measured pins (published since 351d084) and the merged cutover; regenerated with endpoint_doc.py --write"

# b. E4B request 1: a compose namespace of its own (as E3B2's IR2-1 did for e3b2).
python3 - <<'PY'
import pathlib
p = pathlib.Path("tests/integration/harness.py"); s = p.read_text()
old = 'NAMESPACES = {"e2": 0, "e3b2": 1200}\n'
assert s.count(old) == 1
p.write_text(s.replace(old, 'NAMESPACES = {"e2": 0, "e3b2": 1200, "e4b": 1300}\n')
             .replace("(e3b2: 56700-56799, E2's +1200).", "(e3b2: 56700-56799, E2's +1200; e4b: 56800-56899, +1300)."))
p = pathlib.Path("apps/infrx-api/infrx/contracts/tasklocal.py"); s = p.read_text()
old = '    "e3b2": {"postgres": 56732},\n}\n'
assert s.count(old) == 1
s = s.replace(old, '    "e3b2": {"postgres": 56732},\n    # E4B certifies on the E2 stack as namespace `e4b` (56800-56899, E4B request 1)\n'
                   '    "e4b": {"postgres": 56832},\n}\n')
old = '    "e3b2": {"compose": (56700, tuple(range(56701, 56800)))},\n}\n'
assert s.count(old) == 1
s = s.replace(old, old[:-2] + '    "e4b": {"compose": (56800, tuple(range(56801, 56900)))},\n}\n')
p.write_text(s)
PY
git add tests/integration/harness.py apps/infrx-api/infrx/contracts/tasklocal.py
git commit -q -m "tests/integration: namespace e4b (56800-56899) in harness.NAMESPACES and tasklocal TASK_BLOCKS/TASK_PORTS (E4B request 1, as E3B2 IR2-1)"

# c. the manifest.
E4M=$(git log -1 --merges --format=%h --grep="^merge: E4B certify")
E4M=$E4M DAY=$(date -u +%F) python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path("research/plan/tasks.json")
raw = p.read_text(encoding="utf-8")
d = json.loads(raw)
assert json.dumps(d, indent=2, ensure_ascii=False) + "\n" == raw
t = {x["id"]: x for x in d["tasks"]}["E4B"]
assert t["status"] == "planned" and t["disposition"] == "backend-first-addition"
t["status"] = "implemented"
t["disposition"] = (
    f"backend-first-addition. Software half merged --no-ff on claude/backend-impl at "
    f"{os.environ['E4M']} ({os.environ['DAY']}) after review (fix_required at 37a4652 -> one fix "
    f"round -> verifier {os.environ['E4B_VERIFY']}): the certify runner "
    "(tests/integration/backend/certify.py), the protocol (amendment 3), the endpoint document and "
    "the release decision (PENDING). The box half - certify.py --box after the I2B rollout, with "
    "E1B L2-L7 - and the release decision remain; BACKEND-READY is not claimed")
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
python3 research/plan/scripts/validate_plan.py --write-ledger
git add research/plan/tasks.json research/plan/17-task-ledger.md
git commit -q -m "plan: manifest E4B implemented (software half; the box half and the release decision remain); ledger regenerated"

# d. the api-mutants lists in one canonical order: track groups in the existing order, each
#    track's lists together (M1-L2's S3 list moves beside M's), E4B's root-run command second.
python3 - <<'PY'
import pathlib, re
p = pathlib.Path("Makefile"); s = p.read_text()
m = re.search(r"^\tcd \$\(API\) && INFRX_MUTANTS=all uv run --frozen pytest -q (.*)$", s, re.M)
have = m.group(1).split()
order = ["tests/contracts/test_mutants.py",
         "tests/m/test_mutants.py", "tests/m/test_pilot_mutants.py", "tests/m/test_s3_mutants.py",
         "tests/q/test_mutants.py", "tests/q/test_valkey_mutants.py", "tests/q/test_reconcile_mutants.py",
         "tests/j/test_mutants.py",
         "tests/w/test_mutants.py", "tests/w/test_loop_mutants.py", "tests/w/test_w3_mutants.py",
         "tests/w/test_w4_mutants.py",
         "tests/t/test_trace_mutants.py",
         "tests/d/test_migration_mutants.py", "tests/d/test_code_mutants.py",
         "tests/d/test_code_mutants_d3.py", "tests/d/test_code_mutants_d4.py",
         "tests/d/test_code_mutants_d5.py", "tests/d/test_signup.py",
         "tests/g/test_mutants.py", "tests/g/ops/test_mutants.py",
         "tests/g/uploads/test_uploads_mutants.py", "tests/g/jobs/test_jobs_mutants.py",
         "tests/i/test_mutants.py"]
assert sorted(have) == sorted(order) and len(set(have)) == len(have), set(have) ^ set(order)
s = s[:m.start(1)] + " ".join(order) + s[m.end(1):]
old = "# Track mutant lists join here as their task merges (M1, Q1, J1, W1, T1, D1, G1, M1-L2 — "
assert s.count(old) == 1
s = s.replace(old, "# Track mutant lists join here as their task merges (M1, M pilot, M1-L2, Q1, J1, W1, T1, D1-D5, G1, I; E4B below — ")
p.write_text(s)
PY
git add Makefile
git commit -q -m "Makefile: api-mutants lists in one canonical order (each track's lists together; M pilot and M1-L2 beside M); the comment names every list"
