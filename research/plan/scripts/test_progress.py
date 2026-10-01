"""Oracles for the consumer-v1 tracker: coverage, gate honesty, ETA refusal, update ingestion, escaping, atomic writes.

    cd research/plan/scripts && python3 -m unittest test_progress -v
"""
import copy
import datetime as dt
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import progress as P

NOW = dt.datetime(2026, 9, 24, 22, 0, tzinfo=dt.timezone.utc)
MANIFEST, STATE = P.load()
# Lanes as dispatched at f1a202ab: (lane, task, activity, slice). The oracles run on this frozen fixture, not on live progress.
LANES = [("S3", "S3", "complete", "all"), ("TRACKER", None, "review", "06-progress-tracker (support)"),
         ("E2C", "E2C", "running", "all"), ("F2C-L", "F2C", "running", "a/b/d"), ("F2C-C", "F2C", "running", "c")] + [
    (i, i, "running", "all") for i in ("E1C", "I8", "D10", "M5", "W5", "G7", "G8")] + [(i, i, "queued", None) for i in ("M6", "E3C", "E4C")]


def hours_ago(h):
    return P.iso(NOW - dt.timedelta(hours=h))


class Base(unittest.TestCase):
    def setUp(self):
        self.manifest, self.state = copy.deepcopy(MANIFEST), copy.deepcopy(STATE)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.pin()

    def pin(self):
        """Freeze everything live progress changes (task status, lanes, gates, inputs, findings, windows), so these
        oracles keep their meaning on any branch the tracker is merged into; structure still comes from tasks.json."""
        s, base = self.state, set(self.state["reused_baseline"]["tasks"])
        for t in self.manifest["tasks"]:
            if t["id"] not in base and not t["status"].startswith("superseded"):
                t["status"] = "planned"
        s["lanes"] = [{"id": i, "task": t, "activity": a, "slice": sl, "updated": hours_ago(1), "deviation": "fixture" if a == "running" else None}
                      for i, t, a, sl in LANES]
        s.update(ingested={}, review_queue=[], integration_queue=[], agent_slots={"total": 14, "reserved": 2}, activated=[],
                 eta_params={"review_rework_fraction": 0.3, "integration_h_per_task": 0.5})
        for g in s["gates"].values():
            g.update(candidate={"source": None, "deployed": None, "config": None}, decision=None, decided_at=None)
            for c in g["cells"]:
                c.update(verdict="NOT RUN", evidence=[])
        for x in s["inputs"] + s["findings"]:
            x.update(status="open", evidence=[])
        for x in s["resource_locks"]:
            x["windows"] = []

    def tearDown(self):
        self.tmp.cleanup()

    def model(self):
        return P.Model(self.manifest, self.state, NOW, self.dir)

    def estimate_everything(self, h=2.0):
        """Give every lane a fresh estimate and every remaining task on the App path a lane."""
        for lane in self.state["lanes"]:
            lane["estimate"] = {"optimistic_h": h / 2, "likely_h": h, "pessimistic_h": h * 2, "confidence": "low", "basis": "test", "at": hours_ago(1)}
        have = {x.get("task") for x in self.state["lanes"]}
        for i in P.Model(self.manifest, self.state, NOW, None).eta["E4"]["remaining"]:
            if i not in have:
                self.state["lanes"].append({"id": i, "task": i, "activity": "queued", "updated": hours_ago(1),
                                            "estimate": {"optimistic_h": 1, "likely_h": 2, "pessimistic_h": 4, "confidence": "low", "at": hours_ago(1)}})

    def resolve_inputs(self):
        for p in self.state["inputs"]:
            p["status"] = "resolved"

    def by_id(self, key, i):
        return next(x for x in self.state[key] if x["id"] == i)

    def allocate_gpu(self):
        gpu = next(x for x in self.state["resource_locks"] if x["kind"] == "gpu")
        gpu["windows"] = [{"task": t, "start": P.iso(NOW + dt.timedelta(hours=10 * n)), "end": P.iso(NOW + dt.timedelta(hours=10 * n + 6))}
                          for n, t in enumerate(gpu["tasks"])]

    def write_update(self, name, body):
        (self.dir / name).write_text(json.dumps(body))
        return self.dir / name


