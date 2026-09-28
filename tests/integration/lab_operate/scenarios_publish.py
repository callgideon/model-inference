"""E3L l02-l06, l09, l10, l12: provider publication, rollback and the control service
(LAB-PUBLISH, LAB-ACCESS, SPLIT-CONTRACT), bound to L3 (E3L-BIND): `LabControl` over lab-sql's
`PgControlStore` (0032), A3's `PgRegistry`/`PgCatalogDirectory` and L2's `LabAccess` on the
scenario's clone (`lab_world.control`), and - for l10 and l12 - R186's control factory as its own
process (`control_box.py`) behind a session-verifier stand-in (the stack runs no GoTrue). The
engine smoke is `lab_world.EngineSmoke` (WR-L3-2 is not wired: the factory's smoke is 503).

04 LAB-PUBLISH: register -> dev validation -> publication -> App call -> rollback during a
queued request; dev stays private, public approval and rate audited, old requests pinned, new
routing correct. Not bound here, and why: the route halves of register/smoke and the
Operations list reads (`models`, `deployments`) need lab-sql's `ControlReads` (WR-LSQ-9, not
merged) and the engine smoke adapter (WR-L3-2); the Lab App journey J01/J02 needs WR-LAB-API-2's
composition (codex/w5-composition-2, not merged).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_world as lab                                 # noqa: E402

world, stack, harness = lab.world, lab.stack, lab.harness
A, DEV_A, ADMIN_A = lab.PROVIDER_A, lab.DEV_A, lab.ADMIN_A


def discovery(trip, tenant) -> list[dict]:
    answer = trip.http.get("/v1/models", headers=trip.headers(tenant))
    assert answer.status_code == 200, answer.text[:300]
    return answer.json()["data"]


def listed(entries) -> list[dict]:
    """/v1/models without its clock (`availability_as_of` moves on every read)."""
    return [{k: v for k, v in entry.items() if k != "availability_as_of"} for entry in entries]


# ------------------------------------------------------------------ l02 dev exclusion


def test_l02_the_seeded_private_dev_deployment_is_not_discoverable_or_admissible(workdir,
                                                                               record_property):
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        listed = trip.http.get("/v1/models", headers=trip.headers(alpha))
        assert listed.status_code == 200, listed.text[:300]
        ids = [model["id"] for model in listed.json()["data"]]
        refused = trip.send(alpha, "async", world.TEXT, "e3l-l02-private", model=lab.PRIVATE_MODEL)
        record_property("discovery", {"ids": ids, "private": [refused.status_code,
                                                             world.code(refused)]})
        assert stack.CREDIT_ALIAS in ids, f"premise: the public alias is listed: {ids}"
        assert not any(i.endswith("-dev") or stack.SEED_DEV_DEPLOYMENT in i for i in ids), ids
        assert (refused.status_code, world.code(refused)) == (404, "not_found"), refused.text[:300]
        assert world.job_of(trip, alpha.org_id, "e3l-l02-private") == []
        # The catalog port itself (the gateway's and L3's): a consumer never resolves a
        # private revision, even naming its endpoint; its provider_dev credential does.
        from infrx.contracts.v2.records import CredentialAudience
        from infrx.state.catalog import PgCatalogDirectory
        from infrx.state.jobstore import connector
        directory = PgCatalogDirectory(connector(harness.pg_dsn(trip.world.database)))
        found = {audience.value: lab.call(directory.resolve(
            lab.PRIVATE_MODEL, audience=audience, endpoint_id=stack.SEED_DEV_ENDPOINT))
            for audience in (CredentialAudience.consumer, CredentialAudience.provider_dev)}
        assert found["provider_dev"] is not None, "premise: the dev credential resolves it"
        assert found["consumer"] is None, found["consumer"]


def test_l02_a_provider_created_dev_revision_never_reaches_app_discovery(workdir,
                                                                         record_property):
    """Oracle: DEV_A's registered, validated, internally priced dev revision with its
    provider_dev key is served to that key's endpoint only; alpha's /v1/models is unchanged
    and names none of it, and alpha's key is 404 on its dev name and on the alias pinned to
    its label; the catalog port never resolves it for a consumer, even naming its endpoint."""
    with world.composed(workdir, start=("gateway",)) as trip:
        lab.seed_lab(trip)
        alpha, ctl = trip.world.alpha, lab.control(trip)
        before = discovery(trip, alpha)
        revision, dev = lab.ready_dev(ctl, "e3l-l02")
        lab.call(ctl.price_dev(lab.operator(), dev.deployment_revision_id,
                               rate_card_version="rc_e3l_l02_internal", input_rate="400",
                               output_rate="1200"))
        key = lab.call(ctl.issue_dev_key(DEV_A, A, dev.deployment_revision_id))
        scope = trip.one("select audience, provider_org_id::text, endpoint_id::text from "
                         "public.api_keys where id = %s", key.key_id)
        assert scope == ("provider_dev", A, dev.endpoint_id), scope
        after = discovery(trip, alpha)
        names = ("nemostation/e3l-l02-dev", f"{stack.CREDIT_ALIAS}@{revision.revision_label}")
        answers = {name: trip.send(alpha, "async", world.TEXT, f"e3l-l02-{i}", model=name)
                   for i, name in enumerate(names)}
        record_property("discovery", {"before": [m["id"] for m in before],
                                      "after": [m["id"] for m in after],
                                      "refused": {n: [a.status_code, world.code(a)]
                                                  for n, a in answers.items()}})
        assert listed(after) == listed(before), (before, after)
        for name, answer in answers.items():
            assert (answer.status_code, world.code(answer)) == (404, "not_found"), \
                (name, answer.text[:300])
        assert trip.db("select 1 from infrx.jobs where org_id = %s", alpha.org_id) == []
        from infrx.contracts.v2.records import CredentialAudience as Aud
        found = {aud.value: lab.call(ctl.catalog.resolve(names[0], audience=aud,
                                                         endpoint_id=dev.endpoint_id))
                 for aud in (Aud.consumer, Aud.provider_dev)}
        assert found["provider_dev"] is not None, "premise: its own dev key resolves it"
        assert found["provider_dev"].deployment_revision_id == dev.deployment_revision_id
        assert found["consumer"] is None, found["consumer"]


# ------------------------------------------------------------------ l03-l06


BAD_REGISTRATIONS = {
    "moving_tag": ("InvalidRequest", lambda r: {"runtime_image_ref": "vllm/vllm-openai:latest"}),
    "unsupported_runtime": ("InvalidRequest",
                            lambda r: {"runtime_image_ref": "evil/runner@sha256:" + "ab" * 32}),
    "unsupported_schema": ("InvalidRequest", lambda r: {"capability": r.capability.model_copy(
        update={"input_schema_ref": "custom.request.v9"})}),
    "provider_b_model": ("NotFound", lambda r: {"model_id": lab.MODEL_B}),
}


def test_l03_registry_validation_refuses_bad_artifacts_and_foreign_ownership(workdir,
                                                                            record_property):
    """Oracle: registration writes only a supported runtime pinned by digest, serving the
    gateway's schemas, for a model of the caller's own provider: a moving tag, another image,
    a custom schema and provider B's model are each refused with no row written; DEV_A in
    B's workspace is not_found; the valid registration is written once (the same row again
    is a no-op, another row under its id a conflict)."""
    from infrx.contracts import errors
    with world.composed(workdir, start=()) as trip:
        lab.seed_lab(trip)
        ctl = lab.control(trip)
        good = lab.serving(ctl, "e3l-l03")
        rows = lambda: trip.one("select count(*) from infrx.serving_versions")[0]  # noqa: E731
        before, refused = rows(), {}
        for name, (expected, change) in BAD_REGISTRATIONS.items():
            refused[name] = lab.refused_as(ctl.register(DEV_A, A, good.model_copy(
                update=change(good))))
        refused["other_workspace"] = lab.refused_as(ctl.register(DEV_A, lab.PROVIDER_B, good))
        record_property("refused", refused)
        assert refused == {**{n: e for n, (e, _) in BAD_REGISTRATIONS.items()},
                           "other_workspace": "NotFound"}, refused
        assert rows() == before, "a refused registration wrote a row"
        assert lab.call(ctl.register(DEV_A, A, good)) is True
        assert lab.call(ctl.register(DEV_A, A, good)) is False
        assert lab.refused_as(ctl.register(DEV_A, A, good.model_copy(
            update={"precision": "fp8"}))) == "Conflict"
        assert rows() == before + 1


def test_l04_publication_needs_operator_approval_and_snapshots_the_rate(workdir,
                                                                       record_property):
    """Oracle: only A's administrator proposes (a developer is forbidden; B's administrator
    finds nothing, in A's workspace or naming A's revision in its own); the proposal alone moves no listing; the operator's approval adds listing
    version 2 on the proposal at a NEW card - the rate snapshot the operator approved, under
    the operator's name - and the audit names both actors with the before/after listing.
    The seed's card is untouched."""
    with world.composed(workdir, start=()) as trip:
        lab.seed_lab(trip)
        ctl = lab.control(trip)
        revision, dev = lab.ready_dev(ctl, "l04")
        source = dev.deployment_revision_id
        refused = {who: lab.refused_as(ctl.propose(user, provider, source,
                                                   endpoint_name="marlin-2b"))
                   for who, user, provider in (("developer", DEV_A, A),
                                               ("other_provider", lab.ADMIN_B, A),
                                               ("other_workspace", lab.ADMIN_B, lab.PROVIDER_B))}
        seed = lab.listing(trip)
        proposal = lab.call(ctl.propose(ADMIN_A, A, source, endpoint_name="marlin-2b"))
        assert lab.listing(trip) == seed, "a proposal alone moved the listing"
        published = lab.call(ctl.approve(lab.operator(), proposal.deployment_revision_id,
                                          rate_card_version="rc_e3l_l04", input_rate="300",
                                          output_rate="900", expected_version=seed[0],
                                          reason="e3l l04"))
        card = trip.one("select deployment_revision_id::text, serving_version_id::text, "
                        "input_rate_per_million::text, output_rate_per_million::text, "
                        "approved_by, provisional from infrx.rate_card_versions where "
                        "rate_card_version = 'rc_e3l_l04'")
        events = [(e.action, e.actor, e.subject, e.before and e.before.get("version"),
                   e.after.get("version"), e.after.get("rate_card_version"))
                  for e in lab.call(ctl.events(ADMIN_A, A))
                  if e.action in ("lab_propose", "lab_publish")]
        seed_card = trip.one("select input_rate_per_million::text, output_rate_per_million::text"
                             " from infrx.rate_card_versions where rate_card_version = %s",
                             stack.SEED_CARD)
        record_property("publication", {"refused": refused, "seed": seed, "card": card,
                                        "events": events})
        assert refused == {"developer": "Forbidden", "other_provider": "NotFound",
                           "other_workspace": "NotFound"}, refused
        assert (published.version, published.deployment_revision_id,
                published.rate_card_version) == (seed[0] + 1, proposal.deployment_revision_id,
                                                 "rc_e3l_l04")
        assert lab.listing(trip) == (seed[0] + 1, proposal.deployment_revision_id, "rc_e3l_l04")
        assert card[:2] == (proposal.deployment_revision_id, revision.serving_version_id), card
        assert [float(r) for r in card[2:4]] == [300, 900] and card[4:] == (lab.OPERATOR,
                                                                              False), card
        assert events == [
            ("lab_propose", ADMIN_A, proposal.deployment_revision_id, None, None, None),
            ("lab_publish", lab.OPERATOR, stack.CREDIT_ALIAS, seed[0], seed[0] + 1,
             "rc_e3l_l04")], events
        assert [float(r) for r in seed_card] == [400, 1200], seed_card


