#!/usr/bin/env python3
"""WR-AP01-1: the pilot composition mounts AP-01's routes only when their switches are on.

    uv run --frozen pytest -q tests/ap01/test_composed.py

Composed as the gateway composes them (`pilot.build_ingress_deps`, then every module in
`app.ROUTERS`), over the g track's fakes for the four durable adapters and AP-01's in-memory
world for the identity store.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI

from infrx.auth.context import AuthResolver
from infrx.auth_facade import AuthFacade
from infrx.config import RuntimeMisconfigured
from infrx.console.session import SessionActors
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.gateway import app as gateway_app, pilot
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler

from tests.ap01.worlds import FakeWorld
from tests.g import support
from tests.g.test_composition import Journal

AP01 = {"/auth/v1/sign-in", "/auth/v1/availability", "/console/v1/me",
        "/console/v1/capabilities", "/lab/v1/workspaces", "/lab/v1/capabilities",
        "/lab/v1/workspaces/{provider_org_id}/members", "/operator/v1/providers"}


def composed(*, with_identity=True, **switches):
    harness = credit_jobstore_factory()
    jobs, clock = harness.port, harness.clock
    jobs.catalog = catalog = support.catalog()
    config = support.settings()
    config.deployment = config.deployment.replace(**switches)
    rt = support.runtime(config, sb=support.supabase(), clock=lambda: clock.now().timestamp())
    world = FakeWorld()
    identity = dict(identity=world.identity, lab_access=world.access) if with_identity else {}
    pilot.build_ingress_deps(rt, catalog=catalog, stream=Journal(jobs),
                             objects=InMemoryObjectStore(), jobs=jobs,
                             index=MemoryScheduler(clock.now), **identity)
    return rt


def paths(rt) -> set[str]:
    app = FastAPI()
    for module in (m for m in gateway_app.ROUTERS
                   if m.__name__.rsplit(".", 1)[-1] in ("auth", "console_me", "lab_workspaces",
                                                        "operator_providers")):
        module.register(app, rt)
    return {route.path for route in app.routes}


def test_wr_ap01__off_by_default_nothing_mounts():
    """Oracle: the launched API is unchanged - no switch, no actor source, no route."""
    rt = composed()
    assert (rt.actors, rt.identity, rt.lab_access, rt.auth_facade) == (None,) * 4
    assert paths(rt) & AP01 == set()


def test_wr_ap01__identity_api_composes_the_session_actor_source():
    """Oracle: IDENTITY_API on puts `SessionActors` on `rt.actors` (the session verifier on the
    project's auth server, the operator-key door through `AuthResolver`, the configured web
    origins) and mounts the identity routes; the four modules are in ROUTERS."""
    rt = composed(identity_api=True, web_origins=" https://app.example ,https://lab.example")
    assert isinstance(rt.actors, SessionActors) and isinstance(rt.actors.keys, AuthResolver)
    assert rt.actors.origins == ("https://app.example", "https://lab.example")
    assert rt.identity is not None and rt.lab_access is not None and rt.auth_facade is None
    assert {"/console/v1/me", "/lab/v1/workspaces", "/operator/v1/providers"} <= paths(rt)
    assert not any(p.startswith("/auth/v1/") for p in paths(rt))


def test_wr_ap01__identity_api_without_its_store_refuses_to_start():
    with pytest.raises(RuntimeMisconfigured, match="identity"):
        composed(with_identity=False, identity_api=True)


def test_wr_ap01__the_auth_facade_needs_the_publishable_key():
    """Oracle: AUTH_FACADE on without SUPABASE_ANON_KEY refuses to start naming the setting
    (never its value); with it, the facade uses that key - never the service-role key."""
    with pytest.raises(RuntimeMisconfigured, match="SUPABASE_ANON_KEY"):
        composed(auth_facade=True)
    rt = composed(auth_facade=True, supabase_anon_key="anon-publishable",
                  auth_captcha_required=True, web_origins="https://app.example")
    assert isinstance(rt.auth_facade, AuthFacade)
    assert rt.auth_facade.apikey == "anon-publishable" != rt.settings.supabase_key
    assert rt.auth_facade.captcha_required and rt.auth_facade.origins == ("https://app.example",)
    assert {"/auth/v1/sign-in", "/auth/v1/availability"} <= paths(rt)
    assert "anon-publishable" not in repr(rt.settings.deployment)


def test_wr_ap01__adapters_from_env_builds_the_identity_store_only_when_enabled():
    from infrx.console.session import PgIdentity
    from infrx.lab.access import LabAccess

    async def connect():
        raise AssertionError("nothing connects while composing")
    off = support.settings()
    on = support.settings(deployment=off.deployment.replace(identity_api=True))
    assert pilot._identity(off, connect) == {}
    built = pilot._identity(on, connect)
    assert isinstance(built["identity"], PgIdentity) and isinstance(built["lab_access"],
                                                                      LabAccess)
