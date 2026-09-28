"""E3L l02-l06, l09, l10, l12: provider publication, rollback and the control service
(LAB-PUBLISH, LAB-ACCESS, SPLIT-CONTRACT). They need L3's control operations
(`infrx/lab/control/`, lab-access) over L3-SQL's revision state machine (lab-sql) and L4's
workflows (lab-app); none is on this base, so each case below is NOT RUN with the rerun command
until they merge. l02 has one base-half case that runs now: the seed's private dev deployment
is neither discoverable nor admissible by a consumer key.

Each NOT RUN case states its oracle and steps (04 LAB-PUBLISH: register -> dev validation ->
publication -> App call -> rollback during a queued request; dev stays private, public approval
and rate audited, old requests pinned, new routing correct), so the binding after the merge is
the control port's calls only (evidence E3L: follow-up E3L-BIND).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_world as lab                                 # noqa: E402

world, stack, harness = lab.world, lab.stack, lab.harness
A, DEV_A, ADMIN_A = lab.PROVIDER_A, lab.DEV_A, lab.ADMIN_A


def control_merged() -> bool:
    return importlib.util.find_spec("infrx.lab.control") is not None


def waits(sid: str, *lanes: str, steps: str):
    """NOT RUN until L3's control package exists; then NOT RUN until the case is bound to it."""
    why = ("infrx.lab.control is absent on this tree" if not control_merged()
           else "L3 merged; the case is not yet bound to its control port (E3L-BIND)")
    lab.not_run(sid, *lanes, why=f"{why}. Steps: {steps}")


def discovery(trip, tenant) -> list[dict]:
    answer = trip.http.get("/v1/models", headers=trip.headers(tenant))
    assert answer.status_code == 200, answer.text[:300]
    return answer.json()["data"]


def listed(entries) -> list[dict]:
    """/v1/models without its clock (`availability_as_of` moves on every read)."""
    return [{k: v for k, v in entry.items() if k != "availability_as_of"} for entry in entries]


def unbound():
    """Reached only if `waits` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E3L-BIND: this case is not bound to L3's control port yet")


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


def test_l09_publish_and_rollback_cas_under_injected_faults(workdir):
    waits("l09", "L3", steps="two concurrent publish/rollback transitions on one alias with a "
          "stale expected version: exactly one wins, the other is a typed conflict; a crash "
          "between the CAS and the audit write leaves no transition without its audit")
    unbound()


def test_l10_a_control_service_restart_mid_operation_loses_nothing(workdir):
    waits("l10", "L3", steps="SIGKILL the control process between accepting a publication and "
          "answering; on restart the operation is either applied once or absent, the retry "
          "under the same key answers the recorded result, App inference serves throughout")
    unbound()


def test_l12_a_consumer_key_is_refused_by_every_control_operation(workdir):
    waits("l12", "L3", steps="every control-port operation called with alpha's consumer /v1 "
          "key (header and query) is 401 invalid_audience before any read; with DEV_A's Lab "
          "session it is judged by LabAccess")
    unbound()