def published_l05(trip):
    """l05's world: A's revision `l05` published at 300/900 (listing 2); the App's processes
    still on the seed's card. Yields (alpha, revision, proposal, listing, card)."""
    lab.seed_lab(trip)
    revision, proposal, published = lab.ship(lab.control(trip), "l05", expected_version=1)
    return trip.world.alpha, revision, proposal, published, lab.card_of("l05")


def test_l05_app_discovers_and_serves_the_published_revision(workdir, record_property):
    """Oracle: after the operator's approval the App never serves the new listing at a card
    its runtime did not approve (R69: the alias is unlisted and a call is 400 invalid_request,
    nothing admitted or charged); once the runtime is rolled onto the approved card,
    /v1/models lists the alias on the published deployment, serving revision and card at the
    snapshot rates, and alpha's call is admitted on exactly those pins and settled once at the
    snapshot rate (CREDIT books conserved)."""
    with world.composed(workdir) as trip:
        alpha, revision, proposal, published, card = published_l05(trip)
        unrolled = trip.send(alpha, "sync", world.TEXT, "e3l-l05-unrolled")
        unrolled_listing = discovery(trip, alpha)
        lab.roll_runtime(trip, card, roles=("worker", "gateway"))
        entries = discovery(trip, alpha)
        served = trip.send(alpha, "sync", world.TEXT, "e3l-l05")
        record_property("app", {"unrolled": [unrolled.status_code, world.code(unrolled),
                                             [e["id"] for e in unrolled_listing]],
                                "listed": [(e["id"], e["listing_version"],
                                            e["deployment_revision_id"], e["pricing"])
                                           for e in entries],
                                "served": served.status_code})
        assert (unrolled.status_code, world.code(unrolled)) == (400, "invalid_request"), \
            unrolled.text[:300]
        assert unrolled_listing == [], unrolled_listing
        assert world.job_of(trip, alpha.org_id, "e3l-l05-unrolled") == []
        assert [(e["id"], e["deployment_revision_id"], e["serving"]["serving_version_id"],
                 e["pricing"]["credit"]["rate_card_version"],
                 e["pricing"]["credit"]["input_rate_per_million"],
                 e["pricing"]["credit"]["output_rate_per_million"]) for e in entries] == [
            (stack.CREDIT_ALIAS, proposal.deployment_revision_id, revision.serving_version_id,
             card, "300.00000000", "900.00000000")], entries
        assert served.status_code == 200, served.text[:300]
        assert lab.pins(trip, alpha.org_id, "e3l-l05") == (
            proposal.deployment_revision_id, revision.serving_version_id, card, "succeeded")
        world.settled_once(trip, world.job_of(trip, alpha.org_id, "e3l-l05")[0][0])
        trip.conserved(alpha)