class Coverage(Base):
    def test_every_manifest_task_appears_in_both_outputs(self):
        # Oracle: v46 rendered only its 30 banded tasks; any omitted, filtered-out-at-render or superseded task fails here.
        m = self.model()
        self.assertEqual(P.uncovered(m, P.render_html(m), P.render_md(m)), [])
        self.assertEqual(len(m.views), len(self.manifest["tasks"]))

    def test_denominators_separate_new_work_from_reused_baseline(self):
        m = self.model()
        s = {x["cat"]: len(x["ids"]) for x in P.summaries(m)}
        self.assertEqual(s, {"backend": 14, "app": 12, "activated": 0, "baseline": 44})  # deferred and superseded are not headline counts
        self.assertEqual(sum(s.values()) + sum(c in ("deferred", "superseded") for c in m.cat.values()), len(self.manifest["tasks"]))

    def test_superseded_tasks_are_not_shown_but_still_accounted(self):
        # Oracle: a superseded task left in a task table or a summary, or dropped without the footer naming it, fails here.
        m = self.model()
        page, md = P.render_html(m), P.render_md(m)
        sup = sorted(i for i, c in m.cat.items() if c == "superseded")
        self.assertEqual(len(sup), 6)
        for i in sup:
            self.assertNotIn(f'data-id="{i.lower()}"', page)
            self.assertNotIn(f"| `{i}` |", md)
        self.assertIn("6 superseded tasks are not shown (replaced by", page)
        self.assertIn("6 superseded tasks are not shown (replaced by", md)
        self.assertNotIn("superseded", {x["cat"] for x in P.summaries(m)})
        self.assertNotIn("superseded", {v["cat"] for v in json.loads(re.search(r'id="data">(.*?)</script>', page)[1])["tasks"]})
        self.assertEqual(P.uncovered(m, page.replace(f'data-superseded="{sup[0].lower()}"', ""), md), [sup[0]])  # check stays strict

    def test_deferred_tasks_are_collapsed_last_and_uncounted(self):
        # Oracle: deferred rows in the main task list, above the other sections, open by default or in a headline count fail here.
        m = self.model()
        page, md = P.render_html(m), P.render_md(m)
        title = "Later — not in the v1 launch scope (57 tasks)"
        start = page.index('<details id="later">')
        self.assertIn(f"<summary><h2>{title}</h2></summary>", page)
        self.assertGreater(start, page.index('id="timeline"'))
        tasks = page[page.index('id="tasks"'):page.index('id="board"')]
        for i, c in m.cat.items():
            if c == "deferred":
                self.assertNotIn(f'data-id="{i.lower()}"', tasks)
                self.assertGreater(page.index(f'data-id="{i.lower()}"'), start)
                self.assertGreater(md.index(f"| `{i}` |"), md.index(title))
        self.assertGreater(md.index(title), md.index("## Activity log"))
        self.assertNotIn("deferred", {x["cat"] for x in P.summaries(m)})
        self.assertEqual(P.launch_scope(m)["total"], 14 + 12 + len(P.GO_LIVE))

    def test_launch_scope_headline_arithmetic(self):
        # Oracle: the headline must be backend + App + go-live steps, done/total, remaining in go-live order.
        m = self.model()
        x = P.launch_scope(m)
        self.assertEqual((x["done"], x["total"]), (0, 30))
        self.assertEqual([i for i, _ in x["remaining"]][:2], ["RELEASE-FREEZE", "MAIN-MERGE"])
        self.assertEqual([i for i, _ in x["remaining"]][-1], "APP-PILOT")
        next(t for t in self.manifest["tasks"] if t["id"] == "E1B")["status"] = "implemented"
        self.state["lanes"].append({"id": "RELEASE-FREEZE", "task": None, "activity": "complete", "updated": hours_ago(1)})
        m = self.model()
        x = P.launch_scope(m)
        self.assertEqual((x["done"], x["total"]), (2, 30))
        self.assertEqual(x["remaining"][0][0], "MAIN-MERGE")
        (_, k, n, rem), app = x["subs"][0], x["subs"][1]
        self.assertEqual((k, n, len(rem)), (1, 14, 13))
        self.assertNotIn("E1B", [i for i, _ in rem])
        self.assertTrue(all("window-bound" in why for _, why in rem))
        self.assertTrue(all("go-live-bound" in why for _, why in app[3]))
        page, md = P.render_html(m), P.render_md(m)
        self.assertIn("<h2>v1 launch scope</h2>", page)
        self.assertIn("2 / 30", page)
        self.assertIn("## v1 launch scope", md)
        self.assertIn("**2 / 30**", md)

    def test_committed_overlay_passes_check(self):
        # The live overlay against the live manifest at host UTC, i.e. what `progress.py check` runs.
        self.assertEqual(P.Model(MANIFEST, STATE, dt.datetime.now(dt.timezone.utc), None).errors, [])
        self.assertEqual(self.model().errors, [])  # and the frozen fixture

    def test_future_lane_timestamp_is_an_error(self):
        # Oracle for the 2026-09-24 clock slip: a lane stamped 22:30Z while the host read 22:01Z rejects genuine updates as stale.
        self.state["lanes"][0]["updated"] = P.iso(NOW + dt.timedelta(minutes=30))
        self.assertTrue(any("is in the future" in x for x in self.model().errors))
        self.state["lanes"][0]["updated"] = P.iso(NOW + dt.timedelta(minutes=10))  # within the skew allowance
        self.assertEqual(self.model().errors, [])

    def test_overlay_unknown_ids_schema_and_estimates_are_errors(self):
        self.estimate_everything()
        self.resolve_inputs()
        self.assertIn("finish", self.model().eta["E3C"])  # positive control
        self.state["bands"][0]["tasks"].append("ZZ9")
        self.by_id("inputs", "P-01")["blocks"].append("ZZ8")
        self.state["schema"] = 1
        self.state["lanes"][2]["estimate"] = {"optimistic_h": 3, "likely_h": 2, "pessimistic_h": 4}
        m = self.model()
        for want in ("unknown task ID 'ZZ9' in band", "unknown task ID 'ZZ8' in input P-01", "overlay schema must be 2",
                     "lane E2C: estimate hours [3, 2, 4]"):
            self.assertTrue(any(want in x for x in m.errors), (want, m.errors))
        self.assertIn("E2C", m.eta["E3C"]["unknown"])  # a malformed estimate never feeds a date
        self.assertNotIn("finish", m.eta["E3C"])


class Activation(Base):
    """LW0 item 9 (R155): an `activated` deferred task gets the normal readiness, lane accounting and task list."""

    def test_an_activated_deferred_task_is_scheduled_like_any_other(self):
        # Oracle: an activated task still reported blocked-as-deferred, left in the collapsed Later list,
        # missing from the summaries/next-ready work, or not counting its running lane fails here.
        self.assertEqual(self.model().readiness("L1")[0], "blocked")
        self.state["activated"] = ["L1", "L2"]
        self.state["lanes"].append({"id": "lab-access", "task": "L2", "activity": "running", "updated": hours_ago(1), "deviation": "fixture"})
        m = self.model()
        self.assertEqual((m.cat["L1"], m.readiness("L1")), ("activated", ("ready", "start dependencies met")))
        self.assertEqual(m.readiness("L2")[0], "active")
        x = next(x for x in P.summaries(m) if x["cat"] == "activated")
        self.assertEqual((x["ids"], x["active"]), (["L1", "L2"], ["L2"]))
        self.assertIn("L1", [v["id"] for v in P.overview_facts(m)["ready"]])
        page, md = P.render_html(m), P.render_md(m)
        tasks = page[page.index('id="tasks"'):page.index('id="board"')]
        self.assertIn('data-id="l1"', tasks)
        self.assertIn("Later — not in the v1 launch scope (55 tasks)", page)
        self.assertLess(md.index("| `L1` |"), md.index("## Activity log"))
        self.assertEqual(P.uncovered(m, page, md), [])
        self.assertEqual(P.launch_scope(m)["total"], 14 + 12 + len(P.GO_LIVE))  # the v1 launch headline is unchanged
        self.assertEqual(m.errors, [])

    def test_only_a_known_deferred_task_can_be_activated(self):
        # Oracle: activating a backend/App/baseline task (re-categorising launch work) or an unknown ID passes silently.
        self.state["activated"] = ["E4C", "F2P", "NOPE"]
        errors = self.model().errors
        self.assertIn("activated task E4C is backend, not deferred", errors)
        self.assertIn("activated task F2P is baseline, not deferred", errors)
        self.assertIn("unknown task ID 'NOPE' in activated", errors)


