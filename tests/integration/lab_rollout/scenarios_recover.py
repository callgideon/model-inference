"""E8L k04-k06: R2's controller on D9's release rows (`PgReleaseStore`, 0039) and L3's serving
alias (`operations.Serving` on `PgControlStore`, 0032) - ROLLOUT-RECOVER. Traffic is R1's own
over D9 (0043); `Live` is built from R1's counts of that traffic plus the case's injected
health, latency and spend; the B2 reports are `reports.compare` over owned case records
(`lab_world.records`: their declared scores are the expectation).

The I7 unit's process (`python -m infrx.lab.workers rollout`) is composition-2's: a "restart"
here is a fresh `Controller` over the same durable rows, in process (k09 is the process).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import lab_world as lw
import pytest
from lab_world import run
from scenarios_route import admit, request, router, who


def traffic(lab, r, subjects: int, per: int, tag: int) -> dict[str, str]:
    """`per` fresh admissions for each of `subjects` subjects; org -> revision served."""
    served = {}
    for i, org in enumerate(lab.subjects(subjects)):
        for k in range(per):
            served[org] = admit(r, org, i * per + k, tag=tag).model_revision
    return served


def horizon(lab, ref, extra_s: int = 1):
    started = run(lab.releases().release(ref)).started_at
    return started + timedelta(seconds=lw.PLAN["horizon_s"] + extra_s)


class Partitioned:
    """L3's port with the serving control unreachable for the next `fail` calls."""

    def __init__(self, port, fail: int) -> None:
        self.port, self.fail = port, fail

    async def serving(self, endpoint_id):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("serving control partitioned")
        return await self.port.serving(endpoint_id)

    async def rollback(self, *args, **kwargs):
        return await self.port.rollback(*args, **kwargs)


# ------------------------------------------------------------------------------------ k04
def test_k04_a_breach_rolls_back_once_under_two_controllers_for_future_admissions(lab, workdir):
    """ROLLOUT-RECOVER: two controllers read the same fence and both see an error-rate breach;
    D9's CAS records one rollback, the loser rereads and accepts it. The subject admitted on
    the candidate before keeps that admission's pin and its D9 row; its next request is the
    baseline; the alias (never moved by a canary) stays where it was."""
    listed = lab.listing()
    policy, ref = lab.launch(lab.policy(weights=(5_000,), candidates=(lab.CAND,)), lw.plan())
    r = router(lab)
    served = traffic(lab, r, 20, 2, 0x4a)
    promised = next(org for org, rev in served.items() if rev == lab.PIN)
    before = [a for a in lab.assignments(ref) if a["serving_ref"] == lab.CAND]
    now = lab.now()
    breach = lw.live(r.counts, policy.policy_id, now=now, errors_=10)
    one, two = lab.controller(), lab.controller()

    async def both():
        return await asyncio.gather(
            one.step(policy, ref, lw.plan(), breach, now=now),
            two.step(policy, ref, lw.plan(), breach, now=now))
    verdicts = run(both())
    lw.save(workdir, "decisions.json", lab.decisions(ref))
    assert [v.action for v in verdicts] == ["rollback", "rollback"]
    assert lab.decisions(ref) == [("rollback", "rolled_back", lw.CONTROLLER, ["error_rate"])]
    got = run(lab.releases().release(ref))
    assert (got.state, got.fence) == ("rolled_back", 2)
    assert admit(r, promised, 900, tag=0x4a).model_revision == lab.ALIAS
    assert [a for a in lab.assignments(ref) if a["serving_ref"] == lab.CAND] == before
    assert lab.listing() == listed


