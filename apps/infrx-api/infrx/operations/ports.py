"""The storage shapes G6B needs and the contracts do not yet name.

v2 already provides `WalletDirectory` and `CatalogDirectory` (read) and v1 the
`JobStore`; those are used as they are. What is missing is the *write* half an
operator needs - key rows, suspension, the A1 grant, the D5 adjustment and
reconciliation, the audit trail, immutable catalog rows - and a tenant's read of its
own usage and holds. They are Protocols here, owned by nobody yet: each one is an
integration request to D (see the G6B evidence), and D's adapter replaces the fake.

Nothing in this module can set a balance. The ledger port appends entries whose
operation id is the idempotency key; the wallet total is D's trigger, not a call.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..contracts.records import HoldState, Role
from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import (CredentialAudience, CreditLedgerEntry, DeploymentRevision,
                                    RateCardSnapshot, ServingRevision, SignupGrant,
                                    UsageHistory, WalletRef)

# `infrx.audit_entries.action` CHECK, verbatim (0003's four, extended by 0009 with the
# headless operator actions). Closed: a new operator write that fits none of these is a D
# migration first, not a new string.
AUDIT_ACTIONS = ("admin_grant", "admin_set_suspension", "admin_set_entitlements",
                 "calibration_label", "admin_key_issue", "admin_key_revoke", "admin_publish",
                 "admin_job_cancel", "admin_reconcile", "admin_adjust")

# D1 `organizations_suspension_reason_check`: a closed code, never free text (R59 (2)).
SUSPENSION_REASONS = ("abuse", "nonpayment", "security", "operator_request", "other")


@dataclasses.dataclass(frozen=True)
class VerifiedIdentity:
    """An existing individual whose verification the identity source has recorded.

    `personal_org_id` is the protected personal-org binding of `01-architecture.md`:
    the consumer org this user's keys authenticate and whose wallet they spend.
    """

    user_id: str
    personal_org_id: str
    verification_evidence_ref: str


@dataclasses.dataclass(frozen=True)
class KeyRow:
    """`public.api_keys` plus the v2 `audience` column (06a §1). Never the secret."""

    key_id: str
    org_id: str
    audience: CredentialAudience
    key_hash: str                       # sha256 hex of the whole secret, as keys.py computes
    prefix: str                         # `sk-infrx-` + 8, shown for recognition only
    name: str
    user_id: str | None = None
    role: Role = Role.service
    created_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclasses.dataclass(frozen=True)
class AuditEntry:
    """One `infrx.audit_entries` row (R34), exactly its columns."""

    id: str
    at: datetime
    actor_principal: str
    action: str
    target_org_id: str | None
    reason: str
    before: dict[str, Any] | None
    after: dict[str, Any]
    idempotency_key: str


@dataclasses.dataclass(frozen=True)
class HoldView:
    """One CREDIT hold as its owner may see it."""

    request_id: str
    state: HoldState
    amount: Credit


@runtime_checkable
class IdentityDirectory(Protocol):
    """A1/D. Verified individuals only: an unverified user answers None."""

    async def verified_user(self, user_id: str) -> VerifiedIdentity | None: ...


@runtime_checkable
class TenantStore(Protocol):
    """D. Key rows and organization suspension."""

    async def key_by_hash(self, key_hash: str) -> KeyRow | None: ...

    async def key(self, org_id: str, key_id: str) -> KeyRow | None:
        """Tenant-scoped: another organization's key id answers None."""

    async def insert_key(self, row: KeyRow) -> bool:
        """False when a row with this `key_id` already exists (a replay), never a second row."""

    async def revoke_key(self, org_id: str, key_id: str, at: datetime) -> KeyRow:
        """One-way: an already revoked row keeps its first `revoked_at`."""

    async def suspension(self, org_id: str) -> str | None:
        """The reason code when suspended, else None."""

    async def set_suspension(self, org_id: str, reason: str | None, at: datetime) -> None: ...


@runtime_checkable
class Ledger(Protocol):
    """A1 (grant) and D5 (adjustment, reconciliation). The only ways credit moves."""

    async def grant_initial(self, identity: VerifiedIdentity, operation_id: str,
                            at: datetime) -> tuple[SignupGrant, bool]:
        """A1: one transaction creates/locks the wallet, inserts the unique
        `(user_id, initial_signup_grant)` entitlement and appends +10,000 CREDIT.
        A replay - any operation id - returns the existing grant and True."""

    async def adjust(self, wallet: WalletRef, amount: Credit, operation_id: str, actor: str,
                     reason: str, at: datetime) -> tuple[CreditLedgerEntry, bool]:
        """D5: append one `operator_adjustment`. A replayed operation id returns the
        existing entry and True, and appends nothing."""

    async def reconcile(self, org_id: str, request_id: str, operation_id: str, actor: str,
                        at: datetime) -> str:
        """D5: settle an unknown-usage hold by the 24 h rule; returns the settlement
        state. Refuses before `reconcile_after` and a foreign request (NotFound)."""


@runtime_checkable
class AuditLog(Protocol):
    """D. `infrx.audit_entries`, append-only; looked up by idempotency key."""

    async def by_idempotency_key(self, key: str) -> AuditEntry | None: ...

    async def append(self, entry: AuditEntry) -> None: ...


@runtime_checkable
class Registry(Protocol):
    """D. Immutable `serving_versions`/`deployment_revisions`/`rate_cards` and the alias."""

    async def put(self, record: ServingRevision | DeploymentRevision | RateCardSnapshot) -> bool:
        """Insert. False when the identical row exists; `Conflict` when a different row
        holds the id (the rows are immutable - a new rate is a new version)."""

    async def move_alias(self, requested_model: str, deployment_revision_id: str) -> None: ...


@runtime_checkable
class AccountView(Protocol):
    """D. A tenant's own settled usage and holds, keyed by the authenticated org."""

    async def usage(self, org_id: str) -> UsageHistory: ...

    async def holds(self, org_id: str) -> tuple[HoldView, ...]: ...
