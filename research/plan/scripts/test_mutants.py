"""Runner-visible mutants for the tracker's post-launch waves (WR-LW0-3): every decision is killed by a named Waves case.

    cd research/plan/scripts && python3 -m unittest test_mutants -v

Each mutant is one textual edit of progress.py, executed as the `progress` module the Waves cases see; it must make at
least one named case fail. The list is checked for anchors that exist exactly once and for a mutant naming every case.
"""
import types
import unittest
from pathlib import Path

import test_progress as T

SRC = Path(T.P.__file__).read_text()
CASES = [n for n in dir(T.Waves) if n.startswith("test_")]
MAP, ERR, GATE, ETA, OUT = CASES[2], CASES[0], CASES[1], CASES[4], CASES[3]  # sorted: missing…, section…, map…, wave_eta…, wave_exit…
assert (MAP, ERR, GATE, ETA, OUT) == ("test_the_map_places_every_deferred_task_in_exactly_one_wave",
                                      "test_a_task_missing_repeated_unknown_or_in_launch_scope_is_an_error",
                                      "test_a_wave_exit_is_met_only_with_its_tasks_implemented_and_its_gate_lane_complete",
                                      "test_wave_eta_uses_the_live_lanes_and_eta_params",
                                      "test_the_section_renders_and_everything_else_is_unchanged"), CASES

# (name, old, new, cases that must kill it)
MUTANTS = [
    ("loader_accepts_an_empty_map", 'raise ValueError(f"{path}: no wave rows")', "pass", (MAP,)),
    ("loader_keeps_notes_as_ids", 're.sub(r"\\([^)]*\\)", "", ids)', "ids", (MAP,)),
    ("loader_ignores_italics", '"gating": not star', '"gating": True', (MAP, GATE)),
    ("loader_reads_no_gate_lanes", '"gate_lanes": [{"task": t or None, "lane": n} for', '"gate_lanes": [] and [{"task": t, "lane": n} for', (MAP, GATE)),
    ("missing_task_not_flagged", 'err(f"wave map: deferred task {i} is in no wave")', "pass", (ERR,)),
    ("map_check_runs_without_a_map", ' if self.waves else []:', ":", (OUT,)),
    ("duplicate_not_flagged", "if listed.count(i) > 1}", "if listed.count(i) > 2}", (ERR,)),
    ("launch_scope_task_allowed", 'if self.cat[i] not in ("deferred", "activated")}', 'if self.cat[i] == "superseded"}', (ERR,)),
    ("unknown_id_allowed", 'if i.split("-")[0] not in ids:', "if False:", (ERR,)),
    ("slice_is_an_unknown_id", 'if i.split("-")[0] not in ids:', "if i not in ids:", (ERR,)),
    ("foreign_gate_task_allowed", 'if g["task"] and g["task"] not in mine:', "if False:", (ERR,)),
    ("slices_counted", 'ids = [i for x in w["lanes"] for i in x["ids"] if i in M.tasks]', 'ids = [i for x in w["lanes"] for i in x["ids"] if i[:1] != "?"]', (MAP,)),
    ("discovery_gates_its_wave", 'gating = [i for x in w["lanes"] if x["gating"] for i', 'gating = [i for x in w["lanes"] for i', (GATE,)),
    ("wave_lane_by_id_missed", 'if x["id"] == w["id"] or x.get("task") in ids or', 'if x.get("task") in ids or', (GATE,)),
    ("task_keyed_lane_missed", 'x["id"] == w["id"] or x.get("task") in ids or re.search', 'x["id"] == w["id"] or re.search', (GATE,)),
    ("wave_lane_by_slice_missed", ' or re.search(rf"\\(wave5 {w[\'id\']}\\b", str(x.get("slice") or ""))]', "]", (GATE, ETA)),
    ("gate_lane_in_review_counts", 'if not xs or any(x["activity"] != "complete" for x in xs):', "if not xs:", (GATE,)),
    ("similar_lane_name_counts", "re.escape(g['lane'])}(?![\\w-])", "re.escape(g['lane'])}", (GATE,)),
    ("similar_map_lane_name_serves", "re.escape(x['name'])}(?![\\w-])", "re.escape(x['name'])}", (ETA,)),
    ("exit_always_met", '"state": "PENDING" if why else "EXIT MET"}', '"state": "EXIT MET"}', (GATE,)),
    ("met_wave_still_forecast", 'if v["state"] == "EXIT MET":', "if False:", (GATE,)),
    ("finished_lane_is_live", 'x["activity"] not in ("complete", "deferred")]\n    need', 'x["activity"] != "deferred"]\n    need', (ETA,)),
    ("open_input_ignored", 'if inputs:\n        return {"status": "blocked"', 'if False:\n        return {"status": "blocked"', (ETA,)),
    ("no_rework_allowance", '* (1 + rework) + integ for x in live}', "+ integ for x in live}", (ETA,)),
    ("no_merge_per_lane", '* (1 + rework) + integ for x in live}', "* (1 + rework) for x in live}", (ETA,)),
    ("serial_queue_dropped", "(len(live) * integ, f\"serial", "(0, f\"serial", (ETA,)),
    ("capacity_dropped", '(eff / cap, f"capacity: {eff:.1f} effort-h over {cap} implementation slots")]', '(0, "capacity")]', (ETA,)),
    ("optimistic_confidence", 'conf = min(((x.get("estimate")', 'conf = max(((x.get("estimate")', (ETA,)),
    ("html_nav_always", "'<a href=\"#waves\">Post-launch waves</a>' if M.waves else ''", "'<a href=\"#waves\">Post-launch waves</a>'", (OUT,)),
    ("html_section_always", "if M.waves:  # 5b. post-launch waves", "if True:  # 5b. post-launch waves", (OUT,)),
    ("md_section_missing", '    if M.waves:\n        L += ["## Post-launch waves"', '    if False:\n        L += ["## Post-launch waves"', (OUT,)),
    ("activated_task_not_flagged", 'if c in ("deferred", "activated") and i not in listed', 'if c == "deferred" and i not in listed', (ERR,)),
    ("open_lane_coverage_ignored", "    if uncovered:\n", "    if False:\n", (ETA,)),
    ("finished_work_lane_is_counted", "    if need:  # a lane for finished work", "    if False:  # a lane for finished work", (ETA,)),
    ("coverage_by_any_task_of_the_lane", 'y.get("task") in set(x["ids"]) & set(v["open"])', 'y.get("task") in x["ids"]', (ETA,)),
    ("slice_chip_unmarked", """'<span class="pill">slice</span>'""", """''""", (OUT,)),
]


