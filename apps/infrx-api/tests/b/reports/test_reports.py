"""B2 (EVAL-COMPARE): pair two runs over one frozen case universe, score with errors and
missing cases in the denominator, cluster-robust intervals, per-slice verdicts against
predeclared thresholds, unit-separated costs; refuse unsupported improvement claims.

    uv run --frozen pytest -q tests/b/reports/test_reports.py
"""
from __future__ import annotations

import hashlib
import math

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.evaluation import reports

from ..runner.world import NEMO, eval_run, uid

DATASET = f"lab:dataset:{NEMO}:{uid(1, 0xda)}@sha256:" + "1" * 64
HARNESS = f"lab:harness:{NEMO}:{uid(1, 0xa7)}@sha256:" + "2" * 64
UNIVERSE = [uid(i, 0xca) for i in range(1, 41)]
PROTOCOL = {"confidence": 0.95, "margin": 0.05, "min_cases": 10, "metric_source":
            "deterministic_metric", "required_slices": {"safety": {"margin": 0.0,
                                                                   "min_cases": 5}}}


def runs(**candidate):
    base = eval_run(DATASET, HARNESS, run=1)
    return base, {**eval_run(DATASET, HARNESS, run=2),
                  "serving_ref": base["serving_ref"][:-64] + "6" * 64, **candidate}


def cases(scores, *, safety=range(5), cluster=None, **extra):
    """One record per universe case; `scores[i]` is None for not comparable."""
    return [{"case_id": cid, "score": scores[i], "slices": ["safety"] if i in safety else [],
             "cluster": cluster(i) if cluster else cid, "latency_ms": 100 + i,
             "costs": [{"unit": "CREDIT", "value": "1.00000000"}], **extra}
            for i, cid in enumerate(UNIVERSE)]


def compare(base_cases, cand_cases, protocol=PROTOCOL, pair=None, universe=UNIVERSE):
    base, cand = pair or runs()
    return reports.compare(base, base_cases, cand, cand_cases, universe=universe,
                           protocol=protocol)


GOOD = [1.0] * 40
BETTER = [1.0] * 40


def test_b2_incompatible_runs_are_refused_and_factors_are_labelled() -> None:
    """B2.a: another dataset (another case universe) is refused; two differing factors need
    the protocol's multifactor tag; one is labelled single_factor."""
    other = f"lab:dataset:{NEMO}:{uid(2, 0xda)}@sha256:" + "1" * 64
    with pytest.raises(errors.InvalidRequest, match="universe"):
        compare(cases(GOOD), cases(GOOD), pair=runs(dataset_ref=other))
    two = runs(seed=9)
    with pytest.raises(errors.InvalidRequest, match="multifactor"):
        compare(cases(GOOD), cases(GOOD), pair=two)
    tagged = compare(cases(GOOD), cases(GOOD), pair=two,
                     protocol={**PROTOCOL, "multifactor_tag": "serving+seed sweep"})
    assert tagged["pairing"] == {"kind": "multifactor", "factors": ["serving_ref", "seed"],
                                 "tag": "serving+seed sweep"}
    assert compare(cases(GOOD), cases(GOOD))["pairing"] == {
        "kind": "single_factor", "factors": ["serving_ref"], "tag": None}


def test_b2_duplicate_and_foreign_cases_are_refused() -> None:
    """B2.a: exact case ids - a case twice, or one outside the frozen universe, is refused."""
    doubled = cases(GOOD)
    with pytest.raises(errors.InvalidRequest, match="duplicate"):
        compare(cases(GOOD), doubled + doubled[:1])
    foreign = cases(GOOD) + [{**cases(GOOD)[0], "case_id": uid(99, 0xca)}]
    with pytest.raises(errors.InvalidRequest, match="universe"):
        compare(foreign, cases(GOOD))


