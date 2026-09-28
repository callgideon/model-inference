"""B1 (EVAL-DURABLE, EVAL-REPRO) in the fake world: `freeze` pins a run, `Runner` drives
D7's lease loop against a provider_dev endpoint that debits a CREDIT wallet. The drills
(a worker killed mid-attempt, a durable cancel, duplicate delivery, an exhausted dev wallet)
run again on the real `PgLabDataStore` in `test_runner_pg.py`.

    uv run --frozen pytest -q tests/b/runner/test_runner.py
"""
from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest
from infrx.contracts import errors
from infrx.datasets import versions
from infrx.datasets.imports import sample_key
from infrx.evaluation import runner
from infrx.evaluation.runner import HttpDevEndpoint, Limits, Runner
from infrx.harnesses.replay import recording_key
from infrx.media.store import InMemoryObjectStore

from .world import (DEPLOYMENT, DEV, EVALUATOR, EVALUATOR_ID, NEMO, OTHER, OUTSIDER, RATE_CARD, SPEC,
                    VIEWER, Crash, DevWallet, FakeEvalStore, access, content, eval_run, harness,
                    manifest)

N = 5
LIMITS = Limits(lease_s=30, max_attempts=2, dispatch_retries=2, concurrency=1)


def run(coro):
    return asyncio.run(coro)


class World:
    def __init__(self, n: int = N, funded: str = "1000", **run_kw) -> None:
        self.store, self.objects = FakeEvalStore(), InMemoryObjectStore()
        self.access = access()
        self.grant = self.store.add_grant()
        source = run(self.store.register_source(
            provider_org_id=NEMO, source_id="5a000000-0000-4000-8000-000000000001",
            content_digest="sha256:" + "1" * 64, grant_ref=self.grant, actor="dev@nemo"))
        self.manifest = manifest(n, self.grant, source)
        for i, sample in enumerate(self.manifest["samples"], 1):
            run(self.objects.put_if_absent(sample_key(NEMO, sample["content_digest"]),
                                           content(i), "application/json"))
        self.dataset = run(self.store.publish(self.manifest, provider_org_id=NEMO,
                                              actor="dev@nemo"))
        self.harness_ref = run(self.store.publish(harness(**run_kw.pop("harness", {})),
                                                  provider_org_id=NEMO, actor="dev@nemo"))
        self.payload = eval_run(self.dataset, self.harness_ref, **run_kw)
        self.wallet = DevWallet(funded)
        self.ids = sorted(s["sample_id"] for s in self.manifest["samples"])

    def freeze(self, payload=None, spec=SPEC, user=DEV, provider=NEMO):
        return run(runner.freeze(self.store, payload or self.payload, evaluator=spec,
                                 access=self.access, user_id=user, provider_org_id=provider))

    def runner(self, limits=LIMITS, worker="w1", endpoint=None) -> Runner:
        return Runner(self.store, self.objects, endpoint or self.wallet, DEPLOYMENT,
                      worker_id=worker, limits=limits)

    def results(self) -> dict[str, dict]:
        return {k[1]: json.loads(v["body"]) for k, v in self.store.results.items()}

    def states(self, frozen) -> dict[str, int]:
        return run(self.store.run_status(frozen.run.run_id, provider_org_id=NEMO))["cases"]


def per_case() -> str:
    """The CREDIT one default answer costs: `len("answer qN") * 100` in, 1000 out."""
    return str(RATE_CARD.debit(900, 1000))


# ------------------------------------------------------------------------ B1.a freeze
def test_b1_freeze_pins_the_run_and_the_same_record_is_the_same_cases() -> None:
    """EVAL-REPRO: freezing the same record twice is one run ref and one case set - the first
    max_cases sample ids (R168); the split digest is the manifest's and the holdout is the
    manifest's holdout that is still accessible; another seed is another run."""
    w = World(max_cases=4)
    a, b = w.freeze(), w.freeze()
    assert a.run_ref == b.run_ref and a.cases == b.cases == tuple(w.ids[:4])
    assert a.split_digest == versions.split_digest(w.manifest["splits"])
    assert a.holdout == (w.ids[3],)            # the holdout is ids 4-5; the run has 4 cases
    assert a.evaluator == SPEC and a.harness == harness()
    assert w.freeze({**w.payload, "seed": 8}).run_ref != a.run_ref


