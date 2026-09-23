#!/usr/bin/env python3
"""API-AUTH / SPLIT-CONTRACT (G1R item 1): a credential's audience and identity come from
its key row, and only from it.

`auth_context` is the one derivation (the operations surface, G6B, is asked to build its
sessions with it too). The resolver is driven directly, as in `test_auth`.
"""
import asyncio

import httpx
import pytest

from infrx.auth.context import KEY_COLUMNS, AuthResolver
from infrx.contracts import errors
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.records import CredentialAudience

from . import support

IDS = v2fix.IDS
CREATOR = "5e5e5e5e-0000-4000-8000-000000000005"
# 0009's rows, one per audience. The legacy consumer key predates `user_id`: its
# individual is the member who created it, as D2's `coalesce(user_id, created_by)` reads.
CONSUMER = {**support.ROW, "created_by": CREATOR}
LEGACY_CONSUMER = {**support.ROW, "user_id": None, "created_by": CREATOR}
PROVIDER = support.PROVIDER_ROW
OPERATOR = {**support.OPERATOR_ROW, "created_by": CREATOR}


class Req:
    headers = {"authorization": f"Bearer {support.TOKEN}"}


def context_of(row, seen=None):
    rt = support.runtime(sb=support.supabase(rows=(row,), seen=seen))
    return asyncio.run(AuthResolver(rt).context(Req()))


def test_api_auth__each_audience_carries_only_its_own_identity():
    """A consumer key is its individual (the wallet owner, R66); a provider dev key is
    its provider and private endpoint (R70) and no individual; an operator key is none
    of these. The row's other columns never leak into another audience's scope."""
    consumer = context_of(CONSUMER)
    assert (consumer.audience, consumer.user_id) == (CredentialAudience.consumer, support.USER)
    assert (consumer.provider_org_id, consumer.endpoint_id) == (None, None)
    assert (consumer.org_id, consumer.key_id, consumer.principal) == (
        support.ORG, support.KEY, support.KEY)
    assert context_of(LEGACY_CONSUMER).user_id == CREATOR
    provider = context_of(PROVIDER)
    assert provider.audience is CredentialAudience.provider_dev
    assert (provider.provider_org_id, provider.endpoint_id) == (IDS.provider_org,
                                                                IDS.dev_endpoint)
    assert provider.user_id is None                  # its creator is not a wallet owner
    operator = context_of(OPERATOR)
    assert operator.audience is CredentialAudience.operator and operator.is_operator
    assert operator.user_id is None


def test_api_auth__the_ingress_reads_the_0009_columns_and_the_legacy_route_does_not():
    """The pilot ingress asks for the audience and scope columns; the legacy route keeps
    F1's three, which every deployed schema has (hosted runs 0001-0002 today)."""
    seen = []
    context_of(CONSUMER, seen=seen)
    assert "select=" + KEY_COLUMNS.replace(",", "%2C") in seen[0]
    for column in ("audience", "user_id", "created_by", "provider_org_id", "endpoint_id"):
        assert column in KEY_COLUMNS
    rt = support.runtime(sb=support.supabase(seen=seen))
    asyncio.run(rt.auth.authenticate(Req()))
    assert seen[-1].endswith("select=id%2Corg_id%2Crevoked_at")


UNUSABLE = (("no audience", {k: v for k, v in CONSUMER.items() if k != "audience"},
             errors.InvalidApiKey),
            ("an unknown audience", {**CONSUMER, "audience": "admin"}, errors.InvalidApiKey),
            ("a consumer key naming nobody", {**LEGACY_CONSUMER, "created_by": None},
             errors.InvalidApiKey),
            ("a provider key with no endpoint", {**PROVIDER, "endpoint_id": None},
             errors.InternalError),
            # 0009's CHECK forbids it; if a row ever carries it, it is refused, not scrubbed.
            ("a consumer key with a provider scope", {**CONSUMER, "provider_org_id": IDS.provider_org,
                                                      "endpoint_id": IDS.dev_endpoint},
             errors.InternalError))


@pytest.mark.parametrize("name,row,error", UNUSABLE, ids=[u[0] for u in UNUSABLE])
def test_api_auth__a_row_without_a_usable_audience_is_not_a_credential(name, row, error):
    """Typed, and without the row's values: an unknown audience or a consumer key with no
    individual is not a credential; a provider row missing its scope is our data."""
    with pytest.raises(error) as raised:
        context_of(row)
    assert "admin" not in str(raised.value) and CREATOR not in str(raised.value)


def projecting(row, seen):
    """A PostgREST stand-in that answers exactly the columns `select` names."""
    def handler(request):
        columns = request.url.params["select"].split(",")
        seen.append(columns)
        return httpx.Response(200, json=[{column: row.get(column) for column in columns}])

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


def test_api_auth__a_row_cached_by_the_legacy_select_is_not_an_identity_without_audience():
    """One Runtime, two readers: F1's three-column read caches the key first; the ingress
    must look it up again with its own columns, not answer 401 until KEY_TTL."""
    seen = []
    rt = support.runtime(sb=projecting(CONSUMER, seen))
    row, status = asyncio.run(rt.auth.authenticate(Req()))
    assert status is None and "audience" not in row
    auth = asyncio.run(AuthResolver(rt).context(Req()))
    assert (auth.audience, auth.user_id) == (CredentialAudience.consumer, support.USER)
    assert len(seen) == 2 and "audience" in seen[1]
