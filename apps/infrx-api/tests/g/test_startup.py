#!/usr/bin/env python3
"""`M-FAILCLOSED`: what `pilot` refuses to start without, what health says, and what
the composition root does since the G2 cutover.

`O-FAILOPEN` is the hazard these cases exist for: an install run that loses a
parameter read must not be able to publish an ingress that authenticates nobody and
meters nothing. Two independent refusals cover it - `config.validate_runtime` at the
composition root, and this router refusing to register - and the last group pins
that the composition root serves chat through the metered ingress alone.
"""
import asyncio
import functools
import pathlib

import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured, Settings, validate_runtime
from infrx.contracts import errors, wire
from infrx.gateway import app as composition
from infrx.gateway.routes import (chat, feedback, health, ingress, jobs, lab_checkpoints,
                                  lab_control, lab_datasets, lab_evaluations, lab_pipelines,
                                  lab_releases, lab_traces, models, trace_export, uploads)
from infrx.observe import host
from infrx.observe import route as metrics
from infrx.scheduling.memory import MemoryScheduler

from . import relay_support

from . import support

BOTH_OK = {"price_source": lambda: True, "journal": lambda: True}
# G2 item 5: readiness answers the host's own direct loopback probe (I2B's `wait_ready`).
LOOPBACK = ("127.0.0.1", 50000)


def local(app):
    """A client that is a direct loopback peer, as `deploy/lib.sh wait_ready` is."""
    return TestClient(app, client=LOOPBACK)


def boom():
    raise ConnectionError("price_versions at postgresql://infrx:pw@db/infrx is unreachable")


REFUSALS = (("no probes at all", {}, ("price_source", "journal")),
            ("no journal probe", {"price_source": lambda: True}, ("journal",)),
            ("price source answers no", {**BOTH_OK, "price_source": lambda: False},
             ("price_source",)),
            ("journal probe raises", {**BOTH_OK, "journal": boom}, ("journal",)))


@pytest.mark.parametrize("name,checks,named", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_f_base__pilot_refuses_to_start_when_a_component_is_unreachable(name, checks, named):
    """A missing probe is not a passing probe, and a probe that raises is a failure
    whose exception text stays in the log."""
    with pytest.raises(RuntimeMisconfigured) as raised:
        support.cutover_app(ingress_deps=support.deps(checks=checks))
    message = str(raised.value)
    for component in named:
        assert component in message
    assert "postgresql" not in message and "pw@db" not in message


def test_f_base__dev_starts_with_unreachable_components_and_says_so():
    """`dev` is explicitly permissive (infra/README.md): it starts, and readiness is
    where the truth is - not a silent 200."""
    app, mounted = support.cutover_app(support.settings("dev"),
                                       ingress_deps=support.deps(checks={}))
    assert mounted.startup_state == {"price_source": "unavailable", "journal": "unavailable"}
    response = local(app).get(support.READY_PATH)
    assert response.status_code == 503, response.text
    error = support.error_of(response)
    assert error["code"] == "dependency_unavailable"
    assert error["infrx"]["components"] == {"price_source": "unavailable",
                                            "journal": "unavailable"}


def test_f_base__readiness_explains_component_state_to_a_direct_loopback_peer():
    """G2 item 5: I2B's `deploy/lib.sh wait_ready` polls `127.0.0.1:8001/readyz` with no key
    and needs a 2xx, so the host's own direct probe gets component state - no tenant key."""
    app, mounted = support.cutover_app()
    assert mounted.startup_state == {"price_source": "ok", "journal": "ok"}
    response = local(app).get(support.READY_PATH)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "mode": "pilot",
                               "components": {"price_source": "ok", "journal": "ok"}}
    assert response.headers[wire.HEADER_INFERENCE_ID]


def test_dur_rls__readiness_is_protected():
    """Component state is operational detail, for the host only: any other peer - a valid
    tenant key included - and a loopback peer the edge relayed (a proxy header) get the
    unknown-path answer, the same 404 the edge gives `/readyz` (observe/route.py's rule)."""
    app, _ = support.cutover_app()
    for client, headers in ((TestClient(app), support.AUTH),
                            (local(app), {"X-Forwarded-For": "203.0.113.9"}),
                            (local(app), {"Via": "1.1 caddy"})):
        response = client.get(support.READY_PATH, headers=headers)
        assert response.status_code == 404, response.text
        assert support.error_of(response)["code"] == "not_found"
        assert "components" not in response.text


def test_f_base__public_health_is_generic():
    """No component state, no counters, no mode: a public liveness probe is not a
    reconnaissance endpoint."""
    app, _ = support.cutover_app(support.settings("dev"),
                                 ingress_deps=support.deps(checks={}))
    response = TestClient(app).get(support.HEALTH_PATH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_f_base__a_readiness_probe_that_raises_is_unavailable_not_a_500():
    app, _ = support.cutover_app(support.settings("dev"),
                                 ingress_deps=support.deps(checks={**BOTH_OK,
                                                                         "journal": boom}))
    response = local(app).get(support.READY_PATH)
    assert response.status_code == 503
    assert support.error_of(response)["infrx"]["components"]["journal"] == "unavailable"
    assert "postgresql" not in response.text


# --- the composition root, since the G2 cutover ------------------------------------
def pilot_app(config=None, routers=None):
    """`create_app` as the cutover composes it, over the contract fakes for its adapters
    (`routers`, if given, is `ROUTERS` while it runs)."""
    from unittest import mock

    world = relay_support.World()
    world.stream.usage = lambda: asyncio.sleep(0, {})
    with mock.patch.object(composition, "ROUTERS", routers or composition.ROUTERS):
        return composition.create_app(
            config if config is not None else support.settings(), client=support.upstream(),
            sb=support.supabase(), clock=world.now_s, catalog=world.catalog,
            stream=world.stream, objects=world.objects, jobs=world.jobs,
            index=MemoryScheduler(world.clock.now))



def test_f_base__pilot_refuses_to_start_past_the_approved_release_profile():
    """G7 WR-1: `create_app` refuses a pilot whose enforced profile exceeds the approved
    release profile (profile v1's 120 s > P-20's 82 s), naming the setting; the code
    default (82 s) starts. Oracle: without the refusal a pilot at 120 s would admit
    83-120 s clips the engine refuses while discovery publishes nothing."""
    with pytest.raises(RuntimeMisconfigured, match="MAX_VIDEO_SECONDS"):
        pilot_app(support.settings(max_video_seconds=120.0))
    assert pilot_app(support.settings()).state.runtime.mode == "pilot"

UPLOAD_ROUTES = {("POST", "/v1/uploads"), ("PUT", "/v1/uploads/{handle}"),
                 ("POST", "/v1/uploads/{handle}/complete")}


def test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only():
    """The cutover (r1 R44): `ROUTERS` is (health, models, ingress, uploads, jobs, metrics) -
    `health` stays, Caddy proxies the public `/health` to it; uploads and jobs after the
    ingress, over the store and relay its composition made (G3/G4U request (a)); I3B's
    loopback-only /metrics last - the one chat handler is the ingress's, every upload and jobs
    route is its own router's, and nothing FastAPI would publish by itself (docs, schema,
    slash redirects) is served."""
    assert composition.ROUTERS == (health, models, ingress, uploads, jobs, feedback, trace_export,
                                   lab_control, lab_traces, lab_evaluations, lab_pipelines,
                                   lab_releases, lab_datasets, lab_checkpoints, metrics)
    app = pilot_app()
    rt = app.state.runtime
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert {"/v1/chat/completions", "/health", "/v1/models", ingress.HEALTH_PATH,
            ingress.READY_PATH} <= paths
    assert not paths & {"/docs", "/redoc", "/openapi.json"}
    assert app.router.redirect_slashes is False
    (route,) = [r for r in app.routes if getattr(r, "path", "") == support.CHAT_PATH]
    assert route.endpoint.__module__ == ingress.__name__
    served = {(method, route.path): route.endpoint.__module__ for route in app.routes
              for method in (getattr(route, "methods", None) or ())}
    assert {served.get(key) for key in UPLOAD_ROUTES} == {uploads.__name__}
    assert {served.get(key) for key in ingress.JOBS_ROUTES} == {jobs.__name__}
    assert rt.relay.on_async is not None            # jobs' 202 hook, on the pilot's relay
    ingress.assert_route_table(app)


def test_feedback_ack__the_feedback_route_is_mounted_only_when_the_deployment_enables_it():
    """G4F (WR-G4F-1): `FEEDBACK_API` is off by default, and then no feedback route exists
    even with a service at hand (the launched API is unchanged); on, `POST /v1/feedback` is
    the feedback router's and acknowledges through the composed service."""
    import dataclasses

    from infrx.config import deployment_from_env
    from infrx.contracts.records import Feedback

    class Stub:
        async def accept(self, auth, request_id, signal, idem):
            return Feedback(feedback_id="fb_" + "Q" * 43, request_id=request_id,
                            org_id=auth.org_id, author_principal=auth.principal,
                            author_role="customer", channel="api", created_at="2026-09-27T00:00:00Z",
                            **signal)

    assert deployment_from_env({}).feedback_api is False

    def composed(on):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        config = support.settings(deployment=dataclasses.replace(support.BUILD, feedback_api=on))
        return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                      clock=world.now_s, catalog=world.catalog,
                                      stream=world.stream, objects=world.objects,
                                      jobs=world.jobs, index=MemoryScheduler(world.clock.now),
                                      feedback=Stub())

    body = {"request_id": support.REQUEST_ID, "name": "thumb", "value": True}
    headers = {**support.AUTH, "Idempotency-Key": "wr-g4f-1"}
    off = composed(False)
    assert off.state.runtime.feedback is None
    assert all(getattr(r, "path", "") != feedback.FEEDBACK_PATH for r in off.routes)
    assert local(off).post(feedback.FEEDBACK_PATH, json=body, headers=headers).status_code == 404
    on = composed(True)
    assert on.state.runtime.feedback is not None
    assert [r.methods for r in on.routes
            if getattr(r, "path", "") == feedback.FEEDBACK_PATH] == [{"POST"}]
    answer = local(on).post(feedback.FEEDBACK_PATH, json=body, headers=headers)
    assert answer.status_code == 201 and answer.json()["author_role"] == "customer"
    ingress.assert_route_table(on)


