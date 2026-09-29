"""E6L j05-j07, j11: the evaluation half of the journey on the e6l stack - B1 runs on the real
D7 store through `HttpDevEndpoint` against synthetic endpoints, B2's comparison and decision.

The imported text benchmark (j01's) is the frozen case universe: 14 `lookup` and 6 `math`
questions, one episode each. `fixtures/benchmark.json` declares which questions each
endpoint gets right - baseline: the 6 math + L01-L04 (10/20); regressing: the 14 lookup, no
math (14/20); improving: all 20; missing: improving minus M01, which it refuses (a 400).
The expectations below are that declaration's arithmetic, not the code's output.
"""
from __future__ import annotations

import asyncio
import threading

import lab_world as lw
import psycopg
import pytest
from lab_world import run

PROTOCOL = {"confidence": 0.95, "margin": 0.05, "min_cases": 10,
            "metric_source": "deterministic_metric",
            "required_slices": {"math": {"margin": 0.0, "min_cases": 5}}}


def text(lab) -> str:
    return lab.dataset("text", 1).dataset_ref


def harness(lab) -> str:
    return lab.harness_ref(1)


def ids(lab) -> dict[str, str]:
    """fixture question id -> sample (case) id."""
    return {k: v["sample_id"] for k, v in lab.rows(text(lab)).items()}


def completed(lab, name: str, n: int):
    """Endpoint `name`'s full run over the benchmark, once per session: (frozen, report,
    wallet)."""
    if name not in lab.runs:
        frozen = lab.freeze(lab.run_payload(n, text(lab), harness(lab), lab.serving(name)))
        with lw.endpoint(name) as (wallet, http):
            report = run(lab.runner(http).run(frozen))
        lab.runs[name] = (frozen, report, wallet)
    return lab.runs[name]


def compare(lab, baseline: str, candidate: str, workdir, n: tuple[int, int]):
    from infrx.evaluation import reports
    base, _, _ = completed(lab, baseline, n[0])
    cand, _, _ = completed(lab, candidate, n[1])
    report = reports.compare(base.run.model_dump(by_alias=True, exclude_unset=True),
                             lab.case_records(base),
                             cand.run.model_dump(by_alias=True, exclude_unset=True),
                             lab.case_records(cand), universe=list(base.cases),
                             protocol=PROTOCOL)
    lw.save(workdir, f"report-{baseline}-{candidate}.json", report)
    return report


# ------------------------------------------------------------------------------------ j05
class Dying:
    """The worker dies after the endpoint charged, before the answer reached it."""

    def __init__(self, http) -> None:
        self.http = http

    async def complete(self, **request):
        await self.http.complete(**request)
        raise KillWorker("killed after the charge")


class KillWorker(BaseException):
    """A worker process dying mid-attempt."""


def test_j05_a_worker_killed_mid_attempt_is_recovered_and_each_case_scored_once(lab):
    """Killed after the endpoint charged: the lease expires on the DB clock, `lab_recover`
    puts the case back, attempt 2 finishes it; every case is scored once, the killed attempt
    is `expired` with no cost recorded, and the wallet shows both debits (bounded, visible)."""
    from infrx.evaluation.runner import Limits
    frozen = lab.freeze(lab.run_payload(51, text(lab), harness(lab), lab.serving("baseline"),
                                        max_cases=4))
    with lw.endpoint("baseline") as (wallet, http):
        with pytest.raises(KillWorker):
            run(lab.runner(Dying(http), limits=Limits(30, 3, 2, 1)).run(frozen))
        lab.advance(31)
        assert run(lab.store.recover()) >= 1
        report = run(lab.runner(http, worker="w2").run(frozen))
    assert report["state"] == "succeeded" and report["cases"] == {"done": 4}
    attempts = lab.sql("select case_id::text, attempt, state, cost_value from "
                       "infrx.lab_eval_attempts where run_id = %s order by case_id, attempt",
                       frozen.run.run_id)
    first = frozen.cases[0]
    assert [(a[1], a[2], a[3]) for a in attempts if a[0] == first][0] == (1, "expired", None)
    assert sorted(lab.results(frozen.run.run_id)) == sorted(frozen.cases)
    assert len(wallet.replies) == 5, "four cases plus the killed attempt's charge"
    assert report["costs"]["CREDIT"] != str(wallet.debited)


