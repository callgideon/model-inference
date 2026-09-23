"""D5 item 7: the PostgreSQL adapters of G6B's operator ports (`infrx/operations/ports.py`),
over the 0009/0015/0018 relations and functions. `tests/g/ops/fakes.py` is the reference.

Every operation is one statement or one transaction on a `Connect` (`jobstore.connector`: a
connection acting as `service_role`); refusals are typed (`jobstore.domain_error`, plus
`Conflict` for a duplicate key and `InvalidRequest` for a missing registry parent). Nothing
here sets a balance: money moves only through `infrx.grant_credit`/`infrx.reconcile` (D5)
and A1's `claim_signup_grant`, whose triggers move the totals.

    PgTenantStore   key rows and suspension (public.api_keys, 0009 `set_suspension`)
    PgAuditLog      infrx.audit_entries (append-only; looked up by idempotency key)
    PgRegistry      immutable serving/deployment/card rows and the alias (a listing version)
    PgAccountView   a tenant's own usage (per unit, R73) and CREDIT holds
    PgLedger        A1's `grant_initial`, D5's `adjust` and `reconcile`
    PgWalletDirectory  the v2 wallet lookup the service composes with (keyed by identity)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from ..contracts import errors, money
from ..contracts.records import HoldState, Role, SettlementState, Usage, UsageCertainty
from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import (CapabilityRecord, CredentialAudience, CreditLedgerEntry,
                                    DeploymentRevision, RateCardSnapshot, ServingRevision,
                                    UsageHistory, UsageRecordV2, WalletRef)
from ..operations.ports import AuditEntry, HoldView, KeyRow, VerifiedIdentity
from .jobstore import Connect, PgJobStore, domain_error
from .signup import PgSignup

_KEY = ("select id, org_id, audience, key_hash, prefix, name, user_id, created_at, revoked_at "
        "from public.api_keys where ")
_AUDIT = ("id", "at", "actor_principal", "action", "target_org_id", "reason", "before",
          "after", "idempotency_key")


class _Db:
    """One connection per call (the `Connect` the gateway already builds), errors typed."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @asynccontextmanager
    async def connection(self):
        """The `pool.connection()` shape A1's `PgSignup` takes."""
        conn = await self._connect()
        try:
            yield conn
        finally:
            await conn.close()

    async def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        from psycopg import Error
        async with self.connection() as conn:
            try:
                return await (await conn.execute(sql, params)).fetchall()
            except Error as failed:
                raise _typed(failed) from None

    async def one(self, sql: str, params: tuple = ()) -> tuple | None:
        found = await self.rows(sql, params)
        return found[0] if found else None


def _typed(failed: Exception) -> Exception:
    state = getattr(failed, "sqlstate", None)
    if state == "23505":
        return errors.Conflict(f"a row with this identity already exists: {failed}")
    if state == "23503":
        return errors.InvalidRequest("the row names a model, endpoint, provider or card "
                                     "that is not registered")
    return domain_error(failed)


def _key_row(row) -> KeyRow:
    key_id, org_id, audience, key_hash, prefix, name, user_id, created_at, revoked_at = row
    audience = CredentialAudience(audience)
    return KeyRow(key_id=str(key_id), org_id=str(org_id), audience=audience, key_hash=key_hash,
                  prefix=prefix, name=name, user_id=None if user_id is None else str(user_id),
                  # the row carries no role column: an operator key acts as the operator
                  role=Role.operator if audience is CredentialAudience.operator else Role.service,
                  created_at=created_at, revoked_at=revoked_at)