def test_trace_tenant__the_trace_export_is_mounted_only_when_the_deployment_enables_it():
    """G4T (WR-G4T-1): `TRACE_EXPORT_API` is off by default, and then no export route exists
    even with an export at hand (the launched API is unchanged); on, `GET /v1/traces` is the
    trace export router's and answers through the composed export; on without ClickHouse
    refuses to start rather than silently mounting nothing."""
    import dataclasses

    from infrx.config import deployment_from_env

    class Stub:
        async def page(self, org_id, **kw):
            return [], None

    assert deployment_from_env({}).trace_export_api is False

    def composed(on, export=Stub()):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        config = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                                 trace_export_api=on))
        return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                      clock=world.now_s, catalog=world.catalog,
                                      stream=world.stream, objects=world.objects,
                                      jobs=world.jobs, index=MemoryScheduler(world.clock.now),
                                      trace_export=export)

    off = composed(False)
    assert off.state.runtime.trace_export is None
    assert all(getattr(r, "path", "") != trace_export.EXPORT_PATH for r in off.routes)
    assert local(off).get(trace_export.EXPORT_PATH, headers=support.AUTH).status_code == 404
    on = composed(True)
    assert on.state.runtime.trace_export is not None
    assert [r.methods for r in on.routes
            if getattr(r, "path", "") == trace_export.EXPORT_PATH] == [{"GET"}]
    answer = local(on).get(trace_export.EXPORT_PATH, headers=support.AUTH)
    assert answer.status_code == 200 and answer.json() == {"data": [], "next_cursor": None}
    ingress.assert_route_table(on)
    with pytest.raises(RuntimeMisconfigured, match="CLICKHOUSE_URL"):
        composed(True, export=None)


def test_lab_access__the_lab_routes_are_mounted_only_when_the_deployment_enables_them():
    """LAB-API (WR-LAB-API-1): `LAB_CONTROL` and `LAB_TRACES` are off by default, and then no
    Lab route exists even with both surfaces composed (the launched API is unchanged); each
    switch mounts its own surface only, which answers through `lab_auth` (no session: 401)."""
    import dataclasses

    from infrx.config import deployment_from_env

    assert (deployment_from_env({}).lab_control, deployment_from_env({}).lab_traces) \
        == (False, False)
    surfaces = {"lab_control": (lab_control.LabControl(sessions=None, access=None),
                                lab_control.CONTROL_PREFIX + "/models"),
                "lab_traces": (lab_traces.LabTraces(None, None, None, None, None),
                               lab_traces.TRACES_PATH)}

    def composed(**on):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
        return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                      clock=world.now_s, catalog=world.catalog,
                                      stream=world.stream, objects=world.objects,
                                      jobs=world.jobs, index=MemoryScheduler(world.clock.now),
                                      **{name: deps for name, (deps, _) in surfaces.items()})

    for on in ({}, {"lab_control": True}, {"lab_traces": True},
               {"lab_control": True, "lab_traces": True}):
        app = composed(**on)
        paths = {getattr(r, "path", "") for r in app.routes}
        for name, (deps, path) in surfaces.items():
            enabled = on.get(name, False)
            assert (getattr(app.state.runtime, name) is deps) is enabled, (on, name)
            assert (path in paths) is enabled, (on, name)
            answer = local(app).get(path)
            assert answer.status_code == (401 if enabled else 404), (on, name)
        ingress.assert_route_table(app)


