#!/usr/bin/env python3
"""Consumer-v1 progress tracker (program 22; brief consumer-v1/06-progress-tracker.md).

    python3 research/plan/scripts/progress.py                # render PROGRESS.md + progress.html next to the overlay
    python3 research/plan/scripts/progress.py apply-updates  # one writer: merge evidence/coordinator/updates/*.json, then render
    python3 research/plan/scripts/progress.py check          # validate the overlay against tasks.json; exit 1 on errors

Authority: tasks.json owns task IDs, dependency edges, release-gate roots and implementation status.
progress-state.json (overlay schema 2, documented in evidence/coordinator/updates/README.md) owns lanes, activity,
estimates, locks, inputs and candidate-specific gate decisions. Both outputs are generated; never hand-edit them.
The v46 (backend-first) renderer and state are preserved in evidence/coordinator/tracker-v46/.
"""
import datetime as dt
import fnmatch
import html
import json
import math
import os
import re
import sys
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1]
ROOT = PLAN.parents[1]
EVID = PLAN / "evidence" / "coordinator"
STATE = EVID / "progress-state.json"
UPDATES = EVID / "updates"
sys.path.insert(0, str(PLAN / "scripts"))
from validate_plan import closure  # noqa: E402

UTC = dt.timezone.utc
ACTIVITIES = ["blocked", "changes-requested", "running", "review", "integration", "ready", "queued", "complete", "deferred"]  # attention order
ACTIVE = {"running", "review", "changes-requested", "integration"}
VERDICTS = ["PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN", "PENDING"]
CONFIDENCE = ["unknown", "low", "medium", "high"]
DONE = {"implemented", "integrated"}
HOURS = ("optimistic_h", "likely_h", "pessimistic_h")
CATEGORIES = {"backend": "Backend corrections", "app": "App completion", "deferred": "Deferred Lab / hosting / later",
              "baseline": "Reused baseline", "superseded": "Superseded"}
STALE_ESTIMATE_H = 6
STALE_VIEW_MIN = 15
CLOCK_SKEW_H = 0.25  # a lane or update stamped further ahead of host UTC than this is future-dated


def parse(ts):
    if not ts:
        return None
    d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"timestamp {ts!r} has no UTC offset")
    return d


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hm(ts):
    return parse(ts).strftime("%Y-%m-%d %H:%MZ") if ts else "—"


def hours(a, b):
    return (b - a).total_seconds() / 3600


def reach(start, deps):
    seen, stack = set(), list(start)
    while stack:
        i = stack.pop()
        if i not in seen:
            seen.add(i)
            stack.extend(deps(i))
    return seen


def estimate_problem(est):
    """Why an estimate is malformed, or None: hours are all null (unknown) or finite numbers with 0 <= o <= l <= p."""
    if est is None:
        return None
    if not isinstance(est, dict):
        return "estimate must be an object"
    v = [est.get(k) for k in HOURS]
    if v != [None] * 3 and not (all(type(h) in (int, float) and math.isfinite(h) for h in v) and 0 <= v[0] <= v[1] <= v[2]):
        return f"estimate hours {v} must be all null or numbers with 0 <= optimistic <= likely <= pessimistic"
    if est.get("confidence", "unknown") not in CONFIDENCE:
        return f"estimate confidence {est.get('confidence')!r} not in {CONFIDENCE}"
    try:
        parse(est.get("at"))
    except (TypeError, ValueError) as exc:
        return f"estimate at: {exc}"
    return None


def owned_overlap(a, b):
    a, b = a.split(" (")[0].strip(), b.split(" (")[0].strip()
    if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    a, b = a.rstrip("/") + "/", b.rstrip("/") + "/"
    return a.startswith(b) or b.startswith(a)


