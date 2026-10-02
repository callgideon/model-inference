"""AP-03: the repository's own refusals, before any database call.

    uv run --frozen pytest -q tests/ap03/test_actions.py

The repository is a trust boundary of its own (contracts.md §1: "nonoperator refused at API
and repository"): a caller that skips the routes still cannot act as an operator, act
without a session or write an unbounded key/reason. Every case hands it a connection
factory that fails the test if it is ever dialled.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.console.actions import ConsoleActions
from infrx.contracts import api, errors

USER = "2b2b2b2b-0000-4000-8000-000000000002"
ORG = "1a1a1a1a-0000-4000-8000-000000000001"
KEY = "3c3c3c3c-0000-4000-8000-000000000003"
CONSUMER = api.Actor(audience="session", user_id=USER, org_id=ORG)
OPERATOR = CONSUMER.model_copy(update={"operator": True})


async def never():
    raise AssertionError("the repository dialled the database before refusing")


def run(coro):
    return asyncio.run(coro)


repo = ConsoleActions(never)


def test_actions__a_consumer_actor_is_refused_every_operator_write():
    """Oracle: the operator check lives only in the route (or only in SQL)."""
    for write in (repo.adjust_credit(CONSUMER, USER, "1", "r", "k"),
                  repo.set_suspension(CONSUMER, ORG, True, "r", "k"),
                  repo.revoke_key_as_operator(CONSUMER, KEY, "r", "k")):
        with pytest.raises(errors.Forbidden):
            run(write)


def test_actions__an_operator_key_is_not_an_operator_session():
    """The operator SQL authorizes the bound session subject; an operator-audience key has
    no individual to bind. Oracle: an operator-audience credential reached the database."""
    key_operator = api.Actor(audience="operator", user_id=USER, org_id=ORG, operator=True)
    with pytest.raises(errors.Forbidden):
        run(repo.set_suspension(key_operator, ORG, True, "r", "k"))


def test_actions__console_writes_need_a_session_individual_with_an_account():
    """A consumer API key, a session without an account, or no user: refused. Oracle: a
    machine credential minted keys or claimed a grant for a user it named."""
    for actor in (api.Actor(audience="consumer", user_id=USER, org_id=ORG),
                  api.Actor(audience="session", user_id=USER),
                  api.Actor(audience="session", org_id=ORG)):
        with pytest.raises(errors.Forbidden):
            run(repo.create_key(actor, "n", "k"))
        with pytest.raises(errors.Forbidden):
            run(repo.revoke_key(actor, KEY, None))
    with pytest.raises(errors.Forbidden):
        run(repo.claim_grant(api.Actor(audience="consumer", user_id=USER, org_id=ORG)))


def test_actions__keys_reasons_and_names_are_bounded():
    """Oracle: an empty or oversized idempotency key, reason or name reached the database."""
    for bad in ("", "k" * 201, "tab\there"):
        with pytest.raises(errors.InvalidRequest):
            run(repo.create_key(CONSUMER, "n", bad))
        with pytest.raises(errors.InvalidRequest):
            run(repo.adjust_credit(OPERATOR, USER, "1", "r", bad))
    for name in ("", "   ", "n" * 201):
        with pytest.raises(errors.InvalidRequest):
            run(repo.create_key(CONSUMER, name, "k"))
    for reason in ("", "  ", "r" * 501):
        with pytest.raises(errors.InvalidRequest):
            run(repo.set_suspension(OPERATOR, ORG, True, reason, "k"))


def test_actions__a_malformed_key_id_is_not_found_without_a_query():
    with pytest.raises(errors.NotFound):
        run(repo.revoke_key(CONSUMER, "not-a-uuid", None))