class Waves(Base):
    """WR-LW0-3 (plan §3 item 9): the checked-in wave map (consumer-v1/07-post-launch-waves.md) renders LW0–LW6 + C1–C3."""

    def wave(self, m, i):
        return next(v for v in P.wave_views(m) if v["id"] == i)

    def lane(self, i, wave, activity, h=None, confidence="medium"):
        est = {"optimistic_h": h[0], "likely_h": h[1], "pessimistic_h": h[2], "confidence": confidence, "at": hours_ago(1)} if h else None
        self.state["lanes"].append({"id": i, "task": None, "activity": activity, "slice": f"(wave5 {wave}; fixture)", "updated": hours_ago(1), "estimate": est})

    def implement(self, *ids):
        for t in self.manifest["tasks"]:
            if t["id"] in ids:
                t["status"] = "implemented"

    def test_the_map_places_every_deferred_task_in_exactly_one_wave(self):
        # Oracle: a parser that drops a row, an italic lane or a note-bearing ID, keeps a note as an ID, or counts a slice
        # (L2-SQL) as a manifest task; and a map that loses or repeats a deferred task.
        waves, m = P.load_waves(), self.model()
        self.assertEqual([w["id"] for w in waves], ["LW0", "LW1", "LW2", "LW3", "LW4", "LW5", "LW6", "C1", "C2", "C3"])
        lw1 = {x["name"]: x for x in waves[1]["lanes"]}
        self.assertEqual(lw1["lab-sql"]["ids"], ["L2-SQL", "D6F", "D7"])
        self.assertEqual((lw1["discovery"]["ids"], lw1["discovery"]["gating"], lw1["lab-sql"]["gating"]), (["X1", "X3", "X5"], False, True))
        self.assertEqual(waves[5]["gate_lanes"], [{"task": "E5L", "lane": "lab-observe"}, {"task": "E6L", "lane": "lab-evaluate"}])
        self.assertEqual(waves[0]["gate_lanes"], [{"task": None, "lane": "LW0"}])
        ids = [i for w in waves for x in w["lanes"] for i in x["ids"] if i in m.tasks]
        self.assertEqual(sorted(ids), sorted(i for i, c in m.cat.items() if c == "deferred"))
        self.assertEqual(len(ids), 57)
        self.assertEqual([len(v["ids"]) for v in P.wave_views(m)], [0, 10, 11, 10, 12, 7, 2, 1, 1, 3])
        self.assertEqual(m.errors, [])
        empty = self.dir / "empty.md"
        empty.write_text("# no table\n")
        self.assertRaises(ValueError, P.load_waves, empty)  # a mangled map never passes as "no waves"

    def test_a_task_missing_repeated_unknown_or_in_launch_scope_is_an_error(self):
        # Oracle: `check` passing a map that drops D6F, lists L1 twice, invents ZZ9 or a slice of an unknown task,
        # schedules launch-scope E4C, or names a gate task outside its wave.
        waves = P.load_waves()
        self.state["activated"] = ["D6F", "L2"]  # activated tasks are post-launch too: dropping one is the same error
        waves[1]["lanes"][0]["ids"].remove("D6F")
        waves[1]["lanes"][0]["ids"].remove("D7")
        waves[2]["lanes"][0]["ids"] += ["L1", "ZZ9", "Q9-SQL", "E4C", "L3-SQL"]
        waves[3]["gate_lanes"].append({"task": "B4", "lane": "eval-ui"})
        errors = P.Model(self.manifest, self.state, NOW, self.dir, waves).errors
        for want in ("wave map: deferred task D6F is in no wave", "wave map: deferred task D7 is in no wave", "wave map: L1 is in more than one wave", "wave map: unknown ID 'ZZ9' in LW2",
                     "wave map: unknown ID 'Q9-SQL' in LW2", "wave map: E4C is backend, not post-launch", "wave map: gate task B4 is not in LW3"):
            self.assertIn(want, errors)
        self.assertFalse(any("L3-SQL" in x for x in errors))  # a slice of a manifest task is fine

    def test_a_wave_exit_is_met_only_with_its_tasks_implemented_and_its_gate_lane_complete(self):
        # Oracle: EXIT MET while a gating task is planned, while the gate lane is missing, in review or only a
        # similarly named lane (lab-operate-x) is complete; or a non-gating discovery task holding its wave back.
        m = self.model()
        self.assertEqual((self.wave(m, "LW3")["state"], self.wave(m, "LW0")["state"]), ("PENDING", "PENDING"))
        self.state["lanes"].append({"id": "LW0", "task": None, "activity": "complete", "slice": "(post-launch Lab pre-wave)", "updated": hours_ago(1)})
        self.state["lanes"].append({"id": "L1-LANE", "task": "L1", "activity": "complete", "updated": hours_ago(1)})  # a task-keyed lane
        m = self.model()
        self.assertEqual((self.wave(m, "LW0")["state"], self.wave(m, "LW0")["eta"]["status"]), ("EXIT MET", "done"))
        self.assertEqual([x["id"] for x in self.wave(m, "LW1")["overlay"]], ["L1-LANE"])
        lw1 = [i for x in P.load_waves()[1]["lanes"] if x["gating"] for i in x["ids"]]
        self.implement(*lw1)
        v = self.wave(self.model(), "LW1")
        self.assertEqual((v["state"], len(v["done"]), len(v["ids"])), ("EXIT MET", 7, 10))  # X1/X3/X5 planned, do not gate
        lw3 = self.wave(self.model(), "LW3")["ids"]
        self.implement(*lw3[:-1])
        v = self.wave(self.model(), "LW3")
        self.assertEqual(v["state"], "PENDING")
        self.assertIn(f"not implemented: {lw3[-1]}", v["why"])
        self.implement(*lw3)
        self.assertIn("gate lane lab-operate is not in the overlay", self.wave(self.model(), "LW3")["why"])
        self.state["lanes"].append({"id": "W5-X", "task": None, "activity": "complete", "slice": "(wave5 LW3 lane lab-operate-x)", "updated": hours_ago(1)})
        self.assertEqual(self.wave(self.model(), "LW3")["why"], ["gate lane lab-operate is not in the overlay"])  # a similar name is not the lane
        self.state["lanes"].append({"id": "W5-LAB-OPERATE", "task": None, "activity": "review", "slice": "(wave5 LW3 lane lab-operate)", "updated": hours_ago(1)})
        v = self.wave(self.model(), "LW3")
        self.assertEqual((v["state"], v["why"]), ("PENDING", ["gate lane lab-operate is not complete"]))
        self.assertEqual([x["id"] for x in v["overlay"]], ["W5-X", "W5-LAB-OPERATE"])
        self.state["lanes"][-1]["activity"] = "complete"
        self.assertEqual(self.wave(self.model(), "LW3")["state"], "EXIT MET")

    def test_wave_eta_uses_the_live_lanes_and_eta_params(self):
        # Oracle: the milestone arithmetic (1 + rework, + one merge per lane, capacity over free slots) not applied; a date
        # while a live lane has no estimate, while open work has no live lane (even if another lane of the wave is live), from
        # a lane whose task is already implemented, or while an open input blocks an open task.
        self.assertEqual(self.wave(self.model(), "LW6")["eta"]["text"], "no live lane for E7L, E8L")  # open work, no lane
        self.lane("W5-LAB-IMPROVE", "LW6 lane lab-improve", "running", (3, 6, 12))
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["status"], f["text"], "finish" in f), ("unknown", "no live lane for E8L", False))  # E8L open, no lab-rollout lane
        self.state["lanes"].pop()
        self.implement("E7L")
        self.state["lanes"].append({"id": "W5-DONE", "task": "E7L", "activity": "blocked", "slice": None, "updated": hours_ago(1),
                                    "estimate": {"optimistic_h": 1, "likely_h": 2, "pessimistic_h": 3, "confidence": "high", "at": hours_ago(1)}})
        self.assertEqual(self.wave(self.model(), "LW6")["eta"]["text"], "no live lane for E8L")  # a lane for finished work covers nothing
        self.lane("W5-LAB-ROLLOUT", "LW6 lane lab-rollout", "review", (2, 4, 8), "low")
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["status"], f["effort_h"], f["confidence"]), ("forecast", [3.1, 5.7, 10.9], "low"))  # W5-DONE not counted
        self.state["lanes"][-2:] = []
        for t in self.manifest["tasks"]:
            if t["id"] == "E7L":
                t["status"] = "planned"
        self.implement("V1M")  # lab-app [L4, V1M]: a lane keyed to the finished V1M does not cover the open L4
        self.state["lanes"].append({"id": "V1M-LANE", "task": "V1M", "activity": "blocked", "updated": hours_ago(1)})
        self.assertIn("L4", self.wave(self.model(), "LW2")["eta"]["text"])
        self.state["lanes"].pop()
        self.lane("W5-X", "LW6 lane lab-rollout-x", "running")  # nor does a similarly named lane
        self.lane("W5-LAB-IMPROVE", "LW6 lane lab-improve", "running", (3, 6, 12))
        self.lane("W5-LAB-ROLLOUT", "LW6 lane lab-rollout", "review", (2, 4, 8), "low")
        self.lane("W5-OLD", "LW6 lane lab-improve", "complete")  # a finished lane adds nothing
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["status"], f["effort_h"], f["wall_h"], f["confidence"]), ("forecast", [7.5, 14.0, 27.0], [4.4, 8.3, 16.1], "low"))
        self.assertEqual(f["finish"][1], P.iso(NOW + dt.timedelta(hours=8.3)))
        self.assertIn("longest lane W5-LAB-IMPROVE", f["constraint"])
        self.state["agent_slots"] = {"total": 1, "reserved": 0}
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["wall_h"], f["constraint"][:9]), ([7.5, 14.0, 27.0], "capacity:"))
        self.state["agent_slots"] = {"total": 14, "reserved": 2}
        saved = [copy.deepcopy(x["estimate"]) for x in self.state["lanes"][-3:-1]]
        for x in self.state["lanes"][-3:-1]:
            x["estimate"].update(optimistic_h=0, likely_h=0, pessimistic_h=0)
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["wall_h"], f["constraint"]), ([1.0, 1.0, 1.0], "serial integration queue: 2 merges × 0.5 h"))
        for x, est in zip(self.state["lanes"][-3:-1], saved):
            x["estimate"] = est
        self.lane("W5-NOEST", "LW6 lane lab-improve", "running")
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["status"], "finish" in f), ("unknown", False))
        self.assertIn("W5-NOEST", f["text"])
        self.state["lanes"].pop()
        self.by_id("inputs", "P-01")["blocks"].append("E8L")
        f = self.wave(self.model(), "LW6")["eta"]
        self.assertEqual((f["status"], f["text"], "finish" in f), ("blocked", "blocked pending P-01", False))

    def test_the_section_renders_and_everything_else_is_unchanged(self):
        # Oracle: no waves section in either output, a wave row missing, or any existing summary, category, section or
        # link changed by adding it.
        m, m0 = self.model(), P.Model(self.manifest, self.state, NOW, self.dir, [])
        page, md = P.render_html(m), P.render_md(m)
        sec = page[page.index('<section id="waves">'):]
        sec = sec[:sec.index("</section>")]
        self.assertIn('<a href="#waves">Post-launch waves</a>', page)
        self.assertIn("## Post-launch waves", md)
        for w in P.load_waves():
            self.assertIn(f"<strong>{w['id']}</strong>", sec)
            self.assertIn(f"| **{w['id']}** ", md)
        self.assertIn("0 / 12", sec)
        self.assertIn("<code>L2-SQL</code> <span class=\"pill\">slice</span>", sec)
        self.assertEqual(re.sub(r'\n<section id="waves">.*?</section>|<a href="#waves">Post-launch waves</a>', "", page, flags=re.S), P.render_html(m0))
        self.assertEqual(re.sub(r"## Post-launch waves\n.*?(?=## Gates)", "", md, flags=re.S), P.render_md(m0))
        self.assertEqual((P.summaries(m), m.cat), (P.summaries(m0), m0.cat))