def test_l05_discovery_reports_the_listing_version_it_serves(workdir, record_property):
    """Oracle: /v1/models' `listing_version` is the catalog listing the entry serves - after
    the publication, listing 2 (the published deployment), never a constant."""
    with world.composed(workdir, start=()) as trip:
        alpha, _, proposal, published, card = published_l05(trip)
        trip.box.start("gateway", ACTIVE_RATE_CARD_VERSION=card)
        entries = discovery(trip, alpha)
        record_property("listed", [(e["listing_version"], e["deployment_revision_id"])
                                   for e in entries])
        assert [(e["listing_version"], e["deployment_revision_id"]) for e in entries] == [
            (published.version, proposal.deployment_revision_id)], entries


def test_l06_rollback_during_a_queued_request_keeps_its_serving_and_rate_pins(workdir,
                                                                            record_property):
    """Oracle: alpha's async job accepted on the published revision R2 (listing 2) with no
    worker running keeps R2's deployment, serving revision and card through the operator's
    rollback to listing 1 (which adds listing 3, audited) and the runtime roll back onto the
    seed's card: the worker runs it once on R2 and it settles once at R2's admitted card;
    a new call routes to R1 at R1's card; the CREDIT books are conserved."""
    with world.composed(workdir, start=()) as trip:
        lab.seed_lab(trip)
        alpha, ctl = trip.world.alpha, lab.control(trip)
        revision, proposal, published = lab.ship(ctl, "l06", expected_version=1)
        r2 = (proposal.deployment_revision_id, revision.serving_version_id, lab.card_of("l06"))
        r1 = (stack.SEED_PUBLIC_DEPLOYMENT, stack.SEED_SERVING, stack.SEED_CARD)
        trip.box.start("gateway", ACTIVE_RATE_CARD_VERSION=r2[2])
        queued = trip.send(alpha, "async", world.TEXT, "e3l-l06-queued")
        assert queued.status_code == 202, queued.text[:300]
        admitted = lab.pins(trip, alpha.org_id, "e3l-l06-queued")
        rolled = lab.call(ctl.rollback(lab.operator(), stack.CREDIT_ALIAS, to_version=1,
                                       expected_version=published.version,
                                       reason="e3l l06 regression"))
        lab.roll_runtime(trip, stack.SEED_CARD)
        trip.box.start("worker", ACTIVE_RATE_CARD_VERSION=stack.SEED_CARD)
        request_id = queued.json()["request_id"]
        final = world.terminal(trip, request_id, timeout=90.0)
        fresh = trip.send(alpha, "sync", world.TEXT, "e3l-l06-new")
        audit = [(e.action, e.actor, e.before and e.before.get("version"), e.after["version"])
                 for e in lab.call(ctl.events(ADMIN_A, A)) if e.action == "lab_rollback"]
        record_property("rollback", {"admitted": admitted, "rolled": vars(rolled),
                                     "final": final, "fresh": fresh.status_code,
                                     "audit": audit})
        assert admitted[:3] == r2 and admitted[3] not in world.pilotbox.FINISHED, admitted
        assert (rolled.version, rolled.deployment_revision_id, rolled.rate_card_version) == (
            published.version + 1, r1[0], r1[2]), rolled
        assert lab.listing(trip) == (published.version + 1, r1[0], r1[2])
        assert audit == [("lab_rollback", lab.OPERATOR, published.version,
                          published.version + 1)], audit
        assert final == "succeeded" and world.attempts(trip, request_id) == 1
        assert lab.pins(trip, alpha.org_id, "e3l-l06-queued") == (*r2, "succeeded")
        world.settled_once(trip, request_id)
        assert fresh.status_code == 200, fresh.text[:300]
        assert lab.pins(trip, alpha.org_id, "e3l-l06-new") == (*r1, "succeeded")
        trip.conserved(alpha)


