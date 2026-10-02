"""WR-AP09L-2 (AP-09 09c): the Lab control unit mounts the auth facade `/auth/v1/*` on its own
publishable key, only with AUTH_FACADE (default off); WEB_ORIGINS names the Lab's origin.

    uv run --frozen pytest -q tests/l/control/test_lab_auth_facade.py

Failure oracles: a facade route mounted with the switch off; the facade dialling anything but
`INFRX_LAB_SUPABASE_URL` (here unreachable: sign-in answers a typed 503, never a 500 or a
session); a foreign browser Origin accepted on sign-in.
"""
from __future__ import annotations

from tests.i.lab_control.test_control_routes import client, unit  # noqa: F401

FACADE = ("/auth/v1/sign-in", "/auth/v1/refresh", "/auth/v1/sign-out", "/auth/v1/callback")
SIGN_IN = {"email": "dev@example.com", "password": "correct horse battery"}


def _paths(c) -> set[str]:
    return {getattr(r, "path", None) for r in c.app.routes}


def test_wr_ap09l_2__off_by_default_no_facade_route(unit, monkeypatch):  # noqa: F811
    monkeypatch.delenv("AUTH_FACADE", raising=False)
    c = client()
    assert not _paths(c) & set(FACADE)
    assert c.post("/auth/v1/sign-in", json=SIGN_IN).status_code == 404


def test_wr_ap09l_2__auth_facade_mounts_on_the_lab_key(unit, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AUTH_FACADE", "1")
    monkeypatch.setenv("WEB_ORIGINS", "https://lab.example")
    c = client()
    assert set(FACADE) <= _paths(c)
    down = c.post("/auth/v1/sign-in", json=SIGN_IN, headers={"origin": "https://lab.example"})
    assert down.status_code == 503 and down.json()["error"]["code"] == "unavailable", down.text
    foreign = c.post("/auth/v1/sign-in", json=SIGN_IN, headers={"origin": "https://evil.example"})
    assert foreign.status_code == 403, foreign.text