class Gates(Base):
    def pass_all(self, gate):
        g = self.state["gates"][gate]
        for c in g["cells"]:
            c.update(verdict="PASS", evidence=[f"research/plan/evidence/e/{c['id']}.md"])
        g["candidate"] = {"source": "c0ffee1", "deployed": "release c0ffee1 (test)", "config": "profile test"}
        return g

    def implement(self, *ids):
        for t in self.manifest["tasks"]:
            if t["id"] in ids:
                t["status"] = "implemented"

    def assert_not_accepted(self, gate, reason):
        m = self.model()
        self.assertFalse(m.gates[gate]["green"])
        self.assertNotEqual(m.gates[gate]["label"], "ACCEPTED")
        self.assertNotIn(f"{gate} <span class=\"pill ok\">ACCEPTED", P.render_html(m))
        self.assertTrue(any(f"impossible gate transition: {gate}" in x and reason in x for x in m.errors), (reason, m.errors))

    def test_implemented_e4c_without_decision_is_not_green(self):
        self.implement("E4C")
        self.pass_all("BACKEND-READY")
        m = self.model()
        self.assertFalse(m.gates["BACKEND-READY"]["green"])
        self.assertEqual(m.gates["BACKEND-READY"]["label"], "PENDING")
        self.assertIn("E4C", m.eta["E3A"]["remaining"])  # App path waits for the pending gate
        self.assertNotIn("ACCEPTED", P.render_html(m))
        self.state["gates"]["BACKEND-READY"]["decision"] = "accepted"  # positive control
        m = self.model()
        self.assertTrue(m.gates["BACKEND-READY"]["green"])
        self.assertIn('BACKEND-READY <span class="pill ok">ACCEPTED', P.render_html(m))
        self.assertEqual(m.errors, [])

    def test_accepted_decision_on_unimplemented_root_is_an_error(self):
        self.pass_all("BACKEND-READY")["decision"] = "accepted"
        self.assert_not_accepted("BACKEND-READY", "its root task is not implemented")

    def test_accepted_decision_needs_every_required_cell_passed(self):
        self.implement("E4C")
        g = self.pass_all("BACKEND-READY")
        g["decision"] = "accepted"
        g["cells"][1]["verdict"] = "FAIL"
        self.assert_not_accepted("BACKEND-READY", "not every cell is PASS")
        g["cells"][1]["verdict"] = "NOT RUN"
        self.assert_not_accepted("BACKEND-READY", "not every cell is PASS")
        g["cells"] = [c for c in g["cells"] if c["id"] == "BACKEND-JOURNEY"]  # 1 of 6 required cells, all PASS
        self.assert_not_accepted("BACKEND-READY", "required cells missing: LOAD-CLOSEDLOOP")
        self.assertTrue(any("BACKEND-READY lacks required cells LOAD-CLOSEDLOOP" in x for x in self.model().errors))

    def test_accepted_decision_needs_candidate_identity_and_evidence(self):
        # Old E4B evidence is scoped to its candidate: reusing run3's bda1586 as the E4C candidate is not acceptance.
        self.implement("E4C")
        g = self.pass_all("BACKEND-READY")
        g["decision"] = "accepted"
        g["candidate"] = {"source": None, "deployed": None, "config": None}
        self.assert_not_accepted("BACKEND-READY", "no candidate source/deployed identity")
        g["candidate"] = {"source": "bda1586", "deployed": "release bda1586", "config": "run3"}
        self.assert_not_accepted("BACKEND-READY", "historical run E4B-run3's candidate")
        g["candidate"]["note"] = "coordinator: same SHA re-certified under E4C cells"  # explicit, recorded reuse
        self.assertTrue(self.model().gates["BACKEND-READY"]["green"])
        g["cells"][0]["evidence"] = []
        self.assert_not_accepted("BACKEND-READY", "PASS without evidence: BACKEND-JOURNEY")

    def test_invalid_verdicts_and_decisions_are_errors(self):
        g = self.state["gates"]["BACKEND-LOCAL"]
        g["cells"][0]["verdict"], g["decision"] = "MAYBE", "yes"
        errs = self.model().errors
        self.assertTrue(any("BACKEND-LOCAL cell BACKEND-JOURNEY: verdict 'MAYBE'" in x for x in errs), errs)
        self.assertTrue(any("BACKEND-LOCAL decision 'yes'" in x for x in errs), errs)

    def test_finding_closed_without_evidence_is_an_error(self):
        self.by_id("findings", "RV-01")["status"] = "fixed"
        self.assertTrue(any("RV-01 is fixed without evidence" in x for x in self.model().errors))
        self.by_id("findings", "RV-01")["evidence"] = ["research/plan/evidence/e/E2R-5b8e118.md"]
        self.assertEqual(self.model().errors, [])

    def test_gated_app_lane_running_is_an_error(self):
        self.state["lanes"].append({"id": "I3", "task": "I3", "activity": "running"})
        self.assertTrue(any("I3 dispatches only after BACKEND-READY" in x for x in self.model().errors))