# ------------------------------------------------------------------ l09, l10, l12


ADVISORY = "select pg_advisory_{}(hashtextextended('catalog_listings/' || %s, 0))"


def raced(trip, *calls):
    """`calls` (coroutine factories) started together while this test holds the alias's
    listing lock (0032's advisory lock), released once every one waits on it: each call's
    typed outcome - the Listing, or the refusal's class name."""
    import threading

    import psycopg
    outcomes: list = [None] * len(calls)

    def run(i, make):
        try:
            outcomes[i] = lab.call(make())
        except Exception as refused:                      # noqa: BLE001 - recorded, judged
            outcomes[i] = type(refused).__name__
    with psycopg.connect(harness.pg_dsn(trip.world.database), autocommit=True) as holder:
        holder.execute(ADVISORY.format("lock"), (stack.CREDIT_ALIAS,))
        threads = [threading.Thread(target=run, args=(i, make)) for i, make in enumerate(calls)]
        for thread in threads:
            thread.start()
        world.wait_for(lambda: waiting(trip, "advisory") >= len(calls), 30,
                       f"{len(calls)} transitions waiting on the listing lock")
        holder.execute(ADVISORY.format("unlock"), (stack.CREDIT_ALIAS,))
    for thread in threads:
        thread.join(60)
    return outcomes


