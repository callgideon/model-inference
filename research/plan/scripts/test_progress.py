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
        s.update(ingested={}, review_queue=[], integration_queue=[], agent_slots={"total": 14, "reserved": 2},
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
        s = {x["cat"]: len(x["ids"]) for x in P.summaries(self.model())}
        self.assertEqual((s["backend"], s["app"], s["baseline"], s["superseded"]), (14, 12, 44, 6))
        self.assertEqual(sum(s.values()), len(self.manifest["tasks"]))

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
        self.state["lanes"].append({"id": "C0", "task": "C0", "activity": "running"})
        self.assertTrue(any("C0 dispatches only after BACKEND-READY" in x for x in self.model().errors))


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
        out = self.apply(self.write_update("C0-20260924T2150Z.json", {"task": "C0", "activity": "running"}))
        self.assertIn("dispatches only after BACKEND-READY", out[0])
        self.assertFalse(any(x["id"] == "C0" for x in self.state["lanes"]))
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