def test_lab_access__the_lab_surfaces_are_composed_from_settings_only_when_enabled(monkeypatch):
    """LAB-API (WR-LAB-API-1): off, nothing Lab is built; `LAB_CONTROL` builds the control
    over the project's auth server and L2 on the pool, with L3's operations (WR-LAB-API-2:
    `Operations` over `LabControl` on the same pool - the control store, A3's registry and
    catalog; its listings wait on WR-LSQ-9's reads, 503); `LAB_TRACES` without the trace
    projection and bucket refuses startup."""
    import dataclasses

    from infrx.gateway import pilot
    from infrx.lab.access import LabAccess

    def settings(**on):
        return support.settings(deployment=dataclasses.replace(support.BUILD, **on))

    assert pilot._lab(settings(), connect=None) == {}
    built = pilot._lab(settings(lab_control=True), connect="pool")
    assert list(built) == ["lab_control"]
    control = built["lab_control"]
    assert (str(control.sessions.client.base_url), control.sessions.apikey) \
        == ("https://fake.supabase.co", "service-role")
    assert isinstance(control.access, LabAccess)
    from infrx.lab.control.operations import Operations
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.lab_control import PgControlStore
    from infrx.state.operations import PgRegistry
    ops = control.operations
    assert type(ops) is Operations and type(ops.reads) is PgControlStore
    assert ops.reads._connect == "pool"          # WR-LSQ-9-C: the real control reads
    l3 = ops.control
    assert (type(l3.store), type(l3.registry), type(l3.catalog)) \
        == (PgControlStore, PgRegistry, PgCatalogDirectory)
    assert l3.access is control.access and l3.store._connect == "pool"
    import clickhouse_connect

    def connected(**kw):
        raise AssertionError("connected to a projection that is not configured")
    monkeypatch.setattr(clickhouse_connect, "get_client", connected)
    with pytest.raises(RuntimeMisconfigured, match="CLICKHOUSE_URL.*S3_TRACE_BUCKET"):
        pilot._lab(settings(lab_traces=True), connect=None)
    monkeypatch.setattr(pilot, "_lab_traces", lambda *args: "traces")
    assert pilot._lab(settings(lab_traces=True), connect=None) == {"lab_traces": "traces"}


def test_rollout_routing__admission_is_routed_only_when_the_deployment_enables_it():
    """R1 (WR-R1-1): `ROLLOUT_ROUTING` is off by default, and then the relay's own `accept`
    serves the ingress and the jobs route even with a router at hand (the launched API is
    unchanged); on without a router the gateway refuses to start; on, every admission goes
    through the router first - here a release-store outage, answered 503 before anything is
    admitted."""
    import dataclasses

    from infrx.config import deployment_from_env
    from infrx.gateway.routes.relay import Relay
    from infrx.rollouts import routing

    class Down:
        asked: list = []

        async def active(self, requested_model):
            self.asked.append(requested_model)
            raise ConnectionError("release store unreachable")

    assert deployment_from_env({}).rollout_routing is False

    def composed(on, router):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        world.during.append(lambda: world.clock.advance(3_600))    # a wait ends, never hangs
        config = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                                 rollout_routing=on))
        return world, composition.create_app(
            config, client=support.upstream(), sb=support.supabase(), clock=world.now_s,
            catalog=world.catalog, stream=world.stream, objects=world.objects, jobs=world.jobs,
            index=MemoryScheduler(world.clock.now), rollouts=router)

    store = Down()
    _, off = composed(False, routing.Router(store, None))
    rt = off.state.runtime
    assert getattr(rt.relay.accept, "__func__", None) is Relay.accept
    assert rt.ingress.accept == rt.relay.accept
    with pytest.raises(RuntimeMisconfigured, match="ROLLOUT_ROUTING"):
        composed(True, None)
    world, on = composed(True, routing.Router(store, None))
    assert on.state.runtime.ingress.accept is on.state.runtime.relay.accept
    answer = local(on).post(support.CHAT_PATH, json=support.BODY, headers=support.AUTH)
    assert answer.status_code == 503, answer.text
    assert answer.json()["error"]["code"] == "dependency_unavailable"
    assert store.asked == [support.PUBLIC_MODEL] and world.jobs.jobs == {}


def test_rollout_routing__the_router_is_r1_over_d9_on_the_runtime_login_only_when_on():
    """WR-R1-3-C: off (the default), the composition builds no router; on, R1's `Router` over
    D9's `PgRoutingReleases` on the gateway's own pool - only when `DATABASE_URL` logs in as
    `infrx_runtime` (bare or Supavisor's `<role>.<ref>`: SR-R1-1's functions are EXECUTE
    infrx_runtime only), never the broad/service_role login or the monitor's (refused by
    name, no credential echoed). No provider-funded shadow runner exists yet: a shadow
    duplicate is a typed 503 inside R1 (counted, never run, never charged)."""
    import dataclasses

    from infrx.contracts import errors
    from infrx.gateway import pilot
    from infrx.rollouts import routing
    from infrx.state.lab_rollout import PgRoutingReleases

    runtime = "postgresql://infrx_runtime.proj:pw-do-not-print@db.invalid:6543/postgres"

    def settings(on, dsn=runtime):
        return support.settings(database_url=dsn, deployment=dataclasses.replace(
            support.BUILD, rollout_routing=on))

    objects = relay_support.World().objects
    assert "rollouts" not in pilot.adapters_from_env(settings(False), objects=objects)
    built = pilot.adapters_from_env(settings(True), objects=objects)
    assert "rollouts" in built
    router = built["rollouts"]
    assert type(router) is routing.Router and type(router.releases) is PgRoutingReleases
    assert router.releases._connect is built["jobs"]._connect
    assert pilot._rollouts(settings(True, "postgresql://infrx_runtime@db/x"), "c")[
        "rollouts"].releases._connect == "c"
    with pytest.raises(errors.DependencyUnavailable):
        asyncio.run(router.shadows.run(None, "lab:serving:x", None))
    for login in ("postgres", "infrx_monitor", "service_role", ""):
        dsn = f"postgresql://{login}:pw-do-not-print@db.invalid:5432/postgres" if login \
            else "postgresql://db.invalid:5432/postgres"
        refused = outcome(lambda: pilot._rollouts(settings(True, dsn), "c"))
        assert type(refused) is RuntimeMisconfigured, (login, refused)
        assert "DATABASE_URL" in str(refused) and "do-not-print" not in str(refused)


LAB_2 = {"lab_evaluations": ("lab_evals", lab_evaluations.LabEvaluations,
                             lab_evaluations.EVALS_PREFIX + "/runs"),
         "lab_pipelines": ("lab_pipelines", lab_pipelines.LabPipelines,
                           lab_pipelines.PIPELINES_PREFIX + "/training-runs"),
         "lab_releases": ("lab_releases", lab_releases.LabReleases, lab_releases.RELEASES_PATH)}


def test_lab_api_2__the_lab_surfaces_are_mounted_only_when_the_deployment_enables_them():
    """LAB-API-2 (WR-LAB2-1): `LAB_EVALS`, `LAB_PIPELINES` and `LAB_RELEASES` are off by
    default, and then none of their routes exists even with every surface composed; each
    switch mounts its own surface only, which answers through `lab_auth` (no session: 401)."""
    import dataclasses

    from infrx.config import deployment_from_env

    assert [getattr(deployment_from_env({}), s) for s, _, _ in LAB_2.values()] == [False] * 3
    surfaces = {name: (switch, kind(None, None), path)
                for name, (switch, kind, path) in LAB_2.items()}

    def composed(**on):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
        return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                      clock=world.now_s, catalog=world.catalog,
                                      stream=world.stream, objects=world.objects,
                                      jobs=world.jobs, index=MemoryScheduler(world.clock.now),
                                      **{name: deps for name, (_, deps, _) in surfaces.items()})

    every = {switch: True for switch, _, _ in surfaces.values()}
    for on in ({}, *({switch: True} for switch in every), every):
        app = composed(**on)
        paths = {getattr(r, "path", "") for r in app.routes}
        for name, (switch, deps, path) in surfaces.items():
            enabled = on.get(switch, False)
            assert (getattr(app.state.runtime, name) is deps) is enabled, (on, name)
            assert (path in paths) is enabled, (on, name)
            assert local(app).get(path).status_code == (401 if enabled else 404), (on, name)
        ingress.assert_route_table(app)