class Model:
    """Everything the views and checks need, derived from the manifest + overlay at instant `now`."""

    def __init__(self, manifest, state, now, updates_dir=UPDATES):
        self.m, self.s, self.now = manifest, state, now
        self.tasks = {t["id"]: t for t in manifest["tasks"]}
        self.rg = manifest["release_gates"]
        self.lanes = state.get("lanes", [])
        self.by_task = {}
        for lane in self.lanes:
            self.by_task.setdefault(lane.get("task") or lane["id"], []).append(lane)
        self.gates = {g: self.gate(g, v) for g, v in state.get("gates", {}).items() if g in self.rg}
        self.accepted = {g for g, v in self.gates.items() if v["green"]}
        self.pending_roots = {r for v in self.gates.values() if not v["green"] for r in v["roots"]}
        base = set(state.get("reused_baseline", {}).get("tasks", []))
        backend = closure(self.tasks, self.rg["BACKEND-READY"]["requires"])
        app = closure(self.tasks, self.rg["APP-PILOT"]["requires"])
        self.cat = {i: "superseded" if t["status"].startswith("superseded") else "baseline" if i in base
                    else "backend" if i in backend else "app" if i in app else "deferred" for i, t in self.tasks.items()}
        locks = state.get("resource_locks", [])
        self.gpu_tasks = {t for x in locks if x.get("kind") == "gpu" for t in x.get("tasks", [])}
        self.windows = {w["task"]: w for x in locks if x.get("kind") == "gpu" for w in x.get("windows", [])}
        self.views = [self.view(i) for i in self.tasks]
        self.errors, self.warnings = [], []
        self.validate(updates_dir)
        self.eta = {r: self.forecast(r, g) for g in self.gates for r in self.gates[g]["roots"]}

    # ---- gates, tasks, lanes -------------------------------------------------------------
    def gate(self, g, v):
        roots, cells, cand = self.rg[g]["requires"], v.get("cells", []), v.get("candidate") or {}
        passed = sum(c.get("verdict") == "PASS" for c in cells)
        implemented = all(self.tasks.get(r, {}).get("status") in DONE for r in roots)
        verdict = {c.get("id"): c.get("verdict") for c in cells}
        missing = [x for r in roots for x in self.tasks.get(r, {}).get("test_ids", []) if x not in verdict]
        bare = [c.get("id") for c in cells if c.get("verdict") == "PASS" and not c.get("evidence")]
        src = str(cand.get("source") or "")
        reused = [h for h in self.s.get("historical_runs", []) if src and h.get("task") not in roots and str(h.get("candidate") or "")[:7] == src[:7]]
        why = (([] if implemented else ["its root task is not implemented"])
               + (["required cells missing: " + ", ".join(missing)] if missing else [])
               + ([] if cells and passed == len(cells) else ["not every cell is PASS"])
               + (["PASS without evidence: " + ", ".join(map(str, bare))] if bare else [])
               + ([] if src and cand.get("deployed") else ["no candidate source/deployed identity recorded"])
               + ([f"candidate {src} is historical run {h['id']}'s candidate (task {h.get('task')}); record candidate.note to reuse it"
                   for h in reused] if not cand.get("note") else []))
        green = v.get("decision") == "accepted" and not why
        label = "ACCEPTED" if green else "REJECTED" if v.get("decision") == "rejected" else "PENDING"
        return {"id": g, "roots": roots, "cells": cells, "passed": passed, "implemented": implemented, "green": green,
                "label": label, "why": why, "missing": missing, **{k: v.get(k) for k in ("candidate", "decision", "decided_at", "note")}}

    def finished(self, i):
        lanes = self.by_task.get(i)
        return self.tasks[i]["status"] in DONE or bool(lanes) and all(x["activity"] == "complete" for x in lanes)

    def activity(self, i):
        lanes = self.by_task.get(i)
        if lanes:
            return min((x["activity"] for x in lanes), key=lambda a: ACTIVITIES.index(a) if a in ACTIVITIES else -1)
        return "complete" if self.tasks[i]["status"] in DONE else "unassigned"

    def readiness(self, i):
        t, lanes = self.tasks[i], self.by_task.get(i, [])
        if t["status"] in DONE:
            return "done", "implemented/integrated in the manifest (evidence-backed status, not release acceptance)"
        if self.cat[i] == "superseded":
            return "superseded", "never scheduled; replaced by " + ", ".join(t.get("replaced_by", []))
        if self.cat[i] == "deferred":
            return "blocked", "deferred: Lab, hosting and later work follow App acceptance and their activation gates"
        if self.finished(i):
            return "complete", "every lane complete; the coordinator updates the manifest status from the evidence"
        g = t.get("dispatch_after_gate")
        if g and g not in self.accepted:
            return "blocked", f"gated: dispatch only after {g} is accepted"
        blocked = [x.get("blocker") or "lane blocked" for x in lanes if x["activity"] == "blocked"]
        if blocked:
            return "blocked", "; ".join(blocked)
        wait = [d for d in t["start_dependencies"] if self.tasks[d]["status"] not in DONE]
        if any(x["activity"] in ACTIVE for x in lanes):
            return "active", ("started before " + ", ".join(wait) + " (see lane deviation)") if wait else "lane active"
        if wait:
            return "blocked", "waiting on start dependencies: " + ", ".join(wait)
        return "ready", "start dependencies met"

    def view(self, i):
        t, lanes = self.tasks[i], self.by_task.get(i, [])
        ready, why = self.readiness(i)
        return {"id": i, "title": t["title"], "cat": self.cat[i], "product": t.get("product"), "track": t["track"],
                "status": t["status"], "activity": self.activity(i), "ready": ready, "why": why,
                "lanes": [x["id"] for x in lanes], "agents": sorted({x["agent"] for x in lanes if x.get("agent")})}

    def owned(self, lane):
        return lane.get("owned_paths") or self.tasks.get(lane.get("task"), {}).get("owned_paths", [])

    def stale(self, lane):
        est = lane.get("estimate") or {}
        return lane.get("activity") not in ("complete", "deferred") and est.get("likely_h") is not None and not estimate_problem(est) and (
            not est.get("at") or hours(parse(est["at"]), self.now) > STALE_ESTIMATE_H)

    # ---- validation ------------------------------------------------------------------------
    def validate(self, updates_dir):
        err, warn, s, ids = self.errors.append, self.warnings.append, self.s, set(self.tasks)
        if s.get("schema") != 2:
            err("overlay schema must be 2")
        lane_ids = [x["id"] for x in self.lanes]
        if len(set(lane_ids)) != len(lane_ids):
            err("duplicate lane IDs")
        refs = [(f"lane {x['id']}", x["task"]) for x in self.lanes if x.get("task")]
        refs += [(f"band {b['id']}", i) for b in s.get("bands", []) for i in b["tasks"]]
        refs += [(f"input {p['id']}", i) for p in s.get("inputs", []) for i in p.get("blocks", [])]
        refs += [(f"lock {x['id']}", i) for x in s.get("resource_locks", [])
                 for i in x.get("tasks", []) + [w.get("task") for w in x.get("windows", [])]]
        refs += [(f"historical run {r['id']}", r.get("task")) for r in s.get("historical_runs", [])]
        refs += [("reused_baseline", i) for i in s.get("reused_baseline", {}).get("tasks", [])]
        for where, i in refs:
            if i not in ids:
                err(f"unknown task ID {i!r} in {where}")
        for q in ("review_queue", "integration_queue"):
            for x in s.get(q, []):
                if x.get("lane") not in lane_ids:
                    err(f"{q} names unknown lane {x.get('lane')!r}")
        for g, v in s.get("gates", {}).items():
            if g not in self.rg:
                err(f"unknown gate {g!r}")
                continue
            gv = self.gates[g]
            for c in v.get("cells", []):
                if c.get("verdict") not in VERDICTS:
                    err(f"{g} cell {c.get('id')}: verdict {c.get('verdict')!r} not in {VERDICTS}")
            if gv["missing"]:
                err(f"{g} lacks required cells {', '.join(gv['missing'])}")
            if v.get("decision") not in (None, "accepted", "rejected"):
                err(f"{g} decision {v.get('decision')!r} not in accepted/rejected/null")
            if v.get("decision") == "accepted" and not gv["green"]:
                err(f"impossible gate transition: {g} decision accepted but {'; '.join(gv['why'])}")
        for x in self.lanes:
            a, t, est = x.get("activity"), x.get("task"), x.get("estimate") or {}
            if a not in ACTIVITIES:
                err(f"lane {x['id']}: activity {a!r} not in {ACTIVITIES}")
                continue
            if estimate_problem(x.get("estimate")):
                err(f"lane {x['id']}: {estimate_problem(x.get('estimate'))}")
            if x.get("updated") and hours(self.now, parse(x["updated"])) > CLOCK_SKEW_H:
                err(f"lane {x['id']} updated {x['updated']} is in the future: newer updates would be rejected as stale; check the clock")
            if self.stale(x):
                warn(f"stale estimate: lane {x['id']} estimated at {est.get('at') or 'an unknown time'} (older than {STALE_ESTIMATE_H} h)")
            if t not in self.tasks:
                continue
            g = self.tasks[t].get("dispatch_after_gate")
            if a in ACTIVE | {"complete"} and g and g not in self.accepted:
                err(f"impossible gate transition: lane {x['id']} is {a} but {t} dispatches only after {g} is accepted")
            wait = [d for d in self.tasks[t]["start_dependencies"] if self.tasks[d]["status"] not in DONE]
            if a in ACTIVE and wait and not x.get("deviation"):
                warn(f"lane {x['id']} is {a} before start dependencies {', '.join(wait)} with no deviation recorded")
        mapping = self.m.get("consumer_v1_closure", {}).get("finding_tasks", {})
        for f in s.get("findings", []):
            if f.get("id") not in mapping:
                err(f"unknown finding {f.get('id')!r} (not in consumer_v1_closure.finding_tasks)")
            if f.get("status") not in ("open", "fixed", "superseded"):
                err(f"finding {f.get('id')}: status {f.get('status')!r} not in open/fixed/superseded")
            if f.get("status") != "open" and not f.get("evidence"):
                err(f"finding {f.get('id')} is {f.get('status')} without evidence")
        for p in s.get("inputs", []):
            if p.get("status") not in ("open", "resolved"):
                err(f"input {p.get('id')}: status {p.get('status')!r} not in open/resolved")
        for i in s.get("reused_baseline", {}).get("tasks", []):
            if i in self.tasks and self.tasks[i]["status"] not in DONE:
                warn(f"reused baseline task {i} is no longer implemented/integrated in the manifest")
        self.overlaps = self.overlapping_writers()
        for o in self.overlaps:
            warn(o)
        if updates_dir and Path(updates_dir).is_dir():
            pending = sorted(f.name for f in Path(updates_dir).glob("*.json") if f.name not in s.get("ingested", {}))
            if pending:
                warn(f"{len(pending)} update file(s) not applied yet: {', '.join(pending)} (run apply-updates)")

    def overlapping_writers(self):
        live = [x for x in self.lanes if x.get("activity") not in ("complete", "deferred")]
        starts = {i: reach([i], lambda k: self.tasks[k]["start_dependencies"]) for i in {x.get("task") for x in live} if i in self.tasks}
        out = []
        for n, a in enumerate(live):
            for b in live[n + 1:]:
                ta, tb = a.get("task"), b.get("task")
                if ta in starts and tb in starts and ta != tb and (ta in starts[tb] or tb in starts[ta]):
                    continue  # serialized by start dependencies
                hits = [(p, q) for p in self.owned(a) for q in self.owned(b) if owned_overlap(p, q)]
                if hits:
                    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
                    out.append(f"overlapping writers: {a['id']} ({a['activity']}) and {b['id']} ({b['activity']}) both own "
                               f"{hits[0][0].split(' (')[0]} / {hits[0][1].split(' (')[0]}{more}")
        return out

    # ---- ETA -------------------------------------------------------------------------------
    def deps(self, i):
        t = self.tasks[i]
        g = t.get("dispatch_after_gate")
        return t["start_dependencies"] + t["integration_dependencies"] + (self.rg[g]["requires"] if g and g not in self.accepted else [])

    def effort(self, i):
        """((effort o, l, p), (wall o, l, p)) of the task's remaining lane work; None when unknown."""
        lanes = self.by_task.get(i, [])
        vals = [[(x.get("estimate") or {}).get(k) for k in HOURS] for x in lanes if x["activity"] not in ("complete", "deferred")]
        if not lanes or any(None in v for v in vals) or any(estimate_problem(x.get("estimate")) for x in lanes):
            return None
        if not vals:
            return (0, 0, 0), (0, 0, 0)
        return tuple(map(sum, zip(*vals))), tuple(map(max, zip(*vals)))

    def forecast(self, root, gate):
        out = {"milestone": root, "gate": gate, "computed_at": iso(self.now)}
        if gate in self.accepted:
            return {**out, "status": "accepted", "text": f"accepted {self.gates[gate].get('decided_at') or ''}".strip(),
                    "constraint": "explicit gate decision recorded", "remaining": []}
        rem = {i for i in reach([root], self.deps) if not (self.tasks[i]["status"] in DONE and i not in self.pending_roots)}
        inputs = sorted(p["id"] for p in self.s.get("inputs", []) if p.get("status") != "resolved" and rem & set(p.get("blocks", [])))
        no_window = sorted((self.gpu_tasks & rem) - set(self.windows))
        est = {i: self.effort(i) for i in rem}
        unknown = sorted(i for i, v in est.items() if v is None)
        reasons = (["blocked pending " + ", ".join(inputs)] if inputs else []) \
            + (["no GPU window allocated for " + ", ".join(no_window)] if no_window else []) \
            + (["no remaining-effort estimate for " + ", ".join(unknown)] if unknown else [])
        out.update(remaining=sorted(rem), inputs=inputs, no_window=no_window, unknown=unknown,
                   stale=sorted(x["id"] for i in rem for x in self.by_task.get(i, []) if self.stale(x)),
                   status="blocked" if inputs else "unknown" if reasons else "forecast",
                   text=reasons[0] if inputs else ("unknown: " + reasons[0]) if reasons else "", constraint="; ".join(reasons))
        if unknown:
            return out
        p, slots = self.s.get("eta_params", {}), self.s.get("agent_slots", {})
        rework, integ = p.get("review_rework_fraction", 0.3), p.get("integration_h_per_task", 0.5)
        cap = max(1, slots.get("total", 1) - slots.get("reserved", 0))
        near = {}

        def rdeps(i):  # nearest remaining predecessors, looking through finished tasks
            if i not in near:
                near[i] = set()
                for d in self.deps(i):
                    near[i] |= {d} if d in rem else rdeps(d)
            return near[i]

        scen = []
        for k in range(3):
            dur = {i: est[i][1][k] * (1 + rework) + integ for i in rem}
            eff = sum(est[i][0][k] * (1 + rework) + integ for i in rem)
            ef, prev = {}, {}

            def finish(i):
                if i not in ef:
                    start, prev[i], d_i, w = 0.0, None, dur[i], self.windows.get(i)
                    for d in sorted(rdeps(i)):
                        if finish(d) > start:
                            start, prev[i] = finish(d), d
                    if w:
                        start = max(start, hours(self.now, parse(w["start"])))
                        d_i = max(d_i, hours(parse(w["start"]), parse(w["end"])))
                    ef[i] = start + d_i
                return ef[i]

            path, chain, i = finish(root), [], root
            while i:
                chain.insert(0, i)
                i = prev[i]
            bounds = [(path, "dependency path " + " → ".join(chain))]
            for x in self.s.get("resource_locks", []):
                ts = sorted(rem & set(x.get("tasks", [])))
                if len(ts) > 1:
                    bounds.append((sum(dur[t] for t in ts), f"exclusive {x.get('label', x['id'])}: {', '.join(ts)} run serially"))
            bounds.append((len(rem) * integ, f"serial integration queue: {len(rem)} merges × {integ:g} h"))
            bounds.append((eff / cap, f"capacity: {eff:.1f} effort-h over {cap} implementation slots"))
            wall, why = max(bounds, key=lambda b: b[0])
            scen.append((round(eff, 1), round(wall, 1), why, chain))
        conf = min((((x.get("estimate") or {}).get("confidence") or "unknown") for i in rem for x in self.by_task.get(i, [])
                    if x["activity"] not in ("complete", "deferred")), key=lambda c: CONFIDENCE.index(c) if c in CONFIDENCE else 0, default="unknown")
        effort, wall = [x[0] for x in scen], [x[1] for x in scen]
        out.update(effort_h=effort, wall_h=wall, critical_path=scen[1][3], confidence=conf)
        if reasons:  # durations are conditional on the missing input/window; never a date
            out["conditional"] = f"after {', '.join(inputs + no_window)}: {wall[0]:g}–{wall[2]:g} h wall-clock (likely {wall[1]:g} h); not a date"
            out["constraint"] += f"; then {scen[1][2]}"
        else:
            fin = [iso(self.now + dt.timedelta(hours=h)) for h in wall]
            out.update(finish=fin, constraint=scen[1][2], text=f"{hm(fin[0])} – {hm(fin[2])} (likely {hm(fin[1])})")
        return out


