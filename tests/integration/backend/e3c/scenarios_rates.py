"""E3C s16 (CREDIT-RATE): a rate card and a deployment published while jobs run and wait.

The worker process runs ONE inference at a time and is held right after the running job's
first committed chunk (`world.POINTS["output"]`), so one job is mid-execution, published and
unsettled, and one waits queued behind it. Then, through G8's operator seams:

1. `publish-card` (the CLI, its own process) publishes an approved card B for the deployment
   the listing serves and moves the listing to it. A gateway still configured for card A
   admits nothing (R69: not priced for this deployment); restarted at
   `ACTIVE_RATE_CARD_VERSION=B` (G8's step 4), a same-key retry of the waiting job replays its
   original admission (R91) and a new job is admitted at B.
2. a new serving revision + deployment revision + card C, as one audited G6B publication
   that moves the alias (the service `publish-marlin` fronts, called in-process: that CLI
   verb mints an upper-case id 0007 refuses - G8 open issue F11). Restarted at C, a new job
   pins the new revisions and C.

The worker is never restarted. Oracle (04 CREDIT-RATE; R68/R69/R78): every job settles at the
revision and card it was admitted at - the running and the waiting job at A, then B, then C -
each charge its own card x usage, the three charges distinguishable; one settlement each;
the wallet conserved. An unknown, private or unpriced model is refused at admission and
admits nothing."""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

CARD_B, CARD_C = "rc_e3c_s16_b", "rc_e3c_s16_c"
RATES = {CARD_B: ("800", "2400"), CARD_C: ("1600", "4800")}      # 2x and 4x the seed card
APPROVED = "E3C s16 operator approval for this local run (not a launch price)"
#: The seed's private dev endpoint, as a provider_dev credential names it (catalog.py
#: `_PRIVATE`: `<provider slug>/<endpoint name>-<environment>`).
PRIVATE_MODEL = "nemostation/marlin-2b-dev"


def pins(trip, request_id: str) -> tuple:
    """(deployment revision, serving revision, rate card) the job was admitted at."""
    return trip.one("select deployment_revision_id::text, serving_version_id::text, "
                    "rate_card_version from infrx.jobs where request_id = %s", request_id)


def listed(trip) -> tuple:
    """What the alias's newest listing serves now: (deployment, serving, card)."""
    return trip.one("select deployment_revision_id::text, serving_version_id::text, "
                    "rate_card_version from infrx.catalog_listings where public_model_id = %s "
                    "order by version desc limit 1", stack.CREDIT_ALIAS)


def effective_now() -> str:
    """An RFC 3339 instant a second ago: effective on the database clock of this host."""
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def publish_card(trip, card: str) -> None:
    """G8's `publish-card` as its own process: an approved card for the listed deployment."""
    status, answer = world.cli(trip, "publish-card", "--model", stack.CREDIT_ALIAS,
                               "--card-version", card, "--input-rate", RATES[card][0],
                               "--output-rate", RATES[card][1], "--approved-by", APPROVED,
                               "--effective-at", effective_now(), "--idempotency-key",
                               f"p-{card}", "--reason", "e3c s16: a rate change mid-queue")
    assert status == 0 and answer.get("rate_card_version") == card, (status, answer)


def publish_deployment(trip, card: str) -> tuple[str, str]:
    """A new serving revision (a new label, the same artifacts) and a new public deployment
    revision on the prod endpoint, priced by `card`, published as ONE audited G6B publication
    that moves the alias (`OperatorSession.publish` on D5's registry). (deployment, serving)."""
    from infrx.contracts.v2.records import CredentialAudience, RateCardSnapshot
    ops, now = trip.world.ops, datetime.now(timezone.utc)

    async def publish() -> tuple[str, str]:
        deployment = await ops.catalog.resolve(stack.CREDIT_ALIAS, endpoint_id=None,
                                               audience=CredentialAudience.consumer)
        serving = await ops.catalog.serving_revision(deployment.serving_version_id)
        serving = serving.model_copy(update={
            "serving_version_id": str(uuid.uuid4()), "created_at": now,
            "revision_label": f"e3c-s16-{uuid.uuid4().hex[:8]}"})
        deployment = deployment.model_copy(update={
            "deployment_revision_id": str(uuid.uuid4()), "created_at": now,
            "serving_version_id": serving.serving_version_id})
        rate = RateCardSnapshot(
            rate_card_version=card, model_id=serving.model_id,
            deployment_revision_id=deployment.deployment_revision_id,
            serving_version_id=serving.serving_version_id,
            input_rate_per_million=RATES[card][0], output_rate_per_million=RATES[card][1],
            effective_at=now - timedelta(seconds=1), approved_by=APPROVED)
        operator = await ops.operator(trip.world.operator_secret)
        await operator.publish(serving, deployment, rate, stack.CREDIT_ALIAS,
                               idempotency_key=f"p-{card}",
                               reason="e3c s16: a deployment change mid-queue")
        return deployment.deployment_revision_id, serving.serving_version_id
    return asyncio.run(publish())