def test_lab_api_2__the_lab_surfaces_are_composed_from_settings_only_when_enabled():
    """LAB-API-2 (WR-LAB2-1): off, nothing is built; each switch builds its own surface
    only, over the project's auth server and L2 on the pool, with D7 (merged) under the
    evaluation and pipeline surfaces and every port whose table is not merged absent (503)."""
    import dataclasses

    from infrx.gateway import pilot
    from infrx.lab.access import LabAccess
    from infrx.state.lab_data import PgLabDataStore

    def settings(**on):
        return support.settings(deployment=dataclasses.replace(support.BUILD, **on))

    assert pilot._lab(settings(), connect=None) == {}
    for name, (switch, kind, _) in LAB_2.items():
        built = pilot._lab(settings(**{switch: True}), connect=None)
        assert list(built) == [name]
        x = built[name]
        assert isinstance(x, kind) and isinstance(x.access, LabAccess)
        assert (str(x.sessions.client.base_url), x.sessions.apikey) \
            == ("https://fake.supabase.co", "service-role")
        ports = {f: getattr(x, f) for f in x.__dataclass_fields__
                 if f not in ("sessions", "access")}
        d7 = {"store"} if name != "lab_releases" else set()
        d8 = {"log", "ledger", "evals"} if name == "lab_pipelines" else set()   # WR-P1/P3-D8-C,
        #                                                          WR-E7L-1 (P3's evaluations)
        d9 = {"records", "proposals", "store"} if name == "lab_releases" else set()   # WR-R4-2
        assert {f for f, port in ports.items() if port is not None} == d7 | d8 | d9, name
        assert all(isinstance(ports[f], PgLabDataStore) for f in d7)
    every = pilot._lab(settings(**{s: True for s, _, _ in LAB_2.values()}), connect=None)
    assert sorted(every) == sorted(LAB_2)


def test_lab_optimizations__r3_stores_both_identities_through_d7_and_0058_on_the_pool():
    """WR-LW9-4 (R263): R3's `store` composed on the Lab pool - D7 for the variant, its B2
    report and comparison, 0058's `PgLabVariants` for both revision identities. Nothing is
    bound for the caller but the ports: it names `identities=(base, variant)` itself."""
    from infrx.gateway import pilot
    from infrx.rollouts import optimization
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_variants import PgLabVariants

    store = pilot.lab_optimizations("pool")
    assert store.func is optimization.store, store
    [data] = store.args
    assert type(data) is PgLabDataStore and data._connect == "pool"
    assert set(store.keywords) == {"variants"}, store.keywords
    variants = store.keywords["variants"]
    assert type(variants) is PgLabVariants and variants._connect == "pool"


def test_lab_releases__the_surface_is_d9_d7_the_stored_plan_and_0043s_proposals():
    """WR-R4-2: `LAB_RELEASES` composes the release read models over D9 (0048's listing of the
    three states the Lab shows, 0053's decisions), D7's policy revision and the plan its
    launcher stored beside it (WR-C5-PLAN), the proposals over 0043 (one pending per revision,
    the proposer the session's user), and D9 as the route's store - all on the gateway's
    pool and Lab objects. Nothing assigned yet (D9's Live is None): progress is null
    (WR-LIVE-PAGE: tests/g/lab_releases); the verdict is D9's latest decision; a release whose plan
    is not stored is a typed 503, never a guessed row; R3's variants are 0055's listing for the
    page's provider (WR-C6-VARIANTS)."""
    import dataclasses
    from datetime import datetime, timezone

    from infrx.contracts.lab import records as lab
    from infrx.gateway import pilot
    from infrx.lab.workers.__main__ import plan_key
    from infrx.rollouts import control as r2
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import (Decision, PgReleaseProposals, PgReleaseStore, Release,
                                         ReleaseListing)
    from infrx.state.lab_variants import PgLabVariants

    settings = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                               lab_releases=True))
    objects = relay_support.World().objects
    x = pilot._lab(settings, connect="pool", objects=objects)["lab_releases"]
    assert type(x.store) is PgReleaseStore and x.store._connect == "pool"
    assert type(x.records) is pilot.ReleaseRecords, x.records
    assert type(x.proposals) is pilot.ReleaseProposals, x.proposals
    assert type(x.records.d9) is PgReleaseStore and x.records.d9._connect == "pool"
    assert type(x.records.store) is PgLabDataStore and x.records.store._connect == "pool"
    assert x.records.objects is objects
    assert type(x.records.lab_variants) is PgLabVariants and x.records.lab_variants._connect == "pool"
    assert type(x.proposals.store) is PgReleaseProposals and x.proposals.store._connect == "pool"

    provider = "a0000000-0000-4000-8000-00000000000a"
    serving = f"lab:serving:{provider}:00000001-0000-4000-8000-000000000021@sha256:"
    record = {"schema": "lab.rollout_policy.1", "provider_org_id": provider,
              "policy_id": "00000001-0000-4000-8000-00000000006e", "version": 2,
              "created_at": "2026-09-27T10:00:00Z",
              "endpoint_id": "00000001-0000-4000-8000-00000000006f",
              "baseline_ref": serving + "f" * 64, "mode": "canary", "cohort": "session",
              "candidates": [{"serving_ref": serving + "b" * 64, "weight_bp": 1_000}]}
    ref, at = lab.ref_of(record), datetime(2026, 9, 28, 9, 30, 5, tzinfo=timezone.utc)
    plan = r2.Plan(horizon_s=3_600, min_requests=100, max_error_rate=0.02, max_p99_ms=900,
                   max_skew_bp=500, min_quality_coverage=0.5, max_lag_s=120,
                   budget=lab.Amount(unit="PROVIDER_USD", value="12.50000000"),
                   protocol={"kind": "b2"})
    asked = []

    class D9:
        async def releases_in(self, states=(), *, provider_org_id):
            asked.append((tuple(states), provider_org_id))
            return [ReleaseListing(
                policy_id=record["policy_id"], provider_org_id=provider,
                endpoint_id=record["endpoint_id"], policy_ref=ref,
                release=Release(state="rolled_back", fence=3, plan_digest=r2.plan_digest(plan),
                                started_at=datetime(2026, 9, 27, 10, tzinfo=timezone.utc)),
                latest_decision=Decision(decision="rollback", reasons=("error_rate",),
                                         evidence_refs=("run:x",), decided_by="op", at=at))]

        async def live(self, policy_ref):
            return None

        async def decisions(self, *, provider_org_id):
            return [{"policy_ref": ref, "decision": "rollback", "reasons": ["error_rate"],
                     "evidence_refs": ["run:x"], "decided_by": "op",
                     "decided_at": "2026-09-28T11:30:05.123456+02:00"}]

    class D7:
        async def resolve(self, wanted, *, provider_org_id):
            assert (wanted, provider_org_id) == (ref, provider)
            return lab.parse(record)

    class Variants:
        async def variants(self, provider_org_id):
            return [{"variant_ref": "lab:variant:v"}] if provider_org_id == provider else []

    records = pilot.ReleaseRecords(D9(), D7(), objects, Variants())
    died = outcome(lambda: asyncio.run(records.releases(provider)))
    assert type(died) is errors.DependencyUnavailable and "WR-C5-PLAN" in str(died), died
    asyncio.run(objects.put_if_absent(plan_key(provider, record["policy_id"]),
                                      plan.model_dump_json().encode(), "application/json"))
    [row] = asyncio.run(records.releases(provider))
    assert asked[-1] == (("running", "approved", "rolled_back"), provider)
    assert row == {
        "policy_ref": ref, "endpoint_id": record["endpoint_id"], "version": 2,
        "baseline_ref": record["baseline_ref"], "mode": "canary", "cohort": "session",
        "candidates": record["candidates"], "state": "rolled_back", "fence": 3,
        "plan_digest": r2.plan_digest(plan),
        "plan": {"horizon_s": 3_600, "min_requests": 100, "max_error_rate": 0.02,
                 "max_p99_ms": 900, "max_skew_bp": 500, "min_quality_coverage": 0.5,
                 "max_lag_s": 120, "budget": {"amount": "12.50000000", "unit": "PROVIDER_USD"}},
        "started_at": "2026-09-27T10:00:00Z", "progress": None,
        "verdict": {"action": "rollback", "reasons": ["error_rate"], "evidence_refs": ["run:x"],
                    "evaluated_at": "2026-09-28T09:30:05Z"}}
    assert asyncio.run(records.decisions(provider)) == [
        {"policy_ref": ref, "decision": "rollback", "reasons": ["error_rate"],
         "evidence_refs": ["run:x"], "decided_by": "op", "decided_at": "2026-09-28T09:30:05Z"}]
    assert asyncio.run(records.variants(provider)) == [{"variant_ref": "lab:variant:v"}]

    made = []

    class Proposals:
        async def propose(self, policy_ref, **kw):
            made.append((policy_ref, kw))
            return {"proposal_id": kw["proposal_id"], "policy_ref": policy_ref,
                    "kind": kw["kind"], "fence": kw["fence"], "state": "proposed",
                    "proposed_by": kw["proposed_by"],
                    "proposed_at": "2026-09-28T09:30:05.5+00:00", "decided_by": None,
                    "decided_at": None}

        async def proposals(self, *, provider_org_id):
            return [{"proposal_id": "p", "policy_ref": ref, "kind": "rollback", "fence": 3,
                     "state": "rejected", "proposed_by": "u", "decided_by": "op",
                     "proposed_at": "2026-09-28T09:30:05+00:00",
                     "decided_at": "2026-09-28T10:00:00+00:00"}] if provider_org_id == provider \
                else []

    proposals = pilot.ReleaseProposals(Proposals())
    stored = asyncio.run(proposals.add(provider, {
        "proposal_id": "q", "kind": "expand", "policy_ref": ref, "fence": 3,
        "state": "proposed", "proposed_at": "ignored", "decided_at": None,
        "proposed_by": "user-1"}))
    assert made == [(ref, {"provider_org_id": provider, "proposal_id": "q", "kind": "expand",
                           "fence": 3, "proposed_by": "user-1"})]
    assert stored == {"proposal_id": "q", "kind": "expand", "policy_ref": ref, "fence": 3,
                      "state": "proposed", "proposed_at": "2026-09-28T09:30:05Z",
                      "decided_at": None}
    assert asyncio.run(proposals.proposals(provider)) == [
        {"proposal_id": "p", "kind": "rollback", "policy_ref": ref, "fence": 3,
         "state": "rejected", "proposed_at": "2026-09-28T09:30:05Z",
         "decided_at": "2026-09-28T10:00:00Z"}]
    assert asyncio.run(proposals.proposals("b0000000-0000-4000-8000-00000000000b")) == []


