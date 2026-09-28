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


def control_merged() -> bool:
    return importlib.util.find_spec("infrx.lab.control") is not None


def waits(sid: str, *lanes: str, steps: str):
    """NOT RUN until L3's control package exists; then NOT RUN until the case is bound to it."""
    why = ("infrx.lab.control is absent on this tree" if not control_merged()
           else "L3 merged; the case is not yet bound to its control port (E3L-BIND)")
    lab.not_run(sid, *lanes, why=f"{why}. Steps: {steps}")


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


def test_l02_a_provider_created_dev_revision_never_reaches_app_discovery(workdir):
    waits("l02", "L3", "L4", steps="DEV_A registers a model version and creates a private dev "
          "revision through the control port; its endpoint credential serves it; /v1/models with "
          "alpha's key lists no id of it and admission by alpha's key is 404 not_found")
    unbound()


# ------------------------------------------------------------------ l03-l06


def test_l03_registry_validation_refuses_bad_artifacts_and_foreign_ownership(workdir):
    waits("l03", "L3", steps="register with an unsupported artifact/schema/runtime, a mutable "
          "tag instead of a digest, and a model of provider B as DEV_A: each refused, no row "
          "written; the valid registration is accepted once under its idempotency key")
    unbound()


def test_l04_publication_needs_operator_approval_and_snapshots_the_rate(workdir):
    waits("l04", "L3", "L4", steps="DEV_A proposes publication; the proposal alone changes no "
          "listing; the operator approves with a rate card; the audit row names both actors and "
          "the rate snapshot; DEV_B cannot propose or approve for A")
    unbound()


def test_l05_app_discovers_and_serves_the_published_revision(workdir):
    waits("l05", "L3", "L4", steps="after l04's approval /v1/models lists the revision with its "
          "rate; alpha's request is admitted on its pins and settles once at the snapshot rate; "
          "the App runs unchanged with the Lab control service stopped")
    unbound()


def test_l06_rollback_during_a_queued_request_keeps_its_serving_and_rate_pins(workdir):
    waits("l06", "L3", steps="alpha's async job is accepted on revision R2 with no worker "
          "running; the provider rolls the alias back to R1; the worker starts: the queued job "
          "runs on R2 at R2's admitted card and settles once; a new request routes to R1")
    unbound()


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