def test_j05_duplicate_delivery_scores_each_case_once(lab):
    """Two workers delivered the same run at once, then a third: one result per case, one
    paid call per case, the run's recorded cost equals the wallet's debit."""
    frozen = lab.freeze(lab.run_payload(52, text(lab), harness(lab), lab.serving("baseline"),
                                        max_cases=6))

    async def both(http):
        return await asyncio.gather(lab.runner(http, worker="a").run(frozen),
                                    lab.runner(http, worker="b").run(frozen))
    with lw.endpoint("baseline") as (wallet, http):
        run(both(http))
        report = run(lab.runner(http, worker="c").run(frozen))
    assert report["state"] == "succeeded" and report["cases"] == {"done": 6}
    assert sorted(lab.results(frozen.run.run_id)) == sorted(frozen.cases)
    assert len(wallet.calls) == 6 and len({c["key"] for c in wallet.calls}) == 6
    assert report["costs"] == {"CREDIT": str(wallet.debited)}


def cancel(lab, frozen) -> None:
    """A durable cancel from another session (the provider's), committed on return."""
    with psycopg.connect(lab.dsn, autocommit=True) as other:
        lab.l2.call(other, "lab_cancel_run", {"provider_org_id": lab.NEMO,
                                              "run_id": frozen.run.run_id})


class Gated:
    """The object store as the runner sees it: the second case fetched - after its lease,
    before its model call - waits until the cancel has committed."""

    def __init__(self, objects) -> None:
        self.objects, self.fetched = objects, 0
        self.leased, self.cancelled = threading.Event(), threading.Event()

    async def get(self, key):
        self.fetched += 1
        if self.fetched == 2:
            self.leased.set()
            assert await asyncio.to_thread(self.cancelled.wait, 30), "the cancel never came"
        return await self.objects.get(key)


def test_j05_a_cancel_mid_run_stops_spending(lab):
    """Two workers (production's concurrency): the first case is at the endpoint, the second
    is leased and about to call, when a durable cancel commits. The run is `cancelled`; the
    in-flight case is abandoned with its charge reported unrecorded; the leased one is stopped
    by B1's per-call fence - no call is sent after the cancel, no result is kept."""
    from infrx.evaluation.runner import Limits
    frozen = lab.freeze(lab.run_payload(53, text(lab), harness(lab), lab.serving("baseline"),
                                        max_cases=6))
    gate, sent_at_cancel = Gated(lab.objects), []
    with lw.endpoint("baseline") as (wallet, http):
        model = wallet.answer

        def answer(prompt):
            if not gate.cancelled.is_set():
                assert gate.leased.wait(30), "the second worker never leased"
                cancel(lab, frozen)
                sent_at_cancel.append(len(wallet.calls))
                gate.cancelled.set()
            return model(prompt)
        wallet.answer = answer
        report = run(lab.runner(http, objects=gate, limits=Limits(30, 3, 2, 2)).run(frozen))
    assert report["state"] == "cancelled" and report["stopped"] == "cancelled"
    assert sent_at_cancel == [1] and len(wallet.calls) == 1, "a call was sent after the cancel"
    assert lab.results(frozen.run.run_id) == {}
    assert report["abandoned"] == sorted(frozen.cases[:2])
    assert report["unrecorded"]["CREDIT"] == str(wallet.debited) != "0"


# ------------------------------------------------------------------------------------ j06
def test_j06_two_serving_versions_reproduce_the_frozen_benchmark(lab, workdir):
    """The same frozen benchmark (dataset, harness, evaluator, seed) on two serving versions
    of the same model: every case's output and score are equal, B2 labels the pair
    `single_factor` on `serving_ref` with a zero difference; re-freezing a run's payload is the
    same run (ref, cases), and redelivering the finished run sends nothing. A reproduction
    claims no improvement."""
    one, _, _ = completed(lab, "baseline", 61)
    two, _, _ = completed(lab, "baseline_v2", 62)
    a, b = lab.results(one.run.run_id), lab.results(two.run.run_id)
    assert sorted(a) == sorted(b) == sorted(one.cases)
    assert {c: (a[c]["output"], a[c]["score"]) for c in a} == \
        {c: (b[c]["output"], b[c]["score"]) for c in b}
    assert sum(r["score"] for r in a.values()) == 10.0
    report = compare(lab, "baseline", "baseline_v2", workdir, (61, 62))
    assert report["pairing"] == {"kind": "single_factor", "factors": ["serving_ref"],
                                 "tag": None}
    assert report["estimates"]["overall"]["diff"] == 0.0
    assert report["estimates"]["overall"]["improved"] is False, "a reproduction claims no gain"
    payload = one.run.model_dump(by_alias=True, exclude_unset=True)
    again = lab.freeze(payload)
    assert (again.run_ref, again.cases, again.split_digest) == \
        (one.run_ref, one.cases, one.split_digest)
    assert lab.sql("select count(*) from infrx.lab_eval_runs where run_id = %s",
                   one.run.run_id) == [(1,)]
    with lw.endpoint("baseline") as (wallet, http):
        report = run(lab.runner(http).run(again))
    assert report["state"] == "succeeded" and wallet.calls == []