def test_lab_api_2__the_pipeline_surface_is_p1_and_p3_on_d8s_ledgers(monkeypatch):
    """WR-P1-D8-C / WR-P3-D8-C: `LAB_PIPELINES` composes P1's label log (D8's `PgLabelLog`)
    and P3's run ledger (D8's `PgRunLedger`: the CAS and the named payer's PROVIDER_USD
    reservation on D6J's budget, `lab_submission`-gated in SQL) on the pool, over the Lab
    objects (R182), and P3's evaluation port over B3/B1 (WR-E7L-1). The run and checkpoint
    listings (SR-P3-1, WR-LAB2-4) are not written yet: a typed 503 each, never an
    AttributeError read as a bug."""
    import dataclasses

    from infrx.contracts import errors
    from infrx.gateway import pilot
    from infrx.state.lab_pipeline import PgLabelLog, PgRunLedger

    from infrx.evaluation import checkpoints
    production, asked = checkpoints.production_suites, []
    monkeypatch.setattr(checkpoints, "production_suites",
                        lambda connect: (asked.append(connect), production(connect))[1])
    settings = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                               lab_pipelines=True))
    objects = relay_support.World().objects
    x = pilot._lab(settings, connect="pool", objects=objects)["lab_pipelines"]
    assert type(x.log) is PgLabelLog and x.log._connect == "pool"
    assert x.objects is objects and x.store._connect == "pool"
    d8 = getattr(x.ledger, "ledger", None)
    assert type(d8) is PgRunLedger and d8._connect == "pool"
    assert getattr(x.ledger.reserve, "__func__", None) is PgRunLedger.reserve   # D8's own
    for listing in (x.ledger.run_rows, x.ledger.checkpoint_rows):
        died = outcome(lambda: asyncio.run(listing("p")))
        assert type(died) is errors.DependencyUnavailable, died
    # WR-E7L-1 / WR-B3-EVALS: P3's evaluation port is B3/B1's over the same D7 store, Lab
    # objects and L2; WR-C4-B3-SUITES: its suites are the production ones on the same pool
    from infrx.evaluation.checkpoints import Evaluations
    assert type(x.evals) is Evaluations and x.evals.suites is not None and asked == ["pool"]
    assert (x.evals.store, x.evals.objects, x.evals.access) == (x.store, objects, x.access)
    assert asyncio.run(x.evals.evaluation(provider_org_id="p", checkpoint_id="c")) is None


TEACHER_FAKE = "http://127.0.0.1:57529"


