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

import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured, Settings, validate_runtime
from infrx.contracts import errors, wire
from infrx.gateway import app as composition
from infrx.gateway.routes import chat, health, ingress, jobs, models, uploads
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


UPLOAD_ROUTES = {("POST", "/v1/uploads"), ("PUT", "/v1/uploads/{handle}"),
                 ("POST", "/v1/uploads/{handle}/complete")}


def test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only():
    """The cutover (r1 R44): `ROUTERS` is (health, models, ingress, uploads, jobs, metrics) -
    `health` stays, Caddy proxies the public `/health` to it; uploads and jobs after the
    ingress, over the store and relay its composition made (G3/G4U request (a)); I3B's
    loopback-only /metrics last - the one chat handler is the ingress's, every upload and jobs
    route is its own router's, and nothing FastAPI would publish by itself (docs, schema,
    slash redirects) is served."""
    assert composition.ROUTERS == (health, models, ingress, uploads, jobs, metrics)
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


def test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as():
    """E4B's served-build check: /metrics (loopback only) carries
    `infrx_build_info{revision, image} 1` from the settings the installer wrote
    (`INFRX_RELEASE_SHA`, `INFRX_IMAGE`), set at startup and never read from git at runtime.
    A pilot without either refuses to start, naming it; a malformed one is refused as a
    deployment value; dev without them starts with no gauge."""
    import dataclasses

    body = local(pilot_app()).get("/metrics").text
    build = (f'infrx_build_info{{process="gateway",revision="{support.RELEASE}",'
             f'image="{support.IMAGE}"}} 1')
    assert build in body, body
    assert TestClient(pilot_app()).get("/metrics").status_code == 404     # never public
    for name in ("infrx_release_sha", "infrx_image"):
        config = support.settings()
        config.deployment = dataclasses.replace(config.deployment, **{name: ""})
        with pytest.raises(RuntimeMisconfigured, match=f"requires {name.upper()}"):
            pilot_app(config)
        config.deployment = dataclasses.replace(support.BUILD, **{name: "c0ffee"})
        with pytest.raises(RuntimeMisconfigured, match=f"{name.upper()} must be"):
            pilot_app(config)
    dev = support.settings("dev", deployment=support.BUILD.replace(infrx_release_sha="",
                                                                   infrx_image=""))
    assert "infrx_build_info{" not in local(pilot_app(dev)).get("/metrics").text


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