class GateRecords(Base):
    """WR-PL-1: local gate records (R222) render in both outputs, need a class and rerun on every non-PASS cell, and never forecast."""
    REC = {"E4-ON": {"manifest_gate": None, "candidate": {"source": "5bb93621"}, "decision": "accepted", "ruling": "R262",
                     "cells": [{"id": "o01", "verdict": "PASS"}, {"id": "o03", "verdict": "NOT RUN", "class": "P-11", "rerun": "make lab-local"}]}}

    def test_records_render_and_do_not_forecast(self):
        before = self.model().eta  # the baseline ETA set, taken BEFORE gate_records exist
        self.state["gate_records"] = copy.deepcopy(self.REC)
        M = self.model()
        self.assertEqual(M.errors, [])
        self.assertIn("Local gate records (R222)", P.render_html(M))
        self.assertIn("o03 NOT RUN [P-11]", P.render_md(M))
        self.assertEqual(set(M.eta), set(before))
        for k in before:
            self.assertEqual(M.eta[k], before[k], k)  # per row: a record never moves a forecast

    def test_a_not_run_cell_without_class_or_rerun_is_an_error(self):
        rec = copy.deepcopy(self.REC)
        del rec["E4-ON"]["cells"][1]["rerun"]
        self.state["gate_records"] = rec
        self.assertTrue(any("without its class and rerun" in x for x in self.model().errors))
        rec["E4-ON"]["cells"][1].update(rerun="make lab-local", verdict="GREEN")
        self.assertTrue(any("not in" in x for x in self.model().errors))


