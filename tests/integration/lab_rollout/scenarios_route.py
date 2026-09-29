"""E8L k01-k03: R1's routing on D9's real release rows (0043 on the `infrx_runtime` login) -
ROLLOUT-PIN. The consumer's money is G2's relay on its contract fakes (k01 through the composed
gateway, k02 through R1's own relay seam); everything R1 reads and records is PostgreSQL's.

Expectations are the policy's arithmetic (weights, cohorts, grants), never the router's own
output: a share is bounded by the weight with room for sampling (200 subjects, 3 sd).
"""
from __future__ import annotations

import asyncio
import dataclasses

import lab_world as lw
import pytest
from lab_world import run

ALIAS = "nemostation/marlin-2b"          # D8's seeded alias (checks_admission.ALIAS)


def request(model: str, n: int, tag: int = 0x4e):
    from tests.r.routing import test_routing as rt
    return rt.request(model, lw.uid(n, tag))


def who(org: str):
    from tests.r.routing import test_routing as rt
    return rt.auth(org)


def admit(router, org: str, n: int, *, tag: int = 0x4e, model: str | None = None):
    """One fresh admission through R1's hook; the request the relay admitted. The job's
    Inference-Id (`job_of`) is not the request id: the assignment row is keyed by the job."""
    from infrx.contracts import wire
    from infrx.rollouts import routing
    from starlette.responses import Response
    seen = []

    async def accept(_who, asked, _idem):
        seen.append(asked)
        return Response(b"ok", headers={wire.HEADER_INFERENCE_ID: job_of(asked.request_id)})

    run(routing.hook(accept, router)(who(org), request(model or ALIAS, n, tag), None))
    return seen[0]



def job_of(request_id: str) -> str:
    """The admitted job's Inference-Id for a request (a different uuid, derived)."""
    return "10b" + request_id[3:]


def router(lab, shadows=None, dsn=None):
    from infrx.rollouts import routing
    return routing.Router(lab.routing(dsn), shadows)


def share(revisions, pin) -> float:
    return sum(r == pin for r in revisions) / len(revisions)


# ------------------------------------------------------------------------------------ k01
def test_k01_routing_off_serves_todays_request_over_a_live_release(lab, workdir):
    """ROUTING OFF (the launched default): with a 100% canary running on D9 and a router over
    it handed to the composition, the gateway keeps the relay's own `accept` for the ingress;
    a request is answered exactly as by the same composition with no router at all (status,
    error code, jobs), D9 is never asked and no assignment row is written. On, the same
    composition asks D9 first: the flag is the only switch. (The pilot composition's fake
    world prices nothing, so both answers are its price refusal - the comparison, not the
    status, is the oracle.)"""
    from infrx.config import deployment_from_env
    from infrx.gateway import app as composition
    from infrx.gateway.routes.relay import Relay
    from infrx.scheduling.memory import MemoryScheduler
    from tests.g import relay_support, support
    from tests.g.test_startup import local
    assert deployment_from_env({}).rollout_routing is False
    if not lab.sql("select 1 from public.organizations where id = %s", support.ORG):
        lab.grant_org(support.ORG, "e8l-legacy")
    policy, ref = lab.launch(lab.policy(weights=(10_000,), candidates=(lab.CAND,)), lw.plan())

    def composed(on: bool, r):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        world.during.append(lambda: world.clock.advance(3_600))   # a wait ends, never hangs
        config = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                                 rollout_routing=on))
        app = composition.create_app(
            config, client=support.upstream(), sb=support.supabase(), clock=world.now_s,
            catalog=world.catalog, stream=world.stream, objects=world.objects, jobs=world.jobs,
            index=MemoryScheduler(world.clock.now), rollouts=r)
        answer = local(app).post(support.CHAT_PATH, json=support.BODY, headers=support.AUTH)
        return app, (answer.status_code, answer.json().get("error", {}).get("code"),
                     len(world.jobs.jobs))

    _, today = composed(False, None)
    r_off = router(lab)
    off, got = composed(False, r_off)
    lw.save(workdir, "off.json", {"today": today, "off": got, "asked": r_off.releases.asked})
    rt = off.state.runtime
    assert getattr(rt.relay.accept, "__func__", None) is Relay.accept
    assert rt.ingress.accept == rt.relay.accept
    assert got == today, "routing off changed the answer"
    assert r_off.releases.asked == [], "routing off asked D9"
    assert lab.assignments(ref) == []
    r_on = router(lab)
    _, on = composed(True, r_on)
    lw.save(workdir, "on.json", {"on": on, "asked": r_on.releases.asked})
    assert r_on.releases.asked[:2] == ["active", "eligible"], "the flag switches nothing"
    assert run(lab.releases().release(ref)).state == "running"


