"""The headless operations adapter (G6B.a/b).

Two sessions, each derived from a bearer secret and never from a passed-in context:

* `Operations.operator(secret)` - the key row must exist, be unrevoked and carry the
  `operator` audience. There is no parameter through which a caller could hand over
  an `AuthContextV2` it built itself, so a forged or `model_construct`ed operator
  context has nowhere to enter.
* `Operations.tenant(secret)` - a consumer key reads its **own** usage, holds, jobs,
  balance and the rate it would be admitted at. Every lookup is keyed by the org the
  key row names.

Every operator write goes through `_once`: an idempotency key, a reason, a
deterministic operation id derived from both, and one D1 audit row. A replay with the
same key and the same request returns the recorded result; a different request under
the key is `IdempotencyConflict`. The operation id is what the A1/D5 ports dedupe on,
so a crash between the port write and the audit append replays into the same ledger
row instead of a second one.
"""
from __future__ import annotations

import dataclasses
import hashlib
import secrets
import string
import uuid
from datetime import datetime
from typing import Any, Callable

from ..contracts import errors
from ..contracts.records import Role
from ..contracts.v2 import fixtures as v2fix
from ..contracts.v2 import ports as v2ports
from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import (AuthContextV2, BalanceV2, CredentialAudience,
                                    DeploymentRevision, DeploymentState, DigestSource,
                                    RateCardSnapshot, ServingRevision, Visibility, WalletRef)
from .ports import (AUDIT_ACTIONS, SUSPENSION_REASONS, AccountView, AuditEntry, AuditLog,
                    IdentityDirectory, KeyRow, Ledger, Registry, TenantStore, VerifiedIdentity)

# The console's key shape (apps/app/lib/keys.ts): `sk-infrx-` + 40 unbiased base62.
KEY_PREFIX = "sk-infrx-"
KEY_BODY = 40
PREFIX_CHARS = len(KEY_PREFIX) + 8
_ALPHABET = string.ascii_letters + string.digits

MAX_IDEMPOTENCY_KEY = 255
MAX_REASON = 500                         # audit_entries.reason CHECK 1..500

# D1's action vocabulary is closed and has no word for key, publication, cancellation
# or reconciliation writes. Each operation is filed under the nearest action and names
# itself in `after.operation`; the evidence asks D for dedicated actions.
ACTION = {
    "key_issue": "admin_set_entitlements", "key_revoke": "admin_set_entitlements",
    "publish": "admin_set_entitlements", "job_cancel": "admin_set_entitlements",
    "suspension": "admin_set_suspension",
    "signup_grant": "admin_grant", "adjustment": "admin_grant", "reconcile": "admin_grant",
}
assert set(ACTION.values()) <= set(AUDIT_ACTIONS)

_NS = uuid.UUID("6b0f3c2e-8d54-4b8e-9a51-2f6c1d0e7a93")


def stable_id(*parts: str) -> str:
    """A lowercase UUIDv4-shaped id that is a pure function of `parts`."""
    digest = uuid.uuid5(_NS, "\x1f".join(parts)).bytes
    return str(uuid.UUID(bytes=digest, version=4))


def hash_key(secret: str) -> str:
    """What `auth/keys.py` looks the bearer token up by. The secret is never stored."""
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_BODY))


@dataclasses.dataclass(frozen=True)
class IssuedKey:
    """`secret` is set exactly once, on the call that created the row, and never
    appears in `repr` - a log line of this object cannot carry it."""

    key_id: str
    org_id: str
    prefix: str
    secret: str | None = dataclasses.field(default=None, repr=False)
    replayed: bool = False


@dataclasses.dataclass
class Operations:
    identities: IdentityDirectory
    tenants: TenantStore
    ledger: Ledger
    audit: AuditLog
    registry: Registry
    wallets: v2ports.WalletDirectory
    catalog: v2ports.CatalogDirectory
    jobs: Any                            # v1 `contracts.ports.JobStore`
    accounts: AccountView
    clock: Callable[[], datetime]

    async def _context(self, secret: str) -> AuthContextV2:
        if not secret:
            raise errors.InvalidApiKey("no credential")
        row = await self.tenants.key_by_hash(hash_key(secret))
        if row is None or row.revoked_at is not None:
            raise errors.InvalidApiKey("the credential is unknown or revoked")
        if row.audience is CredentialAudience.provider_dev:
            raise errors.Forbidden("provider credentials are served by the Lab surface")
        return AuthContextV2(audience=row.audience, org_id=row.org_id, key_id=row.key_id,
                             principal=row.key_id, role=row.role, entitlement_version=0,
                             user_id=row.user_id if row.audience is CredentialAudience.consumer
                             else None)

    async def operator(self, secret: str) -> OperatorSession:
        auth = await self._context(secret)
        if auth.audience is not CredentialAudience.operator:
            raise errors.Forbidden("an operator credential is required")
        return OperatorSession(self, auth.principal)

    async def tenant(self, secret: str) -> TenantSession:
        auth = await self._context(secret)
        if auth.audience is not CredentialAudience.consumer:
            raise errors.Forbidden("a consumer credential is required")
        return TenantSession(self, auth)

    async def bound_wallet(self, identity: VerifiedIdentity) -> WalletRef:
        """The identity's own wallet, checked by the contract resolver against the
        personal-org binding. Never looked up by a wallet id anyone supplied."""
        probe = AuthContextV2(audience=CredentialAudience.consumer,
                              org_id=identity.personal_org_id, key_id=stable_id("probe"),
                              principal="operations", role=Role.service, entitlement_version=0,
                              user_id=identity.user_id)
        wallet = await self.wallets.consumer_wallet_for_user(identity.user_id)
        return v2ports.resolve_wallet(probe, wallet)