# ---- update ingestion (one writer) -------------------------------------------------------------
FILE_TS = re.compile(r"(\d{8}T\d{4}Z)\.json$")


def update_time(name, u):
    if u.get("at"):
        return parse(u["at"])
    m = FILE_TS.search(name)
    if not m:
        raise ValueError("no `at` field and no <YYYYMMDDTHHMMZ> suffix in the file name")
    return dt.datetime.strptime(m[1], "%Y%m%dT%H%MZ").replace(tzinfo=UTC)


def impossible(prev, new):
    if prev == new:
        return None
    if prev == "complete":
        return "complete is terminal (the coordinator reopens by editing the overlay)"
    if prev == "deferred" and new not in ("queued", "ready"):
        return "a deferred lane is re-queued first"
    if prev in ("queued", "ready") and new in ("review", "integration", "complete"):
        return "work that never ran cannot be in review, integration or complete"
    return None


SHAPES = {"owned_paths": list, "commands": list, "evidence": list, "blockers": list, "wiring_requests": list, "isolation": (dict, type(None))}


def malformed(u):
    """Why an update's fields cannot be stored and rendered, or None."""
    bad = [k for k, t in SHAPES.items() if k in u and not isinstance(u[k], t)]
    if isinstance(u.get("commands"), list) and not all(isinstance(c, dict) for c in u["commands"]):
        bad.append("commands[] (objects)")
    if bad:
        return "wrong type for " + ", ".join(bad)
    return estimate_problem(u["estimate"]) if "estimate" in u else None


CONFIDENCE_ALIASES = {"med": "medium", "mid": "medium"}  # lanes wrote 'med'; the enum is low/medium/high


def judge(model, state, u, at):
    est = u.get("estimate")
    if isinstance(est, dict) and est.get("confidence") in CONFIDENCE_ALIASES:
        est["confidence"] = CONFIDENCE_ALIASES[est["confidence"]]
    """(lane or None, rejection reason or None) for one update."""
    task, act, lanes = u.get("task"), u.get("activity"), state["lanes"]
    if not isinstance(task, str) or task not in set(model.tasks) | {x["id"] for x in lanes if not x.get("task")}:
        return None, f"unknown task ID {task!r}"
    if act not in ACTIVITIES:
        return None, f"unknown activity {act!r}"
    if hours(model.now, at) > CLOCK_SKEW_H:
        return None, f"future-dated: at {iso(at)} is after host UTC now {iso(model.now)} (+{CLOCK_SKEW_H * 60:g} min skew allowed); check the clock"
    why = malformed(u)
    if why:
        return None, f"malformed: {why}"
    cands = [x for x in lanes if (x.get("task") or x["id"]) == task]
    if u.get("lane"):
        cands = [x for x in lanes if x["id"] == u["lane"]]
        if cands and (cands[0].get("task") or cands[0]["id"]) != task:
            return None, f"lane {u['lane']} belongs to {cands[0].get('task')}, not {task}"
    elif len(cands) > 1 and u.get("slice"):
        cands = [x for x in cands if u["slice"] == x.get("slice") or u["slice"] in str(x.get("slice") or "").split("/")]
    if len(cands) > 1:
        return None, f"ambiguous: {task} has lanes {', '.join(x['id'] for x in cands)}; name one in `lane`"
    lane = cands[0] if cands else None
    prev = lane["activity"] if lane else "queued"
    if lane and lane.get("updated") and at <= parse(lane["updated"]):
        return lane, f"stale: at {iso(at)} is not newer than lane {lane['id']} state {lane['updated']}"
    why = impossible(prev, act)
    if why:
        return lane, f"impossible transition {prev} → {act}: {why}"
    t = model.tasks.get(task)
    if t and act in ACTIVE | {"complete"} and t.get("dispatch_after_gate") and t["dispatch_after_gate"] not in model.accepted:
        return lane, f"impossible transition: {task} dispatches only after {t['dispatch_after_gate']} is accepted"
    if t and act == "complete":
        wait = [d for d in t["start_dependencies"] if not model.finished(d)]
        if wait:
            return lane, f"impossible transition: {task} cannot be complete before start dependencies {', '.join(wait)}"
    return lane, None


def apply_updates(manifest, state, files, now):
    """Merge update files into `state` (mutated) in `at` order; newer wins, the rest are rejected visibly. Returns log lines."""
    ingested, batch, out = state.setdefault("ingested", {}), [], []
    state.setdefault("activity_log", [])
    for f in files:
        if f.name in ingested:
            continue
        try:
            u = json.loads(Path(f).read_text())
            if not isinstance(u, dict):
                raise ValueError("not a JSON object")
            batch.append((update_time(f.name, u), f.name, u))
        except (OSError, ValueError) as exc:
            ingested[f.name] = {"status": "rejected", "at": None, "reason": f"unreadable: {exc}"}
            out.append(f"REJECTED {f.name}: unreadable: {exc}")
    for at, name, u in sorted(batch, key=lambda b: (b[0], b[1])):
        lane, reason = judge(Model(manifest, state, now, None), state, u, at)
        if reason:
            ingested[name] = {"status": "rejected", "at": iso(at), "reason": reason}
            state["activity_log"].append({"at": iso(now), "by": "tracker", "what": f"rejected update: {reason}", "source": f"updates/{name}"})
            out.append(f"REJECTED {name}: {reason}")
            continue
        if lane is None:
            lane = {"id": u.get("lane") or u["task"], "task": u["task"], "activity": "queued", "evidence": [], "commands": []}
            state["lanes"].append(lane)
        prev, old = lane["activity"], (lane.get("estimate") or {}).get("likely_h")
        for k in ("slice", "agent", "branch", "worktree", "base", "head", "owned_paths", "isolation", "commands",
                  "estimate", "next_action", "wiring_requests"):
            if k in u:
                lane[k] = u[k]
        if "blockers" in u:
            lane["blocker"] = "; ".join(map(str, u["blockers"])) or None
        lane["evidence"] = lane.get("evidence", []) + [x for x in u.get("evidence", []) if x not in lane.get("evidence", [])]
        lane["activity"], lane["updated"] = u["activity"], iso(at)
        if u["activity"] in ACTIVE and not lane.get("started"):
            lane["started"] = iso(at)
        for q, a in (("review_queue", "review"), ("integration_queue", "integration")):
            cur = [x for x in state.get(q, []) if x["lane"] == lane["id"]]
            state[q] = [x for x in state.get(q, []) if x["lane"] != lane["id"]] + (
                [{**(cur[0] if cur else {"since": iso(at)}), "lane": lane["id"], "task": u["task"], "head": lane.get("head")}] if u["activity"] == a else [])
        what = f"{prev} → {u['activity']}" + (f"; head {u['head']}" if u.get("head") else "")
        new = (lane.get("estimate") or {}).get("likely_h")
        if "estimate" in u and new != old:
            what += f"; estimate likely {old if old is not None else 'unknown'} → {new if new is not None else 'unknown'} h ({(lane['estimate'] or {}).get('basis') or 'no basis given'})"
        if lane.get("blocker") and u["activity"] == "blocked":
            what += f"; blocker: {lane['blocker']}"
        state["activity_log"].append({"at": iso(at), "by": lane["id"], "what": what, "source": f"updates/{name}"})
        ingested[name] = {"status": "applied", "at": iso(at)}
        out.append(f"applied {name}: {lane['id']} {what}")
    return out