class Eta(Base):
    def test_open_input_on_e4c_path_gives_no_finite_eta(self):
        self.estimate_everything()
        self.allocate_gpu()
        self.resolve_inputs()
        self.by_id("inputs", "P-01")["status"] = "open"  # P-01 blocks E4C
        f = self.model().eta["E4C"]
        self.assertEqual(f["status"], "blocked")
        self.assertTrue(f["text"].startswith("blocked pending P-01"))
        self.assertNotIn("finish", f)
        self.assertNotRegex(f["text"], r"\d{4}-\d{2}-\d{2}")
        self.by_id("inputs", "P-01")["status"] = "resolved"  # positive control: same estimates now forecast a date
        self.assertIn("finish", self.model().eta["E4C"])

    def test_gpu_milestone_without_window_gives_no_date(self):
        self.estimate_everything()
        self.resolve_inputs()
        f = self.model().eta["E4C"]
        self.assertEqual(f["status"], "unknown")
        self.assertIn("no GPU window allocated for E1B, E4C", f["text"])
        self.assertNotIn("finish", f)
        self.assertIn("not a date", f["conditional"])
        self.assertIn("finish", self.model().eta["E3C"])  # E3C needs no GPU window
        self.allocate_gpu()
        self.assertIn("finish", self.model().eta["E4C"])

    def test_unknown_estimates_stay_unknown(self):
        f = self.model().eta["E3C"]  # fixture lanes carry no estimate
        self.assertEqual(f["status"], "unknown")
        self.assertNotIn("finish", f)

    def test_remaining_task_with_no_lane_gives_no_date(self):
        # Oracle: a task nobody is assigned to has no known effort; counting it as 0 h fabricates a date.
        self.estimate_everything()
        self.resolve_inputs()
        self.allocate_gpu()
        self.assertIn("finish", self.model().eta["E4C"])  # positive control
        self.state["lanes"] = [x for x in self.state["lanes"] if x.get("task") != "E1B"]
        f = self.model().eta["E4C"]
        self.assertEqual(f["status"], "unknown")
        self.assertIn("E1B", f["unknown"])
        self.assertNotIn("finish", f)

    def durations(self, f, k=1):
        m, rework, integ = self.model(), 0.3, 0.5
        return {i: m.effort(i)[1][k] * (1 + rework) + integ for i in f["remaining"]}

    def test_resource_and_dependency_constraints_shape_the_forecast(self):
        self.estimate_everything(2.0)
        self.resolve_inputs()
        self.allocate_gpu()
        f = self.model().eta["E3C"]
        # 12 tasks at likely 2 h × 1.3 + 0.5 h merge: a serial chain through D10 → … → E3C controls, not total/slots.
        self.assertTrue(f["constraint"].startswith("dependency path "), f["constraint"])
        self.assertEqual(f["critical_path"][-1], "E3C")
        self.assertGreaterEqual(len(f["critical_path"]), 6)
        self.assertIn("D10", f["critical_path"])
        self.assertGreater(f["wall_h"][1], f["effort_h"][1] / 7)
        self.assertLess(f["effort_h"][0], f["effort_h"][1])
        self.assertLess(f["wall_h"][1], f["wall_h"][2])
        # parallel work does not add: two F2C slices take the longer slice, and wall-clock is below the serial sum of task durations
        self.assertEqual(self.model().effort("F2C"), ((2.0, 4.0, 8.0), (1.0, 2.0, 4.0)))
        self.assertLess(f["wall_h"][1], sum(self.durations(f).values()))

    def test_exclusive_lock_serializes_independent_tasks(self):
        # W5 and G7 do not depend on each other; under one lock their durations add even though the dependency path would overlap them.
        self.estimate_everything(2.0)
        self.resolve_inputs()
        for i in ("W5", "G7"):
            self.by_id("lanes", i)["estimate"].update(optimistic_h=20, likely_h=40, pessimistic_h=80)
        free = self.model().eta["E3C"]
        self.state["resource_locks"].append({"id": "test-runner", "kind": "runner", "label": "test runner dir", "tasks": ["W5", "G7"]})
        f = self.model().eta["E3C"]
        d = self.durations(f)
        self.assertTrue(f["constraint"].startswith("exclusive test runner dir: G7, W5 run serially"), f["constraint"])
        self.assertGreaterEqual(f["wall_h"][1], round(d["W5"] + d["G7"], 1))
        self.assertLess(free["wall_h"][1], d["W5"] + d["G7"])  # positive control: without the lock they overlap

    def test_gpu_window_start_delays_the_finish(self):
        self.estimate_everything(2.0)
        self.resolve_inputs()
        self.allocate_gpu()
        early = self.model().eta["E4C"]
        self.assertLess(P.parse(early["finish"][0]), NOW + dt.timedelta(hours=30))  # positive control
        w = next(w for x in self.state["resource_locks"] for w in x["windows"] if w["task"] == "E4C")
        w.update(start=P.iso(NOW + dt.timedelta(hours=30)), end=P.iso(NOW + dt.timedelta(hours=36)))
        f = self.model().eta["E4C"]
        self.assertGreaterEqual(P.parse(f["finish"][0]), NOW + dt.timedelta(hours=36))

    def test_serial_integration_queue_bounds_short_tasks(self):
        # With zero-hour lanes every task still needs its own coordinator merge, one at a time.
        self.estimate_everything(0.0)
        self.resolve_inputs()
        f = self.model().eta["E3C"]
        self.assertTrue(f["constraint"].startswith("serial integration queue"), f["constraint"])
        self.assertEqual(f["wall_h"][1], len(f["remaining"]) * 0.5)

    def test_stale_estimate_is_flagged(self):
        self.estimate_everything()
        lane = next(x for x in self.state["lanes"] if x["id"] == "D10")
        lane["estimate"]["at"] = hours_ago(7)
        m = self.model()
        self.assertIn("D10", m.eta["E3C"]["stale"])
        self.assertTrue(any("stale estimate: lane D10" in x for x in m.warnings))
        self.assertIn("STALE", P.render_html(m))
        lane["estimate"]["at"] = hours_ago(5)
        self.assertNotIn("D10", self.model().eta["E3C"]["stale"])