def test_b2_a_hidden_slice_regression_is_rejected_despite_an_aggregate_win() -> None:
    """EVAL-COMPARE: the candidate fixes 30 non-safety failures and breaks 4 of 5 safety
    cases: the overall interval is a confident improvement, the safety slice is inferior,
    and the decision is reject."""
    base = [1.0] * 5 + [0.0] * 30 + [1.0] * 5
    cand = [1.0, 0.0, 0.0, 0.0, 0.0] + [1.0] * 35
    r = compare(cases(base), cases(cand), protocol={**PROTOCOL, "required_slices": {
        "safety": {"margin": 0.0, "min_cases": 5}}})
    assert r["estimates"]["overall"]["verdict"] == "non_inferior"
    assert r["estimates"]["overall"]["improved"] is True
    assert r["estimates"]["slices"]["safety"]["verdict"] == "inferior"
    assert r["decision"] == {"outcome": "reject", "reasons": ["slice safety inferior"]}
    small = compare(cases(GOOD), cases([0.99] + [1.0] * 39))    # inside the overall margin
    assert small["estimates"]["overall"]["verdict"] == "non_inferior"
    assert small["decision"] == {"outcome": "inconclusive", "reasons": ["slice safety uncertain"]}


def test_b2_missing_and_errored_cases_stay_in_the_denominator() -> None:
    """EVAL-COMPARE: a candidate that drops its failures does not look better - a missing
    case scores 0 in the observed mean, the coverage shows it and the decision is
    inconclusive; an error is a 0 score and a paired case; a not comparable (blocked) case is
    unpaired."""
    base = cases([1.0] * 40)
    cand = cases([1.0] * 40)[:36]                         # four cases never came back
    r = compare(base, cand)
    assert r["observed"]["candidate"]["missing"] == 4
    assert r["observed"]["candidate"]["mean"] == 36 / 40
    assert r["observed"]["paired"] == 36 and r["observed"]["universe"] == 40
    assert r["decision"]["outcome"] == "inconclusive"
    assert r["decision"]["reasons"] == ["coverage 36/40"]
    errored = cases([1.0] * 40)
    errored[7] = {**errored[7], "score": None, "error": "dependency_unavailable"}
    blocked = cases([1.0] * 39 + [None])
    r = compare(blocked, errored)
    assert r["observed"]["candidate"]["errors"] == 1
    assert r["observed"]["candidate"]["mean"] == 39 / 40
    assert r["observed"]["baseline"]["not_comparable"] == 1
    assert r["observed"]["baseline"]["mean"] == 39 / 40
    assert r["observed"]["paired"] == 39
    assert r["estimates"]["overall"]["diff"] == pytest.approx(-1 / 39)


def test_b2_small_samples_and_wide_intervals_are_inconclusive() -> None:
    """EVAL-COMPARE: fewer paired cases than the protocol's minimum is insufficient; an
    interval that crosses the margin is uncertain; neither is a win."""
    few = UNIVERSE[:8]
    r = compare(cases(GOOD)[:8], cases(GOOD)[:8], universe=few,
                protocol={**PROTOCOL, "required_slices": {}})
    assert r["estimates"]["overall"]["verdict"] == "insufficient"
    assert r["decision"] == {"outcome": "inconclusive", "reasons": ["overall insufficient"]}
    base = [1.0, 0.0] * 20
    cand = [0.0, 1.0] * 19 + [1.0, 1.0]
    r = compare(cases(base), cases(cand), protocol={**PROTOCOL, "required_slices": {}})
    overall = r["estimates"]["overall"]
    assert overall["low"] < -0.05 < overall["high"] and overall["verdict"] == "uncertain"
    assert overall["improved"] is False
    assert r["decision"] == {"outcome": "inconclusive", "reasons": ["overall uncertain"]}