# --------------------------------------------------------------------- tenants
class PgTenantStore:
    """`ports.TenantStore` over `public.api_keys` and `public.organizations`."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    async def key_by_hash(self, key_hash: str) -> KeyRow | None:
        row = await self._db.one(_KEY + "key_hash = %s", (key_hash,))
        return None if row is None else _key_row(row)

    async def key(self, org_id: str, key_id: str) -> KeyRow | None:
        """Tenant-scoped: another organization's key id answers None."""
        row = await self._db.one(_KEY + "id = %s and org_id = %s", (key_id, org_id))
        return None if row is None else _key_row(row)

    async def insert_key(self, row: KeyRow) -> bool:
        """False when a row with this id exists (a replay): never a second row. Another
        row's hash is a `Conflict` (the hash is unique)."""
        written = await self._db.one(
            "insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash, "
            "audience, user_id, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, "
            "coalesce(%s, infrx.now())) on conflict (id) do nothing returning id",
            (row.key_id, row.org_id, row.user_id, row.name, row.prefix, row.key_hash,
             row.audience.value, row.user_id, row.created_at))
        return written is not None

    async def revoke_key(self, org_id: str, key_id: str, at: datetime) -> KeyRow:
        """One-way: a revoked row keeps its FIRST `revoked_at`, which is the database clock
        (R7; `at` is the caller's). Another organization's key is `not_found`."""
        row = await self._db.one(
            "update public.api_keys set revoked_at = coalesce(revoked_at, infrx.now()) "
            "where id = %s and org_id = %s returning id, org_id, audience, key_hash, prefix, "
            "name, user_id, created_at, revoked_at", (key_id, org_id))
        if row is None:
            raise errors.NotFound("no such key in that organization")
        return _key_row(row)

    async def suspension(self, org_id: str) -> str | None:
        row = await self._db.one("select suspension_reason from public.organizations "
                                 "where id = %s and suspended", (org_id,))
        return None if row is None else row[0]

    async def set_suspension(self, org_id: str, reason: str | None, at: datetime) -> None:
        """Through 0009's audited `infrx.set_suspension` (the organization carries only the
        closed code, R59-2). Its audit row is keyed by this call (organization, code,
        caller instant), so a retry of the same call is answered from it."""
        await self._db.one(
            "select infrx.set_suspension(%s, %s, %s, 'operations', %s, %s)",
            (org_id, reason is not None, reason,
             "suspension set by the operator operations service",
             f"set_suspension:{org_id}:{reason}:{at.isoformat()}"))


