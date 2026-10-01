#!/usr/bin/env python3
"""Three-way merge of progress-state.json during a conflicted `git merge` (fix round 1-PL-1).

Lanes merge by id: a lane changed on one side only takes that side; a lane new on
either side is kept (the lane's own record wins over a stub with the same id); a
lane changed on both sides keeps ours and is reported. Other top-level keys: the
side that changed it wins; both changed -> ours, reported. revision = max + 1.
    python3 research/plan/evidence/w6/PLAN-LEDGER-raw/overlay_merge.py
"""
import json, subprocess
P = "research/plan/evidence/coordinator/progress-state.json"
b, o, t = (json.loads(subprocess.run(["git", "show", f":{n}:{P}"], capture_output=True, text=True, check=True).stdout) for n in (1, 2, 3))
out, both = dict(o), []
for k in set(o) | set(t):
    if k in ("lanes", "revision"): continue
    if b.get(k) != t.get(k) and b.get(k) == o.get(k): out[k] = t[k]
    elif b.get(k) != t.get(k) != o.get(k): both.append(k)
bl, ol, tl = ({x["id"]: x for x in s["lanes"]} for s in (b, o, t))
lanes = []
for x in t["lanes"]:  # the tip's order, then ours-only lanes
    i = x["id"]
    if i not in ol: lanes.append(x)
    elif i in bl and ol[i] == bl[i]: lanes.append(x)
    else:
        lanes.append(ol[i])
        if x != bl.get(i, x) and x != ol[i]: both.append(f"lane {i}")
lanes += [x for x in o["lanes"] if x["id"] not in tl]
out["lanes"], out["revision"] = lanes, max(o["revision"], t["revision"]) + 1
ids = [x["id"] for x in lanes]
assert len(ids) == len(set(ids)), "duplicate lane ids"
open(P, "w").write(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print("revision", out["revision"], "; both sides changed (ours kept):", both or "none")
