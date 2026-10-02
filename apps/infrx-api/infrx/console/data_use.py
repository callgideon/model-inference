"""AP-07a: the grantor's data-use controls (research/plan/api-lifecycle/contracts.md §7).

No migration (0063 not taken): the schema already carries every decision, and this module
writes it the way its readers already read it.

* **Key-level capture** is `public.api_keys.trace_mode` (0005; null = off). The gateway reads it
  with the organization's consent head through 0057's `infrx.trace_consent` and admits the
  LOWER of the two (`gateway.capture.effective`, reused here for `effective_mode`).
* **The consent version** is `infrx.consent_history` (0003: append-only, a revocation the only
  edit). A capture change is a consent decision (0005's note), so `put_capture` appends the next
  version - its mode the highest any live key of the organization then holds, so the head never
  caps a key below its own choice - and sets the key's mode, in one transaction, compare-and-set
  on the version the caller read. The same decision again writes nothing (a lost response).
* **Purpose grants with expiry** are `infrx.lab_access_grants` (0027: one versioned grant per
  grantor and recipient provider; every change, a revocation included, the next immutable
  version), written only through `lab_put_access_grant` / `lab_revoke_access_grant`. The
  purposes are the frozen `DataPurpose` four: capture, provider_sharing, external_judging,
  training. Annotation and export are `training` (`contracts.lab.records.GATE_PURPOSES`: the
  export gate checks training; teacher annotation runs on exported datasets) - no new purpose.
  `grant_version` is the compare-and-set token: the pair's current version (0: none).

The grantor is always the actor's own organization and the actor must be its owner, in a
verified web session: no body field names an organization (`api.Wire` refuses extras), so a
provider member - administrator or not - cannot grant itself a customer's content, and the SQL
writers check the owner again. A suspended organization writes nothing (R33) but one thing:
it may still WITHDRAW a grant - a revocation goes through 0066's revoke-only door
`infrx.lab_withdraw_access_grant`, the owner check without 0027's suspension refusal.

ponytail: direct statements on the platform pool (`service_role`, as `PgAccessStore` and
`PgTenantStore`); a dedicated `infrx_runtime` login needs SECURITY DEFINER writers (0063).
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from ..contracts import api, errors
from ..contracts.v2.records import AccessGrant, DataCategory, DataPurpose
from ..gateway.capture import effective
from ..state import rpc
from ..state.jobstore import domain_error
from ..state.lab_access import PgAccessStore

Mode = Literal["off", "minimal", "full"]
#: Ascending, as the gateway orders them (`capture.ORDER`).
MODES: tuple[str, ...] = ("off", "minimal", "full")
GrantState = Literal["active", "expired", "revoked"]

OWNER_SQL = ("select o.suspended, exists (select 1 from public.org_members m where m.org_id = o.id "
             "and m.user_id = %s and m.role = 'owner') from public.organizations o where o.id = %s")
HEAD_SQL = ("select consent_version, trace_mode, content_retention_days, evaluation_consent, "
            "effective_at, revoked_at from infrx.consent_history where org_id = %s "
            "order by consent_version desc limit 1")
KEYS_SQL = ("select id::text, name, trace_mode from public.api_keys where org_id = %s "
            "and revoked_at is null order by created_at, id")
GRANTS_SQL = ("select infrx.lab_grant_json(g) from infrx.lab_access_grants g "
              "where g.grantor_org_id = %s and g.version = (select max(h.version) from "
              "infrx.lab_access_grants h where h.grant_id = g.grant_id) "
              "order by g.recipient_provider_org_id")


class CaptureRequest(api.Wire):
    """The key's mode plus the organization-level consent it is decided under."""

    mode: Mode
    consent_version: int = Field(ge=0)
    retention_days: int = Field(default=30, ge=1, le=90)
    evaluation_consent: bool = False

    @model_validator(mode="after")
    def _evaluation_needs_full(self) -> CaptureRequest:
        if self.evaluation_consent and self.mode != "full":
            raise ValueError("evaluation consent is given with full capture only")
        return self


class GrantRequest(api.Wire):
    provider_org_id: uuid.UUID
    model_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    categories: tuple[DataCategory, ...] = Field(min_length=1)
    purposes: tuple[DataPurpose, ...] = Field(min_length=1)
    retention_days: int = Field(ge=1, le=90)
    expires_at: AwareDatetime | None = None
    grant_version: int = Field(ge=0)