def test_k04_a_controller_killed_before_the_alias_cas_converges_on_restart(lab, workdir):
    """The controller dies after D9's decision, before L3's alias read (a partition): the pass
    fails, D9 is rolled back once. Restarted (a fresh controller over the same rows), it only
    converges - no second decision whatever the metrics and report now say - and the
    operator's approval of the rolled-back release is refused."""
    from infrx.contracts import errors
    listed = lab.listing()
    policy, ref = lab.launch(lab.policy(weights=(5_000,), candidates=(lab.CAND,)), lw.plan())
    r = router(lab)
    traffic(lab, r, 20, 2, 0x4b)
    now = lab.now()
    killed = lab.controller(Partitioned(lab.serving(), fail=1))
    with pytest.raises(ConnectionError):
        run(killed.step(policy, ref, lw.plan(),
                        lw.live(r.counts, policy.policy_id, now=now, p99=5_001), now=now))
    assert run(lab.releases().release(ref)).state == "rolled_back"
    runs = lab.runs(0x4c, lab.BASE, lab.CAND)
    report = lab.report(runs, "improving", lw.PROTOCOL)
    at = horizon(lab, ref)
    healthy = lw.live(r.counts, policy.policy_id, now=at)
    for _ in range(2):
        verdict = run(lab.controller().step(policy, ref, lw.plan(), healthy, now=at,
                                            report=report, runs=runs))
        assert (verdict.action, verdict.reasons) == ("rolled_back", ())
    assert lab.decisions(ref) == [("rollback", "rolled_back", lw.CONTROLLER, ["latency"])]
    with pytest.raises(errors.StateConflict):
        run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(), healthy, now=at,
                                     report=report, runs=runs))
    assert lab.listing() == listed


# ------------------------------------------------------------------------------------ k05
def launched(lab, tag: int):
    policy, ref = lab.launch(lab.policy(weights=(5_000,), candidates=(lab.CAND,)), lw.plan())
    r = router(lab)
    traffic(lab, r, 40, 2, tag)
    return policy, ref, r, lab.runs(tag, lab.BASE, lab.CAND)


def test_k05_an_accepting_report_after_the_horizon_is_approved_once(lab, workdir):
    """B2 accepts `improving` over the baseline (every slice non-inferior) and reports the cost
    delta in CREDIT only (0.40: 40 x 0.02 - 40 x 0.01, never converted). Before the horizon a
    good look is not approvable (no peeking); after it the operator's approval is recorded
    once through D9 with the two runs as evidence; a second approval is refused."""
    from infrx.contracts import errors
    from infrx.contracts.lab import records
    policy, ref, r, runs = launched(lab, 0x5a)
    report = lab.report(runs, "improving", lw.PROTOCOL)
    lw.save(workdir, "report.json", report)
    assert report["decision"] == {"outcome": "accept", "reasons": []}
    assert {u: Decimal(v) for u, v in report["observed"]["cost_delta"].items()} == \
        {"CREDIT": Decimal("0.40")}
    early = horizon(lab, ref, -60)
    with pytest.raises(errors.StateConflict, match="before_horizon"):
        run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(),
                                     lw.live(r.counts, policy.policy_id, now=early), now=early,
                                     report=report, runs=runs))
    at = horizon(lab, ref)
    healthy = lw.live(r.counts, policy.policy_id, now=at)
    assert run(lab.controller().step(policy, ref, lw.plan(), healthy, now=at, report=report,
                                     runs=runs)).action == "expand"
    assert lab.decisions(ref) == [], "the controller expanded by itself"
    run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(), healthy, now=at,
                                 report=report, runs=runs))
    assert [(d[1], d[2]) for d in lab.decisions(ref)] == [("approved", lw.OPERATOR)]
    evidence = lab.sql("select e.evidence_refs from infrx.lab_rollout_events e join "
                       "infrx.lab_rollouts o using (policy_id) where o.policy_ref = %s and "
                       "e.to_state = 'approved'", ref)[0][0]
    assert sorted(evidence) == sorted(records.ref_of(x) for x in runs)
    with pytest.raises(errors.StateConflict):
        run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(), healthy, now=at,
                                     report=report, runs=runs))


