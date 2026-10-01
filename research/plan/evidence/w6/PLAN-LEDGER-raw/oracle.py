#!/usr/bin/env python3
"""W6 plan-ledger oracle: one assertion per brief item (INT-01/02/03/04/06/07/10/11, P-08). Read-only.

    python3 research/plan/evidence/w6/PLAN-LEDGER-raw/oracle.py   # exit 1 and the failing items until the lane is done
"""
import json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PLAN = ROOT / "research/plan"
EVID = PLAN / "evidence/coordinator"
BASE = "08983639"
AUDIT = EVID / "2026-10-01-v1-audit.md"
PENDING = [(49, 68), (143, 155), (224, 236), (298, 307), (385, 396)]  # the five dimensions' "Pending" lists (audit line ranges)
RESUME6 = ("WR-LEM-SPAN WR-LW9-5 WR-LW9-6 WR-LR7 CMO-4 WR-C6-B1-FLAKE k08 P-08 WR-B4-2 WR-LAB2-4 WR-B3-1 WR-LL2-5 WR-R3I-OPEN P-11").split()
GATES = ["E3L", "E5L", "E6L", "E7L", "E8L", "E4-ON", "COMPLETE-LOCAL"]
fails = []


def check(name, ok, why=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f" -- {why}"))
    ok or fails.append(name)


def run(*cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


s = json.loads((EVID / "progress-state.json").read_text())
m = json.loads((PLAN / "tasks.json").read_text())
T = {t["id"]: t for t in m["tasks"]}
base_tasks = {t["id"]: t for t in json.loads(run("git", "show", f"{BASE}:research/plan/tasks.json")[1])["tasks"]}

# INT-01: header and one dated deployed block at the launch state
d = json.dumps(s.get("deployed", {}))
check("INT-01 main = 41693d5d", str(s.get("main", "")).startswith("41693d5d"), s.get("main"))
check("INT-01 integration_head = the wave base", str(s.get("integration_head", "")).startswith(BASE), s.get("integration_head"))
check("INT-01 deployed: runtime 41693d5d CREDIT", str(s["deployed"].get("release", "")).startswith("41693d5d") and s["deployed"].get("regime") == "credit")
check("INT-01 deployed: hosted 0001-0059 + digests, Lab 7ecbab0e, lab.callbill.ai, lab-control edge",
      all(x in d for x in ("0001-0059", "9566fa25", "88f9d412", "7ecbab0e", "lab.callbill.ai", "lab-control.callbill.ai", "infrx-lab")))
e4c = next(x for x in s["lanes"] if x["id"] == "E4C")
check("INT-01 E4C next_action = the four remaining steps",
      all(k in (e4c.get("next_action") or "") for k in ("members", "main", "certify", "checklist")), e4c.get("next_action"))

# INT-03: seven gate records with candidate, cells (PASS / NOT RUN[class] + rerun), evidence paths
g = s.get("gate_records", {})
check("INT-03 seven gate records", sorted(g) == sorted(GATES), sorted(g))
for k in GATES:
    r = g.get(k, {})
    cells = r.get("cells", [])
    bad = [c.get("id") for c in cells if c.get("verdict") != "PASS" and not (c.get("class") and c.get("rerun"))]
    ev = [p for p in r.get("evidence", []) + [p for c in cells for p in c.get("evidence", [])] if not (ROOT / p).exists()]
    check(f"INT-03 {k}: candidate, decision, cells, classes+reruns, evidence exist",
          bool(r.get("candidate", {}).get("source")) and r.get("decision") and cells and not bad and r.get("evidence") and not ev,
          f"bad={bad} missing={ev}")
for gg in ("APP-LOCAL", "APP-PILOT"):
    check(f"INT-03 {gg} decision as recorded (note cites session-03)", "session-03" in (s["gates"][gg].get("note") or ""))

# INT-04: closure notes; no status changed
for i in ("E5L", "E6L", "E8L", "I2A", "E3A"):
    check(f"INT-04 {i} closure_note", bool(T[i].get("closure_note")))
changed = [i for i in T if T[i]["status"] != base_tasks.get(i, {}).get("status")]
check("INT-04 no manifest status changed", not changed, changed)

# INT-02: every update file ingested
pending = [f.name for f in (EVID / "updates").glob("*.json") if f.name not in s.get("ingested", {})]
check("INT-02 every update file is ingested", not pending, f"{len(pending)} pending")

# INT-07: the register names every item of the five pending lists and RESUME-NOW:6
reg_p = PLAN / "consumer-v1/10-carried-work-register.md"
reg = reg_p.read_text() if reg_p.exists() else ""
lines = AUDIT.read_text().split("\n")
tokens = set(RESUME6)
for a, b in PENDING:
    for n in range(a + 1, b):
        if lines[n - 1].startswith("- "):
            tokens.add(f"audit:{n}")  # every pending bullet is cited by line
            tokens |= set(re.findall(r"\b(?:WR-[A-Z0-9]+(?:-[A-Za-z0-9]+)*|P-\d\d|CMO-\d|k08|INT-\d\d|LAB-\d\d|INFRA-\d\d|DT-\d\d|A\d{1,2})\b", lines[n - 1]))
missing = sorted(t for t in tokens if not re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w])", reg))
check("INT-07 register names every pending item", reg and not missing, missing[:40])

