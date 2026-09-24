#!/usr/bin/env bash
# Coordinator (2026-09-24), after 10-prep-worker.sh: PREP-WORKER's two ruling candidates get numbers
# R104 and R105 in the lane's exact text (evidence/w/PREP-WORKER-4f7e32a.md "## Ruling candidates");
# its third item ("for decision": a fenced fail_preparation) is NOT numbered - a pending decision.
set -euo pipefail
DAY=$(date -u +%F) python3 - <<'PY'
import os, pathlib, re
root = pathlib.Path("research/plan")
def flat(text):
    return " ".join(text.split())
t = (root / "evidence/w/PREP-WORKER-4f7e32a.md").read_text()
sec = t.split("## Ruling candidates (proposed, not numbered; next free is R104)\n", 1)[1]
sec = sec.split("\n## ", 1)[0]
items = [flat(x) for x in re.split(r"\n- (?=\*\*)", "\n" + sec.strip()) if x.strip()]
assert len(items) >= 2, items
rows = []
for n, item in zip(("R104", "R105"), items[:2]):
    title, text = item.lstrip("- ").split("** ", 1)
    title = title.strip("*").rstrip(".")
    assert "|" not in text and "|" not in title
    rows.append((n, title + " (PREP-WORKER proposal)", text))
assert rows[0][1].startswith("Preparation's exact count travels with") and rows[1][1].startswith("A preparation count is the engine's"), [r[1] for r in rows]
p = root / "08-contracts-v1-encoding.md"
s = p.read_text()
anchor = next(l for l in s.splitlines(keepends=True) if l.startswith("| R103 |"))
assert s.count(anchor) == 1 and "| R104 |" not in s
s = s.replace(anchor, anchor + "".join(f"| {n} | {t} | {x} |\n" for n, t, x in rows))
s = s.rstrip("\n") + "\n" + (f"- {os.environ['DAY']}: Rulings R104–R105 numbered (PREP-WORKER's proposed text): R104 preparation's exact count travels "
    "with prepared; R105 a preparation count is the engine's or there is none. Its third candidate (a fenced fail_preparation) stays a "
    "pending decision. Next free ruling R106.\n")
p.write_text(s)
PY
git add research/plan/08-contracts-v1-encoding.md
git commit -q -m "plan: 08 §10 rulings R104-R105 numbered (PREP-WORKER: the exact count travels with prepared; a count is the engine's or none)"