def test_lab_teachers__p2_is_composed_under_lab_pipelines_only_when_lab_teachers_is_on():
    """WR-P4B-1: `LAB_TEACHERS` is off by default and then the pipeline surface has no
    teacher port (`GET teacher-batches` is a typed 503). On (beside `LAB_PIPELINES`), P2's
    `TeacherWiring` is L2's members, D8's `PgTeacherLedger` and `PgLabelLog`, D7's store, all
    on the pool, the Lab objects, P1's import, the approved rate table, the pilot settings
    (judge mode not live by default: an approval is a 503) and N2's public redaction
    (WR-P2-4), sending only to the local teacher fake `LAB_TEACHER_URL` names (P-10). On
    without `LAB_PIPELINES`, without a URL, or with any other host refuses to start by name."""
    import dataclasses
    from types import SimpleNamespace

    from infrx.config import deployment_from_env
    from infrx.datasets.versions import redact_content
    from infrx.gateway import pilot
    from infrx.judge.cost import APPROVED_RATES
    from infrx.judge.submit import HttpJudgeProvider
    from infrx.pipelines import annotations as p1
    from infrx.pipelines.teachers import TeacherWiring
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_pipeline import PgLabelLog, PgTeacherLedger

    default = deployment_from_env({})
    assert (default.lab_teachers, default.lab_teacher_url) == (False, "")

    def settings(**on):
        return support.settings(deployment=dataclasses.replace(support.BUILD, **on))

    objects, who = relay_support.World().objects, SimpleNamespace(provider_org_id="p")
    off = pilot._lab(settings(lab_pipelines=True, lab_teacher_url=TEACHER_FAKE), connect="pool",
                     objects=objects)["lab_pipelines"]
    assert off.teachers is None
    died = outcome(lambda: asyncio.run(lab_pipelines.teacher_batches(off, who)))
    assert type(died) is errors.DependencyUnavailable, died
    on = settings(lab_pipelines=True, lab_teachers=True, lab_teacher_url=TEACHER_FAKE + "/")
    t = pilot._lab(on, connect="pool", objects=objects)["lab_pipelines"].teachers
    assert type(t) is TeacherWiring
    stores = (t.members, t.ledger, t.store, t.log)
    assert [type(x) for x in stores] == [PgAccessStore, PgTeacherLedger, PgLabDataStore,
                                         PgLabelLog]
    assert {x._connect for x in stores} == {"pool"}
    assert (t.objects, t.labels, t.rates, t.redact) == (objects, p1.import_labels,
                                                        APPROVED_RATES, redact_content)
    assert t.settings is on.pilot and t.settings.judge_mode != "live"
    assert (type(t.provider), t.provider.base_url) == (HttpJudgeProvider, TEACHER_FAKE)
    for bad, name in ((settings(lab_teachers=True, lab_teacher_url=TEACHER_FAKE), "LAB_PIPELINES"),
                      (settings(lab_pipelines=True, lab_teachers=True), "LAB_TEACHER_URL"),
                      (settings(lab_pipelines=True, lab_teachers=True,
                                lab_teacher_url="https://api.teacher.example"), "LAB_TEACHER_URL")):
        refused = outcome(lambda: pilot._lab(bad, connect="pool", objects=objects))
        assert type(refused) is RuntimeMisconfigured and name in str(refused), (name, refused)
        assert "teacher.example" not in str(refused)


LAB_DATA = {"lab_datasets": ("lab_datasets", lab_datasets.LabDatasets,
                             lab_datasets.PREFIX + "/versions", "GET"),
            "lab_checkpoints": ("lab_checkpoints", lab_checkpoints.LabCheckpoints,
                                lab_checkpoints.CHECKPOINTS_PATH, "POST")}


def outcome(call):
    """What `call()` returns, or the exception it raised (a mutant's crash is compared)."""
    try:
        return call()
    except Exception as died:              # noqa: BLE001
        return died


def test_lab_data__the_datasets_and_checkpoint_routes_are_mounted_only_when_enabled():
    """Composition batch 2 (WR-N4-1, WR-B3-2): `LAB_DATASETS` and `LAB_CHECKPOINTS` are off
    by default, and then neither route exists even composed; each switch mounts its own
    surface only - the datasets as the Lab session (no session: 401), the receiver as the
    signing key (unsigned: 401)."""
    import dataclasses

    from infrx.config import deployment_from_env

    assert [getattr(deployment_from_env({}), s) for s, *_ in LAB_DATA.values()] == [False] * 2
    fields = {name: len(dataclasses.fields(kind)) for name, (_, kind, *_) in LAB_DATA.items()}
    surfaces = {name: (switch, kind(*[None] * fields[name]), path, method)
                for name, (switch, kind, path, method) in LAB_DATA.items()}

    def composed(**on):
        world = relay_support.World()
        world.stream.usage = lambda: asyncio.sleep(0, {})
        config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
        return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                      clock=world.now_s, catalog=world.catalog,
                                      stream=world.stream, objects=world.objects,
                                      jobs=world.jobs, index=MemoryScheduler(world.clock.now),
                                      **{name: deps for name, (_, deps, _, _) in surfaces.items()})

    every = {switch: True for switch, *_ in surfaces.values()}
    for on in ({}, *({switch: True} for switch in every), every):
        app = composed(**on)
        paths = {getattr(r, "path", "") for r in app.routes}
        for name, (switch, deps, path, method) in surfaces.items():
            enabled = on.get(switch, False)
            assert (getattr(app.state.runtime, name) is deps) is enabled, (on, name)
            assert (path in paths) is enabled, (on, name)
            url = path.replace("{provider}", FakeProvider)
            answer = local(app).request(method, url, content=b"{}",
                                        headers={"content-type": "application/json"})
            assert answer.status_code == (401 if enabled else 404), (on, name, answer.text)
        ingress.assert_route_table(app)


FakeProvider = "a0000000-0000-4000-8000-00000000000a"


def test_lab_data__the_datasets_and_checkpoint_surfaces_are_composed_only_when_enabled(
        monkeypatch):
    """Off, nothing is built. `LAB_DATASETS`: N4's surface over the project's auth server, L2
    and D7 on the pool and the gateway's own object store (the Lab objects, R182).
    `LAB_CHECKPOINTS`: B3's receiver over D8's checkpoint ledger (0042) and D7 on the pool
    with the key directory `LAB_CHECKPOINT_KEYS` names - refused by name without a valid
    one, and without the ledger in the build."""
    import dataclasses
    import sys
    import types

    from infrx.gateway import pilot
    from infrx.lab.access import LabAccess
    from infrx.state.lab_data import PgLabDataStore

    def settings(**on):
        return support.settings(deployment=dataclasses.replace(support.BUILD, **on))

    objects = object()
    assert pilot._lab(settings(), connect=None, objects=objects) == {}
    assert "lab_datasets" not in pilot._lab(settings(lab_control=True), None, objects)
    built = pilot._lab(settings(lab_datasets=True), connect="pool", objects=objects)
    assert list(built) == ["lab_datasets"]
    x = built["lab_datasets"]
    assert isinstance(x, lab_datasets.LabDatasets) and isinstance(x.access, LabAccess)
    assert (str(x.sessions.client.base_url), x.sessions.apikey) \
        == ("https://fake.supabase.co", "service-role")
    assert type(x.store) is PgLabDataStore and x.objects is objects
    from infrx.state.lab_data import PgLabImportJobs          # WR-C5-N4-ROUTE: 0051's queue
    assert type(x.jobs) is PgLabImportJobs and x.jobs._connect is x.store._connect
    assert outcome(lambda: pilot._lab_checkpoints(settings(), None)) == {}
    for keys in ("", "not a directory"):
        refused = outcome(lambda: pilot._lab_checkpoints(
            settings(lab_checkpoints=True, lab_checkpoint_keys=keys), None))
        assert type(refused) is RuntimeMisconfigured and "LAB_CHECKPOINT_KEYS" in str(refused)
    secret = "cd" * 32
    good = settings(lab_checkpoints=True, lab_checkpoint_keys='{"k": {"provider_org_id": '
                    f'"{FakeProvider}", "secret": "{secret}"}}}}')
    monkeypatch.setitem(sys.modules, "infrx.state.lab_pipeline", None)       # before #16
    refused = outcome(lambda: pilot._lab_checkpoints(good, None))
    assert type(refused) is RuntimeMisconfigured and "0042" in str(refused)
    assert secret not in str(refused)
    ledger = types.ModuleType("infrx.state.lab_pipeline")
    ledger.PgCheckpointLedger = lambda connect: ("ledger", connect)
    monkeypatch.setitem(sys.modules, "infrx.state.lab_pipeline", ledger)
    x = pilot._lab_checkpoints(good, "connect")["lab_checkpoints"]
    assert isinstance(x, lab_checkpoints.LabCheckpoints) and x.ledger == ("ledger", "connect")
    assert type(x.store) is PgLabDataStore and x.store._connect == "connect"
    assert x.keys("k") == (FakeProvider, bytes.fromhex(secret)) and x.keys("other") is None