def test_k05_an_inconclusive_report_blocks_promotion(lab, workdir):
    """A candidate that answered 8 of 40 cases: B2 is inconclusive (coverage), R2 holds on it
    and the operator's approval is refused; D9 records nothing."""
    from infrx.contracts import errors
    policy, ref, r, runs = launched(lab, 0x5b)
    report = lab.report(runs, "thin", lw.PROTOCOL)
    lw.save(workdir, "report.json", report)
    assert report["decision"]["outcome"] == "inconclusive"
    at = horizon(lab, ref)
    healthy = lw.live(r.counts, policy.policy_id, now=at)
    verdict = run(lab.controller().step(policy, ref, lw.plan(), healthy, now=at, report=report,
                                        runs=runs))
    assert verdict.action == "hold" and "report_inconclusive" in verdict.reasons
    with pytest.raises(errors.StateConflict):
        run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(), healthy, now=at,
                                     report=report, runs=runs))
    assert lab.decisions(ref) == [] and run(lab.releases().release(ref)).state == "running"


def test_k05_missing_or_stale_evidence_never_expands(lab, workdir):
    """ROLLOUT-RECOVER's delayed metrics: after the horizon, on an accepting report, each gap
    in the live evidence alone holds - metrics older than `max_lag_s`, quality coverage under
    `min_quality_coverage`, fewer than `min_requests` candidate requests, a cohort skewed past
    `max_skew_bp` - the operator's approval is refused on it, and D9 records nothing."""
    from infrx.contracts import errors
    policy, ref, r, runs = launched(lab, 0x5e)
    report = lab.report(runs, "improving", lw.PROTOCOL)
    at = horizon(lab, ref)
    pid = policy.policy_id
    cand = r.counts[(pid, "candidate")]
    assert cand >= lw.PLAN["min_requests"] and cand > 0
    gaps = {
        "metrics_stale": lw.live(r.counts, pid, now=at, lag_s=lw.PLAN["max_lag_s"] + 1),
        "quality_coverage": lw.live(r.counts, pid, now=at, covered=0),
        "min_requests": lw.live({(pid, "candidate"): 10, (pid, "baseline"): 10}, pid, now=at),
        "cohort_skew": lw.live({(pid, "candidate"): 20, (pid, "baseline"): 100}, pid, now=at),
    }
    seen = {}
    for reason, stale in gaps.items():
        verdict = run(lab.controller().step(policy, ref, lw.plan(), stale, now=at,
                                            report=report, runs=runs))
        seen[reason] = [verdict.action, list(verdict.reasons)]
        assert (verdict.action, verdict.reasons) == ("hold", (reason,)), (reason, verdict)
        with pytest.raises(errors.StateConflict, match=reason):
            run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(), stale, now=at,
                                         report=report, runs=runs))
    lw.save(workdir, "holds.json", seen)
    assert lab.decisions(ref) == [] and run(lab.releases().release(ref)).state == "running"


def test_k05_a_slice_regression_under_an_aggregate_gain_rolls_back(lab, workdir):
    """`regressing` wins overall (28/40 over 16/40) and loses every math case: B2 rejects on
    the required slice and R2 rolls back on it, whatever the aggregate says."""
    policy, ref, r, runs = launched(lab, 0x5c)
    report = lab.report(runs, "regressing", lw.PROTOCOL)
    lw.save(workdir, "report.json", report)
    observed = report["observed"]
    assert observed["candidate"]["mean"] > observed["baseline"]["mean"]
    assert report["decision"] == {"outcome": "reject", "reasons": ["slice math inferior"]}
    at = horizon(lab, ref)
    verdict = run(lab.controller().step(policy, ref, lw.plan(),
                                        lw.live(r.counts, policy.policy_id, now=at), now=at,
                                        report=report, runs=runs))
    assert verdict.action == "rollback"
    assert lab.decisions(ref) == [("rollback", "rolled_back", lw.CONTROLLER,
                                   ["quality_reject", "slice math inferior"])]


def test_k05_overspend_rolls_back_and_units_never_mix(lab, workdir):
    """Spend in PROVIDER_USD against the plan's CREDIT budget is refused before any decision
    (units never mix); spend one unit past the CREDIT budget rolls back on `budget`."""
    from infrx.contracts import errors
    policy, ref, r, runs = launched(lab, 0x5d)
    now = lab.now()
    with pytest.raises(errors.InvalidRequest, match="units never mix"):
        run(lab.controller().step(policy, ref, lw.plan(),
                                  lw.live(r.counts, policy.policy_id, now=now, unit="PROVIDER_USD"),
                                  now=now))
    assert lab.decisions(ref) == []
    verdict = run(lab.controller().step(
        policy, ref, lw.plan(), lw.live(r.counts, policy.policy_id, now=now,
                                        spent="10.00000001"), now=now))
    assert verdict.action == "rollback"
    assert lab.decisions(ref) == [("rollback", "rolled_back", lw.CONTROLLER, ["budget"])]