def restart_gateway(trip, card: str) -> None:
    """G8's step 4 for the gateway: restarted at the published card. Its readiness
    (`price_source`) holds only when the listing's card is the one it serves."""
    trip.box.stop("gateway")
    trip.box.start("gateway", ACTIVE_RATE_CARD_VERSION=card)


def admitted(trip, key: str) -> str:
    answer = trip.send(trip.world.alpha, "async", world.TEXT, key)
    assert answer.status_code == 202, f"{key}: {answer.status_code} {answer.text[:300]}"
    return answer.json()["request_id"]


def settled_at_its_card(trip, request_id: str) -> tuple:
    """The job's one CREDIT usage row: (card, charged, card x usage at the ADMITTED card)."""
    from infrx.contracts.v2 import records as v2
    card, rate_card, prompt, completion, charged = trip.one(
        "select u.rate_card_version, infrx.job_admission(u.id)->'rate_card', u.prompt_tokens, "
        "u.completion_tokens, u.charged_credits from public.usage_events u where u.id = %s "
        "and u.accounting_regime = 'credit'", request_id)
    due = v2.RateCardSnapshot.model_validate(rate_card).debit(prompt, completion).raw("CREDIT")
    return card, charged, due, (prompt, completion)


def test_s16_jobs_waiting_and_running_keep_their_admitted_revision_and_rates(workdir,
                                                                           record_property):
    from infrx.operations import cli
    if "publish-card" not in next(a for a in cli.parser()._actions if a.dest == "cmd").choices:
        world.blocked("G8", why="the operator CLI has no publish-card")
    with world.composed(workdir, start=("gateway",)) as trip:
        seed = listed(trip)
        trip.box.start("worker", INFRX_E3C_BARRIER="output", WORKER_CONCURRENCY="1")
        running = admitted(trip, "e3c-s16-run")
        trip.box.reached("worker")                   # published, unsettled, the one runner held
        waiting = admitted(trip, "e3c-s16-wait")
        world.wait_for(lambda: trip.db("select 1 from infrx.jobs where request_id = %s and "
                                       "state = 'queued'", waiting), 60, "the waiting job queued")
        publish_card(trip, CARD_B)
        assert listed(trip) == (seed[0], seed[1], CARD_B), listed(trip)
        stale = trip.send(trip.world.alpha, "async", world.TEXT, "e3c-s16-at-a")
        assert (stale.status_code, world.code(stale)) == (400, "invalid_request"), stale.text
        assert world.job_of(trip, trip.world.alpha.org_id, "e3c-s16-at-a") == []
        restart_gateway(trip, CARD_B)
        replay = trip.send(trip.world.alpha, "async", world.TEXT, "e3c-s16-wait")
        assert (replay.status_code, replay.json().get("request_id"),
                replay.json().get("idempotency_replayed")) == (202, waiting, True), replay.text
        at_b = admitted(trip, "e3c-s16-at-b")
        deployment, serving = publish_deployment(trip, CARD_C)
        assert listed(trip) == (deployment, serving, CARD_C), listed(trip)
        restart_gateway(trip, CARD_C)
        at_c = admitted(trip, "e3c-s16-at-c")
        expected = {running: seed, waiting: seed, at_b: (seed[0], seed[1], CARD_B),
                    at_c: (deployment, serving, CARD_C)}
        if trip.db("select 1 from infrx.jobs where request_id = any(%s) and settled_at is not "
                   "null", [running, waiting]) or \
                trip.one("select state from infrx.jobs where request_id = %s", waiting) != \
                ("queued",):
            raise world.harness.HarnessError(
                "premise: the running/waiting jobs moved on before the publications: "
                f"{world.diagnose(trip, running)} {world.diagnose(trip, waiting)}")
        trip.box.release("worker")
        for request_id in expected:
            assert world.terminal(trip, request_id, timeout=90.0) == "succeeded", \
                world.diagnose(trip, request_id)
        seen = {}
        for request_id, want in expected.items():
            assert pins(trip, request_id) == want, \
                f"{request_id}: pinned {pins(trip, request_id)}, admitted at {want}"
            card, charged, due, usage = settled_at_its_card(trip, request_id)
            seen[request_id] = {"card": card, "charged": str(charged), "usage": usage}
            assert (card, charged) == (want[2], due), \
                f"{request_id} admitted at {want[2]} settled {charged} at {card} " \
                f"(card x usage {due}) (CREDIT-RATE)"
            world.settled_once(trip, request_id)
        record_property("settlements", seen)
        charges = {seen[r]["charged"] for r in (running, at_b, at_c)}
        assert len(charges) == 3, f"the three cards do not tell the charges apart: {seen}"
        trip.conserved(trip.world.alpha)