def test_b2_skewed_source_clusters_widen_the_interval() -> None:
    """EVAL-COMPARE: the same per-case gains are a confident win when every case is its own
    source, and uncertain when they come from a few correlated sources (clustered SE,
    t with clusters - 1 degrees of freedom)."""
    base = [0.0] * 40
    cand = [1.0] * 12 + [0.0] * 28
    loose = compare(cases(base), cases(cand), protocol={**PROTOCOL, "required_slices": {}})
    assert loose["estimates"]["overall"]["improved"] is True
    tight = compare(cases(base, cluster=lambda i: f"s{i // 10}"),
                    cases(cand, cluster=lambda i: f"s{i // 10}"),
                    protocol={**PROTOCOL, "required_slices": {}})
    assert tight["estimates"]["overall"]["clusters"] == 4
    assert tight["estimates"]["overall"]["improved"] is False
    se = math.sqrt(4 / 3 * (7**2 + 1**2 + 3**2 + 3**2) / 40**2)     # cluster sums of d - 0.3
    assert tight["estimates"]["overall"]["low"] == pytest.approx(
        0.3 - reports.t_quantile(0.975, 3) * se)
    narrow = compare(cases(base), cases(cand), protocol={**PROTOCOL, "confidence": 0.8,
                                                         "required_slices": {}})
    width = [e["estimates"]["overall"]["high"] - e["estimates"]["overall"]["low"]
             for e in (loose, narrow)]
    assert width[1] < width[0]
    assert tight["estimates"]["overall"]["high"] - tight["estimates"]["overall"]["low"] > \
        2 * (loose["estimates"]["overall"]["high"] - loose["estimates"]["overall"]["low"])
    one = compare(cases(base, cluster=lambda i: "s"), cases(cand, cluster=lambda i: "s"),
                  protocol={**PROTOCOL, "required_slices": {}})
    assert one["estimates"]["overall"]["verdict"] == "insufficient"


def test_b2_an_equivalent_candidate_is_accepted_with_its_evidence() -> None:
    """B2.c: a non-inferior candidate on every required slice is accepted; the report binds
    the protocol, both runs and the universe by digest and is itself digested."""
    r = compare(cases(GOOD), cases(BETTER))
    assert r["decision"] == {"outcome": "accept", "reasons": []}
    assert r["estimates"]["overall"]["verdict"] == "non_inferior"
    assert r["estimates"]["basis"] == "deterministic_metric"
    digest = lambda v: "sha256:" + hashlib.sha256(records.canonical(v)).hexdigest()  # noqa: E731
    assert r["protocol"] == PROTOCOL and r["protocol_digest"] == digest(PROTOCOL)
    assert r["baseline_run"] != r["candidate_run"]
    assert r["universe_digest"] == digest(sorted(UNIVERSE))
    assert compare(cases(GOOD), cases(BETTER))["report_digest"] == r["report_digest"]
    assert compare(cases(GOOD), cases(BETTER), protocol={**PROTOCOL, "margin": 0.1}
                   )["report_digest"] != r["report_digest"]
    near = compare(cases(GOOD), cases([1.0] * 39 + [0.9]))       # a loss inside the margin
    assert near["decision"]["outcome"] == "accept"
    assert near["estimates"]["overall"]["improved"] is False
    teacher = compare(cases(GOOD), cases(GOOD),
                      protocol={**PROTOCOL, "metric_source": "teacher_judgment"})
    assert teacher["estimates"]["basis"] == "teacher_judgment"


def test_b2_thresholds_are_workload_inputs() -> None:
    """No platform default: every threshold is declared, with a sane range."""
    for key in ("confidence", "margin", "min_cases", "metric_source", "required_slices"):
        with pytest.raises(errors.InvalidRequest, match=key):
            compare(cases(GOOD), cases(GOOD),
                    protocol={k: v for k, v in PROTOCOL.items() if k != key})
    for bad in ({"confidence": 1.0}, {"margin": -0.1}, {"min_cases": 1}):
        with pytest.raises(errors.InvalidRequest):
            compare(cases(GOOD), cases(GOOD), protocol={**PROTOCOL, **bad})


def test_b2_costs_are_per_unit_and_a_foreign_unit_is_refused() -> None:
    """CREDIT and PROVIDER_USD are totalled apart and compared per unit, never converted; a
    legacy USD (or unitless) amount is refused."""
    judged = cases(GOOD, costs=[{"unit": "CREDIT", "value": "2.00000000"},
                                {"unit": "PROVIDER_USD", "value": "0.01000000"}])
    r = compare(cases(GOOD), judged)
    assert r["observed"]["baseline"]["costs"] == {"CREDIT": "40.00000000"}
    assert r["observed"]["candidate"]["costs"] == {"CREDIT": "80.00000000",
                                                   "PROVIDER_USD": "0.40000000"}
    assert r["observed"]["cost_delta"] == {"CREDIT": "40.00000000"}
    for amount in ({"unit": "USD", "value": "1.00000000"}, {"value": "1.00000000"}):
        with pytest.raises(errors.InvalidRequest, match="unit"):
            compare(cases(GOOD), cases(GOOD, costs=[amount]))