def refresh_eta(manifest, state, now):
    """Store the forecast in the overlay; log every milestone whose forecast moved, with the reason."""
    model, old, moved = Model(manifest, state, now, None), state.get("eta", {}).get("milestones", {}), []
    keep = ("gate", "status", "text", "constraint", "effort_h", "wall_h", "finish", "conditional", "confidence", "critical_path", "stale")
    ms = {r: {k: f.get(k) for k in keep} for r, f in model.eta.items()}
    for r, f in ms.items():
        o = old.get(r, {})
        if (o.get("status"), o.get("constraint"), o.get("wall_h")) != (f["status"], f["constraint"], f["wall_h"]):
            what = f"forecast {r}: {o.get('text') or o.get('status') or 'none'} → {f['text'] or f['status']} (because: {f['constraint'] or 'estimates complete'})"
            state.setdefault("activity_log", []).append({"at": iso(now), "by": "tracker", "what": what})
            moved.append(what)
    state["eta"] = {"computed_at": iso(now), "params": state.get("eta_params"), "milestones": ms}
    return moved


def atomic_write(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_state(path, state, expected_revision):
    """One writer: exclusive lock file, revision check, then atomic replacement."""
    path = Path(path)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"{lock} exists: another writer is active (delete it only if no writer is running)")
    try:
        on_disk = json.loads(path.read_text()).get("revision", 0) if path.exists() else 0
        if on_disk != expected_revision:
            raise SystemExit(f"revision conflict: {path.name} is at {on_disk} but this writer read {expected_revision}; re-run")
        text = json.dumps({**state, "revision": expected_revision + 1}, indent=2, ensure_ascii=False) + "\n"
        atomic_write(path, text)
        state["revision"] = expected_revision + 1
    finally:
        os.close(fd)
        lock.unlink()


# ---- rendering helpers ------------------------------------------------------------------------
TONES = {"PASS": "ok", "ACCEPTED": "ok", "complete": "ok", "done": "ok", "forecast": "ok", "resolved": "ok", "applied": "ok",
         "FAIL": "bad", "INVALID": "bad", "REJECTED": "bad", "BLOCKED": "bad", "blocked": "bad", "changes-requested": "bad", "rejected": "bad",
         "PENDING": "warn", "open": "warn", "unknown": "warn", "NOT RUN": "warn",
         "RUNNING": "info", "running": "info", "review": "info", "integration": "info", "ready": "info", "active": "info"}


def e(x):
    return html.escape("" if x is None else str(x))


def pill(x, prefix=""):
    return f'<span class="pill {TONES.get(str(x), "")}">{e(prefix)}{e(x)}</span>'


def ref_target(p):
    path = str(p).split(" (")[0].strip()
    if not path or path.startswith("/") or re.match(r"^[a-z]+:", path):
        return None
    return next((r / path for r in (ROOT, PLAN) if (r / path).exists()), None)


def link(p, base=EVID):
    """Evidence reference -> <a> relative to the output directory when it exists in the repo, else <code>."""
    s = str(p)
    if re.match(r"^https?://", s):
        return f'<a href="{e(s.split()[0])}">{e(s)}</a>'
    t = ref_target(s)
    return f'<a href="{e(os.path.relpath(t, base))}">{e(s)}</a>' if t else f"<code>{e(s)}</code>"


def mdlink(p, base=EVID):
    s, t = str(p), ref_target(p)
    if not t:
        return f"`{s}`"
    rel, head = os.path.relpath(t, base), s.split(" (")[0].strip()
    return f"[{head}]({'<' + rel + '>' if ' ' in rel else rel})" + s[len(head):]


def kv(rows):
    return '<dl class="kv">' + "".join(f"<dt>{e(k)}</dt><dd>{v}</dd>" for k, v in rows if v not in (None, "", [])) + "</dl>"


