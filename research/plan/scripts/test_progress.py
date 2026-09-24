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


def hours_ago(h):
    return P.iso(NOW - dt.timedelta(hours=h))


class Base(unittest.TestCase):
    def setUp(self):
        self.manifest, self.state = copy.deepcopy(MANIFEST), copy.deepcopy(STATE)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state["ingested"] = {}

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
        self.assertEqual(self.model().errors, [])


    def test_future_lane_timestamp_is_flagged(self):
        # Oracle for the 2026-09-24 clock slip: a lane stamped 22:30Z while the host read 22:01Z rejects genuine updates as stale.
        self.state["lanes"][0]["updated"] = P.iso(NOW + dt.timedelta(minutes=30))
        self.assertTrue(any("is in the future" in x for x in self.model().warnings))


class Gates(Base):
    def pass_all(self, gate):
        for c in self.state["gates"][gate]["cells"]:
            c["verdict"] = "PASS"

    def test_implemented_e4c_without_decision_is_not_green(self):
        next(t for t in self.manifest["tasks"] if t["id"] == "E4C")["status"] = "implemented"
        self.pass_all("BACKEND-READY")
        m = self.model()
        self.assertFalse(m.gates["BACKEND-READY"]["green"])
        self.assertEqual(m.gates["BACKEND-READY"]["label"], "PENDING")
        self.assertIn("E4C", m.eta["E3A"]["remaining"])  # App path waits for the pending gate
        self.assertNotIn("ACCEPTED", P.render_html(m))
        self.state["gates"]["BACKEND-READY"]["decision"] = "accepted"  # positive control
        self.assertTrue(self.model().gates["BACKEND-READY"]["green"])

    def test_accepted_decision_on_unimplemented_root_is_an_error(self):
        self.pass_all("BACKEND-READY")
        self.state["gates"]["BACKEND-READY"]["decision"] = "accepted"
        m = self.model()
        self.assertFalse(m.gates["BACKEND-READY"]["green"])
        self.assertTrue(any("impossible gate transition: BACKEND-READY" in x for x in m.errors))

    def test_finding_closed_without_evidence_is_an_error(self):
        self.state["findings"][0]["status"] = "fixed"
        self.assertTrue(any("RV-01 is fixed without evidence" in x for x in self.model().errors))
        self.state["findings"][0]["evidence"] = ["research/plan/evidence/e/E2R-5b8e118.md"]
        self.assertEqual(self.model().errors, [])

    def test_gated_app_lane_running_is_an_error(self):
        self.state["lanes"].append({"id": "C0", "task": "C0", "activity": "running"})
        self.assertTrue(any("C0 dispatches only after BACKEND-READY" in x for x in self.model().errors))


class Eta(Base):
    def test_open_input_on_e4c_path_gives_no_finite_eta(self):
        self.estimate_everything()
        self.allocate_gpu()
        self.resolve_inputs()
        self.state["inputs"][0]["status"] = "open"  # P-01 blocks E4C
        f = self.model().eta["E4C"]
        self.assertEqual(f["status"], "blocked")
        self.assertTrue(f["text"].startswith("blocked pending P-01"))
        self.assertNotIn("finish", f)
        self.assertNotRegex(f["text"], r"\d{4}-\d{2}-\d{2}")
        self.state["inputs"][0]["status"] = "resolved"  # positive control: same estimates now forecast a date
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
        f = self.model().eta["E3C"]  # committed baseline: every lane estimate unknown
        self.assertEqual(f["status"], "unknown")
        self.assertNotIn("finish", f)

    def test_resource_and_dependency_constraints_shape_the_forecast(self):
        self.estimate_everything(2.0)
        self.resolve_inputs()
        self.allocate_gpu()
        f = self.model().eta["E3C"]
        # 12 tasks at likely 2 h × 1.3 + 0.5 h merge: the serial chain S3 → (E2C|F2C) → D10 → M5 → … → E3C controls, not total/slots.
        self.assertTrue(f["constraint"].startswith("dependency path S3 → "), f["constraint"])
        self.assertEqual(f["critical_path"][-1], "E3C")
        self.assertGreaterEqual(len(f["critical_path"]), 6)
        self.assertIn("D10", f["critical_path"])
        self.assertGreater(f["wall_h"][1], f["effort_h"][1] / 7)
        self.assertLess(f["effort_h"][0], f["effort_h"][1])
        self.assertLess(f["wall_h"][1], f["wall_h"][2])

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

    def test_impossible_transitions_are_rejected(self):
        self.assertEqual(self.lane("M6")["activity"], "queued")
        out = self.apply(self.write_update("M6-20260924T2150Z.json", {"task": "M6", "activity": "complete"}))
        self.assertIn("impossible transition queued → complete", out[0])
        out = self.apply(self.write_update("D10-20260924T2150Z.json", {"task": "D10", "activity": "complete"}))
        self.assertIn("cannot be complete before start dependencies F2C", out[0])
        out = self.apply(self.write_update("C0-20260924T2150Z.json", {"task": "C0", "activity": "running"}))
        self.assertIn("dispatches only after BACKEND-READY", out[0])
        self.assertFalse(any(x["id"] == "C0" for x in self.state["lanes"]))

    def test_applied_update_moves_queues_and_log(self):
        est = {"optimistic_h": 1, "likely_h": 2, "pessimistic_h": 5, "confidence": "low", "basis": "slice read", "at": "2026-09-24T21:50:00Z"}
        self.apply(self.write_update("E2C-20260924T2150Z.json", {"task": "E2C", "activity": "review", "head": "abc1234", "estimate": est,
                                                                   "evidence": ["research/plan/evidence/e/E2R-5b8e118.md"]}))
        self.assertEqual(self.lane("E2C")["activity"], "review")
        self.assertEqual([x["lane"] for x in self.state["review_queue"]], ["E2C"])
        self.assertIn("estimate likely unknown → 2 h (slice read)", self.state["activity_log"][-1]["what"])
        self.assertEqual(self.apply(*self.dir.glob("*.json")), [])  # idempotent


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