def survives(src, cases):
    """True when every named case passes with `src` as the progress module (i.e. the mutant is not killed)."""
    mod = types.ModuleType("progress")
    mod.__file__ = T.P.__file__
    exec(compile(src, T.P.__file__, "exec"), mod.__dict__)
    real, T.P = T.P, mod
    try:
        result = unittest.TestResult()
        unittest.TestSuite(T.Waves(c) for c in cases).run(result)
        return result.wasSuccessful()
    finally:
        T.P = real


class Mutants(unittest.TestCase):
    def test_the_list_is_well_formed(self):
        self.assertEqual(len({m[0] for m in MUTANTS}), len(MUTANTS), "duplicate mutant names")
        for name, old, new, cases in MUTANTS:
            self.assertEqual(SRC.count(old), 1, f"{name}: anchor must occur exactly once")
            self.assertTrue(cases and set(cases) <= set(CASES) and old != new, name)

    def test_every_waves_case_is_named_by_a_mutant(self):
        self.assertEqual(set(CASES) - {c for m in MUTANTS for c in m[3]}, set())

    def test_the_unmutated_tracker_passes_every_case(self):  # a runner that fails everything would "kill" every mutant
        self.assertTrue(survives(SRC, CASES))

    def test_every_mutant_is_killed(self):
        for name, old, new, cases in MUTANTS:
            with self.subTest(mutant=name):
                self.assertFalse(survives(SRC.replace(old, new, 1), cases), f"{name} survived {cases}")


if __name__ == "__main__":
    unittest.main()