def test_b2_latency_is_a_nearest_rank_distribution() -> None:
    r = compare(cases(GOOD), cases(GOOD))
    assert r["observed"]["baseline"]["latency_ms"] == {"n": 40, "p50": 119, "p90": 135,
                                                       "p99": 139, "max": 139}
    none = compare(cases(GOOD, latency_ms=None), cases(GOOD))
    assert none["observed"]["baseline"]["latency_ms"] == {"n": 0}


def test_b2_the_t_quantile_is_close_to_students() -> None:
    """The stdlib has no Student t: a Cornish-Fisher expansion of the normal quantile."""
    assert reports.t_quantile(0.975, 1) == pytest.approx(12.706, abs=0.001)
    assert reports.t_quantile(0.975, 2) == pytest.approx(4.303, abs=0.001)
    assert reports.t_quantile(0.975, 3) == pytest.approx(3.182, abs=0.03)
    assert reports.t_quantile(0.975, 9) == pytest.approx(2.262, abs=0.005)
    assert reports.t_quantile(0.975, 10_000) == pytest.approx(1.960, abs=0.001)


def test_b2_case_records_are_built_from_d7_rows_and_the_manifest() -> None:
    """E6L-O2 (WR-B-8): one function turns D7's `run_results` read into B2's records - a
    result only under the run's own evaluator, a failed case without one an error, a pending
    case missing; every attempt's cost; the sample's group key as its cluster; the slices at
    the declared path (a name or a list), none without one."""
    import asyncio
    import json
    from types import SimpleNamespace as NS

    from infrx.datasets.imports import sample_key
    from infrx.media.store import InMemoryObjectStore
    ev = f"lab:evaluator:{NEMO}:{uid(1, 0xe0)}@sha256:" + "3" * 64
    other = ev[:-64] + "4" * 64
    ids = [uid(i, 0xcb) for i in range(1, 5)]
    objects = InMemoryObjectStore()
    slices = ["math", ["a", "b"], None, "x"]
    for i, cid in enumerate(ids):
        original = {} if slices[i] is None else {"slice": slices[i]}
        asyncio.run(objects.put_if_absent(sample_key(NEMO, f"sha256:{i}"),
                                          json.dumps({"original": original}).encode(),
                                          "application/json"))
    frozen = NS(run=NS(provider_org_id=NEMO, evaluator_ref=ev), cases=tuple(ids),
                manifest=NS(samples=[NS(sample_id=cid, group_key=f"g{i % 2}",
                                        content_digest=f"sha256:{i}")
                                     for i, cid in enumerate(ids)]))
    cost = {"unit": "CREDIT", "value": "0.50000000"}
    body = json.dumps({"score": 1.0, "latency_ms": 12})
    rows = {"case_states": [{"case_id": ids[0], "state": "done"},
                            {"case_id": ids[1], "state": "failed"},
                            {"case_id": ids[2], "state": "pending"},
                            {"case_id": ids[3], "state": "done"}],
            "attempt_rows": [{"case_id": ids[0], "cost": cost}, {"case_id": ids[0], "cost": cost},
                             {"case_id": ids[1], "cost": cost}, {"case_id": ids[2], "cost": None},
                             {"case_id": ids[3], "cost": None}],   # a done case's null cost
            "results": [{"case_id": ids[0], "evaluator_ref": ev, "body": body},
                        {"case_id": ids[1], "evaluator_ref": other, "body": body},
                        {"case_id": ids[3], "evaluator_ref": ev,
                         "body": json.dumps({"score": None, "latency_ms": 7})}]}
    got = asyncio.run(reports.case_records(frozen, rows, objects,
                                           slice_path="sample.original.slice"))
    assert got == [
        {"case_id": ids[0], "cluster": "g0", "costs": [cost, cost], "slices": ["math"],
         "score": 1.0, "latency_ms": 12},
        {"case_id": ids[1], "cluster": "g1", "costs": [cost], "slices": ["a", "b"],
         "score": None, "error": "failed"},
        {"case_id": ids[3], "cluster": "g1", "costs": [], "slices": ["x"], "score": None,
         "latency_ms": 7}]
    bare = asyncio.run(reports.case_records(frozen, rows, objects))
    assert [r["slices"] for r in bare] == [[], [], []]
