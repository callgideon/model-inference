#!/usr/bin/env python3
"""`M-FAILCLOSED`: what `pilot` refuses to start without, what health says, and what
the composition root still does today.

`O-FAILOPEN` is the hazard these cases exist for: an install run that loses a
parameter read must not be able to publish an ingress that authenticates nobody and
meters nothing. Two independent refusals cover it - `config.validate_runtime` at the
composition root, and this router refusing to register - and the last group pins
that the legacy entry point is still exactly what F1 left behind.
"""
import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured, validate_runtime
from infrx.config import Settings
from infrx.contracts import wire
from infrx.gateway import app as composition
from infrx.gateway.routes import chat, health, ingress, models

from . import support

BOTH_OK = {"price_source": lambda: True, "journal": lambda: True}


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
        support.cutover_app(ingress_deps=ingress.IngressDeps(checks=checks))
    message = str(raised.value)
    for component in named:
        assert component in message
    assert "postgresql" not in message and "pw@db" not in message


def test_f_base__dev_starts_with_unreachable_components_and_says_so():
    """`dev` is explicitly permissive (infra/README.md): it starts, and readiness is
    where the truth is - not a silent 200."""
    app, mounted = support.cutover_app(support.settings("dev"),
                                       ingress_deps=ingress.IngressDeps(checks={}))
    assert mounted.startup_state == {"price_source": "unavailable", "journal": "unavailable"}
    response = TestClient(app).get(support.READY_PATH, headers=support.AUTH)
    assert response.status_code == 503, response.text
    error = support.error_of(response)
    assert error["code"] == "dependency_unavailable"
    assert error["infrx"]["components"] == {"price_source": "unavailable",
                                            "journal": "unavailable"}


def test_f_base__readiness_explains_component_state_to_an_authenticated_caller():
    app, mounted = support.cutover_app()
    assert mounted.startup_state == {"price_source": "ok", "journal": "ok"}
    response = TestClient(app).get(support.READY_PATH, headers=support.AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "mode": "pilot",
                               "components": {"price_source": "ok", "journal": "ok"}}
    assert response.headers[wire.HEADER_INFERENCE_ID]


def test_dur_rls__readiness_is_protected():
    """Component state is operational detail: it needs a tenant, and an unknown key
    gets the same 401 the ingress gives."""
    app, _ = support.cutover_app()
    response = TestClient(app).get(support.READY_PATH)
    assert response.status_code == 401, response.text
    assert support.error_of(response)["code"] == "invalid_api_key"


def test_f_base__public_health_is_generic():
    """No component state, no counters, no mode: a public liveness probe is not a
    reconnaissance endpoint."""
    app, _ = support.cutover_app(support.settings("dev"),
                                 ingress_deps=ingress.IngressDeps(checks={}))
    response = TestClient(app).get(support.HEALTH_PATH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_f_base__a_readiness_probe_that_raises_is_unavailable_not_a_500():
    app, _ = support.cutover_app(support.settings("dev"),
                                 ingress_deps=ingress.IngressDeps(checks={**BOTH_OK,
                                                                         "journal": boom}))
    response = TestClient(app).get(support.READY_PATH, headers=support.AUTH)
    assert response.status_code == 503
    assert support.error_of(response)["infrx"]["components"]["journal"] == "unavailable"
    assert "postgresql" not in response.text


# --- the composition root, unchanged until the cutover ----------------------------
def test_f_base__the_composition_root_still_mounts_only_the_legacy_routers():
    """G1 adds modules; it mounts none. The cutover integration request replaces
    `chat` with `ingress` here and nowhere else (r1 R44)."""
    assert composition.ROUTERS == (health, models, chat)
    paths = {route.path for route in support.legacy_app().routes if hasattr(route, "path")}
    assert {"/v1/chat/completions", "/health", "/v1/models"} <= paths
    assert ingress.HEALTH_PATH not in paths and ingress.READY_PATH not in paths


def test_f_base__an_unset_mode_is_still_legacy_behaviour():
    """R44's unset branch: `create_app()` with no `INFRX_MODE` logs `legacy` and
    changes nothing, and the legacy chat route still answers without a key. G1 flips
    this to a refusal at cutover, with I2's installer."""
    assert validate_runtime(Settings()) == "legacy"
    app = support.legacy_app()
    assert app.state.runtime.mode == "legacy"
    response = TestClient(app).post("/v1/chat/completions", json=support.BODY)
    assert response.status_code == 200, response.text


def test_f_base__registering_the_ingress_never_replaces_the_legacy_chat_route():
    """Mounted next to the legacy route, the legacy route keeps its path: the cutover
    is a change to `ROUTERS`, not something a track can do by also registering."""
    app = support.legacy_app()
    rt = app.state.runtime
    rt.mode = "dev"
    ingress.register(app, rt, ingress.IngressDeps(checks=BOTH_OK))
    tc = TestClient(app)
    assert tc.post("/v1/chat/completions", json=support.BODY).status_code == 200   # legacy
    assert tc.get(support.HEALTH_PATH).json() == {"status": "ok"}                  # new routes live


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
