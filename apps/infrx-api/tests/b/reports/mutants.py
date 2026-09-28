#!/usr/bin/env python3
"""R32/R40/R83 for B2: one single-edit defect per decision `test_reports.py` claims, through
the shared runner with `require_every_case`.

    uv run --frozen pytest -q tests/b/reports/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/b/reports/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITE = "tests/b/reports/test_reports.py"
P = "evaluation/reports/__init__.py"

PAIR = "test_b2_incompatible_runs_are_refused_and_factors_are_labelled"
DUPS = "test_b2_duplicate_and_foreign_cases_are_refused"
SLICE = "test_b2_a_hidden_slice_regression_is_rejected_despite_an_aggregate_win"
MISSING = "test_b2_missing_and_errored_cases_stay_in_the_denominator"
SMALL = "test_b2_small_samples_and_wide_intervals_are_inconclusive"
CLUSTER = "test_b2_skewed_source_clusters_widen_the_interval"
ACCEPT = "test_b2_an_equivalent_candidate_is_accepted_with_its_evidence"
THRESH = "test_b2_thresholds_are_workload_inputs"
COSTS = "test_b2_costs_are_per_unit_and_a_foreign_unit_is_refused"
LAT = "test_b2_latency_is_a_nearest_rank_distribution"
TQ = "test_b2_the_t_quantile_is_close_to_students"
ROWS = "test_b2_case_records_are_built_from_d7_rows_and_the_manifest"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- B2.a pairing
    m("b2_multifactor_untagged", "two differing factors need the protocol's tag",
      "multifactor_tag=rules.multifactor_tag)", 'multifactor_tag="x")', PAIR),
    m("b2_tag_unreported", "the multifactor tag is reported",
      '"tag": rules.multifactor_tag}', '"tag": None}', PAIR),
    m("b2_duplicates_kept", "a case twice in one run is refused",
      "        if cid in out:\n", "        if False:\n", DUPS),
    m("b2_foreign_kept", "a case outside the frozen universe is refused",
      "        if cid not in universe:\n", "        if False:\n", DUPS),
    # --- B2.b scoring: the denominator
    m("b2_error_unscored", "an error is a 0 score, paired",
      'return 0.0 if record.get("error") else record.get("score")',
      'return record.get("score")', MISSING),
    m("b2_missing_dropped_from_mean", "the observed mean is over the whole universe",
      '"mean": math.fsum(v or 0.0 for v in values) / len(cases),',
      '"mean": math.fsum(v or 0.0 for v in values) / max(1, sum(v is not None for v in values)),',
      MISSING),
    m("b2_missing_uncounted", "missing cases are counted",
      '"missing": len(cases) - len(recs),', '"missing": 0,', MISSING),
    m("b2_errors_uncounted", "errors are counted",
      '"errors": sum(bool(r.get("error")) for r in recs.values()),', '"errors": 0,', MISSING),
    m("b2_not_comparable_uncounted", "not comparable cases are counted",
      '"not_comparable": sum(_value(r) is None for r in recs.values()),',
      '"not_comparable": 0,', MISSING),
    m("b2_coverage_ignored", "incomplete coverage is inconclusive",
      "    if len(pairs) < len(cases):\n", "    if False:\n", MISSING),
    m("b2_unscored_paired", "only cases scored in both runs pair",
      "if p[1] is not None and p[2] is not None}", "if p[2] is not None}", MISSING,
      dies_by=("TypeError",)),
    # --- B2.b the interval
    m("b2_min_cases_ignored", "fewer cases than the minimum is insufficient",
      "    if n < min_cases or g < 2:\n", "    if g < 2:\n", SMALL),
    m("b2_one_cluster_estimated", "one cluster gives no interval",
      "    if n < min_cases or g < 2:\n", "    if n < min_cases:\n", CLUSTER,
      dies_by=("ZeroDivisionError",)),
    m("b2_clusters_ignored", "cases of one source are one cluster",
      'runs["baseline"][cid].get("cluster") or cid,', "cid,", CLUSTER),
    m("b2_normal_not_t", "the critical value is Student t on clusters - 1",
      "half = t_quantile((1 + confidence) / 2, g - 1) * se",
      "half = NormalDist().inv_cdf((1 + confidence) / 2) * se", CLUSTER),
    m("b2_no_small_cluster_correction", "the clustered variance has the g/(g-1) factor",
      "se = math.sqrt(g / (g - 1) * math.fsum(", "se = math.sqrt(1 * math.fsum(", CLUSTER),
    m("b2_confidence_ignored", "the protocol's confidence sets the interval",
      "t_quantile((1 + confidence) / 2, g - 1)", "t_quantile(0.975, g - 1)", CLUSTER),
    m("b2_t_df1_normal", "df 1 is the exact Cauchy quantile",
      "    if df == 1:\n", "    if False:\n", TQ),
    m("b2_t_df2_normal", "df 2 is exact",
      "    if df == 2:\n", "    if False:\n", TQ),
    m("b2_t_term_1", "the t expansion's first term",
      "(z**3 + z) / (4 * df)", "(z**3 + z) / (2 * df)", TQ),
    m("b2_t_term_3", "the t expansion's third term",
      "+ (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3))", ")", TQ),
    # --- B2.c verdicts and decision
    m("b2_ni_strict", "the lower bound AT -margin is non-inferior",
      '"non_inferior" if low >= -margin', '"non_inferior" if low > -margin', ACCEPT),
    m("b2_margin_ignored", "the declared margin is used",
      '"non_inferior" if low >= -margin', '"non_inferior" if low >= 0', ACCEPT),
    m("b2_inferior_unseen", "an interval below -margin is inferior",
      '"inferior" if high < -margin else "uncertain"', '"uncertain"', SLICE),
    m("b2_improved_loose", "an improvement needs the lower bound above 0",
      '"improved": low > 0}', '"improved": mean > 0}', SMALL),
    m("b2_slice_margin_overall", "a slice uses its own margin",
      "rule.margin, rule.min_cases, rules.confidence)",
      "rules.margin, rule.min_cases, rules.confidence)", SLICE),
    m("b2_slice_min_overall", "a slice uses its own minimum",
      "rule.margin, rule.min_cases, rules.confidence)",
      "rule.margin, rules.min_cases, rules.confidence)", SLICE),
    m("b2_slices_unfiltered", "a slice is its own cases",
      'if name in runs["baseline"][cid].get("slices", [])],', "],", SLICE),
    m("b2_reject_hidden", "an inferior slice rejects",
      '{"outcome": "reject", "reasons": inferior} if inferior else',
      '{"outcome": "reject", "reasons": inferior} if False else', SLICE),
    m("b2_unsure_accepted", "an uncertain or insufficient verdict is inconclusive",
      '{"outcome": "inconclusive", "reasons": unsure} if unsure else',
      '{"outcome": "inconclusive", "reasons": unsure} if False else', SMALL),
    m("b2_uncertain_accepted", "uncertain is not accepted",
      'if v in ("uncertain", "insufficient")]', 'if v in ("insufficient",)]', SMALL),
    m("b2_insufficient_accepted", "insufficient is not accepted",
      'if v in ("uncertain", "insufficient")]', 'if v in ("uncertain",)]', SMALL),
    # --- B2.c evidence
    m("b2_protocol_unbound", "the protocol is bound by digest",
      '"protocol_digest": _digest(protocol),', '"protocol_digest": _digest({}),', ACCEPT),
    m("b2_universe_unbound", "the universe is bound by digest",
      '"universe_digest": _digest(sorted(cases)),', '"universe_digest": _digest([]),', ACCEPT),
    m("b2_candidate_unbound", "the candidate run is bound",
      '"candidate_run": lab.ref_of(candidate_run)', '"candidate_run": lab.ref_of(baseline_run)',
      ACCEPT),
    m("b2_report_digest_partial", "the report digest covers the whole report",
      '"report_digest": _digest(report)', '"report_digest": _digest(report["decision"])',
      ACCEPT),
    m("b2_basis_fixed", "the metric's basis is the protocol's",
      '"basis": rules.metric_source', '"basis": "deterministic_metric"', ACCEPT),
    m("b2_default_confidence", "no default confidence",
      "    confidence: float = Field(gt=0.5, lt=1)\n",
      "    confidence: float = Field(gt=0.5, lt=1, default=0.95)\n", THRESH),
    m("b2_certain_confidence", "a confidence is below 1",
      "Field(gt=0.5, lt=1)", "Field(gt=0.5, le=1)", THRESH, dies_by=("StatisticsError",)),
    m("b2_default_margin", "no default margin",
      "    margin: float = Field(ge=0)\n    min_cases: int = Field(ge=2)\n    metric_source",
      "    margin: float = Field(ge=0, default=0.0)\n    min_cases: int = Field(ge=2)\n"
      "    metric_source", THRESH),
    m("b2_negative_margin", "a margin is not negative",
      "    margin: float = Field(ge=0)\n    min_cases: int = Field(ge=2)\n    metric_source",
      "    margin: float\n    min_cases: int = Field(ge=2)\n    metric_source", THRESH),
    m("b2_default_min_cases", "no default minimum",
      "    min_cases: int = Field(ge=2)\n    metric_source",
      "    min_cases: int = Field(ge=2, default=2)\n    metric_source", THRESH),
    m("b2_one_case_minimum", "a minimum below 2 is refused",
      "    min_cases: int = Field(ge=2)\n    metric_source",
      "    min_cases: int = Field(ge=1)\n    metric_source", THRESH),
    m("b2_default_source", "no default metric basis",
      'metric_source: Literal["deterministic_metric", "teacher_judgment"]\n',
      'metric_source: Literal["deterministic_metric", "teacher_judgment"] = '
      '"deterministic_metric"\n', THRESH),
    m("b2_default_slices", "required slices are declared, even as none",
      "    required_slices: dict[str, SliceRule]\n",
      "    required_slices: dict[str, SliceRule] = {}\n", THRESH),
    # --- costs and latency
    m("b2_costs_not_validated", "a cost is a Lab unit-tagged amount",
      "money = lab.Amount.model_validate(amount)", "money = lab.Amount.model_construct(**amount)",
      COSTS),
    m("b2_cost_units_merged", "units are totalled apart (a merge is a typed refusal)",
      "totals[money.unit] = totals[money.unit] + money.amount if money.unit in totals",
      'totals["CREDIT"] = totals["CREDIT"] + money.amount if "CREDIT" in totals', COSTS,
      dies_by=("TypeError",)),
    m("b2_cost_delta_any_unit", "a delta only for a unit both runs paid in",
      "for u in base_costs if u in cand_costs}", "for u in cand_costs}", COSTS,
      dies_by=("KeyError",)),
    m("b2_cost_delta_sign", "the delta is candidate - baseline",
      "str(cand_costs[u] - base_costs[u])", "str(base_costs[u] - cand_costs[u])", COSTS),
    m("b2_latency_rank", "nearest-rank percentiles",
      "seen[math.ceil(p / 100 * len(seen)) - 1]", "seen[int(p / 100 * len(seen))]", LAT),
    m("b2_latency_empty", "no latency is n=0",
      "    if not seen:\n", "    if False:\n", LAT, dies_by=("IndexError",)),
    # B2.d case records from D7 rows (E6L-O2)
    m("b2r_any_evaluators_result", "a result counts only under the run's own evaluator",
      '               if r["evaluator_ref"] == run.evaluator_ref}', "               if True}", ROWS),
    m("b2r_last_cost_only", "every attempt's cost is kept",
      'costs.setdefault(attempt["case_id"], []).append(attempt["cost"])',
      'costs[attempt["case_id"]] = [attempt["cost"]]', ROWS),
    m("b2r_null_cost_kept", "an attempt without a cost adds none (0-LSQI-2)",
      '        if attempt["cost"]:\n', "        if True:\n", ROWS),
    m("b2r_failed_case_missing", "a failed case without a result is an error, not missing",
      '        elif states.get(cid) == "failed":', "        elif False:", ROWS),
    m("b2r_pending_case_is_an_error", "a case neither scored nor failed is missing",
      '        elif states.get(cid) == "failed":', "        elif True:", ROWS),
    m("b2r_cluster_is_the_case", "the source cluster is the sample's group key",
      '"cluster": sample.group_key,', '"cluster": cid,', ROWS),
    m("b2r_slice_name_split", "a slice name is one slice, not its letters",
      "[found] if isinstance(found, str)", "list(found) if isinstance(found, str)", ROWS),
    m("b2r_unsliced_case_in_a_slice", "a case with no slice value (or no path) is in none",
      '"slices": [] if found is _MISSING', '"slices": [""] if found is _MISSING', ROWS),
    m("b2r_latency_dropped", "a scored case keeps its latency",
      '"latency_ms": results[cid]["latency_ms"]})', '"latency_ms": None})', ROWS),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="b2", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the B2 reports mutation list"))