@dataclasses.dataclass(frozen=True)
class TenantSession:
    """A consumer key's view of its own organization. Foreign ids answer NotFound."""

    ops: Operations
    auth: AuthContextV2

    async def usage(self):
        return await self.ops.accounts.usage(self.auth.org_id)

    async def holds(self):
        return await self.ops.accounts.holds(self.auth.org_id)

    async def job(self, job_handle: str):
        return await self.ops.jobs.get_owned(self.auth.org_id, job_handle)

    async def balance(self) -> BalanceV2:
        wallet = await self.ops.wallets.consumer_wallet_for_user(self.auth.user_id)
        return BalanceV2.of(v2ports.resolve_wallet(self.auth, wallet))

    async def quote(self, requested_model: str):
        """The pins and card a request for this model would be admitted at now.
        Private-to-others is NotFound (R70); unpriced is InvalidRequest (R69)."""
        catalog = self.ops.catalog
        deployment = await catalog.resolve(requested_model, audience=self.auth.audience,
                                           endpoint_id=self.auth.endpoint_id)
        if deployment is None:
            return v2ports.pin_admission(auth=self.auth, requested_model=requested_model,
                                         deployment=None, serving=None, rate_card=None,
                                         policy=None)
        return v2ports.pin_admission(
            auth=self.auth, requested_model=requested_model, deployment=deployment,
            serving=await catalog.serving_revision(deployment.serving_version_id),
            rate_card=await catalog.active_rate_card(deployment.deployment_revision_id),
            policy=await catalog.data_access_policy(deployment.deployment_revision_id))