class Consent(api.Wire):
    version: int
    mode: Mode
    retention_days: int | None
    evaluation_consent: bool
    effective_at: datetime | None
    revoked_at: datetime | None


class KeyCapture(api.Wire):
    key_id: str
    name: str
    mode: Mode
    effective_mode: Mode          # what the gateway admits the key's next request with


class Grant(api.Wire):
    grant_id: str
    version: int
    provider_org_id: str
    model_ids: tuple[str, ...]
    categories: tuple[DataCategory, ...]
    purposes: tuple[DataPurpose, ...]
    retention_days: int
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    state: GrantState


class DataUseDoc(api.Wire):
    consent: Consent
    keys: tuple[KeyCapture, ...]
    grants: tuple[Grant, ...]


NO_CONSENT = Consent(version=0, mode="off", retention_days=None, evaluation_consent=False,
                     effective_at=None, revoked_at=None)


def grant_doc(g: AccessGrant, now: datetime) -> Grant:
    state: GrantState = ("active" if g.is_current(now)
                         else "revoked" if g.revoked_at is not None else "expired")
    return Grant(grant_id=g.grant_id, version=g.version, provider_org_id=g.recipient_provider_org_id,
                 model_ids=g.model_ids, categories=g.categories, purposes=g.purposes,
                 retention_days=g.retention_days, effective_at=g.effective_at,
                 expires_at=g.expires_at, revoked_at=g.revoked_at, state=state)


def same_scope(g: AccessGrant, body: GrantRequest) -> bool:
    return (set(g.model_ids), set(g.categories), set(g.purposes), g.retention_days,
            g.expires_at) == (set(body.model_ids), set(body.categories), set(body.purposes),
                              body.retention_days, body.expires_at)


async def _one(conn, sql: str, params: tuple) -> Any:
    return await (await conn.execute(sql, params)).fetchone()


async def _all(conn, sql: str, params: tuple) -> list:
    return await (await conn.execute(sql, params)).fetchall()


@asynccontextmanager
async def _transaction(conn):
    """BEGIN/COMMIT on `execute` alone (as `console.actions`): the gateway pool's connection
    (`pilot._Pooled`) is execute + close and has no `transaction()`, and a pooled
    connection never goes back to the pool mid-transaction."""
    await conn.execute("begin")
    try:
        yield conn
    except BaseException:
        await conn.execute("rollback")
        raise
    await conn.execute("commit")


