"""In-memory stand-ins for the D/A1/D5 ports `infrx/operations/ports.py` names.

Keyed the way the relations are (06a §1): keys unique by id and by hash, the grant
unique by `(user_id, initial_signup_grant)`, ledger entries unique by operation id,
catalog rows immutable. The wallet total moves only inside `_append`, the fake's
version of D's AFTER INSERT trigger - no method here sets a balance.

No secret is written in this file: operator and consumer secrets are minted at test
time by `service.new_secret()`, so there is no demo credential to leak.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.conformance.v2_fakes import (NOW, FakeCatalogDirectory,
                                                  FakeWalletDirectory, fake_v2_harness)
from infrx.contracts.fakes.factories import jobstore_factory
from infrx.contracts.records import Role
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import Credit
from infrx.operations import service
from infrx.operations.ports import AuditEntry, HoldView, KeyRow, VerifiedIdentity

USER_A = "a1000001-0000-4000-8000-000000000001"
USER_B = "b1000001-0000-4000-8000-000000000001"
USER_C = "c1000001-0000-4000-8000-000000000001"   # verified, but its wallet is bound elsewhere
UNVERIFIED = "d1000001-0000-4000-8000-000000000001"
ORG_A, ORG_B = b.ORG_A, b.ORG_B                    # the v1 JobStore's tenants
ORG_C = "c2000002-0000-4000-8000-000000000002"
OPERATOR_KEY = "e1000001-0000-4000-8000-000000000001"
OPERATOR_ORG = "e2000002-0000-4000-8000-000000000002"


@dataclasses.dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclasses.dataclass
class FakeIdentities:
    users: dict[str, VerifiedIdentity] = dataclasses.field(default_factory=dict)

    async def verified_user(self, user_id):
        return self.users.get(user_id)


@dataclasses.dataclass
class FakeTenants:
    keys: dict[str, KeyRow] = dataclasses.field(default_factory=dict)
    suspended: dict[str, str] = dataclasses.field(default_factory=dict)

    async def key_by_hash(self, key_hash):
        return next((k for k in self.keys.values() if k.key_hash == key_hash), None)

    async def key(self, org_id, key_id):
        row = self.keys.get(key_id)
        return row if row is not None and row.org_id == org_id else None

    async def insert_key(self, row):
        if row.key_id in self.keys:
            return False
        if any(k.key_hash == row.key_hash for k in self.keys.values()):
            raise errors.Conflict("key_hash is unique")
        self.keys[row.key_id] = row
        return True

    async def revoke_key(self, org_id, key_id, at):
        row = await self.key(org_id, key_id)
        if row is None:
            raise errors.NotFound("no such key")
        if row.revoked_at is None:                     # one-way, first time wins
            row = dataclasses.replace(row, revoked_at=at)
            self.keys[key_id] = row
        return row

    async def suspension(self, org_id):
        return self.suspended.get(org_id)

    async def set_suspension(self, org_id, reason, at):
        if reason is None:
            self.suspended.pop(org_id, None)
        else:
            self.suspended[org_id] = reason


@dataclasses.dataclass
class FakeLedger:
    wallets: FakeWalletDirectory
    grants: dict[str, v2.SignupGrant] = dataclasses.field(default_factory=dict)
    entries: dict[str, v2.CreditLedgerEntry] = dataclasses.field(default_factory=dict)
    # request_id -> (org_id, reconcile_after, state); D5's held_unknown rows
    unknown: dict[str, list] = dataclasses.field(default_factory=dict)
    reconciled: dict[str, str] = dataclasses.field(default_factory=dict)

    def _append(self, entry: v2.CreditLedgerEntry, owner: str) -> None:
        """The trigger: the entry and the total move together, or neither does."""
        wallet = self.wallets.by_user[owner]
        try:
            moved = wallet.model_validate({**wallet.model_dump(),
                                           "ledger_total": wallet.ledger_total + entry.amount,
                                           "revision": wallet.revision + 1})
        except ValueError:
            raise errors.InvalidRequest("the entry would take the wallet below its holds")
        self.entries[entry.operation_id] = entry
        self.wallets.by_user[owner] = moved

    async def grant_initial(self, identity, operation_id, at):
        existing = self.grants.get(identity.user_id)
        if existing is not None:
            return existing, True
        wallet = self.wallets.by_user.get(identity.user_id) or v2.WalletRef(
            wallet_id=service.stable_id("wallet", identity.user_id), kind=v2.WalletKind.consumer,
            owner_user_id=identity.user_id, personal_org_id=identity.personal_org_id)
        self.wallets.by_user[identity.user_id] = wallet
        grant, entry = v2.issue_signup_grant(
            wallet=wallet, user_id=identity.user_id,
            verification_evidence_ref=identity.verification_evidence_ref,
            ledger_operation_id=operation_id, granted_at=at)
        self._append(entry, identity.user_id)
        self.grants[identity.user_id] = grant
        return grant, False

    async def adjust(self, wallet, amount, operation_id, actor, reason, at):
        existing = self.entries.get(operation_id)
        if existing is not None:
            return existing, True
        entry = v2.CreditLedgerEntry(
            entry_id=operation_id, wallet_id=wallet.wallet_id, wallet_kind=wallet.kind,
            kind=v2.LedgerEntryKind.operator_adjustment, amount=amount,
            operation_id=operation_id, actor=actor, reason=reason, created_at=at)
        self._append(entry, wallet.owner_user_id)
        return entry, False

    async def reconcile(self, org_id, request_id, operation_id, actor, at):
        row = self.unknown.get(request_id)
        if row is None or row[0] != org_id:
            raise errors.NotFound("no such request in that organization")
        if operation_id in self.reconciled:
            return self.reconciled[operation_id]
        if at < row[1]:
            raise errors.StateConflict("unknown usage is held until reconcile_after")
        row[2] = "released_platform_absorbed"
        self.reconciled[operation_id] = row[2]
        return row[2]

    def total(self, user_id) -> Credit:
        return self.wallets.by_user[user_id].ledger_total


@dataclasses.dataclass
class FakeAudit:
    entries: list[AuditEntry] = dataclasses.field(default_factory=list)
    fail_next: bool = False                            # a crash after the port write

    async def by_idempotency_key(self, key):
        return next((e for e in self.entries if e.idempotency_key == key), None)

    async def append(self, entry):
        if self.fail_next:
            self.fail_next = False
            raise errors.DependencyUnavailable("audit relation unreachable")
        if any(e.id == entry.id for e in self.entries):
            raise errors.Conflict("audit id is the primary key")
        self.entries.append(entry)


@dataclasses.dataclass
class FakeRegistry:
    catalog: FakeCatalogDirectory

    async def put(self, record):
        table, key = {
            v2.ServingRevision: (self.catalog.servings, "serving_version_id"),
            v2.DeploymentRevision: (self.catalog.deployments, "deployment_revision_id"),
        }.get(type(record), (None, None))
        if table is None:                              # a rate card: keyed by its version
            existing = next((c for c in self._cards if c.rate_card_version
                             == record.rate_card_version), None)
            if existing is not None:
                if existing != record:
                    raise errors.Conflict("rate cards are immutable")
                return False
            self._cards.append(record)
            self.catalog.publish(record)
            return True
        existing = table.get(getattr(record, key))
        if existing is not None:
            if existing != record:
                raise errors.Conflict("registry rows are immutable")
            return False
        table[getattr(record, key)] = record
        return True

    async def move_alias(self, requested_model, deployment_revision_id):
        self.catalog.move_alias(requested_model, deployment_revision_id)

    @property
    def _cards(self):
        return self.catalog.__dict__.setdefault("_history", [])


@dataclasses.dataclass
class FakeAccounts:
    usage_by_org: dict[str, v2.UsageHistory] = dataclasses.field(default_factory=dict)
    holds_by_org: dict[str, tuple[HoldView, ...]] = dataclasses.field(default_factory=dict)

    async def usage(self, org_id):
        return self.usage_by_org.get(org_id, v2.UsageHistory(org_id=org_id))

    async def holds(self, org_id):
        return self.holds_by_org.get(org_id, ())


@dataclasses.dataclass
class World:
    ops: service.Operations
    operator_secret: str
    identities: FakeIdentities
    tenants: FakeTenants
    ledger: FakeLedger
    audit: FakeAudit
    catalog: FakeCatalogDirectory
    accounts: FakeAccounts
    jobs: object                                   # the v1 conformance Harness
    clock: Clock


def world(*, empty_catalog: bool = False) -> World:
    """Two verified individuals (A, B), one whose wallet is bound to a foreign org (C),
    one operator key minted now, the committed v2 catalog and a v1 JobStore."""
    clock = Clock()
    v2h = fake_v2_harness()
    wallets = FakeWalletDirectory()
    # C's wallet exists but is bound to B's org: resolve_wallet must refuse it.
    wallets.by_user[USER_C] = v2.WalletRef(
        wallet_id=service.stable_id("wallet", USER_C), kind=v2.WalletKind.consumer,
        owner_user_id=USER_C, personal_org_id=ORG_B)
    identities = FakeIdentities({
        u: VerifiedIdentity(u, org, f"email-verified:{u}")
        for u, org in ((USER_A, ORG_A), (USER_B, ORG_B), (USER_C, ORG_C))})
    secret = service.new_secret()
    tenants = FakeTenants({OPERATOR_KEY: KeyRow(
        key_id=OPERATOR_KEY, org_id=OPERATOR_ORG, audience=v2.CredentialAudience.operator,
        key_hash=service.hash_key(secret), prefix=secret[:service.PREFIX_CHARS],
        name="bootstrap operator", role=Role.operator, created_at=clock())})
    catalog = FakeCatalogDirectory() if empty_catalog else v2h.catalog
    ledger, audit, accounts = FakeLedger(wallets), FakeAudit(), FakeAccounts()
    jobs = jobstore_factory()
    ops = service.Operations(identities=identities, tenants=tenants, ledger=ledger, audit=audit,
                             registry=FakeRegistry(catalog), wallets=wallets, catalog=catalog,
                             jobs=jobs.port, accounts=accounts, clock=clock)
    return World(ops, secret, identities, tenants, ledger, audit, catalog, accounts, jobs, clock)


def later(clock: Clock, **delta) -> None:
    clock.now = clock.now + timedelta(**delta)