def test_b1_freeze_refuses_an_unbound_evaluator_unbounded_spend_and_another_provider() -> None:
    """EVAL-REPRO/EVAL-DURABLE: the evaluator spec must be the one the run's evaluator ref
    names (its digest), with a known metric; a run without a CREDIT budget could spend
    without bound; a caller cannot freeze another provider's run."""
    w = World()
    with pytest.raises(errors.InvalidRequest, match="not an evaluation run"):
        w.freeze(harness())
    with pytest.raises(errors.InvalidRequest, match="evaluator"):
        w.freeze(spec={**SPEC, "reference": "sample.original.q"})
    odd = {**SPEC, "metric": "bleu"}
    bad = runner.evaluator_ref(odd, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID)
    with pytest.raises(errors.InvalidRequest, match="metric"):
        w.freeze(eval_run(w.dataset, w.harness_ref, evaluator=bad), spec=odd)
    usd = [{"limit": {"unit": "PROVIDER_USD", "value": "1.00000000"},
            "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
            "payer_ref": f"lab:payer:{NEMO}:{EVALUATOR_ID}@sha256:" + "0" * 64}]
    for budgets in (usd, []):
        with pytest.raises(errors.InvalidRequest, match="CREDIT budget"):
            w.freeze(eval_run(w.dataset, w.harness_ref, budgets=budgets))
    with pytest.raises(errors.Forbidden):
        w.freeze(user=OUTSIDER, provider=OTHER)
    assert w.store.runs == {}


def test_b1_only_a_current_member_allowed_to_run_evaluations_schedules() -> None:
    """WR-B-4 (R160's membership half; D7 checks the grant half): the provider a run is
    frozen for comes from the L2 port - a user with no current membership of it is told the
    workspace does not exist (a member of another provider, a revoked member), a viewer is
    refused, and nothing is published or created for either; a developer schedules."""
    w = World()
    before = w.store.catalog.refs()
    for user, provider, refusal in ((OUTSIDER, NEMO, errors.NotFound),
                                    (VIEWER, NEMO, errors.Forbidden),
                                    (DEV, OTHER, errors.NotFound)):
        with pytest.raises(refusal):
            w.freeze(user=user, provider=provider)
    store = w.access.store
    store.memberships[(NEMO, DEV)] = store.memberships[(NEMO, DEV)].model_copy(
        update={"revoked_at": store.now})
    with pytest.raises(errors.NotFound):
        w.freeze()
    assert w.store.runs == {} and w.store.catalog.refs() == before
    store.memberships[(NEMO, DEV)] = store.memberships[(NEMO, DEV)].model_copy(
        update={"revoked_at": None})
    assert w.freeze().run.provider_org_id == NEMO and len(w.store.runs) == 1


# ----------------------------------------------------------------- B1.b/c the loop
def test_b1_a_run_scores_every_case_once_and_records_each_attempts_cost() -> None:
    """EVAL-DURABLE: every case ends with exactly one result under the run's evaluator;
    the score is the deterministic metric against the case's reference; the call carries
    the run's seed and the attempt's derived key; each attempt's cost is the dev rate card's
    debit, and the run's CREDIT total is what the dev wallet was debited."""
    w = World()
    w.wallet.answer = lambda p: "wrong" if p.endswith("q3") else "a" + p.split("q")[-1]
    frozen = w.freeze()
    report = run(w.runner().run(frozen))
    results = w.results()
    assert sorted(results) == w.ids and report["cases"] == {"done": N}
    assert {k[2] for k in w.store.results} == {EVALUATOR}
    assert [results[i]["score"] for i in w.ids] == [1.0, 1.0, 0.0, 1.0, 1.0]
    first = results[w.ids[0]]
    assert first["status"] == "complete" and first["output"] == "a1"
    assert first["split"] == "train" and results[w.ids[-1]]["split"] == "holdout"
    assert first["usage"] == {"calls": 1, "prompt_tokens": 900, "completion_tokens": 1000}
    assert isinstance(first["latency_ms"], int)
    assert w.wallet.calls[0]["seed"] == 7 and w.wallet.calls[0]["prompt"] == "answer q1"
    assert w.wallet.calls[0]["key"] == f"attempt:{frozen.run.run_id}:{w.ids[0]}:1:0"
    costs = {a["cost"]["value"] for a in w.store.attempts.values()}
    assert costs == {per_case()} and {a["cost"]["unit"] for a in w.store.attempts.values()} \
        == {"CREDIT"}
    assert report["costs"] == {"CREDIT": str(w.wallet.debited)}
    assert report["stopped"] is None and report["failures"] == {}