def waiting(trip, event: str) -> int:
    """Backends of the scenario's clone waiting on a lock of this kind (advisory/relation)."""
    return trip.one("select count(*) from pg_stat_activity where datname = %s and "
                    "wait_event_type = 'Lock' and wait_event = %s",
                    trip.world.database, event)[0]


def published(trip) -> int:
    return trip.one("select count(*) from infrx.lab_control_events where action = "
                    "'lab_publish'")[0]


def test_l09_publish_and_rollback_cas_under_injected_faults(workdir, record_property):
    """Oracle: (1) two operators approving two proposals against listing 1 at the same time
    publish exactly one - the other is a typed state_conflict and its proposal stays
    proposed; (2) a rollback on a stale fence is refused and moves nothing; (3) two racing
    rollbacks move the alias once; (4) a publisher killed after its CAS wrote listing 4 but
    before its audit row (its backend terminated while the audit insert waits) leaves no
    listing, card or state move and no audit - and the retry on the same fence publishes
    once, with one audit row."""
    import psycopg
    with world.composed(workdir, start=()) as trip:
        lab.seed_lab(trip)
        ctl = lab.control(trip)
        (_, p1), (_, p2), (_, p3) = (lab.proposed(ctl, f"l09{x}") for x in "abc")
        race = raced(trip, lambda: lab.approve(ctl, p1, "l09a", 1),
                     lambda: lab.approve(ctl, p2, "l09b", 1))
        won = [o for o in race if not isinstance(o, str)]
        loser = p2 if won and won[0].deployment_revision_id == p1.deployment_revision_id else p1
        stale = lab.refused_as(ctl.rollback(lab.operator(), stack.CREDIT_ALIAS, to_version=1,
                                            expected_version=1, reason="stale"))
        after_stale = lab.listing(trip)
        rollbacks = raced(trip, *[lambda: ctl.rollback(lab.operator(), stack.CREDIT_ALIAS,
                                                       to_version=1, expected_version=2,
                                                       reason="race")] * 2)
        before_crash, audits = lab.listing(trip), published(trip)
        crashed: list = []
        with psycopg.connect(harness.pg_dsn(trip.world.database)) as holder:
            holder.execute("lock table infrx.lab_control_events in exclusive mode")
            import threading

            def publisher():
                try:
                    crashed.append(lab.call(lab.approve(ctl, p3, "l09c", before_crash[0])))
                except Exception as died:                 # noqa: BLE001 - the injected fault
                    crashed.append(type(died).__name__)
            thread = threading.Thread(target=publisher)
            thread.start()
            world.wait_for(lambda: trip.db(
                "select pid from pg_stat_activity where datname = %s and wait_event = "
                "'relation' and query like %s", trip.world.database,
                "%lab_control_publish%"), 30, "the publisher waiting on its audit insert")
            killed = trip.db("select pg_terminate_backend(pid) from pg_stat_activity where "
                             "datname = %s and wait_event = 'relation' and query like %s",
                             trip.world.database, "%lab_control_publish%")
            thread.join(60)
            holder.rollback()
        left = {"listing": lab.listing(trip), "audits": published(trip),
                "card": trip.db("select 1 from infrx.rate_card_versions where "
                                "rate_card_version = 'rc_e3l_l09c'"),
                "state": trip.one("select state from infrx.deployment_revisions where "
                                  "deployment_revision_id = %s", p3.deployment_revision_id)[0]}
        retried = lab.call(lab.approve(ctl, p3, "l09c", before_crash[0]))
        record_property("cas", {"race": [str(o) for o in race], "stale": stale,
                                "rollbacks": [str(o) for o in rollbacks], "crashed": crashed,
                                "killed": killed, "left": left, "retried": vars(retried)})
        assert len(won) == 1 and race.count("StateConflict") == 1, race
        assert won[0].version == 2
        assert trip.one("select state from infrx.deployment_revisions where "
                        "deployment_revision_id = %s", loser.deployment_revision_id)[0] \
            == "proposed_public"
        assert stale == "StateConflict" and after_stale[0] == 2, (stale, after_stale)
        assert sorted(str(getattr(o, "version", o)) for o in rollbacks) == ["3", "StateConflict"], \
            rollbacks
        assert before_crash[0] == 3
        assert killed == [(True,)] and crashed and isinstance(crashed[0], str), (killed, crashed)
        assert left == {"listing": before_crash, "audits": audits, "card": [],
                        "state": "proposed_public"}, left
        assert (retried.version, retried.deployment_revision_id) == (4, p3.deployment_revision_id)
        assert published(trip) == audits + 1