# --------------------------------------------------------------------- audit
class PgAuditLog:
    """`ports.AuditLog` over `infrx.audit_entries` (append-only, R34)."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    async def by_idempotency_key(self, key: str) -> AuditEntry | None:
        row = await self._db.one(f"select {', '.join(_AUDIT)} from "
                                 "infrx.audit_by_idempotency_key(%s)", (key,))
        if row is None:
            return None
        entry = dict(zip(_AUDIT, row))
        entry["id"] = str(entry["id"])
        entry["target_org_id"] = None if entry["target_org_id"] is None \
            else str(entry["target_org_id"])
        return AuditEntry(**entry)

    async def append(self, entry: AuditEntry) -> None:
        """A second row with the same id or idempotency key is a `Conflict`."""
        from psycopg.types.json import Jsonb
        await self._db.rows(
            "insert into infrx.audit_entries (id, at, actor_principal, action, target_org_id, "
            "reason, before, after, idempotency_key) values (%s, %s, %s, %s, %s, %s, %s, %s, "
            "%s) returning id",
            (entry.id, entry.at, entry.actor_principal, entry.action, entry.target_org_id,
             entry.reason, None if entry.before is None else Jsonb(entry.before),
             Jsonb(entry.after), entry.idempotency_key))


# --------------------------------------------------------------------- registry
_SERVING = """
  select s.serving_version_id, s.model_id, s.model_version_id, s.provider_org_id, m.id,
         s.revision_label, v.model_repo, v.model_commit, v.weight_shard_digests,
         v.adapter_digest, v.tokenizer_digest, v.chat_template_digest, v.digest_source,
         s.prompt_harness_ref, s.preprocessor_profile_version, s.runtime_image_ref,
         s.runtime_image_digest, s.engine_options_digest, s.precision, s.capability,
         s.created_at
  from infrx.serving_versions s
  join infrx.model_versions v on v.model_version_id = s.model_version_id
  join public.models m on m.model_uuid = s.model_id
  where s.serving_version_id = %s"""
_SERVING_FIELDS = ("serving_version_id", "model_id", "model_version_id", "provider_org_id",
                   "public_model_id", "revision_label", "model_repo", "model_commit",
                   "weight_shard_digests", "adapter_digest", "tokenizer_digest",
                   "chat_template_digest", "digest_source", "prompt_harness_ref",
                   "preprocessor_profile_version", "runtime_image_ref", "runtime_image_digest",
                   "engine_options_digest", "precision", "capability", "created_at")
_DEPLOYMENT = ("select deployment_revision_id, endpoint_id, provider_org_id, serving_version_id, "
               "environment, visibility, state, max_input_tokens, max_output_tokens, created_at "
               "from infrx.deployment_revisions where deployment_revision_id = %s")
_DEPLOYMENT_FIELDS = ("deployment_revision_id", "endpoint_id", "provider_org_id",
                      "serving_version_id", "environment", "visibility", "state",
                      "max_input_tokens", "max_output_tokens", "created_at")
_CARD = ("select rate_card_version, model_id, deployment_revision_id, serving_version_id, "
         "input_rate_per_million::text, output_rate_per_million::text, effective_at, "
         "approved_by from infrx.rate_card_versions where rate_card_version = %s")
_CARD_FIELDS = ("rate_card_version", "model_id", "deployment_revision_id", "serving_version_id",
                "input_rate_per_million", "output_rate_per_million", "effective_at",
                "approved_by")


def _record(cls, fields, row):
    doc = {name: (str(value) if name.endswith("_id") and value is not None else value)
           for name, value in zip(fields, row)}
    return cls.model_validate(doc)


class PgRegistry:
    """`ports.Registry`: immutable rows, one transaction per `put`. An identical row again
    is False; a different row under the same id is `Conflict`. A row naming a model,
    endpoint or provider that is not registered is `InvalidRequest` (creating those is
    not this port's). `move_alias` is a new catalog listing version (0007), so a
    publication whose `put`s were not all written is unreachable until it is re-run."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    async def _stored(self, conn, record):
        if isinstance(record, ServingRevision):
            row = await (await conn.execute(_SERVING, (record.serving_version_id,))).fetchone()
            return None if row is None else _record(ServingRevision, _SERVING_FIELDS, row)
        if isinstance(record, DeploymentRevision):
            row = await (await conn.execute(_DEPLOYMENT,
                                            (record.deployment_revision_id,))).fetchone()
            return None if row is None else _record(DeploymentRevision, _DEPLOYMENT_FIELDS, row)
        row = await (await conn.execute(_CARD, (record.rate_card_version,))).fetchone()
        return None if row is None else _record(RateCardSnapshot, _CARD_FIELDS, row)

    async def put(self, record: ServingRevision | DeploymentRevision | RateCardSnapshot) -> bool:
        from psycopg import Error
        async with self._db.connection() as conn:
            try:
                async with conn.transaction():
                    stored = await self._stored(conn, record)
                    if stored is not None:
                        if stored != record:
                            raise errors.Conflict("registry rows are immutable: a different "
                                                  "row holds this id")
                        return False
                    await _insert(conn, record)
                    return True
            except Error as failed:
                raise _typed(failed) from None

    async def move_alias(self, requested_model: str, deployment_revision_id: str) -> None:
        """The alias (the `@label`-less model id) now resolves to this deployment, at its
        current card: a new listing version, effective now. Already there: nothing."""
        await self._db.rows("""
          with d as (
            select d.deployment_revision_id, d.serving_version_id, s.model_id,
                   (select c.rate_card_version from infrx.rate_card_versions c
                     where c.deployment_revision_id = d.deployment_revision_id
                       and c.effective_at <= infrx.now()
                     order by c.effective_at desc, c.created_at desc limit 1) as card
            from infrx.deployment_revisions d
            join infrx.serving_versions s on s.serving_version_id = d.serving_version_id
            where d.deployment_revision_id = %(deployment)s),
          latest as (
            select * from infrx.catalog_listings where public_model_id = %(alias)s
            order by version desc limit 1)
          insert into infrx.catalog_listings (public_model_id, version, model_id,
            deployment_revision_id, serving_version_id, rate_card_version, effective_at,
            approved_by)
          select %(alias)s, coalesce((select version from latest), 0) + 1, d.model_id,
                 d.deployment_revision_id, d.serving_version_id, d.card, infrx.now(),
                 'operations'
          from d
          where not exists (select 1 from latest l where l.deployment_revision_id =
                            d.deployment_revision_id and l.rate_card_version = d.card)
          returning version""",
                            {"alias": requested_model.split("@", 1)[0],
                             "deployment": deployment_revision_id})


async def _insert(conn, record) -> None:
    from psycopg.types.json import Jsonb
    if isinstance(record, ServingRevision):
        await conn.execute(
            "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
            "model_repo, model_commit, weight_shard_digests, adapter_digest, tokenizer_digest, "
            "chat_template_digest, digest_source, created_by, created_at) values (%s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, 'operations', %s) on conflict do nothing",
            (record.model_version_id, record.model_id, record.provider_org_id,
             record.model_repo, record.model_commit, list(record.weight_shard_digests),
             record.adapter_digest, record.tokenizer_digest, record.chat_template_digest,
             record.digest_source.value, record.created_at))
        await conn.execute(
            "insert into infrx.serving_versions (serving_version_id, model_version_id, "
            "model_id, provider_org_id, revision_label, prompt_harness_ref, "
            "preprocessor_profile_version, runtime_image_ref, runtime_image_digest, "
            "engine_options_digest, precision, capability, created_by, created_at) values "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'operations', %s)",
            (record.serving_version_id, record.model_version_id, record.model_id,
             record.provider_org_id, record.revision_label, record.prompt_harness_ref,
             record.preprocessor_profile_version, record.runtime_image_ref,
             record.runtime_image_digest, record.engine_options_digest, record.precision,
             Jsonb(record.capability.model_dump(mode="json")), record.created_at))
        stored = await (await conn.execute(_SERVING, (record.serving_version_id,))).fetchone()
        if _record(ServingRevision, _SERVING_FIELDS, stored) != record:
            # the model version row was already there with other artifact digests
            raise errors.Conflict("the model version already records another artifact")
    elif isinstance(record, DeploymentRevision):
        await conn.execute(
            "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
            "provider_org_id, environment, serving_version_id, visibility, state, "
            "max_input_tokens, max_output_tokens, created_by, created_at) values (%s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, 'operations', %s)",
            (record.deployment_revision_id, record.endpoint_id, record.provider_org_id,
             record.environment.value, record.serving_version_id, record.visibility.value,
             record.state.value, record.max_input_tokens, record.max_output_tokens,
             record.created_at))
    else:
        # `provisional` labels a card whose price is not operator-approved (P-01); the card
        # record says so in `approved_by`, the convention `marlin_release` writes.
        await conn.execute(
            "insert into infrx.rate_card_versions (rate_card_version, model_id, "
            "deployment_revision_id, serving_version_id, input_rate_per_million, "
            "output_rate_per_million, effective_at, approved_by, provisional) values (%s, %s, "
            "%s, %s, %s, %s, %s, %s, %s)",
            (record.rate_card_version, record.model_id, record.deployment_revision_id,
             record.serving_version_id, record.input_rate_per_million.raw("CREDIT"),
             record.output_rate_per_million.raw("CREDIT"), record.effective_at,
             record.approved_by, "P-01" in record.approved_by))