def test_b1_a_transient_failure_is_retried_under_the_same_key_and_bounded() -> None:
    """EVAL-DURABLE: a retry of a dispatch reuses the attempt's key (the endpoint replays,
    no second debit); retries stop after `dispatch_retries`, and a non-retryable refusal is
    not retried; both leave the case failed with the reason visible."""
    w = World(n=3)
    w.wallet.fail = [errors.DependencyUnavailable("x"), errors.RateLimited("y"), None,
                     errors.DependencyUnavailable("x"), errors.DependencyUnavailable("x"),
                     errors.DeadlineExceeded("z"), errors.InvalidRequest("no")]
    report = run(w.runner().run(w.freeze()))
    keys = [c["key"] for c in w.wallet.calls]
    assert keys[:3] == [keys[0]] * 3                   # case 1: two retries, then answered
    assert keys[3:6] == [keys[3]] * 3 and keys[6] != keys[3]    # case 2: bounded at 2
    assert len(keys) == 7                              # case 3: not retried
    assert report["cases"] == {"done": 1, "failed": 2}
    assert report["failures"] == {w.ids[1]: "deadline_exceeded", w.ids[2]: "invalid_request"}
    assert w.wallet.debited == RATE_CARD.debit(900, 1000)


def test_b1_a_worker_killed_mid_attempt_is_a_new_bounded_attempt_after_recovery() -> None:
    """EVAL-DURABLE: a worker dying after the endpoint charged leaves its lease to expire;
    recovery puts the case back and the next attempt (a new key, a new paid attempt)
    finishes it - one result, the killed attempt `expired`; once `max_attempts` leases are
    spent the case fails without another call."""
    w = World(n=2)
    frozen = w.freeze()
    w.wallet.crash_after_charge = True
    with pytest.raises(Crash):
        run(w.runner().run(frozen))
    w.wallet.crash_after_charge = False
    w.store.now += 31
    assert run(w.store.recover()) == 1
    report = run(w.runner(worker="w2").run(frozen))
    assert report["cases"] == {"done": 2} and len(w.store.results) == 2
    assert w.store.attempts[(frozen.run.run_id, w.ids[0], 1)]["state"] == "expired"
    assert w.store.results[(frozen.run.run_id, w.ids[0], EVALUATOR)]["attempt"] == 2
    assert w.wallet.calls[0]["key"] != w.wallet.calls[1]["key"]

    once = World(n=1)
    frozen = once.freeze()
    once.wallet.crash_after_charge = True
    for _ in range(2):
        with pytest.raises(Crash):
            run(once.runner().run(frozen))
        once.store.now += 31
        run(once.store.recover())
    once.wallet.crash_after_charge = False
    report = run(once.runner().run(frozen))
    assert report["failures"] == {once.ids[0]: "attempts_exhausted"}
    assert report["cases"] == {"failed": 1} and len(once.wallet.calls) == 2