# ------------------------------------------------------------------------------------ j07
def test_j07_a_required_slice_regression_rejects_despite_an_aggregate_gain(lab, workdir):
    """regressing vs baseline: +10 lookup, -6 math -> the overall mean gains 0.2 (0.5 -> 0.7),
    yet the required `math` slice is inferior, so the decision is `reject`."""
    report = compare(lab, "baseline", "regressing", workdir, (61, 63))
    observed = report["observed"]
    assert (observed["baseline"]["mean"], observed["candidate"]["mean"]) == (0.5, 0.7)
    assert report["estimates"]["overall"]["diff"] == pytest.approx(0.2)
    assert report["estimates"]["slices"]["math"]["verdict"] == "inferior"
    assert report["decision"] == {"outcome": "reject", "reasons": ["slice math inferior"]}


def test_j07_a_clean_win_is_accepted_and_claimed_improved(lab, workdir):
    """improving vs baseline: +10 lookup, math unchanged -> accept, improved (lower bound
    above 0), every case paired, costs compared per unit (CREDIT only)."""
    report = compare(lab, "baseline", "improving", workdir, (61, 64))
    overall = report["estimates"]["overall"]
    assert overall["diff"] == pytest.approx(0.5) and overall["improved"] is True
    assert overall["low"] > 0 and report["observed"]["paired"] == 20
    assert report["decision"] == {"outcome": "accept", "reasons": []}
    assert set(report["observed"]["cost_delta"]) == {"CREDIT"}


def test_j07_a_missing_candidate_output_is_counted_not_dropped(lab, workdir):
    """missing refuses M01 (baseline right): the case fails with no result, is counted as 0
    (mean 19/20, one error), pairs as a math regression, and the decision is not `accept`."""
    frozen, run_report, _ = completed(lab, "missing", 65)
    m01 = ids(lab)["M01"]
    assert run_report["failures"] == {m01: "invalid_request"}
    assert run_report["cases"] == {"done": 19, "failed": 1}
    report = compare(lab, "baseline", "missing", workdir, (61, 65))
    candidate = report["observed"]["candidate"]
    assert (candidate["mean"], candidate["errors"], candidate["missing"]) == (0.95, 1, 0)
    assert report["observed"]["paired"] == 20
    assert report["estimates"]["slices"]["math"]["diff"] == pytest.approx(-1 / 6)
    assert report["decision"]["outcome"] == "inconclusive"
    assert "slice math uncertain" in report["decision"]["reasons"]


def test_j07_an_unfinished_candidate_counts_its_missing_cases(lab, workdir):
    """improving, cancelled while its 10th case is at the endpoint (one worker): 9 results
    and 11 cases with no record at all. B2 counts them missing, never drops them: the mean is
    over the universe (9/20, not 9/9), 9 pairs, and the decision is `inconclusive` on
    coverage 9/20."""
    from infrx.evaluation import reports
    from infrx.evaluation.runner import Limits
    frozen = lab.freeze(lab.run_payload(66, text(lab), harness(lab), lab.serving("improving")))
    with lw.endpoint("improving") as (wallet, http):
        model, calls = wallet.answer, []

        def answer(prompt):
            calls.append(prompt)
            if len(calls) == 10:
                cancel(lab, frozen)
            return model(prompt)
        wallet.answer = answer
        ran = run(lab.runner(http, limits=Limits(30, 3, 2, 1)).run(frozen))
    assert ran["state"] == "cancelled" and len(lab.results(frozen.run.run_id)) == 9
    base, _, _ = completed(lab, "baseline", 61)
    report = reports.compare(base.run.model_dump(by_alias=True, exclude_unset=True),
                             lab.case_records(base),
                             frozen.run.model_dump(by_alias=True, exclude_unset=True),
                             lab.case_records(frozen), universe=list(base.cases),
                             protocol=PROTOCOL)
    lw.save(workdir, "report-baseline-unfinished.json", report)
    candidate = report["observed"]["candidate"]
    assert (candidate["missing"], candidate["errors"]) == (11, 0)
    assert candidate["mean"] == pytest.approx(9 / 20)
    assert report["observed"]["paired"] == 9
    assert report["decision"]["outcome"] == "inconclusive"
    assert report["decision"]["reasons"][0] == "coverage 9/20"


