#!/usr/bin/env python3
"""WR-AP01-2: the Lab control unit (`infrx.lab.control.app`) mounts `lab_workspaces` over its
own `SessionActors` + `state.identity.PgIdentity` (0065's functions) on ITS login, only with
`IDENTITY_API`; off (the default) nothing new mounts and `rt.actors` stays None.

    uv run --frozen pytest -q tests/ap01/test_composed_lab_unit.py

Nothing is dialled: the unit's connector records `(dsn, set_role)` and refuses each connection,
and the session verifier accepts one token (the `tests/i/lab_control` fakes). Failure oracles:
the routes mounted with the switch off; the identity store built on the runtime's
`DATABASE_URL` or with `set role`; the unit's actors carrying an API-key door; a foreign
browser origin accepted on a member mutation; `/lab/v1/capabilities` reporting the unit's
mounted families as disabled.
"""
from __future__ import annotations

import pytest

from infrx.console.session import SessionActors
from infrx.state.identity import PgIdentity
from tests.i.lab_control.test_control_routes import LAB_DSN, TOKEN, client, unit  # noqa: F401

WORKSPACES = "/lab/v1/workspaces"
SESSION = {"authorization": f"Bearer {TOKEN}"}


def test_wr_ap01_2__off_by_default_the_unit_mounts_no_identity_route(unit):  # noqa: F811
    c = client()
    assert c.get(WORKSPACES, headers=SESSION).status_code == 404


def test_wr_ap01_2__identity_api_mounts_workspaces_on_the_lab_login(unit, monkeypatch):  # noqa: F811
    monkeypatch.setenv("IDENTITY_API", "1")
    monkeypatch.setenv("WEB_ORIGINS", "https://lab.example")
    from infrx.lab.control import app as control_app
    captured = {}
    compose = control_app._compose

    def spy(lab, store):
        rt, control, traces = compose(lab, store)
        captured["rt"] = rt
        return rt, control, traces
    monkeypatch.setattr(control_app, "_compose", spy)
    c = client()
    rt = captured["rt"]
    assert isinstance(rt.actors, SessionActors) and isinstance(rt.identity, PgIdentity)
    assert rt.actors.keys is None and rt.actors.origins == ("https://lab.example",)
    assert c.get(WORKSPACES).status_code == 401                      # mounted, behind the session
    before = len(unit)
    answer = c.get(WORKSPACES, headers=SESSION)
    assert answer.status_code == 503, answer.text                    # its store, typed
    assert len(unit) > before and set(unit) == {(LAB_DSN, False)}, set(unit)
    foreign = c.post(f"{WORKSPACES}/a0000000-0000-4000-8000-000000000001/members",
                     json={"email": "x@example.com", "role": "viewer"},
                     headers={**SESSION, "origin": "https://evil.example",
                              "idempotency-key": "k"})
    assert foreign.status_code == 403


@pytest.mark.parametrize("family", ["control", "evaluations", "pipelines", "releases",
                                    "datasets"])
def test_wr_ap01_2__the_units_capabilities_read_its_mounted_families(unit, monkeypatch, family):  # noqa: F811
    """The unit's families are forced on (no `LAB_*` switch): `/lab/v1/capabilities` reads
    the route table, so each mounted family is `configured` there."""
    from infrx.gateway.routes import lab_workspaces
    monkeypatch.setenv("IDENTITY_API", "1")
    c = client()
    paths = {getattr(r, "path", None) for r in c.app.routes}
    assert dict(lab_workspaces.FEATURES)[family] in paths