def test_b1_a_durable_cancel_stops_spending_and_drops_the_in_flight_result() -> None:
    """EVAL-DURABLE: the lease is fenced before every paid call and at finish, so a cancel
    mid-batch stops dispatch at once and the in-flight answer is never recorded."""
    w = World()
    frozen = w.freeze()

    def answer(prompt):
        if prompt.endswith("q2"):
            run_row = w.store.runs[frozen.run.run_id]
            run_row["state"] = "cancelled"
        return "a" + prompt.split("q")[-1]

    w.wallet.answer = answer
    report = run(w.runner().run(frozen))
    assert report["state"] == "cancelled" and report["stopped"] == "cancelled"
    assert list(w.results()) == [w.ids[0]] and len(w.wallet.calls) == 2
    assert report["abandoned"] == [w.ids[1]]
    assert report["unrecorded"] == {"CREDIT": per_case()}
    two = World(harness={"tools": [{"name": "look", "effect": "read_only",
                                    "input_schema": {}}]})
    frozen = two.freeze()
    two.wallet.answer = lambda p: (two.store.runs[frozen.run.run_id].update(state="cancelled")
                                   or [{"name": "look", "arguments": {}}])
    report = run(Runner(two.store, two.objects, two.wallet, DEPLOYMENT, worker_id="w",
                        limits=LIMITS, recordings={recording_key("look", {}): 1}).run(frozen))
    assert len(two.wallet.calls) == 1 and report["abandoned"] == [two.ids[0]]
    poor = World(n=1, funded="0")               # cancelled while the 402 is in flight
    frozen = poor.freeze()
    poor.wallet.answer = lambda p: poor.store.runs[frozen.run.run_id].update(
        state="cancelled") or "a1"
    report = run(poor.runner().run(frozen))
    assert report["stopped"] == "wallet_exhausted" and report["abandoned"] == [poor.ids[0]]


def test_b1_duplicate_delivery_scores_each_case_once() -> None:
    """EVAL-DURABLE: two workers delivered the same run share its cases through the leases
    and a third delivery after the run finished does nothing: one result and one paid call
    per case."""
    w = World()
    frozen = w.freeze()

    async def both():
        return await asyncio.gather(w.runner(worker="a").run(frozen),
                                    w.runner(worker="b").run(frozen))

    run(both())
    report = run(w.runner(worker="c").run(frozen))
    assert report["state"] == "succeeded" and report["cases"] == {"done": N}
    assert len(w.store.results) == N and len(w.wallet.calls) == N
    assert len({c["key"] for c in w.wallet.calls}) == N


def test_b1_an_exhausted_dev_wallet_stops_the_run_with_its_cases_unresolved() -> None:
    """EVAL-DURABLE: the dev wallet (starting at 0, funded by an allocation) refuses a
    request it cannot hold; the runner stops for good - no further call - and gives the
    refused attempt back, so the cases it did not finish stay pending, never dropped; funded
    again, the run completes at once (no lease to wait out)."""
    w = World(funded="0")
    frozen = w.freeze()
    report = run(w.runner().run(frozen))
    assert report["stopped"] == "wallet_exhausted" and len(w.wallet.calls) == 1
    assert report["cases"] == {"pending": N} and w.store.results == {}
    w.wallet.balance = RATE_CARD.maximum_hold(900, 1000) + RATE_CARD.maximum_hold(900, 1000)
    report = run(w.runner().run(frozen))
    assert report["stopped"] == "wallet_exhausted" and report["cases"] == {
        "done": 2, "pending": N - 2}
    assert not w.wallet.balance.is_negative
    w.wallet.balance = type(w.wallet.balance)("1000")
    assert run(w.runner().run(frozen))["cases"] == {"done": N}