def test_k01_a_candidate_ref_resolves_through_0045_and_matches_l3s_own_computation(lab, workdir):
    """WR-E8L-2b (E8L-F1, R191/R208): the world's candidate ref is a real deployment's
    `infrx.lab_serving_ref`, so a launch resolves through 0045's `release_active` instead of
    refusing `state_conflict` on an opaque, unresolvable stand-in - the fail-first this case
    replaces: before this fix, `lab.CAND` was `q8.serving_ref(<bare serving_version_id>)`, a
    string no deployment ever names, and this exact launch+route raised `state_conflict` (the
    fixture-drift 0045's own `release_active` created for every e8l scenario, not only k06).
    The SQL and Python computations of the same identity (R188) must also agree byte-for-byte,
    or the ref this world hands to a policy is not the one `release_active`/R2 recompute."""
    matched = run(lab.python_serving_ref(lab.q8.W["deployment_2"]))
    lw.save(workdir, "identity.json", {"cand": lab.CAND, "python_computed": matched})
    assert lab.CAND == matched, "infrx.lab_serving_ref disagrees with operations.serving_ref"
    policy, ref = lab.launch(lab.policy(weights=(10_000,), candidates=(lab.CAND,)), lw.plan())
    assert admit(router(lab), lab.subjects(1)[0], 1, tag=0x4f).model_revision == lab.PIN
    assert run(lab.releases().release(ref)).state == "running"


# ------------------------------------------------------------------------------------ k02
def test_k02_a_shadow_changes_nothing_the_user_sees_or_pays(lab, workdir):
    """A shadow release (shadow_limit raised to 1 by the operator): the user's answer, job,
    hold, journal and settlement equal the same request with no router; the candidate's
    duplicate runs once on the provider-funded endpoint (its wallet, not the consumer's) and
    its output is dropped; D9 records one assignment on the baseline. At the default limit
    (0: P-12 gates public shadow) no duplicate runs at all."""
    from infrx.contracts import wire
    from tests.r.routing import test_routing as rt
    today, plain = rt.served()
    with lw.candidate_endpoint() as (wallet, http):
        shadows = lw.Shadows(http)
        policy, ref = lab.launch(lab.policy(weights=(0,), mode="shadow",
                                            candidates=(lab.CAND,)), lw.plan(), shadow_limit=1)
        r = router(lab, shadows)
        world, reply = rt.served(r)
        debited, calls = str(wallet.debited), len(wallet.calls)
        lw.save(workdir, "shadow.json", {"reply": reply.json(), "runs": shadows.runs,
                                         "debited": debited, "rows": lab.assignments(ref)})
        assert reply.status == plain.status == 200
        assert reply.json()["choices"] == plain.json()["choices"], "the user saw the shadow"
        assert rt.fingerprint(world) == rt.fingerprint(today), "the shadow touched money"
        assert world.only_job().settlement.charged == today.only_job().settlement.charged
        assert world.only_job().request.model_revision == lab.ALIAS
        assert shadows.runs == [(lab.CAND, lab.PIN)] and calls == 1
        assert wallet.debited > type(wallet.debited)("0"), "the provider paid nothing"
        rows = lab.assignments(ref)
        assert [(a["request_id"], a["serving_ref"]) for a in rows] == \
            [(reply.headers[wire.HEADER_INFERENCE_ID], lab.BASE)]
        assert r.counts[(policy.policy_id, "shadow")] == 1
        # the default bound: no duplicate until an operator raises it
        policy, ref = lab.launch(lab.policy(weights=(0,), mode="shadow",
                                            candidates=(lab.CAND,)), lw.plan())
        r = router(lab, shadows)
        world, reply = rt.served(r)
        assert reply.status == 200 and len(shadows.runs) == 1 and len(wallet.calls) == calls
        assert r.counts[(policy.policy_id, "shadow_skipped")] == 1


