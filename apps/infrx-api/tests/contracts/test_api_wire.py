"""R270: the shared control-route wire contracts (infrx.contracts.api, infrx.gateway.control)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrx.contracts import api, errors
from infrx.gateway import control


def test_r270_status_mapping_puts_subclasses_first():
    assert api.status_of(errors.OrgSuspended("s"))[:2] == (403, "suspended")
    assert api.status_of(errors.IdempotencyConflict("k"))[:2] == (409, "idempotency_conflict")
    assert api.status_of(errors.ResultExpired("r"))[:2] == (410, "gone")
    assert api.status_of(errors.CapacityExhausted("c")) == (429, "limited", True)
    assert api.status_of(RuntimeError("bug")) == (500, "internal", False)


def test_r270_envelope_hides_a_bug_s_message_and_keeps_a_domain_message():
    bug = api.envelope(RuntimeError("token sk-secret"), "rid")
    assert bug.error.message == "RuntimeError" and bug.error.code == "internal"
    exc = errors.NotFound("project p1 (operator detail)")
    dom = api.envelope(exc, "rid", resource_id="p1")
    assert (dom.error.code, dom.error.message, dom.error.resource_id, dom.error.retryable) == \
        (errors.NotFound.code, exc.message, "p1", False)
    assert "operator detail" not in dom.error.message


def test_r270_wire_models_refuse_extra_fields_and_floats():
    with pytest.raises(ValidationError):
        api.Actor(audience="consumer", role="admin", operator=True, is_operator=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        api.Money(amount="1.5e3", unit="CREDIT")
    assert api.Money(amount="-0.00000001", unit="PROVIDER_USD").unit == "PROVIDER_USD"
    page = api.ListPage[int](data=(1, 2), next_cursor=None)
    assert page.model_dump() == {"data": (1, 2), "next_cursor": None}
    assert api.MAX_PAGE_SIZE == 100 and api.DEFAULT_PAGE_SIZE == 25


def test_r270_error_response_is_no_store_with_the_status():
    r = control.error_response(errors.DependencyUnavailable("db"), "rid-1")
    assert r.status_code == 503 and r.headers.get("cache-control") == "no-store"
    assert b'"request_id":"rid-1"' in r.body and b'"retryable":true' in r.body
    doc = api.OperationDoc(operation_id="o", kind="deployment.create", state="queued",
                           created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z")
    a = control.accepted(doc, "/lab/v1/operations/o")
    assert a.status_code == 202 and a.headers["location"] == "/lab/v1/operations/o"


@pytest.mark.anyio
async def test_r270_static_actors_raise_the_domain_error_or_return_the_actor():
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-request-id", b"abc")]}
    req = Request(scope)
    assert control.request_id(req) == "abc"
    actor = api.Actor(audience="session", user_id="u1")
    assert await control.StaticActors(actor).actor(req) == actor
    with pytest.raises(errors.Forbidden):
        await control.StaticActors(error=errors.Forbidden("no")).actor(req)
    with pytest.raises(errors.InvalidApiKey):
        await control.StaticActors().actor(req)