# --------------------------------------------------------------------- account view
class PgAccountView:
    """`ports.AccountView`: a tenant's own settled usage and CREDIT holds, keyed by the
    authenticated organization (0009's bounded reads)."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    async def usage(self, org_id: str) -> UsageHistory:
        """Newest first, at most 500; `totals()` is per unit (R73) and `{}` when empty."""
        rows = await self._db.rows(
            "select request_id, org_id, accounting_regime, unit, charged_amount, "
            "prompt_tokens, completion_tokens, usage_certainty, outcome, rate_card_version, "
            "serving_version_id, deployment_revision_id, price_version, settled_at "
            "from infrx.usage_records(%s, null, null, 500)", (org_id,))
        return UsageHistory(org_id=org_id, entries=tuple(_usage_record(r) for r in rows))

    async def holds(self, org_id: str) -> tuple[HoldView, ...]:
        """The organization's live CREDIT holds (a `HoldView` is a CREDIT amount; USD holds
        are the legacy statement's, never read as CREDIT)."""
        rows = await self._db.rows(
            "select request_id, state, amount from infrx.active_holds(%s) "
            "where accounting_regime = 'credit'", (org_id,))
        return tuple(HoldView(str(r), HoldState(state), Credit(amount))
                     for r, state, amount in rows)