def table(head, rows):
    return ('<div class="scroll"><table><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>")


def ul(items):
    return '<ul class="plain">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else '<p class="note">None.</p>'


def est_text(lane, model):
    est = lane.get("estimate") or {}
    if est.get("likely_h") is None:
        return "unknown" + (f" ({est['basis']})" if est.get("basis") else "")
    return (f"{est['optimistic_h']:g}–{est['pessimistic_h']:g} h remaining (likely {est['likely_h']:g} h), confidence {est.get('confidence', 'unknown')}, "
            f"estimated {hm(est.get('at'))}" + (" — STALE" if model.stale(lane) else "") + (f"; basis: {est['basis']}" if est.get("basis") else ""))


def hrange(v):
    return f"{v[0]:g} / {v[1]:g} / {v[2]:g} h" if v else "—"


def summaries(M):
    out = []
    for c in CATEGORIES:
        ids = [v["id"] for v in M.views if v["cat"] == c]
        gates = {"backend": ["BACKEND-LOCAL", "BACKEND-READY"], "app": ["APP-LOCAL", "APP-PILOT"]}.get(c, [])
        out.append({"cat": c, "label": CATEGORIES[c], "ids": ids, "impl": [i for i in ids if M.tasks[i]["status"] in DONE],
                    "active": sorted(i for i in ids if any(x["activity"] in ACTIVE for x in M.by_task.get(i, []))),
                    "lanes_complete": sorted(i for i in ids if M.finished(i) and M.tasks[i]["status"] not in DONE),
                    "cells": [(g, M.gates[g]["passed"], len(M.gates[g]["cells"])) for g in gates if g in M.gates]})
    return out


def overview_facts(M):
    s = M.s
    bands = s.get("bands", [])
    open_band = next((b for b in bands if not all(M.tasks[i]["status"] in DONE for i in b["tasks"])), None)
    active_bands = [b["id"] for b in bands if any(any(x["activity"] in ACTIVE for x in M.by_task.get(i, [])) for i in b["tasks"])]
    blockers = [f"lane {x['id']} blocked: {x.get('blocker') or 'no reason given'}" for x in M.lanes if x["activity"] == "blocked"]
    blockers += [f"{p['id']} open — {p['what']} (owner {p.get('owner')}; blocks {', '.join(p.get('blocks', []))})" for p in s.get("inputs", []) if p.get("status") != "resolved"]
    blockers += [f"{x.get('label', x['id'])} held by {x['holder']}" + (f" until ≈{hm(x['until'])}" if x.get("until") else "") for x in s.get("resource_locks", []) if x.get("holder")]
    blockers += [f"{f['milestone']} ({f['gate']}): {f['constraint']}" for f in M.eta.values() if f["status"] in ("blocked", "unknown")]
    ready = [v for v in M.views if v["ready"] == "ready" and v["cat"] in ("backend", "app") and v["activity"] in ("unassigned", "queued", "ready")]
    return {"open_band": open_band, "active_bands": active_bands, "slots": s.get("agent_slots", {}),
            "running": sum(x["activity"] in ACTIVE for x in M.lanes), "blockers": blockers, "ready": ready}


# ---- HTML -------------------------------------------------------------------------------------
CSS = """
:root{--bg:#f5f6f8;--panel:#fff;--ink:#17202c;--muted:#566172;--line:#d6dbe3;--chip:#eceff4;--ok:#1b6b40;--okb:#e2f1e8;--bad:#a3241c;--badb:#fbe6e4;--warn:#7e4c00;--warnb:#fcefd8;--info:#1d4e9a;--infob:#e4edfb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e131a;--panel:#161d27;--ink:#e7ebf1;--muted:#a0abba;--line:#2c3643;--chip:#1f2834;--ok:#80d6a3;--okb:#16321f;--bad:#f59a92;--badb:#3b1d1b;--warn:#f1c579;--warnb:#3a2c13;--info:#a0c5f6;--infob:#1a2a42}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
header.top{background:var(--panel);border-bottom:1px solid var(--line);padding:16px 0 10px}
h1{font-size:1.45rem;margin:0 0 4px;text-wrap:balance}h2{font-size:1.2rem;margin:0 0 12px}h3{font-size:1rem;margin:0 0 8px}
section{margin:28px 0}section>h2{padding-top:10px;border-top:2px solid var(--line)}
.meta,.note{color:var(--muted);font-size:.875rem}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px}
a{color:var(--info)}
a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--info);outline-offset:2px}
.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:8px;background:var(--panel);padding:4px 8px}
.banner{background:var(--warnb);color:var(--warn);border-bottom:1px solid var(--warn);padding:10px 16px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:12px}.grid.wide{grid-template-columns:repeat(auto-fill,minmax(min(100%,440px),1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;min-width:0}
.big{font-size:1.5rem;font-weight:650;font-variant-numeric:tabular-nums;margin:0;overflow-wrap:anywhere}
.bar{height:8px;background:var(--chip);border-radius:4px;overflow:hidden;margin:8px 0}.bar i{display:block;height:100%;background:var(--ok)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.78rem;font-weight:600;background:var(--chip);color:var(--ink);white-space:nowrap}
.pill.ok{background:var(--okb);color:var(--ok)}.pill.bad{background:var(--badb);color:var(--bad)}.pill.warn{background:var(--warnb);color:var(--warn)}.pill.info{background:var(--infob);color:var(--info)}
code{font:.85em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.875rem}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:.76rem;text-transform:uppercase;letter-spacing:.04em}
.filters{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:flex-end;margin-bottom:10px}
.filters label{display:flex;flex-direction:column;font-size:.8rem;color:var(--muted);gap:2px;max-width:100%}
.filters label.check{flex-direction:row;align-items:center;gap:6px;min-height:34px}
input,select{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 8px;min-height:34px;max-width:100%}
details.task{background:var(--panel);border:1px solid var(--line);border-radius:6px;margin:6px 0}
details.task>summary{cursor:pointer;padding:8px 12px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
details.task>summary .t{flex:1 1 240px;min-width:0}
details.task .body{padding:0 12px 12px;border-top:1px solid var(--line)}
dl.kv{display:grid;grid-template-columns:minmax(110px,max-content) minmax(0,1fr);gap:4px 12px;margin:8px 0}
dl.kv dt{color:var(--muted);font-size:.82rem}dl.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
ul.plain{margin:0;padding-left:18px}ul.plain li{margin:3px 0}
.err{color:var(--bad);font-weight:600}.wrn{color:var(--warn);font-weight:600}
@media (max-width:560px){dl.kv{grid-template-columns:1fr}dl.kv dt{margin-top:6px}.big{font-size:1.25rem}}
"""

JS = r"""
function matches(d, f) {
  var hidden = d.cat === 'superseded' || d.cat === 'deferred';
  return (!f.q || d.text.indexOf(f.q) >= 0) &&
    (f.cat ? d.cat === f.cat : (f.all || !hidden)) &&
    (!f.product || d.product === f.product) &&
    (!f.track || d.track === f.track) &&
    (!f.lane || d.lanes.split(' ').indexOf(f.lane) >= 0) &&
    (!f.activity || d.activity === f.activity) &&
    (!f.ready || d.ready === f.ready);
}
(function () {
  var data = JSON.parse(document.getElementById('data').textContent);
  var age = (Date.now() - Date.parse(data.generated)) / 60000;
  if (!(age <= data.stale_after_min)) {
    var b = document.getElementById('stale');
    b.textContent = 'Stale snapshot: generated ' + data.generated + ' UTC, ' + Math.round(age) + ' min ago (threshold ' +
      data.stale_after_min + ' min). Regenerate with progress.py before relying on it; browser refresh is not evidence collection.';
    b.hidden = false;
  }
  var keys = ['q', 'cat', 'product', 'track', 'lane', 'activity', 'ready'];
  var all = document.getElementById('f-all'), count = document.getElementById('f-count');
  var rows = Array.prototype.slice.call(document.querySelectorAll('details.task'));
  function apply() {
    var f = {all: all.checked}, n = 0;
    keys.forEach(function (k) { f[k] = document.getElementById('f-' + k).value.trim().toLowerCase(); });
    rows.forEach(function (r) { var ok = matches(r.dataset, f); r.hidden = !ok; n += ok ? 1 : 0; });
    count.textContent = n + ' of ' + data.tasks.length + ' tasks shown';
  }
  new URLSearchParams(location.hash.slice(1)).forEach(function (v, k) {
    var el = document.getElementById('f-' + k);
    if (el) { if (el.type === 'checkbox') el.checked = v === '1'; else el.value = v.toLowerCase(); }
  });
  keys.concat('all').forEach(function (k) { document.getElementById('f-' + k).addEventListener('input', apply); });
  apply();
})();
"""


def task_details(M, v):
    t, lanes = M.tasks[v["id"]], M.by_task.get(v["id"], [])
    dep = lambda ids: ", ".join(f"<code>{e(d)}</code> {e(M.tasks[d]['status'])}" for d in ids) or "—"
    evid = t.get("evidence") or []
    evid = ([evid] if isinstance(evid, str) else list(evid)) + [x for lane in lanes for x in lane.get("evidence", [])]
    commits = ([f"merged {e(t['merged_commit'])}"] if t.get("merged_commit") else []) + [
        f"{e(x['id'])}: {e(x.get('base') or '—')} → {e(x.get('head') or '—')} on <code>{e(x.get('branch') or 'no branch')}</code>" for x in lanes]
    cmds = [f"<code>{e(c.get('cmd'))}</code> → exit {e(c.get('exit'))} {e(c.get('summary'))}" for x in lanes for c in x.get("commands", [])]
    body = kv([
        ("Readiness", f"{pill(v['ready'])} {e(v['why'])}"),
        ("Manifest", f"{pill(t['status'])} {e(t.get('disposition'))} · milestone {e(t.get('milestone'))} · track {e(t['track'])}"),
        ("Start deps", dep(t["start_dependencies"])),
        ("Integration deps", dep(t["integration_dependencies"])),
        ("Dispatch gate", e(t.get("dispatch_after_gate"))),
        ("Acceptance", e(t.get("acceptance") or t.get("amendment"))),
        ("Failure oracle", e(t.get("failure_oracle"))),
        ("Slices", "<ol>" + "".join(f"<li>{e(x)}</li>" for x in t.get("implementation_slices", [])) + "</ol>" if t.get("implementation_slices") else ""),
        ("Test IDs", ", ".join(f"<code>{e(x)}</code>" for x in t.get("test_ids", []))),
        ("Owned paths", ", ".join(f"<code>{e(x)}</code>" for x in t.get("owned_paths", []))),
        ("Handoff", link("research/plan/" + t["handoff"])),
        ("Evidence", ul([link(x) for x in evid]) if evid else ""),
        ("Commits", ul(commits) if commits else ""),
        ("Lanes", ul([f"{e(x['id'])} {pill(x['activity'])} {e(x.get('slice') or '')} · estimate {e(est_text(x, M))}" for x in lanes]) if lanes else "no lane assigned"),
        ("Commands", ul(cmds) if cmds else ""),
        ("Next action", ul([f"{e(x['id'])}: {e(x.get('next_action') or x.get('blocker'))}" for x in lanes if x.get("next_action") or x.get("blocker")])),
    ])
    low = lambda x: e(str(x or "").lower())
    text = " ".join([v["id"], v["title"], " ".join(v["lanes"]), " ".join(v["agents"]), v["why"]] + [x.get("branch") or "" for x in lanes])
    return (f'<details class="task" data-id="{low(v["id"])}" data-cat="{v["cat"]}" data-product="{low(v["product"])}" data-track="{low(v["track"])}" '
            f'data-lanes="{low(" ".join(v["lanes"]))}" data-activity="{low(v["activity"])}" data-ready="{v["ready"]}" data-text="{low(text)}">'
            f'<summary><code>{e(v["id"])}</code><span class="t">{e(v["title"])}</span>{pill(CATEGORIES[v["cat"]])} {pill(v["status"], "manifest: ")} '
            f'{pill(v["activity"], "lane: ")} {pill(v["ready"], "state: ")}</summary><div class="body">{body}</div></details>')


def lane_card(M, x):
    iso_txt = ", ".join(f"{k} {v}" for k, v in (x.get("isolation") or {}).items() if v) or "none"
    return (f'<div class="card"><h3>{e(x["id"])} {pill(x["activity"])}' + (' <span class="pill warn">stale estimate</span>' if M.stale(x) else "") + "</h3>" + kv([
        ("Task / slice", f"<code>{e(x.get('task') or 'support')}</code> {e(x.get('slice') or '')}"),
        ("Agent", e(x.get("agent") or "unassigned")),
        ("Branch", f"<code>{e(x.get('branch'))}</code>" if x.get("branch") else "—"),
        ("Worktree", f"<code>{e(x.get('worktree'))}</code>" if x.get("worktree") else "—"),
        ("Base → head", f"<code>{e(x.get('base') or '—')}</code> → <code>{e(x.get('head') or '—')}</code>"),
        ("Owned paths", ul([f"<code>{e(p)}</code>" for p in M.owned(x)])),
        ("Isolation", e(iso_txt)),
        ("Started / updated", f"{e(hm(x.get('started')))} / {e(hm(x.get('updated')))} UTC"),
        ("Blocker", e(x.get("blocker"))),
        ("Next action", e(x.get("next_action"))),
        ("Deviation", e(x.get("deviation"))),
        ("Estimate", e(est_text(x, M))),
        ("Evidence", ul([link(p) for p in x.get("evidence", [])]) if x.get("evidence") else ""),
        ("Commands", ul([f"<code>{e(c.get('cmd'))}</code> → exit {e(c.get('exit'))} {e(c.get('summary'))}" for c in x.get("commands", [])]) if x.get("commands") else ""),
        ("Wiring requests", ul([e(w if isinstance(w, str) else json.dumps(w)) for w in x.get("wiring_requests", [])]) if x.get("wiring_requests") else ""),
    ]) + "</div>")


def milestone_card(M, f):
    g = M.gates[f["gate"]]
    return (f'<div class="card"><h3>{e(f["milestone"])} → {e(f["gate"])} {pill(g["label"])}</h3>'
            f'<p class="big">{pill(f["status"])} {e(f["text"])}</p>' + kv([
                ("Controlling constraint", e(f.get("constraint"))),
                ("Conditional duration", e(f.get("conditional"))),
                ("Active effort (o/l/p)", e(hrange(f.get("effort_h"))) + " engineering hours incl. review/rework and merges" if f.get("effort_h") else "unknown"),
                ("Wall-clock (o/l/p)", e(hrange(f.get("wall_h"))) if f.get("wall_h") else "unknown"),
                ("Confidence", e(f.get("confidence", "unknown"))),
                ("Critical path", " → ".join(f"<code>{e(i)}</code>" for i in f.get("critical_path", []))),
                ("Remaining on path", f"{len(f['remaining'])}: " + ", ".join(f"<code>{e(i)}</code>" for i in f["remaining"])),
                ("Stale estimates", e(", ".join(f.get("stale", [])))),
                ("Computed", e(hm(f["computed_at"])) + " UTC"),
            ]) + "</div>")


def soak_text(c, now):
    """Elapsed runtime and expected end, kept apart; the end is an expectation, never progress."""
    if not (c.get("started") and c.get("ends")):
        return ""
    s, t = parse(c["started"]), parse(c["ends"])
    end = (hm(c["ends_earliest"]) + "–" if c.get("ends_earliest") else "≈") + hm(c["ends"])
    if now >= t:
        return f"expected end {end} passed at generation ({max(0.0, hours(s, now)):.1f} h since start); verdict still {c['verdict']}: verify"
    return f"elapsed {max(0.0, hours(s, now)):.1f} h since ≈{hm(c['started'])}; expected end {end} (≤ {hours(now, t):.1f} h remaining at generation)"


def render_html(M):
    s, now, F = M.s, M.now, overview_facts(M)
    gen, d, slots = iso(now), s.get("deployed", {}), F["slots"]
    age = hours(parse(s["updated"]), now) * 60 if s.get("updated") else None
    avail = slots.get("total", 0) - F["running"] - slots.get("reserved", 0)
    H = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Consumer v1 Tracker</title><meta name="description" content="Consumer v1 (program 22) progress, gates and ETA generated from tasks.json and progress-state.json">
<style>{CSS}</style></head><body><a class="skip" href="#tasks">Skip to tasks</a>
<header class="top"><div class="wrap"><h1>Consumer v1 progress tracker</h1>
<p class="meta">Generated <time datetime="{gen}">{e(hm(gen))}</time> UTC · overlay revision {e(s.get('revision'))}, updated {e(hm(s.get('updated')))} UTC ·
manifest v{e(M.m['schema_version'])} · {link(s.get('program_doc', ''))} · generated file, never hand-edited</p>
<nav aria-label="Views"><a href="#overview">Overview</a><a href="#progress">Progress</a><a href="#tasks">Tasks</a><a href="#board">Agents &amp; worktrees</a>
<a href="#milestones">Milestones &amp; ETA</a><a href="#verification">Verification</a><a href="#timeline">ETA, inputs &amp; timeline</a></nav></div></header>
<div id="stale" class="banner" role="alert" hidden></div>
<noscript><div class="banner">JavaScript is off: filters and the view-time stale check are unavailable. Check the generated time above.</div></noscript>
<main class="wrap">"""]
    # 1. overview
    H.append('<section id="overview"><h2>Overview</h2><div class="grid">')
    H.append('<div class="card"><h3>Integration</h3>' + kv([
        ("Program", e(s.get("program"))), ("Branch", f"<code>{e(s.get('integration_branch'))}</code>"),
        ("Base / main", f"<code>{e(s.get('base'))}</code> / <code>{e(s.get('main'))}</code>"),
        ("Integration head", f"<code>{e(s.get('integration_head'))}</code>" if s.get("integration_head") else "")]) + "</div>")
    H.append('<div class="card"><h3>Deployed candidate</h3>' + kv([
        ("Release", f"<code>{e(d.get('release'))}</code> ({e(d.get('install'))})"), ("Image", e(d.get("image"))),
        ("Config", ", ".join(f"<code>{e(k)}={e(v)}</code>" for k, v in (d.get("config") or {}).items())),
        ("Regime", pill(d.get("regime"))), ("Target", e(d.get("target")))]) + "</div>")
    H.append('<div class="card"><h3>State age and band</h3>' + kv([
        ("Overlay age", f"{age:.0f} min at generation" if age is not None else "unknown"),
        ("Lowest open band", f"{e(F['open_band']['id'])} {e(F['open_band']['name'])}" if F["open_band"] else "all bands done"),
        ("Bands with active work", e(", ".join(F["active_bands"]) or "none")),
        ("Validation", f'<a href="#validation">{len(M.errors)} error(s), {len(M.warnings)} warning(s)</a>')]) + "</div>")
    H.append('<div class="card"><h3>Agent slots</h3>' + kv([
        ("Total", e(slots.get("total"))), ("Active lanes", f"{F['running']} (recorded {e(slots.get('active'))})"),
        ("Reserved (review)", e(slots.get("reserved"))), ("Available", e(avail)), ("Note", e(slots.get("note")))]) + "</div></div>")
    H.append('<div class="grid wide" style="margin-top:12px"><div class="card"><h3>Actionable blockers</h3>' + ul([e(b) for b in F["blockers"]]) + "</div>")
    H.append('<div class="card"><h3>Next ready work (start dependencies met, not yet running)</h3>' + ul(
        [f"<code>{e(v['id'])}</code> {e(v['title'])} — {e('lanes ' + ', '.join(v['lanes']) if v['lanes'] else 'no lane assigned')}" for v in F["ready"]]) + "</div></div>")
    H.append('<div class="card" id="validation" style="margin-top:12px"><h3>Validation (progress.py check)</h3>'
             + ul([f'<span class="err">error:</span> {e(x)}' for x in M.errors] + [f'<span class="wrn">warning:</span> {e(x)}' for x in M.warnings]) + "</div></section>")
    # 2. progress summaries
    H.append('<section id="progress"><h2>Progress summaries</h2><p class="note">Task counts use the manifest status (implemented/integrated) over explicit denominators; '
             'acceptance counts are gate cells marked PASS. Neither implies launch readiness: a gate turns green only on an explicit accepted decision.</p><div class="grid">')
    denominators = {"backend": "E4C (BACKEND-READY) closure minus the reused baseline", "app": "APP-PILOT (E4) closure minus backend and reused baseline",
                    "deferred": "every other active task: Lab, hosting, conditional and later core",
                    "baseline": "implemented/integrated before program 22; reused, excluded from new-work percentages",
                    "superseded": "retired mixed tasks; never scheduled"}
    for x in summaries(M):
        n, k = len(x["ids"]), len(x["impl"])
        cells = "; ".join(f"{g} {p}/{t}" for g, p, t in x["cells"])
        H.append(f'<div class="card"><h3>{e(x["label"])}</h3><p class="big">{k} / {n}</p><p class="note">tasks implemented or integrated in the manifest</p>'
                 f'<div class="bar" aria-hidden="true"><i style="width:{100 * k / n if n else 0:.1f}%"></i></div>' + kv([
                     ("Active", e(", ".join(x["active"]) or "none")),
                     ("Lanes complete, manifest not yet updated", e(", ".join(x["lanes_complete"]) or "none")),
                     ("Acceptance cells PASS", e(cells) if cells else ""), ("Denominator", e(denominators[x["cat"]]))]) + "</div>")
    H.append("</div></section>")
    # 3. tasks
    opts = lambda vals: "".join(f'<option value="{e(str(v).lower())}">{e(lbl)}</option>' for v, lbl in vals)
    views, order = M.views, {c: n for n, c in enumerate(CATEGORIES)}
    H.append('<section id="tasks"><h2>Tasks</h2><div class="filters" role="search" aria-label="Filter tasks">'
             '<label>Search<input type="search" id="f-q" placeholder="ID, title, lane, branch"></label>'
             '<label>Category<select id="f-cat"><option value="">Active categories</option>' + opts(CATEGORIES.items()) + "</select></label>"
             '<label>Product<select id="f-product"><option value="">All</option>' + opts((p, p) for p in sorted({v["product"] for v in views})) + "</select></label>"
             '<label>Owner track<select id="f-track"><option value="">All</option>' + opts((t, t) for t in sorted({v["track"] for v in views})) + "</select></label>"
             '<label>Lane<select id="f-lane"><option value="">All</option>' + opts((x["id"], x["id"]) for x in M.lanes) + "</select></label>"
             '<label>Activity<select id="f-activity"><option value="">All</option>' + opts((a, a) for a in ACTIVITIES + ["unassigned"]) + "</select></label>"
             '<label>State<select id="f-ready"><option value="">All</option>' + opts((r, r) for r in ("ready", "active", "blocked", "complete", "done", "superseded")) + "</select></label>"
             '<label class="check"><input type="checkbox" id="f-all"> Include superseded and deferred</label>'
             f'<output id="f-count" aria-live="polite">{len(views)} tasks (filters need JavaScript)</output></div>'
             '<p class="note">The default view hides superseded and deferred tasks; choose a category or tick the box to see them. Expand a task for acceptance, '
             'dependencies, slices, evidence, commits, commands and next action. Filters accept URL presets, e.g. <code>#activity=running</code>.</p>')
    H += [task_details(M, v) for v in sorted(views, key=lambda v: (order[v["cat"]], v["id"]))]
    H.append("</section>")
    # 4. board
    H.append('<section id="board"><h2>Agents and worktrees</h2><div class="grid wide">' + "".join(lane_card(M, x) for x in M.lanes) + "</div>")

    def queue(q):
        rows = [[e(x.get("lane")), e(x.get("task")), f"<code>{e(x.get('head'))}</code>", e(hm(x.get("since")))] for x in s.get(q, [])]
        return table(["Lane", "Task", "Head", "Since (UTC)"], rows) if rows else '<p class="note">Empty.</p>'
    H.append('<div class="grid" style="margin-top:12px"><div class="card"><h3>Review queue</h3>' + queue("review_queue")
             + '</div><div class="card"><h3>Integration queue (serial)</h3>' + queue("integration_queue") + "</div></div>")
    H.append('<h3 style="margin-top:16px">Resource locks</h3>' + table(["Resource", "Holder", "Since / until (UTC)", "Tasks", "GPU windows", "Note"], [
        [e(x.get("label", x["id"])), e(x.get("holder") or "unassigned"), f"{e(hm(x.get('since')))} / {e(hm(x.get('until')))}", e(", ".join(x.get("tasks", []))),
         e("; ".join(f"{w['task']} {hm(w['start'])}–{hm(w['end'])}" for w in x.get("windows", [])) or ("none allocated" if x.get("kind") == "gpu" else "")),
         e(x.get("note"))] for x in s.get("resource_locks", [])]))
    H.append('<h3 style="margin-top:16px">Overlapping writers (flag before dispatch)</h3>' + ul([e(o) for o in M.overlaps]) + "</section>")
    # 5. milestones
    p = s.get("eta_params", {})
    H.append('<section id="milestones"><h2>Milestones, critical path and ETA</h2><p class="note">Backend acceptance (BACKEND-READY on E4C) precedes App dispatch; '
             'Lab and fleet remain gated. A milestone whose remaining path has an open input or an unallocated GPU window shows "blocked pending" or "unknown", never a date. '
             f'Review/rework allowance {e(p.get("review_rework_fraction", 0.3))} of implementation hours; {e(p.get("integration_h_per_task", 0.5))} h per serial merge; '
             f'estimates older than {STALE_ESTIMATE_H} h are stale. Effort is engineering hours; wall-clock is the resource- and dependency-bound elapsed time.</p>'
             '<div class="grid wide">' + "".join(milestone_card(M, f) for f in M.eta.values()) + "</div>")
    H.append('<h3 style="margin-top:16px">Delivery bands (dependency bands, not barriers)</h3>' + table(["Band", "Name", "Tasks"], [
        [e(b["id"]), e(b["name"]), " ".join(f"<code>{e(i)}</code> {pill(M.activity(i))}" for i in b["tasks"])] for b in s.get("bands", [])]) + "</section>")
    # 6. verification
    H.append('<section id="verification"><h2>Verification</h2>' + table(["Gate", "Roots", "Candidate", "Cells PASS", "Not PASS", "Decision", "Note"], [
        [f"{e(g)} {pill(v['label'])}", ", ".join(f"<code>{e(r)}</code> {e(M.tasks[r]['status'])}" for r in v["roots"]),
         e(", ".join(f"{k} {x}" for k, x in (v.get("candidate") or {}).items() if x) or "none recorded"), f"{v['passed']} / {len(v['cells'])}",
         " ".join(f"{e(c['id'])} {pill(c['verdict'])}" for c in v["cells"] if c.get("verdict") != "PASS"),
         e(f"{v.get('decision') or 'none'} {hm(v.get('decided_at')) if v.get('decided_at') else ''}"), e(v.get("note"))] for g, v in M.gates.items()]))
    mapping = M.m.get("consumer_v1_closure", {}).get("finding_tasks", {})
    H.append('<h3 style="margin-top:16px">Readiness findings (RV)</h3>' + table(["Finding", "Status", "Corrective tasks", "As of", "Source"], [
        [e(f["id"]), pill(f.get("status")), " ".join(f"<code>{e(i)}</code> {pill(M.activity(i))}" for i in mapping.get(f["id"], [])), f"<code>{e(f.get('at'))}</code>",
         " ".join(link(x) for x in [f.get("source")] + list(f.get("evidence") or []) if x)] for f in s.get("findings", [])]))
    for r in s.get("historical_runs", []):
        H.append(f'<div class="card" style="margin-top:12px"><h3>Historical run {e(r["id"])} {pill(r.get("status"))}</h3>' + kv([
            ("Task / candidate", f"<code>{e(r.get('task'))}</code> on <code>{e(r.get('candidate'))}</code>"), ("Scope", e(r.get("scope"))),
            ("Started", f"{e(hm(r.get('started')))} UTC {e(r.get('clock') or '')}" if r.get("started") else ""), ("Output", e(r.get("output"))),
            ("Commit", e(r.get("commit"))), ("Evidence", ul([link(x) for x in r.get("evidence", [])]) if r.get("evidence") else "")])
            + table(["Cell", "Verdict", "Note", "Elapsed / remaining"], [[e(c["id"]), pill(c["verdict"]), e(c.get("note")), e(soak_text(c, now))] for c in r.get("cells", [])]) + "</div>")
    H.append('<h3 style="margin-top:16px">Suites (last recorded result)</h3>' + table(["Suite", "Command", "Result", "At (UTC)", "Candidate"], [
        [e(x.get("name")), f"<code>{e(x.get('cmd'))}</code>", e(x.get("result")), e(hm(x.get("at"))), f"<code>{e(x.get('candidate'))}</code>"]
        for x in s.get("verification", {}).get("suites", [])])
        + "<p>Reconciliation (S3): " + (" ".join(f"{pill(x['activity'])} {e(x.get('next_action'))}" for x in M.by_task.get("S3", [])) or "no lane") + "</p></section>")
    # 7. ETA, inputs, timeline
    H.append('<section id="timeline"><h2>ETA, inputs and timeline</h2>' + table(["Milestone", "Gate", "Status", "Forecast", "Effort o/l/p", "Wall-clock o/l/p", "Confidence", "Stale estimates"], [
        [f"<code>{e(f['milestone'])}</code>", e(f["gate"]), pill(f["status"]), e(f["text"]), e(hrange(f.get("effort_h"))), e(hrange(f.get("wall_h"))),
         e(f.get("confidence", "unknown")), e(", ".join(f.get("stale", [])) or "none")] for f in M.eta.values()]))
    H.append('<h3 style="margin-top:16px">Pending inputs</h3>' + table(["Input", "Status", "What", "Owner", "Blocks", "Milestones affected"], [
        [f"<code>{e(x['id'])}</code>", pill(x.get("status")), e(x.get("what")), e(x.get("owner")), e(", ".join(x.get("blocks", []))),
         e(", ".join(f["milestone"] for f in M.eta.values() if x["id"] in f.get("inputs", [])))] for x in s.get("inputs", [])]))
    ing = sorted(s.get("ingested", {}).items(), key=lambda kv_: (kv_[1].get("status") != "rejected", kv_[0]))
    H.append('<h3 style="margin-top:16px">Update files (rejected first)</h3>' + (table(["File", "Status", "At (UTC)", "Reason"], [
        [f"<code>{e(n)}</code>", pill(r.get("status")), e(hm(r.get("at"))), e(r.get("reason"))] for n, r in ing]) if ing else '<p class="note">None ingested yet.</p>'))
    H.append('<h3 style="margin-top:16px">Activity log (append-only, newest first)</h3>' + table(["At (UTC)", "By", "What", "Source"], [
        [e(hm(x.get("at"))), e(x.get("by")), e(x.get("what")), e(x.get("source"))] for x in reversed(s.get("activity_log", []))]))
    v46 = s.get("history", {}).get("v46", {})
    H.append('<h3 style="margin-top:16px">History</h3>' + kv([
        ("v46 snapshot", link(v46["snapshot"]) if v46.get("snapshot") else ""), ("At commit", e(v46.get("commit"))), ("Final numbers", e(v46.get("final"))),
        ("v46 gates", e("; ".join(f"{k}: {x}" for k, x in v46.get("gates", {}).items())))]) + "</section></main>")
    data = {"generated": gen, "stale_after_min": STALE_VIEW_MIN, "revision": s.get("revision"), "tasks": views, "eta": M.eta,
            "gates": {g: {k: v[k] for k in ("label", "passed", "decision")} for g, v in M.gates.items()}, "lanes": M.lanes,
            "inputs": s.get("inputs", []), "errors": M.errors, "warnings": M.warnings}
    H.append('<script type="application/json" id="data">' + json.dumps(data, ensure_ascii=False).replace("<", "\\u003c") + "</script>")
    H.append(f"<script>{JS}</script></body></html>")
    return "\n".join(H) + "\n"


# ---- Markdown ---------------------------------------------------------------------------------
def md_cell(x):
    return str(x if x not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def mdt(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(md_cell(c) for c in r) + " |" for r in rows] + [""]


def render_md(M):
    s, now, F = M.s, M.now, overview_facts(M)
    d = s.get("deployed", {})
    L = ["# Consumer v1 progress tracker", "",
         f"Generated {hm(iso(now))} UTC by `python3 research/plan/scripts/progress.py` from [tasks.json](../../tasks.json) (manifest v{M.m['schema_version']}) and "
         f"[progress-state.json](progress-state.json) (overlay revision {s.get('revision')}, updated {hm(s.get('updated'))} UTC). Generated file; never hand-edit. "
         f"Program: [{s.get('program')}](../../22-consumer-v1-implementation.md). Full view: [progress.html](progress.html).", "",
         "## Overview", "",
         f"- Integration branch `{s.get('integration_branch')}` (head `{s.get('integration_head')}`), base `{s.get('base')}`, main `{s.get('main')}`.",
         f"- Deployed candidate `{d.get('release')}` ({d.get('install')}; image {d.get('image')}; "
         + ", ".join(f"{k}={v}" for k, v in (d.get("config") or {}).items()) + f"; regime **{d.get('regime')}**).",
         f"- Lowest open band: {F['open_band']['id'] + ' ' + F['open_band']['name'] if F['open_band'] else 'none'}; bands with active work: {', '.join(F['active_bands']) or 'none'}.",
         f"- Agent slots: {F['slots'].get('total')} total, {F['running']} active lanes, {F['slots'].get('reserved')} reserved.",
         f"- Validation: {len(M.errors)} error(s), {len(M.warnings)} warning(s).", "", "### Actionable blockers", ""]
    L += [f"- {md_cell(b)}" for b in F["blockers"]] or ["- None."]
    L += ["", "### Next ready work", ""] + ([f"- `{v['id']}` {v['title']} ({'lanes ' + ', '.join(v['lanes']) if v['lanes'] else 'no lane assigned'})" for v in F["ready"]] or ["- None."])
    L += ["", "## Progress summaries", "", "Task counts: manifest implemented/integrated over an explicit denominator. Cells: gate cells marked PASS. Neither implies launch readiness.", ""]
    L += mdt(["Category", "Implemented/integrated", "Active", "Acceptance cells PASS"], [
        [x["label"], f"{len(x['impl'])} / {len(x['ids'])}", ", ".join(x["active"]) or "none", "; ".join(f"{g} {p}/{t}" for g, p, t in x["cells"]) or "n/a"] for x in summaries(M)])
    L += ["## Milestones and ETA", "", "Never a date while an open input or an unallocated GPU window sits on the remaining path.", ""]
    L += mdt(["Milestone", "Gate", "Status", "Forecast", "Controlling constraint", "Effort o/l/p", "Wall-clock o/l/p", "Confidence"], [
        [f"`{f['milestone']}`", f"{f['gate']} ({M.gates[f['gate']]['label']})", f["status"], f["text"], f.get("constraint"), hrange(f.get("effort_h")),
         hrange(f.get("wall_h")), f.get("confidence", "unknown")] for f in M.eta.values()])
    L += ["## Gates", ""] + mdt(["Gate", "Roots", "Cells PASS", "Not PASS", "Decision", "Note"], [
        [f"{g} **{v['label']}**", ", ".join(f"{r} ({M.tasks[r]['status']})" for r in v["roots"]), f"{v['passed']} / {len(v['cells'])}",
         ", ".join(f"{c['id']} {c['verdict']}" for c in v["cells"] if c.get("verdict") != "PASS"), v.get("decision") or "none", v.get("note")] for g, v in M.gates.items()])
    mapping = M.m.get("consumer_v1_closure", {}).get("finding_tasks", {})
    L += ["### Readiness findings (RV)", ""] + mdt(["Finding", "Status", "Corrective tasks", "As of", "Source"], [
        [f["id"], f.get("status"), ", ".join(f"{i} ({M.activity(i)})" for i in mapping.get(f["id"], [])), f.get("at"), mdlink(f.get("source", ""))] for f in s.get("findings", [])])
    for r in s.get("historical_runs", []):
        L += [f"### Historical run {r['id']} ({r.get('status')})", "",
              f"`{r.get('task')}` on `{r.get('candidate')}`. {r.get('scope')} " + " ".join(mdlink(x) for x in r.get("evidence", [])), ""]
        L += mdt(["Cell", "Verdict", "Note", "Elapsed / remaining"], [[c["id"], c["verdict"], c.get("note"), soak_text(c, now)] for c in r.get("cells", [])])
    L += ["## Lanes", ""] + mdt(["Lane", "Task / slice", "Activity", "Branch", "Base → head", "Isolation", "Updated", "Blocker / next", "Estimate"], [
        [x["id"], f"{x.get('task') or 'support'} {x.get('slice') or ''}", x["activity"], x.get("branch"), f"{x.get('base') or '—'} → {x.get('head') or '—'}",
         ", ".join(f"{k} {v}" for k, v in (x.get("isolation") or {}).items() if v) or "none", hm(x.get("updated")),
         x.get("blocker") or x.get("next_action"), est_text(x, M)] for x in M.lanes])
    L += ["### Queues and locks", "", f"- Review queue: {', '.join(x['lane'] for x in s.get('review_queue', [])) or 'empty'}.",
          f"- Integration queue: {', '.join(x['lane'] for x in s.get('integration_queue', [])) or 'empty'}."]
    L += [f"- {x.get('label', x['id'])}: {x.get('holder') or 'unassigned'}" + (f" until ≈{hm(x['until'])}" if x.get("until") else "") + f". {x.get('note') or ''}"
          for x in s.get("resource_locks", [])]
    L += ["", "## Validation", ""] + ([f"- error: {md_cell(x)}" for x in M.errors] + [f"- warning: {md_cell(x)}" for x in M.warnings] or ["- Clean."])
    L += ["", "## Pending inputs", ""] + mdt(["Input", "Status", "What", "Owner", "Blocks"], [
        [x["id"], x.get("status"), x.get("what"), x.get("owner"), ", ".join(x.get("blocks", []))] for x in s.get("inputs", [])])
    rej = [(n, r) for n, r in s.get("ingested", {}).items() if r.get("status") == "rejected"]
    if rej:
        L += ["## Rejected updates", ""] + [f"- `{n}`: {md_cell(r.get('reason'))}" for n, r in rej] + [""]
    L += ["## All manifest tasks", ""] + mdt(["ID", "Title", "Category", "Manifest", "Activity", "State"], [
        [f"`{v['id']}`", v["title"], CATEGORIES[v["cat"]], v["status"], v["activity"], f"{v['ready']}: {v['why']}"] for v in M.views])
    L += ["## Activity log (newest first)", ""] + [f"- {hm(x.get('at'))} UTC, {x.get('by')}: {md_cell(x.get('what'))}" for x in reversed(s.get("activity_log", []))]
    v46 = s.get("history", {}).get("v46")
    if v46:
        L += ["", "## History", "", f"The v46 backend-first tracker is preserved at {mdlink(v46['snapshot'])} (commit `{v46['commit']}`): {v46['final']}."]
    return "\n".join(L) + "\n"


# ---- CLI --------------------------------------------------------------------------------------
def load(state_path=STATE):
    return json.loads((PLAN / "tasks.json").read_text()), json.loads(Path(state_path).read_text())


def uncovered(M, html_text, md_text):
    return [i for i in M.tasks if f'data-id="{i.lower()}"' not in html_text or f"| `{i}` |" not in md_text]


def cmd_render(now):
    manifest, state = load()
    M = Model(manifest, state, now)
    atomic_write(EVID / "progress.html", render_html(M))
    atomic_write(EVID / "PROGRESS.md", render_md(M))
    print(f"rendered {EVID.relative_to(ROOT)}/progress.html and PROGRESS.md: {len(M.tasks)} tasks, {len(M.errors)} error(s), {len(M.warnings)} warning(s)")
    for f in M.eta.values():
        print(f"  {f['milestone']} ({f['gate']}): {f['text']}")
    return 0


def cmd_check(now):
    manifest, state = load()
    M = Model(manifest, state, now)
    missing = uncovered(M, render_html(M), render_md(M))
    if missing:
        M.errors.append("rendered outputs omit manifest tasks: " + ", ".join(missing))
    for x in M.errors:
        print("ERROR:", x)
    for x in M.warnings:
        print("WARNING:", x)
    gates = ", ".join(f"{g} {v['label']}" for g, v in M.gates.items())
    print(f"{'FAIL' if M.errors else 'PASS'}: {len(M.tasks)} manifest tasks rendered; {len(M.lanes)} lanes; gates {gates}; "
          f"{len(M.errors)} error(s), {len(M.warnings)} warning(s)")
    return 1 if M.errors else 0


def cmd_apply(now):
    manifest, state = load()
    rev = state.get("revision", 0)
    lines = apply_updates(manifest, state, sorted(UPDATES.glob("*.json")) if UPDATES.is_dir() else [], now)
    lines += refresh_eta(manifest, state, now)
    for x in lines:
        print(x)
    if lines:
        state["updated"] = iso(now)
        write_state(STATE, state, rev)
        print(f"overlay revision {rev} → {state['revision']}")
    else:
        print("nothing new; overlay unchanged")
    return cmd_render(now)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    cmd = {"render": cmd_render, "apply-updates": cmd_apply, "check": cmd_check}.get(args[0] if args else "render")
    if not cmd:
        print(__doc__)
        return 2
    return cmd(dt.datetime.now(UTC).replace(microsecond=0))


if __name__ == "__main__":
    sys.exit(main())