def crash_mid_proposal(trip, service, workdir, source: str) -> list:
    """ADMIN_A's publication proposal through the control service, SIGKILLed after the
    store committed it and before it answered (control_box's hold point). The client's
    outcome: a status, or the transport error of an answer that never came."""
    import threading

    import httpx
    marker = workdir / "control-hold.json"
    marker.unlink(missing_ok=True)
    service.start(E3L_HOLD="propose", E3L_HOLD_MARKER=str(marker))
    outcome: list = []

    def post():
        try:
            outcome.append(service.call("POST", "proposals", lab.session(ADMIN_A), body={
                "kind": "publish", "deployment_revision_id": source}).status_code)
        except httpx.HTTPError as lost:
            outcome.append(type(lost).__name__)
    thread = threading.Thread(target=post)
    thread.start()
    world.wait_for(marker.exists, 30, "the proposal committed, its answer held")
    service.kill()
    thread.join(60)
    assert not service.answers(), "premise: the control service is down"
    return outcome


def proposals_of(trip, source: str) -> list[tuple]:
    """(proposal id, state) of every publication proposal of this dev revision."""
    return trip.db("select d.deployment_revision_id::text, d.state from "
                   "infrx.lab_control_events e join infrx.deployment_revisions d on "
                   "d.deployment_revision_id::text = e.subject where e.action = 'lab_propose' "
                   "and e.after->>'source' = %s order by e.at", source)