@dataclasses.dataclass(frozen=True)
class OperatorSession:
    ops: Operations
    principal: str

    async def _once(self, operation: str, idempotency_key: str, reason: str,
                    target_org_id: str | None, request: dict[str, Any], write):
        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            raise errors.InvalidRequest("an idempotency key of 1..255 characters is required")
        if not reason.strip() or len(reason) > MAX_REASON:
            raise errors.InvalidRequest("an operator write states a reason of 1..500 characters")
        prior = await self.ops.audit.by_idempotency_key(idempotency_key)
        if prior is not None:
            if prior.after.get("operation") != operation or prior.after.get("request") != request:
                raise errors.IdempotencyConflict("this idempotency key recorded a different "
                                                 "operator write")
            return prior.after["result"], True
        operation_id = stable_id(operation, idempotency_key)
        before, result = await write(operation_id)
        await self.ops.audit.append(AuditEntry(
            id=operation_id, at=self.ops.clock(), actor_principal=self.principal,
            action=ACTION[operation], target_org_id=target_org_id, reason=reason, before=before,
            after={"operation": operation, "request": request, "result": result},
            idempotency_key=idempotency_key))
        return result, False

    async def _identity(self, user_id: str) -> VerifiedIdentity:
        identity = await self.ops.identities.verified_user(user_id)
        if identity is None:
            # Unknown and unverified read the same: no enumeration, and no credit or key
            # for an individual whose verification is not recorded.
            raise errors.NotFound("no verified individual with that id")
        return identity

    # --- G6B.a: keys, suspension, grant, adjustment ---------------------------
    async def issue_key(self, user_id: str, name: str, *, idempotency_key: str,
                        reason: str) -> IssuedKey:
        """A consumer key for a verified individual's personal org. There is no
        audience parameter: this tool cannot mint an operator or provider key."""
        identity = await self._identity(user_id)
        await self.ops.bound_wallet(identity)          # no key without a metered wallet
        org_id = identity.personal_org_id
        if await self.ops.tenants.suspension(org_id) is not None:
            raise errors.OrgSuspended("a suspended organization receives no new key")
        secret = new_secret()

        async def write(operation_id):
            row = KeyRow(key_id=operation_id, org_id=org_id,
                         audience=CredentialAudience.consumer, key_hash=hash_key(secret),
                         prefix=secret[:PREFIX_CHARS], name=name, user_id=identity.user_id,
                         created_at=self.ops.clock())
            inserted = await self.ops.tenants.insert_key(row)
            stored = await self.ops.tenants.key(org_id, operation_id)
            if stored is None:
                raise errors.IdempotencyConflict("this key id belongs to another organization")
            return None, {"key_id": operation_id, "org_id": org_id, "prefix": stored.prefix,
                          "inserted": inserted}

        result, replayed = await self._once("key_issue", idempotency_key, reason, org_id,
                                            {"user_id": user_id, "name": name}, write)
        # Revealed once: a replay - or a crash replay whose row already existed - gets
        # the id and prefix only. A lost secret is a rotation, not a second reveal.
        reveal = secret if result["inserted"] and not replayed else None
        return IssuedKey(key_id=result["key_id"], org_id=result["org_id"],
                         prefix=result["prefix"], secret=reveal,
                         replayed=replayed or not result["inserted"])

    async def revoke_key(self, org_id: str, key_id: str, *, idempotency_key: str,
                         reason: str) -> dict:
        async def write(_):
            row = await self.ops.tenants.key(org_id, key_id)
            if row is None:
                raise errors.NotFound("no such key in that organization")
            after = await self.ops.tenants.revoke_key(org_id, key_id, self.ops.clock())
            return ({"revoked_at": _iso(row.revoked_at)},
                    {"key_id": key_id, "revoked_at": _iso(after.revoked_at)})

        result, _ = await self._once("key_revoke", idempotency_key, reason, org_id,
                                     {"org_id": org_id, "key_id": key_id}, write)
        return result

    async def rotate_key(self, org_id: str, key_id: str, name: str, *, idempotency_key: str,
                         reason: str) -> IssuedKey:
        """Issue first, then revoke: the client is never left without a key. The new
        key belongs to the same individual and, through the binding, the same org."""
        row = await self.ops.tenants.key(org_id, key_id)
        if row is None or row.audience is not CredentialAudience.consumer:
            raise errors.NotFound("no such consumer key in that organization")
        identity = await self._identity(row.user_id)
        if identity.personal_org_id != org_id:
            raise errors.Forbidden("the key's organization is not the individual's personal org")
        issued = await self.issue_key(row.user_id, name, idempotency_key=idempotency_key + ":issue",
                                      reason=reason)
        await self.revoke_key(org_id, key_id, idempotency_key=idempotency_key + ":revoke",
                              reason=reason)
        return issued

    async def set_suspension(self, org_id: str, reason_code: str | None, *,
                             idempotency_key: str, reason: str) -> dict:
        """`reason_code=None` lifts it. The operator's prose stays in the audit row
        (R59 (2)); the organization carries only the closed code."""
        if reason_code is not None and reason_code not in SUSPENSION_REASONS:
            raise errors.InvalidRequest(f"suspension reason is one of {SUSPENSION_REASONS}")

        async def write(_):
            before = await self.ops.tenants.suspension(org_id)
            await self.ops.tenants.set_suspension(org_id, reason_code, self.ops.clock())
            return {"suspension": before}, {"org_id": org_id, "suspension": reason_code}

        result, _ = await self._once("suspension", idempotency_key, reason, org_id,
                                     {"org_id": org_id, "reason_code": reason_code}, write)
        return result

    async def grant_initial(self, user_id: str, *, idempotency_key: str, reason: str) -> dict:
        """A1's one-time individual grant. Keyed by the user, so a replay under any
        idempotency key lands on the same grant (R71)."""
        identity = await self._identity(user_id)
        if await self.ops.wallets.consumer_wallet_for_user(user_id) is not None:
            await self.ops.bound_wallet(identity)   # an existing wallet is already this one's

        async def write(operation_id):
            grant, replayed = await self.ops.ledger.grant_initial(identity, operation_id,
                                                                  self.ops.clock())
            return None, {"user_id": grant.user_id, "wallet_id": grant.wallet_id,
                          "amount": str(grant.amount),
                          "ledger_operation_id": grant.ledger_operation_id, "replayed": replayed}

        result, _ = await self._once("signup_grant", idempotency_key, reason,
                                     identity.personal_org_id, {"user_id": user_id}, write)
        return result

    async def adjust(self, user_id: str, amount: str, *, idempotency_key: str,
                     reason: str) -> dict:
        """A signed, audited D5 adjustment to the individual's own wallet."""
        value = Credit(amount)
        if value.is_zero:
            raise errors.InvalidRequest("an adjustment moves a nonzero amount")
        identity = await self._identity(user_id)
        wallet = await self.ops.bound_wallet(identity)

        async def write(operation_id):
            entry, replayed = await self.ops.ledger.adjust(wallet, value, operation_id,
                                                           self.principal, reason,
                                                           self.ops.clock())
            return None, {"entry_id": entry.entry_id, "wallet_id": entry.wallet_id,
                          "amount": str(entry.amount), "replayed": replayed}

        result, _ = await self._once("adjustment", idempotency_key, reason,
                                     identity.personal_org_id,
                                     {"user_id": user_id, "amount": str(value)}, write)
        return result


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