def unpriced(trip) -> None:
    """R69 on this disposable clone: a newer listing of the alias whose card is not yet
    effective (E3B's `_unpriced_listing`). Listings are immutable: the last step staged."""
    import psycopg
    with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                     "deployment_revision_id, serving_version_id, input_rate_per_million, "
                     "output_rate_per_million, effective_at, approved_by, provisional) values "
                     "('rc_e3c_s16_not_yet', %s, %s, %s, 1, 1, infrx.now() + interval '1 day', "
                     "'e3c s16', true)", (stack.SEED_MODEL, stack.SEED_PUBLIC_DEPLOYMENT,
                                          stack.SEED_SERVING))
        conn.execute("insert into infrx.catalog_listings (public_model_id, version, model_id, "
                     "deployment_revision_id, serving_version_id, rate_card_version, "
                     "effective_at, approved_by) select %s, max(version) + 1, %s, %s, %s, "
                     "'rc_e3c_s16_not_yet', infrx.now(), 'e3c s16' from infrx.catalog_listings "
                     "where public_model_id = %s",
                     (stack.CREDIT_ALIAS, stack.SEED_MODEL, stack.SEED_PUBLIC_DEPLOYMENT,
                      stack.SEED_SERVING, stack.CREDIT_ALIAS))


def test_s16_an_unknown_private_or_unpriced_model_is_refused_at_admission(workdir,
                                                                         record_property):
    with world.composed(workdir) as trip:
        alpha, answers = trip.world.alpha, {}
        for name, model in (("unknown", "nemostation/no-such-model"),
                            ("unknown-revision", f"{stack.CREDIT_ALIAS}@no-such-revision"),
                            ("private", PRIVATE_MODEL)):
            answer = trip.send(alpha, "async", world.TEXT, f"e3c-s16-{name}", model=model)
            answers[name] = (answer.status_code, world.code(answer))
        served = trip.send(alpha, "sync", world.TEXT, "e3c-s16-priced")
        assert served.status_code == 200, f"the priced alias does not serve: {served.text[:300]}"
        unpriced(trip)
        answer = trip.send(alpha, "async", world.TEXT, "e3c-s16-unpriced")
        answers["unpriced"] = (answer.status_code, world.code(answer))
        record_property("refusals", answers)
        assert answers == {"unknown": (404, "not_found"), "unknown-revision": (404, "not_found"),
                           "private": (404, "not_found"),
                           "unpriced": (400, "invalid_request")}, answers
        for name in answers:
            assert world.job_of(trip, alpha.org_id, f"e3c-s16-{name}") == [], \
                f"the {name} model was admitted"
        world.settled_once(trip, served.headers["inference-id"])
        trip.conserved(alpha)
