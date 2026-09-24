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

from ..auth.context import auth_context
from ..contracts import errors
from ..contracts.records import Role
from ..contracts.v2 import fixtures as v2fix
from ..contracts.v2 import ports as v2ports
from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import (AuthContextV2, BalanceV2, CredentialAudience,
                                    DeploymentRevision, DeploymentState, DigestSource,
                                    RateCardSnapshot, ServingRevision, Visibility, WalletRef)
from .transition import unapproved
from .ports import (AUDIT_ACTIONS, SUSPENSION_REASONS, AccountView, AuditEntry, AuditLog,
                    IdentityDirectory, KeyRow, Ledger, Registry, TenantStore, VerifiedIdentity)

# The console's key shape (apps/app/lib/keys.ts): `sk-infrx-` + 40 unbiased base62.
KEY_PREFIX = "sk-infrx-"
KEY_BODY = 40
PREFIX_CHARS = len(KEY_PREFIX) + 8
_ALPHABET = string.ascii_letters + string.digits

MAX_IDEMPOTENCY_KEY = 255
MAX_REASON = 500                         # audit_entries.reason CHECK 1..500

# Each operator write under its own 0009 audit action (G8: a reconciliation or an
# adjustment filed as a grant misreads in an audit); it also names itself in
# `after.operation`. `transition` flips the platform-wide admission flags (no org).
ACTION = {
    "key_issue": "admin_key_issue", "key_revoke": "admin_key_revoke",
    "publish": "admin_publish", "job_cancel": "admin_job_cancel",
    "suspension": "admin_set_suspension", "signup_grant": "admin_grant",
    "adjustment": "admin_adjust", "reconcile": "admin_reconcile",
    "transition": "admin_set_entitlements",
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
    # G8: the regime transition's flags and inventory (`transition.PgTransition`); only
    # the PostgreSQL composition has one - a fake world has no regime to switch.
    transitions: Any = None

    async def _context(self, secret: str) -> AuthContextV2:
        if not secret:
            raise errors.InvalidApiKey("no credential")
        row = await self.tenants.key_by_hash(hash_key(secret))
        if row is None or row.revoked_at is not None:
            raise errors.InvalidApiKey("the credential is unknown or revoked")
        if row.audience is CredentialAudience.provider_dev:
            raise errors.Forbidden("provider credentials are served by the Lab surface")
        return auth_context(audience=row.audience, org_id=row.org_id, key_id=row.key_id,
                            user_id=row.user_id, role=row.role)

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
    """A consumer key's view of its own organization. Foreign ids answer NotFound.
    Built only by `Operations.tenant(secret)`; no public entry point takes a caller-built
    one - the trust boundary is the database credential behind the ports."""

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

    async def statement(self) -> dict:
        """G8: the individual's own exact CREDIT state - available, reserved and spent as
        exact decimal strings - beside its holds and per-unit usage (R73). The wallet is
        the credential's individual's (R66), never looked up by an id anyone supplied."""
        balance, holds, usage = await self.balance(), await self.holds(), await self.usage()
        return {"org_id": self.auth.org_id, "user_id": self.auth.user_id,
                "credit": {**balance.model_dump(mode="json", exclude=_NOT_CREDIT),
                           **_spent(usage)},
                "holds": [_hold(h) for h in holds], "usage_totals": usage.totals()}

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
    """Built only by `Operations.operator(secret)`; no public entry point takes a
    caller-built one - the trust boundary is the database credential behind the ports."""

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
            return _recorded(prior, operation, request), True
        operation_id = stable_id(operation, idempotency_key)
        before, result = await write(operation_id)
        try:
            await self.ops.audit.append(AuditEntry(
                id=operation_id, at=self.ops.clock(), actor_principal=self.principal,
                action=ACTION[operation], target_org_id=target_org_id, reason=reason,
                before=before,
                after={"operation": operation, "request": request, "result": result},
                idempotency_key=idempotency_key))
        except errors.Conflict:
            # G8: a concurrent call under the same key (a callback retry racing its original)
            # recorded first; the ports deduped the write on the operation id, and the
            # recorded row is the answer - never the raw unique violation. Nothing retried.
            prior = await self.ops.audit.by_idempotency_key(idempotency_key)
            if prior is None:
                raise
            return _recorded(prior, operation, request), True
        return result, False

    async def _identity(self, user_id: str) -> VerifiedIdentity:
        identity = await self.ops.identities.verified_user(user_id)
        if identity is None:
            # Unknown and unverified read the same: no enumeration, and no credit or key
            # for an individual whose verification is not recorded.
            raise errors.NotFound("no verified individual with that id")
        return identity

    # --- G8: the trusted account read -------------------------------------------
    async def account(self, user_id: str) -> dict:
        """A verified individual's personal consumer account, resolved from the v2
        identity - the organization the individual created and owns, which the wallet
        binding then freezes - never from whichever membership came first. A wallet bound
        to another organization is Forbidden (R66). Exact CREDIT strings; a read, so no
        audit row."""
        identity = await self._identity(user_id)
        org = identity.personal_org_id
        wallet = None
        if await self.ops.wallets.consumer_wallet_for_user(user_id) is not None:
            wallet = await self.ops.bound_wallet(identity)
        usage = await self.ops.accounts.usage(org)
        credit = ({"wallet_id": None, "unit": "CREDIT"} if wallet is None else
                  BalanceV2.of(wallet).model_dump(mode="json", exclude=_NOT_CREDIT))
        return {"user_id": user_id, "personal_org_id": org,
                "verification_evidence_ref": identity.verification_evidence_ref,
                "suspension": await self.ops.tenants.suspension(org),
                "credit": {**credit, **_spent(usage)},
                "holds": [_hold(h) for h in await self.ops.accounts.holds(org)],
                "usage_totals": usage.totals()}

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
        try:
            value = Credit(amount)
        except (ValueError, ArithmeticError, TypeError):
            raise errors.InvalidRequest("an adjustment is an exact CREDIT decimal") from None
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

    # --- G6B.b: publication, cancellation, reconciliation ---------------------
    async def publish(self, serving: ServingRevision, deployment: DeploymentRevision,
                      card: RateCardSnapshot, requested_model: str, *, idempotency_key: str,
                      reason: str) -> dict:
        """Serving, deployment and card as one audited publication, then the alias.

        Refused before anything is written: a private or not-active deployment (never
        in the public catalog, R70), a card that prices another revision (R69), and a
        card no newer than the one it would replace (stale).
        """
        if deployment.serving_version_id != serving.serving_version_id:
            raise errors.InvalidRequest("the deployment pins a different serving revision")
        if (deployment.visibility is not Visibility.public
                or deployment.state is not DeploymentState.active):
            raise errors.InvalidRequest("only an active public deployment is published")
        if (card.deployment_revision_id != deployment.deployment_revision_id
                or card.serving_version_id != serving.serving_version_id
                or card.model_id != serving.model_id):
            raise errors.InvalidRequest("the rate card prices a different revision")
        active = await self.ops.catalog.active_rate_card(deployment.deployment_revision_id)
        if (active is not None and active.rate_card_version != card.rate_card_version
                and card.effective_at <= active.effective_at):
            raise errors.StateConflict("the card is not newer than the active card")

        async def write(_):
            written = [await self.ops.registry.put(r) for r in (serving, deployment, card)]
            await self.ops.registry.move_alias(requested_model, deployment.deployment_revision_id)
            return ({"rate_card_version": active.rate_card_version if active else None},
                    {"requested_model": requested_model,
                     "serving_version_id": serving.serving_version_id,
                     "deployment_revision_id": deployment.deployment_revision_id,
                     "rate_card_version": card.rate_card_version,
                     "digest_source": serving.digest_source.value,
                     "approved_by": card.approved_by, "written": written})

        request = {"requested_model": requested_model,
                   "serving": serving.model_dump(mode="json"),
                   "deployment": deployment.model_dump(mode="json"),
                   "card": card.model_dump(mode="json")}
        result, _ = await self._once("publish", idempotency_key, reason, None, request, write)
        return result

    async def publish_card(self, requested_model: str, *, rate_card_version: str,
                           input_rate: str, output_rate: str, approved_by: str,
                           effective_at: datetime, idempotency_key: str, reason: str) -> dict:
        """G8 / P-01 / F2C-C: an operator-APPROVED card for the deployment the model's
        effective listing serves now, published additively (a new immutable card; the
        listing then names it). The public rate identity stays the listing's card - never
        an id minted per release - and no historical card or job is rewritten. A
        provisional approval is refused here: this path publishes launch prices only."""
        why = unapproved(approved_by)
        if why:
            raise errors.InvalidRequest(f"publish-card publishes approved prices only: {why}")
        deployment = await self.ops.catalog.resolve(
            requested_model, audience=CredentialAudience.consumer, endpoint_id=None)
        if deployment is None:
            raise errors.NotFound("no public listing serves that model")
        serving = await self.ops.catalog.serving_revision(deployment.serving_version_id)
        try:
            card = RateCardSnapshot(
                rate_card_version=rate_card_version, model_id=serving.model_id,
                deployment_revision_id=deployment.deployment_revision_id,
                serving_version_id=serving.serving_version_id,
                input_rate_per_million=input_rate, output_rate_per_million=output_rate,
                effective_at=effective_at, approved_by=approved_by)
        except ValueError as refused:
            raise errors.InvalidRequest(f"not a publishable card: {refused}") from None
        return await self.publish(serving, deployment, card, requested_model,
                                  idempotency_key=idempotency_key, reason=reason)

    async def cancel_job(self, org_id: str, job_handle: str, *, idempotency_key: str,
                         reason: str) -> dict:
        async def write(_):
            outcome = await self.ops.jobs.cancel(org_id, job_handle)
            return None, {"job_id": outcome.job_id, "state": outcome.state.value,
                          "cause": outcome.cause.value}

        result, _ = await self._once("job_cancel", idempotency_key, reason, org_id,
                                     {"org_id": org_id, "job_handle": job_handle}, write)
        return result

    async def reconcile(self, org_id: str, request_id: str, *, idempotency_key: str,
                        reason: str) -> dict:
        async def write(operation_id):
            state = await self.ops.ledger.reconcile(org_id, request_id, operation_id,
                                                    self.principal, self.ops.clock())
            return None, {"request_id": request_id, "settlement": state}

        result, _ = await self._once("reconcile", idempotency_key, reason, org_id,
                                     {"org_id": org_id, "request_id": request_id}, write)
        return result


def _recorded(prior: AuditEntry, operation: str, request: dict[str, Any]) -> Any:
    """The result an idempotency key recorded, if it recorded THIS write (R34)."""
    if prior.after.get("operation") != operation or prior.after.get("request") != request:
        raise errors.IdempotencyConflict("this idempotency key recorded a different "
                                         "operator write")
    return prior.after["result"]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


#: A `BalanceV2`'s fields that are not the CREDIT wallet's own (the statement is exact
#: CREDIT; the legacy USD statement is never mixed into it, R73).
_NOT_CREDIT = {"schema_version", "legacy_usd"}

#: `AccountView.usage` is one page (the newest `usage_records` rows, at most 500).
USAGE_PAGE = 500


def _spent(usage) -> dict:
    """Settled CREDIT spend: the CREDIT usage total, exact. A full page may have left
    older rows out, so it answers None rather than a short sum.
    ponytail: one page; D10's settled-debit read (D10.c) replaces it."""
    if len(usage.entries) >= USAGE_PAGE:
        return {"spent": None, "spent_complete": False}
    return {"spent": usage.totals().get("CREDIT", str(Credit("0"))), "spent_complete": True}


def _hold(hold) -> dict:
    return {"request_id": hold.request_id, "state": hold.state.value, "amount": str(hold.amount),
            "unit": "CREDIT"}


def marlin_release(*, provider_org_id: str, created_at: datetime, effective_at: datetime,
                   input_rate_per_million: str = v2fix.INPUT_RATE_PER_MILLION,
                   output_rate_per_million: str = v2fix.OUTPUT_RATE_PER_MILLION,
                   approved_by: str = v2fix.RATE_CARD_APPROVER):
    """The pinned Marlin serving/deployment/card, from S2M's recorded values.

    Artifact digests are the served-bytes values `contracts/v2/fixtures.py` carries
    (`digest_source=served_bytes`; registry equality is W3's). The runtime image has no
    digest (a moving tag) and the engine-options digest is the fixture's placeholder
    until W3 records the launched options. The rate defaults to the provisional card
    and says so in `approved_by` and the version label until P-01 is decided.
    Ids are a function of the revision label, so re-running publishes the same rows.
    """
    label = v2fix.REVISION_LABEL
    shape = v2fix.BUILDERS["deployment_revision_public.json"]()     # prod/public/active, limits
    model_id, version_id = stable_id("marlin2b", "model"), stable_id("marlin2b", label, "version")
    serving = ServingRevision(
        serving_version_id=stable_id("marlin2b", label, "serving"), model_id=model_id,
        model_version_id=version_id, provider_org_id=provider_org_id,
        public_model_id=v2fix.PUBLIC_MODEL_ID, revision_label=label,
        model_repo=v2fix.MODEL_REPO, model_commit=v2fix.MODEL_COMMIT,
        weight_shard_digests=v2fix.SHARD_DIGESTS, tokenizer_digest=v2fix.TOKENIZER_DIGEST,
        chat_template_digest=v2fix.CHAT_TEMPLATE_DIGEST,
        digest_source=DigestSource.served_bytes, prompt_harness_ref="marlin2b.chat.v1",
        preprocessor_profile_version="marlin2b.video.v1",
        runtime_image_ref=v2fix.RUNTIME_IMAGE_REF,
        engine_options_digest=v2fix.ENGINE_OPTIONS_DIGEST, precision="bfloat16",
        capability=v2fix.BUILDERS["serving_revision.json"]().capability, created_at=created_at)
    deployment = DeploymentRevision(
        deployment_revision_id=stable_id("marlin2b", label, "deployment", "prod"),
        endpoint_id=stable_id("marlin2b", "endpoint", "prod"), provider_org_id=provider_org_id,
        serving_version_id=serving.serving_version_id, environment=shape.environment,
        visibility=shape.visibility, state=shape.state, max_input_tokens=shape.max_input_tokens,
        max_output_tokens=shape.max_output_tokens, created_at=created_at)
    provisional = "P-01" in approved_by
    card = RateCardSnapshot(
        rate_card_version=(f"rc_marlin2b_{effective_at:%Y%m%dT%H%M%SZ}"
                           + ("_provisional_p01" if provisional else "")),
        model_id=model_id, deployment_revision_id=deployment.deployment_revision_id,
        serving_version_id=serving.serving_version_id,
        input_rate_per_million=input_rate_per_million,
        output_rate_per_million=output_rate_per_million, effective_at=effective_at,
        approved_by=approved_by)
    return serving, deployment, card, v2fix.REQUESTED_MODEL