def test_j07_tool_cases_are_recorded_and_not_comparable(lab, workdir):
    """The tool benchmark: an actuator call is `blocked`, an undeclared tool `unsupported`
    (both recorded, no score), a plain answer scored; a read-only tool's replay needs the
    gateway's tool messages (B-R3), so that case fails `invalid_request`. B2 shows the two
    not-comparable cases and cannot decide (`inconclusive`, coverage 1/4)."""
    from infrx.evaluation import reports
    tools = lab.dataset("tools", 8).dataset_ref
    rows = {k: v["sample_id"] for k, v in lab.rows(tools).items()}
    harness_ref = lab.harness_ref(8, tools=lw.TOOLS)
    frozen = lab.freeze(lab.run_payload(81, tools, harness_ref, lab.serving("tools")))
    from infrx.harnesses.replay import recording_key
    recordings = {recording_key("lookup_fact", {"fact": "boiling point of water"}): "100"}
    with lw.endpoint("tools") as (wallet, http):
        report = run(lab.runner(http, recordings=recordings).run(frozen))
    results = lab.results(frozen.run.run_id)
    assert (results[rows["T1"]]["status"], results[rows["T1"]]["reasons"]) == \
        ("blocked", ["actuator:send_email"])
    assert (results[rows["T2"]]["status"], results[rows["T2"]]["reasons"]) == \
        ("unsupported", ["undeclared_tool:rm_rf"])
    assert results[rows["T3"]]["score"] == 1.0
    assert {results[rows[t]]["score"] for t in ("T1", "T2")} == {None}
    assert report["failures"] == {rows["T4"]: "invalid_request"}
    records = lab.case_records(frozen)
    payload = frozen.run.model_dump(by_alias=True, exclude_unset=True)
    compared = reports.compare(payload, records, payload, records, universe=list(frozen.cases),
                               protocol={**PROTOCOL, "min_cases": 2, "required_slices": {}})
    lw.save(workdir, "report-tools.json", compared)
    assert compared["observed"]["candidate"]["not_comparable"] == 2
    assert compared["decision"]["outcome"] == "inconclusive"
    assert compared["decision"]["reasons"][0] == "coverage 2/4"


# ------------------------------------------------------------------------------------ j11
def test_j11_a_finite_video_case_reaches_the_dev_endpoint(lab, workdir):
    """The imported clips through a `finite_video` harness: each case must reach H1's adapter
    with its media and duration (comparable, sent), then the dev endpoint - whose media path
    is L3's (HttpDevEndpoint refuses media until it merges)."""
    video = lab.dataset("video", 2).dataset_ref
    harness_ref = lab.harness_ref(11, adapter="finite_video", template="label {{media_ref}}")
    frozen = lab.freeze(lab.run_payload(111, video, harness_ref, lab.serving("video")))
    with lw.endpoint("video") as (wallet, http):
        report = run(lab.runner(http).run(frozen))
    results = lab.results(frozen.run.run_id)
    lw.save(workdir, "video-run.json", {"report": report, "results": results})
    unreached = {case: body["reasons"] for case, body in results.items()
                 if body["status"] == "unsupported"}
    assert not unreached, (
        "N1's finite-video content object (media_digest, span_ms) does not carry what H1's "
        f"finite_video adapter reads (sample.media_ref, sample.duration_ms): {unreached}")
    assert set(report["failures"].values()) == {"invalid_request"} and wallet.calls == []
    lw.not_run("j11", "L3", why="the imported clips reach H1; HttpDevEndpoint refuses media "
                               "until L3's media path merges")
    pytest.fail("E6L-BIND: bind j11 to L3's media dispatch")


def _e2e():
    """LAB-E2E's gate half (`apps/lab/tests/e2e/gate.py`), by path under this package's name
    (R213); a mutant copy has no apps/lab, so INFRX_LAB_DIR names the checkout's."""
    import importlib.util
    import os
    from pathlib import Path
    lab = Path(os.environ.get("INFRX_LAB_DIR") or lw.REPO / "apps" / "lab")
    spec = importlib.util.spec_from_file_location("lab_evaluate.lab_e2e_gate",
                                                  lab / "tests" / "e2e" / "gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_j10_the_provider_ui_launches_compares_and_cancels(workdir):
    """j10 (LAB-E2E): `apps/lab/tests/e2e/evaluate` - the Lab's evaluation pages built and
    served, signed in through their own form, over `/lab/v1/evaluations` on the l4 key: the
    page fails closed on the gateway's own LAB_EVALS composition, then a launch through the
    page's form freezes two real D7 runs (B1, L2), progress and a cancel come from D7's records,
    B2's stored report is shown with its slices and intervals, and the unsafe variants are
    refused. A red suite fails this case; a green one is NOT RUN while the gateway's own
    composition lacks the experiments/catalog/ledger ports (WR-B4-2, WR-LAB2-2, WR-B3-1)."""
    e2e = _e2e()
    got = e2e.run("evaluate", workdir)
    lw.save(workdir, "j10.json", {k: v for k, v in got.items() if k != "tail"})
    absent = e2e.missing(got)
    if absent:
        lw.not_run("j10", "WR-B4-2", "WR-LAB2-2", "WR-B3-1",
                   why=f"{e2e.command('evaluate')} passed ({got['pass']} cases) over D7/B1/L2 "
                       f"with the route suite's experiments/catalog/ledger, but pilot._lab_2 "
                       f"composes LabEvaluations without {absent}")