def test_b1_a_402_gives_its_attempt_back_so_billing_never_exhausts_a_case() -> None:
    """EVAL-DURABLE (0-B-R1): a 402 is the wallet's, not the case's. Each refused delivery
    stops after one call with every case pending (the attempt is given back, WR-B-2(d), not
    left to expire), so more unfunded deliveries than `max_attempts` never fail a case as
    `attempts_exhausted`; the re-run is the same attempt under the same key; funded, every
    case is done."""
    w = World(n=2)
    frozen = w.freeze()
    w.wallet.fail = [errors.InsufficientCredit("x")] * (LIMITS.max_attempts + 1)
    for delivery in range(1, LIMITS.max_attempts + 2):
        report = run(w.runner().run(frozen))
        assert report["stopped"] == "wallet_exhausted" and len(w.wallet.calls) == delivery
        assert report["cases"] == {"pending": 2} and report["failures"] == {}
        w.store.now += 31
        run(w.store.recover())
    report = run(w.runner().run(frozen))
    assert report["cases"] == {"done": 2} and report["failures"] == {}
    assert sorted(w.results()) == w.ids
    first = f"attempt:{frozen.run.run_id}:{w.ids[0]}:1:0"
    assert [c["key"] for c in w.wallet.calls][:LIMITS.max_attempts + 2] == \
        [first] * (LIMITS.max_attempts + 2)


def test_b1_the_credit_budget_bounds_spending() -> None:
    """EVAL-DURABLE: once the run's recorded CREDIT reaches its budget limit no further case
    is leased; the rest stay pending. B-R8: a charge this delivery made but could not record
    (its finish was fenced out) counts toward the limit too."""
    w = World(limit=str(RATE_CARD.debit(900, 1000) + RATE_CARD.debit(900, 1000)))
    report = run(w.runner().run(w.freeze()))
    assert report["stopped"] == "budget_exhausted" and len(w.wallet.calls) == 2
    assert report["cases"] == {"done": 2, "pending": N - 2}
    lost = World(limit=per_case())
    lost.wallet.answer = lambda p: setattr(lost.store, "now", lost.store.now + 31) or "a1"
    report = run(lost.runner().run(lost.freeze()))
    assert report["unrecorded"] == {"CREDIT": per_case()} and report["costs"] == {}
    assert report["stopped"] == "budget_exhausted" and len(lost.wallet.calls) == 1


def test_b1_revoked_data_fails_its_case_without_dispatch() -> None:
    """DATA-RIGHTS at every attempt: a case whose grant is no longer current for
    provider_sharing is failed as `revoked` and never sent to the endpoint."""
    w = World(n=2)
    frozen = w.freeze()
    w.store.revoke(w.grant)
    report = run(w.runner().run(frozen))
    assert report["failures"] == {i: "revoked" for i in w.ids} and w.wallet.calls == []
    assert report["cases"] == {"failed": 2}
    unshared = World(n=1)                  # a new grant version drops provider_sharing
    frozen = unshared.freeze()
    unshared.store.grants[unshared.grant]["purposes"] = {"training"}
    report = run(unshared.runner().run(frozen))
    assert report["failures"] == {unshared.ids[0]: "revoked"} and unshared.wallet.calls == []


def test_b1_a_created_run_resumes_after_a_revocation_and_ends_revoked() -> None:
    """EVAL-DURABLE (0-B-R2): a revocation re-gates scheduling (`freeze` is refused), but a
    delivery rebuilds the created run from D7 with `resume` - the same pin, no scheduling
    gate - so the case whose worker was killed and the never-started ones all end `revoked`
    per attempt, none left pending under a running run; another provider's run and an
    evaluator that is not the run's are refused."""
    w = World(n=3)
    frozen = w.freeze()
    w.wallet.crash_after_charge = True
    with pytest.raises(Crash):
        run(w.runner().run(frozen))
    w.wallet.crash_after_charge = False
    w.store.revoke(w.grant)
    w.store.now += 31
    assert run(w.store.recover()) == 1
    with pytest.raises(errors.Forbidden):
        w.freeze()

    def resume(evaluator=SPEC, provider=NEMO):
        return run(runner.resume(w.store, frozen.run.run_id, evaluator=evaluator,
                                 provider_org_id=provider))

    again = resume()
    assert again == frozen
    report = run(w.runner(worker="w2").run(again))
    assert report["failures"] == {i: "revoked" for i in w.ids} and len(w.wallet.calls) == 1
    assert report["cases"] == {"failed": 3} and report["state"] == "succeeded"
    with pytest.raises(errors.NotFound):
        resume(provider=OTHER)
    with pytest.raises(errors.InvalidRequest, match="evaluator"):
        resume(evaluator={**SPEC, "max_requests": 5})