# INT-06: the migrations README convention; no migration touched
readme = ROOT / "apps/app/supabase/migrations/README.md"
check("INT-06 migrations README: LOCAL-ONLY convention + state of record", readme.exists() and "LOCAL-ONLY" in readme.read_text() and "state of record" in readme.read_text())
check("INT-06 no migration changed", run("git", "diff", "--quiet", BASE, "--", "apps/app/supabase/migrations/*.sql")[0] == 0)

# INT-10: WR-LW9-5 applied at COMPOSITION-7:119 + a log line
c7 = (EVID / "COMPOSITION-7-252b65e.md").read_text().split("\n")
check("INT-10 COMPOSITION-7:119 carries the 0058 tally sentence", "per-(serving, pin) tally over terminal jobs (0058, WR-C7-TALLY)" in c7[118])
check("INT-10 COMPOSITION-7 log line", "WR-LW9-5" in c7[-1] or "WR-LW9-5" in c7[-2])

# INT-11: activated minus X1/X3/X5; inputs P-08/P-11/P-21; no overlapping writers
check("INT-11 X1/X3/X5 not activated", not {"X1", "X3", "X5"} & set(s.get("activated", [])))
ids = {p["id"]: p for p in s["inputs"]}
check("INT-11 inputs P-08 resolved, P-11 open, P-21 resolved",
      ids.get("P-08", {}).get("status") == "resolved" and ids.get("P-11", {}).get("status") == "open" and ids.get("P-21", {}).get("status") == "resolved")

# P-08 row
pi = (PLAN / "15-pending-inputs.md").read_text()
check("P-08 ENACTED row in the decisions table", re.search(r"^\| P-08 \| \*\*ENACTED", pi, re.M) is not None)

# the tracker: check 0 errors, no overlap or pending-update warning; validate_plan; render mentions the launch
rc, out = run(sys.executable, "research/plan/scripts/progress.py", "check")
warn = [x for x in out.split("\n") if x.startswith("WARNING")]
check("tracker check 0 errors", rc == 0, out[-300:])
check("tracker: no overlapping-writer / unapplied-update warning", not [w for w in warn if "overlapping" in w or "not applied" in w], warn)
print("remaining warnings:", *warn, sep="\n  ")
check("validate_plan PASS", run(sys.executable, "research/plan/scripts/validate_plan.py")[0] == 0)
check("PROGRESS.md shows the launch (lab.callbill.ai)", "lab.callbill.ai" in (EVID / "PROGRESS.md").read_text())
print(f"{'FAIL' if fails else 'PASS'}: {len(fails)} failing item(s)")
sys.exit(1 if fails else 0)