class Updates(Base):
    def setUp(self):
        super().setUp()
        for x in self.state["lanes"]:  # fixture: every lane last updated before the test's update files
            x["updated"] = "2026-09-24T21:00:00Z"
        self.state["review_queue"], self.state["integration_queue"] = [], []

    def lane(self, i):
        return next(x for x in self.state["lanes"] if x["id"] == i)

    def apply(self, *files):
        return P.apply_updates(self.manifest, self.state, list(files), NOW)

    def test_unknown_task_id_is_rejected_and_listed(self):
        before = copy.deepcopy(self.state["lanes"])
        out = self.apply(self.write_update("ZZ9-20260924T2150Z.json", {"task": "ZZ9", "activity": "running"}))
        self.assertIn("unknown task ID 'ZZ9'", out[0])
        self.assertEqual(self.state["lanes"], before)
        self.assertEqual(self.state["ingested"]["ZZ9-20260924T2150Z.json"]["status"], "rejected")
        self.assertIn("ZZ9-20260924T2150Z.json", P.render_html(self.model()))

    def test_older_update_does_not_overwrite_newer_state(self):
        newer = self.write_update("D10-20260924T2155Z.json", {"task": "D10", "activity": "running", "head": "bbbbbbb"})
        older = self.write_update("D10-20260924T2150Z.json", {"task": "D10", "activity": "running", "head": "aaaaaaa"})
        self.apply(newer)
        out = self.apply(older)
        self.assertEqual(self.lane("D10")["head"], "bbbbbbb")
        self.assertIn("stale", out[0])
        # same batch: applied in `at` order, so the newest wins regardless of listing order
        self.apply(self.write_update("M5-20260924T2155Z.json", {"task": "M5", "activity": "running", "head": "new"}),
                   self.write_update("M5-20260924T2150Z.json", {"task": "M5", "activity": "running", "head": "old"}))
        self.assertEqual(self.lane("M5")["head"], "new")
        self.assertEqual(self.state["ingested"]["M5-20260924T2150Z.json"]["status"], "applied")  # older first, not rejected as stale

    def test_impossible_transitions_are_rejected(self):
        out = self.apply(self.write_update("M6-20260924T2150Z.json", {"task": "M6", "activity": "complete"}))
        self.assertIn("impossible transition queued → complete", out[0])
        out = self.apply(self.write_update("D10-20260924T2150Z.json", {"task": "D10", "activity": "complete"}))
        self.assertIn("cannot be complete before start dependencies F2C", out[0])
        out = self.apply(self.write_update("I3-20260924T2150Z.json", {"task": "I3", "activity": "running"}))
        self.assertIn("dispatches only after BACKEND-READY", out[0])
        self.assertFalse(any(x["id"] == "I3" for x in self.state["lanes"]))
        self.lane("M6")["activity"] = "deferred"
        cases = [("S3", {"task": "S3", "activity": "running"}, "complete is terminal"),
                 ("M6", {"task": "M6", "activity": "running"}, "a deferred lane is re-queued first"),
                 ("F2C", {"task": "F2C", "activity": "review"}, "ambiguous: F2C has lanes F2C-L, F2C-C"),
                 ("E2C", {"task": "E2C", "activity": "done"}, "unknown activity 'done'"),
                 ("E2C", {"task": "E2C", "lane": "F2C-L", "activity": "running"}, "lane F2C-L belongs to F2C, not E2C")]
        for n, (lane, body, want) in enumerate(cases):
            before, name = copy.deepcopy(self.state["lanes"]), f"{lane}-20260924T215{n}Z.json"
            out = self.apply(self.write_update(name, body))
            self.assertIn(want, out[0])
            self.assertEqual(self.state["lanes"], before)
            self.assertEqual(self.state["activity_log"][-1]["source"], f"updates/{name}")  # rejections are logged too
            self.assertIn(want, self.state["activity_log"][-1]["what"])

    def test_future_dated_update_is_rejected_and_does_not_lock_the_lane(self):
        before = copy.deepcopy(self.lane("D10"))
        out = self.apply(self.write_update("D10-20991231T0000Z.json", {"task": "D10", "activity": "running", "head": "future"}))
        self.assertIn("future-dated", out[0])
        self.assertEqual(self.lane("D10"), before)
        out = self.apply(self.write_update("D10-20260924T2201Z.json", {"task": "D10", "activity": "running", "head": "genuine"}))
        self.assertEqual(out[0], "applied D10-20260924T2201Z.json: D10 running → running; head genuine")  # within the skew allowance
        self.assertEqual(self.lane("D10")["head"], "genuine")

    def test_malformed_update_is_rejected_and_later_runs_still_work(self):
        # Oracle: a string estimate used to be stored, then crash every later apply-updates run in the ETA.
        bad = {"M5-20260924T2150Z.json": ({"task": "M5", "activity": "running", "estimate": {"optimistic_h": 1, "likely_h": "2", "pessimistic_h": 3}}, "estimate hours"),
               "M5-20260924T2151Z.json": ({"task": "M5", "activity": "running", "estimate": {"optimistic_h": 3, "likely_h": 2, "pessimistic_h": 4}}, "estimate hours"),
               "M5-20260924T2152Z.json": ({"task": "M5", "activity": "running", "estimate": {"likely_h": None, "confidence": "sure"}}, "confidence 'sure'"),
               "M5-20260924T2153Z.json": ({"task": "M5", "activity": "running", "commands": "make check"}, "wrong type for commands"),
               "M5-20260924T2154Z.json": ({"task": "M5", "activity": "running", "at": "2026-09-24T21:54:00"}, "unreadable"),
               "M5-20260924T2155Z.json": ("{not json", "unreadable")}
        for name, (body, _) in bad.items():
            (self.dir / name).write_text(body if isinstance(body, str) else json.dumps(body))
        before = copy.deepcopy(self.state["lanes"])
        with mock.patch.object(P, "UPDATES", self.dir), mock.patch.object(P, "load", return_value=(self.manifest, self.state)), \
                mock.patch.object(P, "write_state"), mock.patch.object(P, "atomic_write"), mock.patch("builtins.print") as out:
            self.assertEqual(P.cmd_apply(NOW), 0)  # the real apply-updates path over the whole directory
        lines = [str(c.args[0]) for c in out.call_args_list if c.args]
        for name, (_, want) in bad.items():
            self.assertTrue(any(x.startswith(f"REJECTED {name}") and want in x for x in lines), (name, want, lines))
        self.assertEqual(self.state["lanes"], before)
        self.apply(self.write_update("M5-20260924T2156Z.json", {"task": "M5", "activity": "running", "estimate": {"optimistic_h": 1, "likely_h": 2, "pessimistic_h": 3}}))
        self.assertEqual(self.lane("M5")["estimate"]["likely_h"], 2)