def test_l10_a_control_service_restart_mid_operation_loses_nothing(workdir, record_property):
    """Oracle: the control service SIGKILLed between committing ADMIN_A's proposal and
    answering it: App inference serves (and settles once) while it is down; after the
    restart the operation is there exactly once, as a proposal the Lab lists as `proposed`
    with its audit row and no listing moved - and it goes on to publication (the operator's
    approval; the Lab then lists it `approved`)."""
    with world.composed(workdir) as trip:
        lab.seed_lab(trip)
        alpha, ctl = trip.world.alpha, lab.control(trip)
        _, dev = lab.ready_dev(ctl, "l10")
        source = dev.deployment_revision_id
        with lab.control_service(trip, workdir) as (service, _):
            lost = crash_mid_proposal(trip, service, workdir, source)
            during = trip.send(alpha, "sync", world.TEXT, "e3l-l10-down")
            service.start()
            listed = service.call("GET", "proposals", lab.session(ADMIN_A))
            made = proposals_of(trip, source)
            seed = lab.listing(trip)
            approved = lab.call(ctl.approve(lab.operator(), made[0][0] if made else source,
                                            rate_card_version="rc_e3l_l10", input_rate="300",
                                            output_rate="900", expected_version=seed[0],
                                            reason="e3l l10"))
            after = service.call("GET", "proposals", lab.session(ADMIN_A)).json()["data"]
        record_property("restart", {"lost": lost, "during": during.status_code,
                                    "listed": listed.json(), "made": made, "after": after})
        assert lost and isinstance(lost[0], str), f"premise: the answer was lost: {lost}"
        assert during.status_code == 200, during.text[:300]
        world.settled_once(trip, world.job_of(trip, alpha.org_id, "e3l-l10-down")[0][0])
        assert len(made) == 1 and made[0][1] == "proposed_public", made
        assert listed.status_code == 200 and [
            (p["proposal_id"], p["deployment_revision_id"], p["state"])
            for p in listed.json()["data"]] == [(made[0][0], source, "proposed")], listed.text
        assert seed[:2] == (1, stack.SEED_PUBLIC_DEPLOYMENT), seed
        assert (approved.version, approved.deployment_revision_id) == (2, made[0][0])
        assert [(p["proposal_id"], p["state"]) for p in after] == [(made[0][0], "approved")]


def test_l10_a_retry_after_a_lost_answer_proposes_once(workdir, record_property):
    """Oracle: the Lab's retry of a proposal whose answer was lost to the crash, after the
    restart, never leaves a second open proposal of the same dev revision (it answers the
    recorded proposal or is refused) - one operation, one proposal."""
    with world.composed(workdir, start=("gateway",)) as trip:
        lab.seed_lab(trip)
        _, dev = lab.ready_dev(lab.control(trip), "l10r")
        source = dev.deployment_revision_id
        with lab.control_service(trip, workdir) as (service, _):
            lost = crash_mid_proposal(trip, service, workdir, source)
            service.start()
            retried = service.call("POST", "proposals", lab.session(ADMIN_A), body={
                "kind": "publish", "deployment_revision_id": source})
            made = proposals_of(trip, source)
        record_property("retry", {"lost": lost, "retried": [retried.status_code,
                                                             retried.json()], "made": made})
        assert lost and isinstance(lost[0], str), f"premise: the answer was lost: {lost}"
        assert [state for _, state in made].count("proposed_public") == 1, made