# ------------------------------------------------------------------------------------ k03
def test_k03_a_subject_keeps_its_arm_and_each_admission_is_one_d9_row(lab, workdir):
    """A 50% canary, 20 subjects x 3 requests: each subject is served one revision on every
    retry, both arms are served, and D9 holds exactly one row per admission naming the arm
    served - keyed by the job, carrying a cohort digest and never the subject's org."""
    policy, ref = lab.launch(lab.policy(weights=(5_000,), candidates=(lab.CAND,)), lw.plan())
    r = router(lab)
    served, arms = {}, {}
    for i, org in enumerate(lab.subjects(20)):
        got = {admit(r, org, i * 3 + k).model_revision for k in range(3)}
        for k in range(3):
            served[job_of(lw.uid(i * 3 + k, 0x4e))] = org
        assert len(got) == 1, f"{org} drifted between retries: {got}"
        arms[org] = got.pop()
    rows = lab.assignments(ref)
    lw.save(workdir, "rows.json", rows)
    assert set(arms.values()) == {lab.ALIAS, lab.PIN}
    assert len(rows) == 60 and {a["request_id"] for a in rows} == set(served)
    for a in rows:
        org = served[a["request_id"]]
        assert a["serving_ref"] == (lab.CAND if arms[org] == lab.PIN else lab.BASE), a
        assert a["pinned_by"] == "cohort" and a["policy_ref"] == ref
        assert org not in str(a), "a subject's org is stored in its assignment"
    assert len({a["cohort_digest"] for a in rows}) == 20
    assert r.counts[(policy.policy_id, "candidate")] + \
        r.counts[(policy.policy_id, "baseline")] == 60


def test_k03_candidate_traffic_is_bounded_and_a_raised_weight_keeps_its_subjects(lab, workdir):
    """A 10% canary over 200 subjects serves 4-16% on the candidate; D9's expansion to a later
    version at 30% (with run evidence) keeps every subject already promised the candidate
    (R179) and moves only baseline subjects over (20-40%)."""
    from tests.d import test_d9_rollout as d9
    body = lab.policy(weights=(1_000,), candidates=(lab.CAND,))
    policy, ref = lab.launch(body, lw.plan())
    subjects = lab.subjects(200)
    r = router(lab)
    before = {org: run(r.route(who(org), request(lab.ALIAS, i)))[0].model_revision
              for i, org in enumerate(subjects)}
    assert 0.04 < share(before.values(), lab.PIN) < 0.16, share(before.values(), lab.PIN)
    later = {**body, "version": 2, "candidates": [{"serving_ref": lab.CAND, "weight_bp": 3_000}]}
    later_ref = lab.d7.publish(lab.conn, later)
    fence = lab.sql("select fence from infrx.lab_rollouts where policy_ref = %s", ref)[0][0]
    lab.ok("lab_rollout_transition", {
        "provider_org_id": lab.NEMO, "policy_id": policy.policy_id, "fence": fence,
        "action": "expand", "decided_by": lab.DEV, "reason": "raise to 30%",
        "policy_ref": later_ref, "evidence_refs": [d9.evidence(lab.conn, 0xe83)]})
    after = {org: run(r.route(who(org), request(lab.ALIAS, 1000 + i)))[0].model_revision
             for i, org in enumerate(subjects)}
    lw.save(workdir, "arms.json", {"before": before, "after": after})
    promised = [org for org in subjects if before[org] == lab.PIN]
    assert promised and all(after[org] == lab.PIN for org in promised), "a promise moved"
    assert 0.2 < share(after.values(), lab.PIN) < 0.4


