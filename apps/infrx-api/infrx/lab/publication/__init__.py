"""WR-AS3-2 (AP-06): `PgDevCredentials`, the `DevCredentials` port of
`gateway.routes.operator_publication` over 0068's three doors - the provider_dev keys of a
provider's own dev endpoint (`infrx.lab_control_dev_keys`, never the hash), their one-way
revocation (`infrx.lab_control_revoke_dev_key`: a replay answers the first `revoked_at`) and
the dev wallet (`infrx.lab_control_dev_wallet`, CREDIT as exact text). EXECUTE: `service_role`
and the control unit's login `infrx_lab_control`. Refusals are the doors' own (`not_found` for
another provider's endpoint or key, the prod endpoint); a malformed id is `not_found` here,
before any statement.
"""
from __future__ import annotations

from typing import Any

from ...contracts import api, errors
from ...gateway.routes.operator_publication import DevKey, DevWallet
from ...state import rpc
from ...state.jobstore import Connect, domain_error
from ...state.lab_control import _uuid


class PgDevCredentials:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _call(self, function: str, args: dict[str, Any]) -> Any:
        if not all(_uuid(v) for k, v in args.items() if k.endswith("_id")):
            raise errors.NotFound("no such dev endpoint or credential")
        return await rpc.call(self._connect, function, args, error=domain_error)

    async def keys(self, provider_org_id: str, endpoint_id: str) -> list[DevKey]:
        return [DevKey.model_validate(row) for row in await self._call(
            "lab_control_dev_keys", {"provider_org_id": provider_org_id,
                                     "endpoint_id": endpoint_id})]

    async def revoke(self, provider_org_id: str, endpoint_id: str, key_id: str, *,
                     actor: str, idempotency_key: str | None) -> DevKey:
        return DevKey.model_validate(await self._call("lab_control_revoke_dev_key", {
            "provider_org_id": provider_org_id, "endpoint_id": endpoint_id, "key_id": key_id,
            "actor": actor, "idempotency_key": idempotency_key}))

    async def wallet(self, provider_org_id: str) -> DevWallet:
        row = await self._call("lab_control_dev_wallet", {"provider_org_id": provider_org_id})
        return DevWallet(provider_org_id=provider_org_id, opened=row["opened"],
                         balance=api.Money(amount=row["balance"], unit="CREDIT"))