def test_b1_an_unexpected_error_fails_its_case_visibly_and_the_delivery_goes_on() -> None:
    """B-R7: a case that cannot be read (its content object is gone) or whose attempt
    raises anything unexpected fails at once with the reason visible, never aborting the
    delivery (its other cases are scored) nor surfacing later as `attempts_exhausted`."""
    w = World(n=3)
    frozen = w.freeze()
    missing = sample_key(NEMO, w.manifest["samples"][0]["content_digest"])
    w.objects.objects.pop(missing)
    w.wallet.answer = lambda p: 1 / 0 if p.endswith("q2") else "a" + p.split("q")[-1]
    report = run(w.runner().run(frozen))
    assert report["failures"] == {w.ids[0]: "missing_content",
                                  w.ids[1]: "error:ZeroDivisionError"}
    assert report["cases"] == {"failed": 2, "done": 1} and list(w.results()) == [w.ids[2]]


def test_b1_harness_outcomes_are_results_and_bounds_fail_the_attempt() -> None:
    """HARNESS-SAFE through B1: a blocked tool is a recorded, non-comparable result with no
    score; a recorded read_only tool answers from the recording; a run that loops past the
    evaluator's request bound fails the attempt with the bound and its cost."""
    tools = [{"name": "look", "effect": "read_only", "input_schema": {}},
             {"name": "buy", "effect": "network_mutation", "input_schema": {}}]
    w = World(n=3, harness={"tools": tools})
    frozen = w.freeze()
    look = recording_key("look", {})

    def answer(prompt):
        n = prompt.split("q")[-1]
        if n == "1":
            return [{"name": "buy", "arguments": {}}]
        if n == "2":
            return "a2" if len(w.wallet.calls) > 1 and w.wallet.calls[-1]["tool_results"] \
                else [{"name": "look", "arguments": {}}]
        return [{"name": "look", "arguments": {}}]

    w.wallet.answer = answer
    report = run(Runner(w.store, w.objects, w.wallet, DEPLOYMENT, worker_id="w",
                        limits=LIMITS, recordings={look: {"stock": 1}}).run(frozen))
    results = w.results()
    assert sorted(results) == w.ids[:2]
    assert results[w.ids[0]]["status"] == "blocked" and results[w.ids[0]]["score"] is None
    assert results[w.ids[0]]["comparable"] is False
    assert results[w.ids[0]]["reasons"] == ["network_mutation:buy"]
    assert results[w.ids[1]]["score"] == 1.0 and results[w.ids[1]]["usage"] == {
        "calls": 2, "prompt_tokens": 1800, "completion_tokens": 2000}
    assert w.wallet.calls[2]["tool_results"] == [{"name": "look", "result": {"stock": 1}}]
    assert report["failures"] == {w.ids[2]: "bound:requests"}
    failed = w.store.attempts[(frozen.run.run_id, w.ids[2], 1)]
    assert failed["cost"] == {"unit": "CREDIT", "value": str(RATE_CARD.debit(900, 1000)
                                                             + RATE_CARD.debit(900, 1000)
                                                             + RATE_CARD.debit(900, 1000)
                                                             + RATE_CARD.debit(900, 1000))}


def test_b1_an_oversized_output_is_scored_and_recorded_without_its_body() -> None:
    """D7 bounds a result body at 64 KiB: a larger output is scored, then dropped from the
    body with the reason visible, instead of failing the finish."""
    w = World(n=1)
    w.wallet.answer = lambda p: "x" * 70_000
    run(w.runner().run(w.freeze()))
    (result,) = w.results().values()
    assert result["output"] is None and result["reasons"] == ["output_too_large"]
    assert result["score"] == 0.0