class DataUse:
    """The five operations behind `routes.console_data_use`. `actor` is the server's own."""

    def __init__(self, connect) -> None:
        self.connect = connect
        self.store = PgAccessStore(connect)

    async def _grantor(self, actor: api.Actor, *, write: bool = False) -> tuple[str, str]:
        """(organization, owner): the actor's own organization, which it must own."""
        if actor.audience != "session" or not actor.user_id or not actor.org_id:
            raise errors.Forbidden("data use is decided in a signed-in web session")
        async with rpc.connection(self.connect) as conn:
            row = await _one(conn, OWNER_SQL, (actor.user_id, actor.org_id))
        if row is None or not row[1]:
            raise errors.Forbidden("only the organization's owner decides its data use")
        if write and row[0]:
            raise errors.OrgSuspended(f"organization {actor.org_id} is suspended")
        return actor.org_id, actor.user_id

    async def _grants(self, org: str, now: datetime) -> tuple[Grant, ...]:
        async with rpc.connection(self.connect) as conn:
            rows = await _all(conn, GRANTS_SQL, (org,))
        return tuple(grant_doc(AccessGrant.model_validate(row), now) for (row,) in rows)

    async def read(self, actor: api.Actor) -> DataUseDoc:
        org, _ = await self._grantor(actor)
        async with rpc.connection(self.connect) as conn:
            (now,) = await _one(conn, "select infrx.now()", ())
            head = await _one(conn, HEAD_SQL, (org,))
            keys = await _all(conn, KEYS_SQL, (org,))
        consent = NO_CONSENT if head is None else Consent(
            version=head[0], mode=head[1], retention_days=head[2], evaluation_consent=head[3],
            effective_at=head[4], revoked_at=head[5])
        return DataUseDoc(consent=consent, keys=tuple(
            KeyCapture(key_id=key, name=name, mode=mode or "off",
                       effective_mode=effective((mode, *(head or (None,) * 6)), org,
                                                now).trace_mode.value)
            for key, name, mode in keys), grants=await self._grants(org, now))

    async def grants(self, actor: api.Actor) -> tuple[Grant, ...]:
        org, _ = await self._grantor(actor)
        return await self._grants(org, await self.store.db_now())

    async def put_capture(self, actor: api.Actor, key_id: str, body: CaptureRequest) -> DataUseDoc:
        org, user = await self._grantor(actor, write=True)
        try:
            uuid.UUID(key_id)
        except ValueError:
            raise errors.NotFound("no such key") from None
        async with rpc.connection(self.connect) as conn, _transaction(conn):
            await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                               (f"consent_history/{org}",))
            key = await _one(conn, "select trace_mode from public.api_keys where id = %s and "
                             "org_id = %s and revoked_at is null for update", (key_id, org))
            if key is None:
                raise errors.NotFound("no such key")
            head = await _one(conn, HEAD_SQL, (org,))
            others = await _all(conn, "select coalesce(trace_mode, 'off') from public.api_keys "
                                "where org_id = %s and revoked_at is null and id <> %s",
                                (org, key_id))
            mode = max((body.mode, *(m for (m,) in others)), key=MODES.index)
            wanted = (mode, body.retention_days, body.evaluation_consent, None)
            if (key[0] or "off") == body.mode and head is not None \
                    and (head[1], head[2], head[3], head[5]) == wanted:
                pass                                   # the same decision again: nothing new
            elif (head[0] if head else 0) != body.consent_version:
                raise errors.StateConflict("the consent changed since it was read")
            else:
                await conn.execute("update public.api_keys set trace_mode = %s where id = %s",
                                   (body.mode, key_id))
                await conn.execute(
                    "insert into infrx.consent_history (org_id, consent_version, trace_mode, "
                    "content_retention_days, evaluation_consent, actor_principal, effective_at) "
                    "values (%s, %s, %s, %s, %s, %s, infrx.now())",
                    (org, body.consent_version + 1, mode, body.retention_days,
                     body.evaluation_consent, user))
        return await self.read(actor)

    async def put_grant(self, actor: api.Actor, body: GrantRequest) -> tuple[Grant, bool]:
        """(the grant, whether a new version was written)."""
        org, user = await self._grantor(actor, write=True)
        provider = str(body.provider_org_id)
        current = await self.store.current_grant(org, provider)
        now = await self.store.db_now()
        if current is not None and current.is_current(now) and same_scope(current, body):
            return grant_doc(current, now), False
        # ponytail: compare-then-write; two racing writers of one pair get consecutive
        # versions (0027 serializes the pair). The check moves into SQL with a 0063 writer.
        if (current.version if current else 0) != body.grant_version:
            raise errors.StateConflict("the grant changed since it was read")
        written = await self.store.put_grant(user, {
            "grantor_org_id": org, "recipient_provider_org_id": provider,
            "model_ids": list(body.model_ids), "categories": [c.value for c in body.categories],
            "purposes": [p.value for p in body.purposes], "retention_days": body.retention_days,
            **({"expires_at": body.expires_at.isoformat()} if body.expires_at else {})})
        return grant_doc(written, await self.store.db_now()), True

    async def revoke_grant(self, actor: api.Actor, grant_id: str) -> Grant:
        org, user = await self._grantor(actor)          # suspended or not: a withdrawal
        try:
            uuid.UUID(grant_id)
        except ValueError:
            raise errors.NotFound("no such grant") from None
        async with rpc.connection(self.connect) as conn:
            row = await _one(conn, "select recipient_provider_org_id::text from "
                             "infrx.lab_access_grants where grant_id = %s and grantor_org_id = %s "
                             "limit 1", (grant_id, org))
        if row is None:
            raise errors.NotFound("no such grant")
        current = await self.store.current_grant(org, row[0])
        if current is None or current.revoked_at is None:
            current = AccessGrant.model_validate(await rpc.call(
                self.connect, "lab_withdraw_access_grant", {
                    "actor_user_id": user, "grantor_org_id": org,
                    "recipient_provider_org_id": row[0]}, error=domain_error))
        return grant_doc(current, await self.store.db_now())