# ------------------------------------------------------------------------------------ k06
def test_k06_an_emergency_rollback_moves_a_promoted_alias_back(lab, workdir):
    """Approved on an accepting report, the operator promotes the candidate (L3: the alias's
    next listing serves serving_2 on the same endpoint); the operator's emergency rollback
    then records one D9 decision and moves the alias back to the baseline deployment through
    L3's CAS. The policy is the one R1 routes: its candidate ref must be what L3's alias reads
    as the candidate, or the rollback leaves the alias on it.

    E8L-F2, fixed by ROLLOUT-IDENTITY (R216): the candidate's own ref (`lab.CAND`) and the
    promoted deployment's ref (`lab.promote`'s fresh `deployment_revision_id`, mirroring
    `operations.py`'s `propose()`) share the servingVersion and digest but never the deployment
    id; `Controller._converge` now compares by serving identity when the rollback is decided
    (killed by `mutants.py`'s `st_converge_by_full_ref`). The policy's baseline is the alias's
    listed deployment in L3's own ref form (WR-E8L-9, E8L-F4), which L3's rollback resolves."""
    listed = lab.listing()
    # the baseline R2 rolls back to is the alias's listed deployment in L3's own ref form
    # (R188/R208), not the opaque stand-in: L3's rollback resolves it against its listings
    base = lab.sql("select infrx.lab_serving_ref(%s)", listed[1])[0][0]
    policy, ref = lab.launch({**lab.policy(weights=(5_000,), candidates=(lab.CAND,)),
                              "baseline_ref": base}, lw.plan())
    r = router(lab)
    traffic(lab, r, 40, 2, 0x6a)
    runs = lab.runs(0x6a, base, lab.CAND)
    at = horizon(lab, ref)
    report = lab.report(runs, "improving", lw.PROTOCOL)
    run(lab.controller().approve(lw.OPERATOR, policy, ref, lw.plan(),
                                 lw.live(r.counts, policy.policy_id, now=at), now=at,
                                 report=report, runs=runs))
    lab.promote(lab.q8.W["serving_2"], 1)
    serving = lab.serving()
    try:
        promoted = run(serving.serving(policy.endpoint_id))
        run(lab.controller().emergency_rollback(lw.OPERATOR, policy, ref, now=at,
                                                reason="e8l drill"))
        after = lab.listing()
        # the other way round: a policy naming the candidate as L3 reads it does not route
        l3_named = lab.policy(weights=(10_000,), candidates=(promoted[0],))
        lab.launch(l3_named, lw.plan())
        try:
            run(router(lab).route(who(lab.subjects(1)[0]), request(lab.ALIAS, 1, 0x6b)))
            routes = "routed"
        except Exception as refused:                    # noqa: BLE001 - recorded
            routes = type(refused).__name__
        lw.save(workdir, "alias.json", {
            "policy_candidates": [c.serving_ref for c in policy.candidates],
            "alias_serving_ref_when_promoted": promoted, "listing_before": listed,
            "listing_after": after, "decisions": lab.decisions(ref),
            "a_policy_naming_l3s_ref_routes": routes})
        assert [d[1] for d in lab.decisions(ref)] == ["approved", "rolled_back"]
        assert after[1] == listed[1], (
            f"the alias still serves the candidate after the rollback: L3 reads it as "
            f"{promoted[0]}, the policy R1 routes names {policy.candidates[0].serving_ref}; a "
            f"policy naming L3's ref instead routes: {routes}")
    finally:
        lab.restore_alias(listed[0])