def fixture_host(monkeypatch, root: pathlib.Path) -> None:
    """E2C (RV-12): the scrape reads a fixture procfs, not this machine's `/proc`, so a case
    about the build gauge runs on any developer host. `collect_host(proc=...)` is the seam
    host.py already exposes; the Linux reading itself is the next case's, not this one's."""
    (root / "self").mkdir(parents=True)
    (root / "meminfo").write_text("MemTotal:  4096 kB\nMemAvailable:  1024 kB\n")
    (root / "self" / "status").write_text("VmRSS:  512 kB\n")
    monkeypatch.setattr(metrics, "collect_host",
                        functools.partial(host.collect_host, proc=str(root), gpu=False))


def test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as(monkeypatch, tmp_path):
    """E4B's served-build check: /metrics (loopback only) carries
    `infrx_build_info{revision, image} 1` from the settings the installer wrote
    (`INFRX_RELEASE_SHA`, `INFRX_IMAGE`), set at startup and never read from git at runtime.
    A pilot without either refuses to start, naming it; anything but the full shape (a whole
    40-hex commit, `sha256:<64 hex>`) is refused as a deployment value in every mode; dev
    without them starts with no gauge."""
    import dataclasses
    import itertools

    from infrx.contracts.limits import MODES

    fixture_host(monkeypatch, tmp_path / "proc")
    body = local(pilot_app()).get("/metrics").text
    build = (f'infrx_build_info{{process="gateway",revision="{support.RELEASE}",'
             f'image="{support.IMAGE}"}} 1.0')
    exposed = [line for line in body.splitlines() if line.startswith("infrx_build_info{")]
    assert exposed == [build], body                          # one series, exactly these labels
    assert TestClient(pilot_app()).get("/metrics").status_code == 404     # never public
    malformed = {"infrx_release_sha": ("c0ffee", support.RELEASE[:7], support.RELEASE[:-1],
                                       support.RELEASE.upper()),
                 "infrx_image": ("c0ffee", "sha256:c0ffee", "sha256:" + "b" * 63,
                                 support.IMAGE.removeprefix("sha256:"))}
    for name, values in malformed.items():
        config = support.settings()
        config.deployment = dataclasses.replace(config.deployment, **{name: ""})
        with pytest.raises(RuntimeMisconfigured, match=f"requires {name.upper()}"):
            pilot_app(config)
        for value, mode in itertools.product(values, MODES):     # validate_deployment
            config = support.settings(mode, deployment=support.BUILD.replace(**{name: value}))
            with pytest.raises(RuntimeMisconfigured, match=f"{name.upper()} must be"):
                pilot_app(config)
    dev = support.settings("dev", deployment=support.BUILD.replace(infrx_release_sha="",
                                                                   infrx_image=""))
    assert "infrx_build_info{" not in local(pilot_app(dev)).get("/metrics").text


@pytest.mark.skipif(not pathlib.Path("/proc/meminfo").exists(),
                    reason="BLOCKED platform prerequisite: Linux procfs (/proc/meminfo); the "
                           "deployed gateway runs on Linux - tests/integration/ENVIRONMENT.md")
def test_ops_recover__metrics_read_the_linux_host_the_gateway_runs_on():
    """E2C (RV-12): production keeps the real reading. On Linux the unpatched scrape reports
    this host's memory from `/proc/meminfo` and the process's resident set from
    `/proc/self/status` - the values the HostMemoryLow alert is evaluated on."""
    samples = dict(line.rsplit(" ", 1) for line in local(pilot_app()).get("/metrics").text
                   .splitlines() if line and not line.startswith("#"))
    total = float(samples['infrx_host_memory_bytes{process="gateway",state="total"}'])
    available = float(samples['infrx_host_memory_bytes{process="gateway",state="available"}'])
    resident = float(samples['infrx_process_resident_bytes{process="gateway"}'])
    assert 0 < available <= total and 0 < resident < total, (total, available, resident)


def test_f_base__the_route_table_is_asserted_after_every_router_mounted():
    """G1R C4 at the composition root: `create_app` checks what the route table serves after
    the router loop, so a router list that puts another chat handler first - the legacy one,
    in a mode `validate_runtime`'s module check does not guard - refuses to start."""
    with pytest.raises(RuntimeMisconfigured, match="exactly one handler"):
        pilot_app(support.settings("dev"), routers=(chat,) + composition.ROUTERS)


def test_api_auth__a_pilot_never_serves_chat_through_the_legacy_route():
    """E3B dr17: in `pilot`, `create_app` refuses while `app.ROUTERS` composes the legacy
    chat route (no durable admission, no hold) - naming the router list, never a value.
    The cutover composition validates; one that mounts both still refuses (the legacy
    route would keep the path), and so does one with no metered ingress at all, which is
    I0's installer predicate. `dev` and the unset legacy mode are unaffected."""
    from unittest import mock

    config = support.settings()                     # a complete pilot configuration
    assert validate_runtime(config) == "pilot"      # the composition root is the cutover's
    assert pilot_app(config).state.runtime.mode == "pilot"
    for routers in ((health, models, chat), (health, models, chat, ingress), (health, models)):
        with mock.patch.object(composition, "ROUTERS", routers):
            with pytest.raises(RuntimeMisconfigured) as raised:
                validate_runtime(config)
            message = str(raised.value)
            assert "legacy route" in message
            assert "service-role" not in message and "infrx_g1" not in message
    assert validate_runtime(support.settings("dev")) == "dev"


def test_f_base__an_unset_mode_refuses_to_start():
    """R44's unset branch, inverted at the cutover (F2.2 carryover 14): the legacy F1
    entry point is retired, so no `INFRX_MODE` is a startup refusal naming the setting, and
    the installer never writes one (I0)."""
    with pytest.raises(RuntimeMisconfigured) as raised:
        validate_runtime(Settings())
    assert raised.value.missing == ("INFRX_MODE",)
    with pytest.raises(RuntimeMisconfigured, match="INFRX_MODE"):
        pilot_app(Settings(usage_log=support.USAGE_LOG))


# --- what FastAPI would answer by itself (review r1 item 7) ------------------------
def test_f_base__an_unknown_path_and_a_wrong_method_are_envelopes():
    """Without `install_error_handlers` these are FastAPI's `{"detail": …}`: no code,
    no request id, and nothing a client can branch on."""
    app, _ = support.cutover_app()
    tc = TestClient(app)
    # A wrong method answers `not_found` too: the alternative confirms the path
    # exists, and 405 is not in the contract's status table.
    for response, status, code in ((tc.get("/v1/nope"), 404, "not_found"),
                                  (tc.get(support.CHAT_PATH), 404, "not_found"),
                                  (tc.post(support.READY_PATH), 404, "not_found")):
        assert response.status_code == status, response.text
        error = support.error_of(response)
        assert error["code"] == code, error
        assert error["request_id"] == response.headers[wire.HEADER_INFERENCE_ID]
        assert error["message"] == errors.MESSAGES[code]


def test_f_base__an_unhandled_error_outside_a_route_is_still_an_envelope():
    """The app-level handler, not the per-route guard: a dependency raising before the
    handler runs must not produce a bare 500."""
    app, _ = support.cutover_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("postgresql://infrx:service-role@db/infrx is unreachable")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500, response.text
    error = support.error_of(response)
    assert error["code"] == "internal_error"
    assert "postgresql" not in response.text and "service-role" not in response.text
    assert response.headers[wire.HEADER_INFERENCE_ID]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)