def test_b1_a_case_without_its_reference_is_recorded_unscored() -> None:
    """EVAL-REPRO: a reference path the case does not have is visible, never a 0 score."""
    spec = {**SPEC, "reference": "sample.original.missing"}
    w = World(n=1, evaluator=runner.evaluator_ref(spec, provider_org_id=NEMO,
                                                  evaluator_id=EVALUATOR_ID))
    run(w.runner().run(w.freeze(spec=spec)))
    (result,) = w.results().values()
    assert result["score"] is None and result["reasons"] == ["missing_reference"]
    assert result["status"] == "complete" and result["output"] == "a1"


def test_b1_cases_run_with_bounded_concurrency() -> None:
    """`concurrency` workers lease in parallel (a barrier that needs three callers at once
    passes only then), each under its own worker id."""
    w = World(n=3)
    barrier = threading.Barrier(3, timeout=5)

    def answer(prompt):
        barrier.wait()
        return "a" + prompt.split("q")[-1]

    w.wallet.answer = answer
    report = run(w.runner(Limits(lease_s=30, max_attempts=2, dispatch_retries=2,
                                 concurrency=3)).run(w.freeze()))
    assert report["cases"] == {"done": 3}
    assert {a["worker"] for a in w.store.attempts.values()} == {"w1/0", "w1/1", "w1/2"}


def test_b1_the_http_dev_endpoint_speaks_openai_with_the_key_and_prices_by_the_card() -> None:
    """The L3 stand-in adapter: bearer credential, Idempotency-Key, seed; the charge is the
    frozen dev rate card's debit of the reported usage; 402 is `InsufficientCredit`, 429/5xx
    retryable, any other refusal final."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body["messages"][0]["content"] == "poor":
            return httpx.Response(402, json={"error": {"code": "insufficient_credit"}})
        if body["messages"][0]["content"] == "busy":
            return httpx.Response(503, json={})
        if body["messages"][0]["content"] == "slow":
            return httpx.Response(429, json={})
        if body["messages"][0]["content"] == "lost":
            raise httpx.RemoteProtocolError("closed", request=request)
        if body["messages"][0]["content"] == "bad":
            return httpx.Response(400, json={})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hi", "tool_calls": [{"function": {
                "name": "look", "arguments": "{\"x\": 1}"}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = HttpDevEndpoint("http://dev", api_key="k", model="m", rate_card=RATE_CARD,
                           client=client)
    got = run(port.complete(prompt="p", media=[], tool_results=[], seed=7,
                            idempotency_key="attempt:x"))
    assert (got.text, got.tool_calls) == ("hi", [{"name": "look", "arguments": {"x": 1}}])
    assert got.charged == RATE_CARD.debit(10, 20)
    assert (got.prompt_tokens, got.completion_tokens) == (10, 20)
    sent = seen[0]
    assert str(sent.url) == "http://dev/v1/chat/completions"
    assert sent.headers["Authorization"] == "Bearer k"
    assert sent.headers["Idempotency-Key"] == "attempt:x"
    assert json.loads(sent.content) == {"model": "m", "seed": 7, "messages": [
        {"role": "user", "content": "p"}]}
    for prompt, error in (("poor", errors.InsufficientCredit),
                          ("busy", errors.DependencyUnavailable),
                          ("slow", errors.DependencyUnavailable),
                          ("lost", errors.DependencyUnavailable),
                          ("bad", errors.InvalidRequest)):
        with pytest.raises(error):
            run(port.complete(prompt=prompt, media=[], tool_results=[], seed=0,
                              idempotency_key="k"))
    with pytest.raises(errors.InvalidRequest, match="video"):
        run(port.complete(prompt="p", media=["m"], tool_results=[], seed=0,
                          idempotency_key="k"))
    # B-R3: the gateway takes {role, content} messages of system/user/assistant only, and no
    # tools; a recorded tool result is refused here, never sent as a message it refuses.
    with pytest.raises(errors.InvalidRequest, match="tool"):
        run(port.complete(prompt="p", media=[], tool_results=[{"name": "look", "result": 2}],
                          seed=0, idempotency_key="k"))
    assert len(seen) == 6                  # neither refusal reached the endpoint