CONTROL_OPERATIONS = (("GET", "models"), ("GET", "deployments"), ("GET", "proposals"),
                      ("GET", "aggregates"), ("POST", "register"),
                      ("POST", "deployments/{dev}/smoke"), ("POST", "proposals"))


def test_l12_a_consumer_key_is_refused_by_every_control_operation(workdir, record_property):
    """Oracle: on the running control service (R186's factory), every control operation
    called with alpha's consumer /v1 key - as the bearer, or in the query with no bearer -
    and with an anon or a service_role token naming alpha is 401 `unauthenticated` and writes nothing;
    alpha signed in (a consumer with no provider workspace) is 403 `denied`. The premises:
    the same calls with A's sessions are judged by LabAccess (DEV_A reads A's proposals,
    finds nothing of B, and ADMIN_A's proposal is 201)."""
    with world.composed(workdir, start=("gateway",)) as trip:
        lab.seed_lab(trip)
        alpha = trip.world.alpha
        _, dev = lab.ready_dev(lab.control(trip), "l12")
        bodies = {"register": {"name": "marlin-2b", "artifact_digest": "sha256:" + "0" * 64,
                               "schema_version": "chat.v1",
                               "runtime": "vllm/vllm-openai@sha256:" + "0" * 64},
                  "proposals": {"kind": "publish",
                                "deployment_revision_id": dev.deployment_revision_id}}
        rows = lambda: trip.one("select (select count(*) from infrx.deployment_revisions), "  # noqa: E731
                                "(select count(*) from infrx.serving_versions), "
                                "(select count(*) from infrx.lab_control_events)")
        with lab.control_service(trip, workdir) as (service, verifier):
            service.start()
            before = rows()
            answers = {}
            for method, path in CONTROL_OPERATIONS:
                target = path.format(dev=dev.deployment_revision_id)
                body = bodies.get(path) if method == "POST" else None
                for who, token in (("consumer_key", alpha.secret),
                                   ("anon", stack.jwt("anon", alpha.user_id)),
                                   ("service_role", stack.jwt("service_role", alpha.user_id)),
                                   ("consumer_session", lab.session(alpha.user_id))):
                    answer = service.call(method, target, token, body=body)
                    answers[f"{method} {path} {who}"] = (answer.status_code,
                                                         answer.json().get("refusal"))
                in_query = service.http.request(
                    method, f"/lab/v1/control/{target}",
                    params={"provider_org_id": A, "apikey": alpha.secret,
                            "access_token": alpha.secret},
                    **({"json": body} if body is not None else {}))
                answers[f"{method} {path} consumer_key_in_query"] = (
                    in_query.status_code, in_query.json().get("refusal"))
            written = rows()
            premise = {"dev_a_reads_a": service.call("GET", "proposals",
                                                     lab.session(DEV_A)).status_code,
                       "dev_a_reads_b": service.call("GET", "proposals", lab.session(DEV_A),
                                                     provider=lab.PROVIDER_B).status_code,
                       "admin_a_proposes": service.call("POST", "proposals",
                                                        lab.session(ADMIN_A),
                                                        body=bodies["proposals"]).status_code}
            asked = verifier.handler.seen
            record_property("control", {"answers": answers, "premise": premise,
                                        "verifier_asked": asked})
        # the three token-shaped bearers reach the verifier (a /v1 key is refused by shape)
        assert asked >= 3 * len(CONTROL_OPERATIONS), f"premise: the verifier judged ({asked})"
        refused = {k: v for k, v in answers.items() if not k.endswith("consumer_session")}
        assert set(refused.values()) == {(401, "unauthenticated")}, refused
        assert {v for k, v in answers.items() if k.endswith("consumer_session")} == {
            (403, "denied")}, answers
        assert written == before, (before, written)
        assert premise == {"dev_a_reads_a": 200, "dev_a_reads_b": 404,
                           "admin_a_proposes": 201}, premise
