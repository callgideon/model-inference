"""L3-SQL: the Lab's control plane over 0007's registry, through the named RPCs of
`0032_lab_control.sql` (LAB-PUBLISH, the SQL half). No parallel catalog: revisions are
`deployment_revisions`, discovery is `catalog_listings` (read by `catalog.py`), and rows are
written by A3's `operations.PgRegistry`.

* `move` - the provider moves its own private revisions (draft -> validating -> ready_private
  or retired) and withdraws a proposal; public drains and retirements are an operator's.
* `approve` - an operator activates a proposed public revision whose serving version has a
  validated dev revision, as the alias's next listing version at an effective card of it.
* `rollback` - an operator lists an earlier, still active revision of the alias again (a new
  listing version; admitted jobs keep their pins).
* `open_dev_wallet` - the provider's provider_dev CREDIT wallet at 0; money enters only by
  `infrx.grant_credit` operator_allocation (D5, audited).

Every move is audited (`history`). `provider_org_id` and `operator` are the server's (the L2
port, the operator session), never request fields. Nothing composes this store yet (L3 does).
"""
from __future__ import annotations

from typing import Any

from .jobstore import Connect
from .lab_data import PgLabDataStore


class PgLabControlStore:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def move(self, deployment_revision_id: str, state: str, *, provider_org_id: str,
                   actor: str, reason: str, operator: bool = False) -> dict[str, Any]:
        return await self._call("lab_move_deployment", {
            "provider_org_id": provider_org_id, "deployment_revision_id": deployment_revision_id,
            "state": state, "actor": actor, "operator": operator, "reason": reason})

    async def approve(self, deployment_revision_id: str, *, provider_org_id: str,
                      public_model_id: str, rate_card_version: str, actor: str,
                      reason: str) -> dict[str, Any]:
        """An operator's approval (the caller has verified the operator session)."""
        return await self._call("lab_approve_publication", {
            "provider_org_id": provider_org_id, "deployment_revision_id": deployment_revision_id,
            "public_model_id": public_model_id, "rate_card_version": rate_card_version,
            "actor": actor, "operator": True, "reason": reason})

    async def rollback(self, public_model_id: str, deployment_revision_id: str, *, actor: str,
                       reason: str) -> dict[str, Any]:
        """An operator's rollback of the alias to an earlier, still active revision."""
        return await self._call("lab_rollback_publication", {
            "public_model_id": public_model_id, "deployment_revision_id": deployment_revision_id,
            "actor": actor, "operator": True, "reason": reason})

    async def open_dev_wallet(self, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_open_dev_wallet", {"provider_org_id": provider_org_id})

    async def history(self, provider_org_id: str,
                      deployment_revision_id: str | None = None) -> list[dict[str, Any]]:
        return await self._call("lab_control_history", {
            "provider_org_id": provider_org_id, "deployment_revision_id": deployment_revision_id})