# --- review r2 B3 and the same-pass list -------------------------------------------
def test_f_base__register_reads_its_deps_from_the_runtime():
    """The cutover hook itself: `ROUTERS` calls `register(app, rt)` with two arguments,
    so the deps have to arrive on the runtime. Nothing tested that before."""
    from fastapi import FastAPI

    rt = support.runtime(support.settings("dev"))
    calls, accept = support.recorder()
    rt.ingress = support.deps(accept=accept)
    app = FastAPI()
    app.state.runtime = rt
    mounted = ingress.register(app, rt)          # exactly the ROUTERS protocol
    assert mounted.deps is rt.ingress
    assert TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                json=support.BODY).status_code == 202
    assert len(calls) == 1


def test_f_base__the_ingress_refuses_to_start_without_a_catalog():
    """G1R: a model name means only what the trusted catalog says. With no catalog the
    ingress could only guess (the old served-map fallback copied the name through), so
    it refuses to register, in every mode (an unset one refuses before, since the cutover)."""
    for mode in ("pilot", "dev", "test"):
        with pytest.raises(RuntimeMisconfigured) as raised:
            support.cutover_app(support.settings(mode), ingress_deps=support.deps(catalog=None))
        assert "catalog" in str(raised.value)


def test_dur_rls__a_client_that_disconnects_mid_body_is_not_a_server_error(caplog):
    """No 500, and no stack trace in the log: nothing is wrong with the server and
    nobody is listening anyway."""
    from starlette.requests import ClientDisconnect

    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    sent = []

    async def receive():
        raise ClientDisconnect()

    with caplog.at_level("INFO", logger="infrx.gateway"):
        asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                         "method": "POST", "path": support.CHAT_PATH,
                         "raw_path": support.CHAT_PATH.encode(), "query_string": b"",
                         "root_path": "", "scheme": "http", "client": ("127.0.0.1", 1),
                         "server": ("t", 80),
                         "headers": [(key.encode(), value.encode())
                                     for key, value in support.RAW.items()]},
                        receive, lambda message: sent.append(message) or asyncio.sleep(0)))
    assert sent[0]["status"] == 400, sent[0]
    assert "Traceback" not in caplog.text
    assert calls == []


def test_f_base__a_refusal_before_the_body_closes_the_connection():
    """An endless chunked body would otherwise hold the socket until a proxy gives up."""
    app, _ = support.cutover_app(support.settings("dev", supabase_url="", supabase_key=""),
                                 ingress_deps=support.deps())
    unauthenticated = TestClient(app).post(support.CHAT_PATH, headers=support.RAW, content=b"{}")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["connection"] == "close"

    app, _ = support.cutover_app(support.settings(max_request_bytes=8),
                                 ingress_deps=support.deps())
    oversized = TestClient(app).post(support.CHAT_PATH, headers=support.RAW, content=b"x" * 64)
    assert oversized.status_code == 413
    assert oversized.headers["connection"] == "close"


def test_f_base__a_probe_that_answers_falsely_is_unavailable():
    """Not "callable and did not raise": the answer itself has to be true."""
    for falsy in (False, None, 0, ""):
        state = ingress.component_state({"price_source": lambda: falsy, "journal": lambda: True})
        assert state["price_source"] == "unavailable", falsy
    assert ingress.component_state({"price_source": lambda: True,
                                    "journal": lambda: True}) == {"price_source": "ok",
                                                                  "journal": "ok"}


def test_f_base__a_request_id_source_that_misbehaves_never_reaches_a_header():
    """The id is ours, but it lands in a header, so a raising or CRLF-bearing source is
    replaced rather than trusted."""
    for bad in (lambda: (_ for _ in ()).throw(RuntimeError("no id for you")),
                lambda: "4d4d4d4d-0000-4000-8000-000000000004\r\nX-Evil: 1",
                lambda: 17):
        app, _ = support.cutover_app(ingress_deps=support.deps(new_request_id=bad))
        response = local(app).get(support.READY_PATH)
        assert response.status_code == 200, response.text[:120]
        minted = response.headers[wire.HEADER_INFERENCE_ID]
        assert "\r" not in minted and "\n" not in minted and len(minted) == 36


# --- W6 api-L1 (A1/A9): the composition roots ----------------------------------------------
#: What `pilot` re-exports from `infrx.lab.compose` (tests and the e2e worlds import them).
LAB_COMPOSITIONS = ("_lab", "_lab_2", "_lab_checkpoints", "_lab_traces", "_teachers",
                    "RunLedger", "lab_releases", "lab_optimizations", "lab_control",
                    "lab_operations", "control_serving", "SHOWN", "_z", "_progress",
                    "ReportUnavailable", "ReleaseRecords", "ReleaseProposals")


def test_composition_root__the_lab_compositions_are_lab_compose_and_form_no_cycle():
    """A1: the Lab compositions live in `infrx.lab.compose`; `pilot` keeps the consumer ones
    and re-exports the Lab names (the same objects). Neither the gateway nor `compose` imports
    a `__main__` module, and no Lab module imports `gateway.pilot` back - the cycle the audit
    found. Oracle: compose missing, a re-export that is a copy, or any of those imports
    restored goes red."""
    import os
    import subprocess
    import sys

    import infrx
    root = pathlib.Path(infrx.__file__).parent
    imported = subprocess.run(
        [sys.executable, "-c", "import infrx.gateway.pilot, infrx.lab.compose, "
                               "infrx.lab.control.app, infrx.lab.workers.__main__"],
        cwd=root.parent, env={**os.environ, "PYTHONPATH": str(root.parent)},
        capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr[-400:]
    for path in ("gateway/pilot.py", "lab/compose.py"):
        assert "workers.__main__" not in (root / path).read_text(), path
    for path in ("lab/control/app.py", "lab/workers/__main__.py", "worker/__main__.py",
                 "operations/cli.py", "lab/compose.py"):
        assert "gateway.pilot import" not in (root / path).read_text(), path
    from infrx.gateway import pilot
    from infrx.lab import compose
    assert [n for n in LAB_COMPOSITIONS
            if getattr(pilot, n, None) is not getattr(compose, n, False)] == []


def test_composition_root__one_dsn_login_parser():
    """A9: one parser of a DSN's login (`jobstore.login_user`): bare, or Supavisor's
    `<role>.<project-ref>`; `dedicated_login` and R1's `infrx_runtime` check both read it.
    Oracle: the project ref taken for the role, or a missing user crashing, goes red."""
    from infrx.gateway import pilot
    from infrx.state import jobstore
    assert [jobstore.login_user(dsn) for dsn in (
        "postgresql://infrx_runtime.abcdef:pw@db.example:6543/postgres",
        "postgresql://infrx_monitor@db.example/postgres", "host=db.example dbname=x",
        "postgresql://postgres.abcdef:pw@db.example:5432/postgres")] \
        == ["infrx_runtime", "infrx_monitor", "", "postgres"]
    assert pilot.dedicated_login is jobstore.dedicated_login
    assert [jobstore.dedicated_login(dsn) for dsn in (
        "postgresql://infrx_runtime.abcdef@h/d", "postgresql://infrx_monitor@h/d",
        "postgresql://postgres.abcdef@h/d", "postgresql://h/d")] == [True, True, False, False]