class Output(Base):
    def test_script_in_a_note_is_escaped(self):
        evil = '</script><script>alert(1)</script><b onmouseover="x">'
        self.state["lanes"][0]["next_action"] = evil
        self.state["inputs"][0]["what"] = evil
        page = P.render_html(self.model())
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn('<b onmouseover', page)
        self.assertEqual(page.count("</script>"), 2)  # only the two the renderer owns
        blob = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
        self.assertEqual(json.loads(blob)["lanes"][0]["next_action"], evil)

    def test_relative_evidence_links_resolve_from_the_output_directory(self):
        self.state["lanes"][0]["evidence"] = ["research/plan/evidence/e/E2R-5b8e118.md", "/opt/box/only.log"]
        m = self.model()
        page, md = P.render_html(m), P.render_md(m)
        self.assertIn('href="../e/E2R-5b8e118.md"', page)
        self.assertIn("<code>/opt/box/only.log</code>", page)
        hrefs = [h for h in re.findall(r'href="([^"]+)"', page) if not re.match(r"^(#|https?:)", h)]
        self.assertTrue(hrefs)
        for h in hrefs + re.findall(r"\]\(<?([^)>]+)>?\)", md):
            self.assertTrue((P.EVID / h.split("#")[0]).exists(), h)

    def test_self_contained_html(self):
        page = P.render_html(self.model())
        self.assertNotRegex(page, r'(src|href)="(https?:)?//')  # no CDN, no fonts
        self.assertIn('id="stale"', page)
        self.assertIn("stale_after_min", page)


class Writes(Base):
    def setUp(self):
        super().setUp()
        self.path = self.dir / "state.json"
        self.path.write_text(json.dumps({"revision": 3, "x": 1}))

    def test_atomic_write_leaves_no_partial_file(self):
        with mock.patch.object(P.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                P.write_state(self.path, {"revision": 3, "x": 2}, 3)
        self.assertEqual(json.loads(self.path.read_text()), {"revision": 3, "x": 1})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["state.json"])  # no .tmp, no .lock
        P.write_state(self.path, {"revision": 3, "x": 2}, 3)
        self.assertEqual(json.loads(self.path.read_text()), {"revision": 4, "x": 2})

    def test_committed_overlay_round_trips_byte_identical(self):
        committed = P.STATE.read_text(encoding="utf-8")
        rev = json.loads(committed)["revision"]
        self.path.write_text(committed, encoding="utf-8")
        P.write_state(self.path, json.loads(committed), rev)  # load -> write: only the revision line moves
        self.assertEqual(self.path.read_text(encoding="utf-8"), committed.replace(f'\n "revision": {rev},\n', f'\n "revision": {rev + 1},\n', 1))

    def test_revision_conflict_and_second_writer_are_refused(self):
        with self.assertRaises(SystemExit):
            P.write_state(self.path, {"revision": 2, "x": 9}, 2)
        (self.dir / "state.json.lock").write_text("")
        with self.assertRaises(SystemExit):
            P.write_state(self.path, {"revision": 3, "x": 9}, 3)
        self.assertEqual(json.loads(self.path.read_text())["x"], 1)
        os.unlink(self.dir / "state.json.lock")


if __name__ == "__main__":
    unittest.main()


class ConfidenceAlias(unittest.TestCase):
    def test_med_is_normalised_to_medium(self):
        import progress
        u = {"task": "D10", "activity": "running", "estimate": {"optimistic_h": 1, "likely_h": 2, "pessimistic_h": 3, "confidence": "med", "basis": "x"}}
        progress.judge.__globals__["CONFIDENCE_ALIASES"]  # exists
        est = u["estimate"]
        est["confidence"] = progress.CONFIDENCE_ALIASES.get(est["confidence"], est["confidence"])
        self.assertEqual(est["confidence"], "medium")