def test_k03_an_ab_split_serves_each_candidate_within_its_weight(lab, workdir):
    """A/B: two candidates at 40% each and the baseline at 20%, over 200 subjects: each arm
    within its weight, each candidate admitted on its own pinned revision."""
    policy, ref = lab.launch(lab.policy(weights=(4_000, 4_000)), lw.plan())
    r = router(lab)
    got = [run(r.route(who(org), request(lab.ALIAS, i)))[0].model_revision
           for i, org in enumerate(lab.subjects(200))]
    lw.save(workdir, "ab.json", got)
    assert set(got) == {lab.ALIAS, lab.PIN, lab.PIN_B}
    assert 0.3 < share(got, lab.PIN) < 0.5 and 0.3 < share(got, lab.PIN_B) < 0.5
    assert 0.12 < share(got, lab.ALIAS) < 0.28


def test_k03_pins_ineligible_and_session_subjects_are_never_routed(lab, workdir):
    """A 100% canary: an explicit `<model>@<revision>` is served as asked and D9 is not asked;
    an org without a grant, and a subject whose grant is revoked mid-release, are served the
    baseline and not assigned (R1: eligibility read now); a session-cohort release serves
    the baseline and records nothing (R180: no declared session on the wire)."""
    policy, ref = lab.launch(lab.policy(weights=(10_000,), candidates=(lab.CAND,)), lw.plan())
    r = router(lab)
    pinned = request(lab.PIN_B, 1)
    assert run(r.route(who(lab.subjects(1)[0]), pinned)) == (pinned, (), None)
    assert r.releases.asked == []
    assert admit(r, lab.ungranted(), 2).model_revision == lab.ALIAS
    subject = lab.subjects(1)[0]
    assert admit(r, subject, 3).model_revision == lab.PIN
    lab.ok("lab_revoke_access_grant", {"actor_user_id": lab.C1, "grantor_org_id": subject,
                                       "recipient_provider_org_id": lab.NEMO})
    try:
        assert admit(r, subject, 4).model_revision == lab.ALIAS, "a revoked subject routed"
    finally:
        lab.ok("lab_put_access_grant", {
            "actor_user_id": lab.C1, "grantor_org_id": subject,
            "recipient_provider_org_id": lab.NEMO, "model_ids": [lab.cc.MODEL],
            "categories": ["request_content"], "purposes": ["provider_sharing"],
            "retention_days": 30})
    assert [a["request_id"] for a in lab.assignments(ref)] == [job_of(lw.uid(3, 0x4e))]
    assert r.counts[(policy.policy_id, "ineligible")] == 2
    policy, ref = lab.launch(lab.policy(weights=(10_000,), cohort="session",
                                        candidates=(lab.CAND,)), lw.plan())
    assert admit(router(lab), subject, 5).model_revision == lab.ALIAS
    assert lab.assignments(ref) == []


def test_k03_a_release_store_outage_is_a_503_never_the_baseline(lab, workdir):
    """R181: D9 unreachable (a closed port in the block) is a retryable DependencyUnavailable
    before anything is admitted - never a silent move of a promised subject to the baseline."""
    from infrx.contracts import errors
    from infrx.rollouts import routing
    lab.launch(lab.policy(weights=(10_000,), candidates=(lab.CAND,)), lw.plan())
    dead = lab.runtime.rsplit("@", 1)[0] + f"@127.0.0.1:{lw._gate().DEAD_PORT}/" + lw.DATABASE
    from starlette.responses import Response
    admitted = []

    async def accept(*args):
        admitted.append(args)
        return Response(b"admitted")

    with pytest.raises(errors.DependencyUnavailable):
        run(routing.hook(accept, router(lab, dsn=dead))(who(lab.subjects(1)[0]),
                                                         request(lab.ALIAS, 1), None))
    assert admitted == []