def _usage_record(row) -> UsageRecordV2:
    (request_id, org_id, regime, unit, amount, prompt, completion, certainty, outcome, card,
     serving, deployment, price_version, settled_at) = row
    usage = None
    if prompt is not None and completion is not None:
        usage = Usage.of(prompt, completion, UsageCertainty(certainty or "authoritative"))
    return UsageRecordV2(
        request_id=str(request_id), org_id=str(org_id), accounting_regime=regime, unit=unit,
        charged_amount=money.format_money(amount), usage=usage,
        outcome=None if outcome is None else SettlementState(outcome),
        rate_card_version=card, serving_version_id=None if serving is None else str(serving),
        deployment_revision_id=None if deployment is None else str(deployment),
        price_version=price_version, settled_at=settled_at)


# --------------------------------------------------------------------- ledger
class PgLedger:
    """`ports.Ledger`: A1's `grant_initial` (`PgSignup`, unchanged), D5's `adjust`
    (`infrx.grant_credit`) and `reconcile` (`infrx.reconcile`). `at` is the caller's
    clock, sent as audit data only (R7)."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)
        self._store = PgJobStore(connect)
        self._signup = PgSignup(self._db)

    async def grant_initial(self, identity: VerifiedIdentity, operation_id: str,
                            at: datetime):
        return await self._signup.grant_initial(identity, operation_id, at)

    async def adjust(self, wallet: WalletRef, amount: Credit, operation_id: str, actor: str,
                     reason: str, at: datetime) -> tuple[CreditLedgerEntry, bool]:
        """One audited `operator_adjustment` (signed, nonzero, never below the reserved
        total); a replayed operation id answers the existing entry and True."""
        answer = await self._store._call("grant_credit", {
            "wallet_id": wallet.wallet_id, "kind": "operator_adjustment",
            "amount": str(amount), "operation_id": operation_id, "actor": actor,
            "reason": reason, "at": at.isoformat()})
        return _entry(answer["entry"]), bool(answer["replayed"])

    async def reconcile(self, org_id: str, request_id: str, operation_id: str, actor: str,
                        at: datetime) -> str:
        """The 24 h rule on the DATABASE clock: `state_conflict` before `reconcile_after`,
        `not_found` for another organization's request; never a debit."""
        answer = await self._store._call("reconcile", {
            "org_id": org_id, "request_id": request_id, "operation_id": operation_id,
            "actor": actor, "at": at.isoformat()})
        return answer["settlement_state"]


def _entry(doc: dict[str, Any]) -> CreditLedgerEntry:
    return CreditLedgerEntry.model_validate({
        **{k: doc[k] for k in ("entry_id", "wallet_id", "wallet_kind", "kind", "operation_id",
                               "request_id", "actor", "reason", "created_at")},
        "amount": money.format_money(doc["amount"])})


# --------------------------------------------------------------------- wallets
class PgWalletDirectory:
    """`v2.ports.WalletDirectory`: keyed by identity, never by a wallet id a caller names.
    (Needed to compose `Operations` on PostgreSQL; the port's owner is unassigned.)"""

    # `WalletRef` counts revisions from 1; a wallet no row has moved yet (0) reads as 1.
    _SELECT = ("select wallet_id, kind, owner_user_id, personal_org_id, owner_provider_org_id, "
               "ledger_total::text, reserved_total::text, greatest(revision, 1) "
               "from infrx.credit_wallets ")

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    @staticmethod
    def _ref(row) -> WalletRef | None:
        if row is None:
            return None
        wallet_id, kind, user, org, provider, total, reserved, revision = row
        return WalletRef.model_validate({
            "wallet_id": str(wallet_id), "kind": kind,
            "owner_user_id": None if user is None else str(user),
            "personal_org_id": None if org is None else str(org),
            "owner_provider_org_id": None if provider is None else str(provider),
            "ledger_total": total, "reserved_total": reserved, "revision": revision})

    async def consumer_wallet_for_user(self, user_id: str) -> WalletRef | None:
        return self._ref(await self._db.one(
            self._SELECT + "where owner_user_id = %s and kind = 'consumer'", (user_id,)))

    async def provider_dev_wallet(self, provider_org_id: str) -> WalletRef | None:
        return self._ref(await self._db.one(
            self._SELECT + "where owner_provider_org_id = %s and kind = 'provider_dev'",
            (provider_org_id,)))


__all__ = ["PgAccountView", "PgAuditLog", "PgLedger", "PgRegistry", "PgTenantStore",
           "PgWalletDirectory"]