def test_k06_the_worlds_alias_read_is_the_real_control_store_on_its_login(lab, workdir):
    """WR-E8L-3: the alias R2 converges is read through the real `PgControlStore` on 0044's
    `infrx_lab_control` login (what `pilot.control_serving` composes), never a stand-in. R207's
    oracle: an alias that moved to another endpoint leaves its old listing behind (0007 keeps
    history), and the old endpoint then answers nothing - a stand-in reading "the highest
    listing version on this endpoint" still answers the moved alias there."""
    alias, ids = "nemostation/e8l-moved", {k: lw.uid(n, 0xe07) for n, k in enumerate(
        ("m", "v", "s", "ep_a", "ep_b", "dep_a", "dep_b"), 1)}
    params = {**ids, "p": lab.NEMO, "base": lab.cc.MODEL, "base_s": lab.cc.SERVING,
              "alias": alias}
    for statement in (
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values (%(alias)s, 's', 's', 's', "
        "'live', 'https://s.example', 's', 1, 1, 1024, '{text}', '{text}', %(m)s, %(p)s)",
        "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, created_by) select %(v)s, %(m)s, %(p)s, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, 'e8l' from infrx.model_versions "
        "where model_id = %(base)s limit 1",
        "insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
        "provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, runtime_image_digest, engine_options_digest, precision, "
        "capability, created_by) select %(s)s, %(v)s, %(m)s, provider_org_id, 'moved-1', "
        "prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "runtime_image_digest, engine_options_digest, precision, capability, 'e8l' "
        "from infrx.serving_versions where serving_version_id = %(base_s)s",
        "insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, "
        "created_by) values (%(ep_a)s, %(p)s, 'e8l-moved-a', 'prod', 'e8l'), "
        "(%(ep_b)s, %(p)s, 'e8l-moved-b', 'prod', 'e8l')",
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, "
        "max_input_tokens, max_output_tokens, created_by) values "
        "(%(dep_a)s, %(ep_a)s, %(p)s, 'prod', %(s)s, 'public', 'active', 1, 1, 'e8l'), "
        "(%(dep_b)s, %(ep_b)s, %(p)s, 'prod', %(s)s, 'public', 'active', 1, 1, 'e8l')",
        "insert into infrx.rate_card_versions (rate_card_version, model_id, "
        "deployment_revision_id, serving_version_id, input_rate_per_million, "
        "output_rate_per_million, effective_at, approved_by, provisional) values "
        "('rc_e8l_moved_1', %(m)s, %(dep_a)s, %(s)s, 400, 1200, infrx.now(), 'e8l', true), "
        "('rc_e8l_moved_2', %(m)s, %(dep_b)s, %(s)s, 400, 1200, infrx.now(), 'e8l', true)",
        "insert into infrx.catalog_listings (public_model_id, version, model_id, "
        "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
        "approved_by, created_at) values "
        "(%(alias)s, 1, %(m)s, %(dep_a)s, %(s)s, 'rc_e8l_moved_1', infrx.now(), 'e8l', "
        "infrx.now()), (%(alias)s, 2, %(m)s, %(dep_b)s, %(s)s, 'rc_e8l_moved_2', infrx.now(), "
        "'e8l', infrx.now() + interval '1 second')"):
        lab.conn.execute(statement, params)
    reads = lab.serving().reads

    def answer(coro):
        try:
            return run(coro)
        except Exception as refused:                    # noqa: BLE001 - the login's refusal
            return type(refused).__name__
    listed = answer(reads.listing_versions(alias))
    got = {"moved_off": answer(reads.endpoint_alias(ids["ep_a"])),
           "current": answer(reads.endpoint_alias(ids["ep_b"])),
           "versions": listed if isinstance(listed, str) else
           [(x.version, x.deployment_revision_id) for x in listed],
           "reads": type(reads).__name__}
    lw.save(workdir, "reads.json", got)
    assert got["moved_off"] is None, (
        f"{alias} moved off {ids['ep_a']} (its current listing v2 is on {ids['ep_b']}): the "
        f"old endpoint answers nothing under R207, the world's reads answered {got}")
    assert got["current"] == alias, got
    assert got["versions"] == [(1, ids["dep_a"]), (2, ids["dep_b"])], got


# ------------------------------------------------------------------------------------ k09
def test_k09_the_controller_process_restarted_mid_rollout(lab, workdir):
    """composition-2's `python -m infrx.lab.workers rollout emergency-rollback --policy-ref
    --reason` (WR-I7-1) as a REAL OS process, twice, over one running release whose candidate
    the operator promoted (L3: the alias's next listing serves serving_2) - the "process
    killed and restarted" drill for the one subcommand that exists. The process composes
    `pilot.control_serving`, whose reads are now the real `PgControlStore` (merge #30,
    WR-E8L-7): the first invocation records D9's decision and moves the alias back to the
    baseline deployment (exit 0); the restarted one finds the work done - the CAS backstop's
    reread, no second decision, the alias left alone (exit 0). The continuous pass loop waits
    on WR-R2-3 (below)."""
    import os
    import subprocess
    import sys
    listed = lab.listing()
    base = lab.sql("select infrx.lab_serving_ref(%s)", listed[1])[0][0]
    policy, ref = lab.launch({**lab.policy(weights=(5_000,), candidates=(lab.CAND,)),
                              "baseline_ref": base}, lw.plan())
    r = router(lab)
    traffic(lab, r, 20, 2, 0x69)
    promoted = lab.promote(lab.q8.W["serving_2"], 9)
    env = {**os.environ, "LAB_DATABASE_URL": lab.dsn, "LAB_OPERATOR_ID": lw.OPERATOR}
    argv = [sys.executable, "-m", "infrx.lab.workers", "rollout", "emergency-rollback",
            "--policy-ref", ref, "--reason", "e8l k09 real process"]
    try:
        promoted_listing = lab.listing()
        first = subprocess.run(argv, cwd=lw.API, env=env, capture_output=True, text=True,
                               timeout=30)
        after_first, listing_first = run(lab.releases().release(ref)), lab.listing()
        decisions_after_first = lab.decisions(ref)
        second = subprocess.run(argv, cwd=lw.API, env=env, capture_output=True, text=True,
                                timeout=30)
        after_second, listing_second = run(lab.releases().release(ref)), lab.listing()
        lw.save(workdir, "process.json", {
            "first": {"exit": first.returncode, "stderr": first.stderr[-2000:]},
            "second": {"exit": second.returncode, "stderr": second.stderr[-2000:]},
            "listing_before": listed, "listing_promoted": promoted_listing,
            "listing_after_first": listing_first, "listing_after_second": listing_second,
            "decisions_after_first": decisions_after_first,
            "decisions_after_second": lab.decisions(ref)})
        assert promoted_listing[1] == promoted, "the promotion did not list the candidate"
        assert (first.returncode, second.returncode) == (0, 0), (
            f"the real process did not finish: exits {first.returncode}, "
            f"{second.returncode}: {first.stderr[-600:]} {second.stderr[-600:]}")
        assert (after_first.state, after_second.state) == ("rolled_back", "rolled_back"), (
            "the real process's own emergency-rollback did not record D9's decision")
        assert [d[1] for d in decisions_after_first] == ["rolled_back"]
        assert decisions_after_first == lab.decisions(ref), (
            "the restarted process recorded a second decision instead of a no-op reread "
            "(the lost-race backstop, K04's own oracle, broke for a real OS process too)")
        assert listing_first[1] == listed[1], (
            f"the real process left the alias on {listing_first}, not the baseline {listed}")
        assert listing_second == listing_first, "the restarted process moved the alias again"
    finally:
        lab.restore_alias(listed[0])
    lw.not_run("k09", "WR-R2-3", why="the pass loop `python -m infrx.lab.workers rollout` (no "
               "subcommand) refuses by name (composition-2's own `_rollout`: \"the rollout "
               "pass needs every running or rolled-back D9 release with its frozen plan, R1's "
               "live aggregates, the stored B2 report and L3's alias reads\"). Steps once "
               "WR-R2-3 (lab-sql + R1) lands the pass loop's read models: start the bare "
               "`rollout` role against this stack's Lab database, let it see a breach, "
               "`kill -9` it between D9's decision and the alias CAS, restart it, require one "
               "decision, the alias converged and `infrx_lab_rollout_alias_converged` set")
