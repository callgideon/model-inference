"""A1: the individual signup grant over PostgreSQL (migration 0015).

One eligibility operation, `public.claim_signup_grant(user, campaign, op)`, serves every
path: the console's auth-callback retry and first login (A2, server-side with the
service role), the operator's `grant_initial` (G6B), and the existing-user backfill
below. The database derives the verification evidence itself; nothing here passes one.

`PgSignup` implements the A1 port shapes of `infrx/operations/ports.py` G6B consumes:
`IdentityDirectory.verified_user` and `Ledger.grant_initial`. `adjust`/`reconcile` are
D5's and are not here. Imports psycopg only through the pool it is handed, so the
request path never loads it.
"""
from __future__ import annotations

import uuid
from collections import Counter

from ..contracts import errors
from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import SignupGrant
from ..operations.ports import VerifiedIdentity

#: `claim_signup_grant` answers; only the first two carry a grant.
GRANTED, REPLAYED = "granted", "replayed"
DENIED = ("unverified", "identity_reused", "rollout_hold", "retired")

CLAIM = "select status from public.claim_signup_grant(%s, %s, %s)"
GRANT_ROW = """
  select e.user_id, e.wallet_id, e.amount::text, e.verification_evidence_ref,
         e.ledger_operation_id, e.campaign_version, e.granted_at, w.personal_org_id
  from infrx.signup_entitlements e join infrx.credit_wallets w on w.wallet_id = e.wallet_id
  where e.user_id = %s and e.entitlement = 'initial_signup_grant'"""
VERIFIED = ("select user_id, personal_org_id, verification_evidence_ref "
            "from infrx.verified_user(%s)")
#: Keyset over individuals (every profile is one auth user), in id order.
PAGE = "select id from public.profiles where id > %s order by id limit %s"
_BEFORE_ALL = uuid.UUID(int=0)


def identity_from(row) -> VerifiedIdentity | None:
    """`infrx.verified_user` row -> the port's identity; unverified or orgless is None."""
    if row is None or row[1] is None or row[2] is None:
        return None
    return VerifiedIdentity(str(row[0]), str(row[1]), row[2])


def answer(status: str, row, identity: VerifiedIdentity) -> tuple[SignupGrant, bool]:
    """The claim's status and the stored grant row -> the `grant_initial` answer.

    Unverified reads as NotFound (no enumeration, as `service._identity` does); every
    other denial is Forbidden and names itself. A grant bound to another organization than
    the identity's personal org is Forbidden too: the caller's transaction rolls it back.
    """
    if status not in (GRANTED, REPLAYED):
        if status == "unverified":
            raise errors.NotFound("no verified individual with that id")
        raise errors.Forbidden(f"signup grant refused: {status}")
    user_id, wallet_id, amount, evidence, operation_id, campaign, granted_at, org = row
    if str(org) != identity.personal_org_id:
        raise errors.Forbidden("the individual's wallet is bound to another organization")
    grant = SignupGrant(user_id=str(user_id), wallet_id=str(wallet_id), amount=Credit(amount),
                        verification_evidence_ref=evidence,
                        ledger_operation_id=str(operation_id), campaign_version=campaign,
                        granted_at=granted_at)
    return grant, status == REPLAYED


class PgSignup:
    """`IdentityDirectory` + `Ledger.grant_initial` over a psycopg `AsyncConnectionPool`
    whose role is `service_role` (or the migration owner)."""

    def __init__(self, pool) -> None:
        self.pool = pool

    async def verified_user(self, user_id: str) -> VerifiedIdentity | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(VERIFIED, (user_id,))
            return identity_from(await cur.fetchone())

    async def grant_initial(self, identity: VerifiedIdentity, operation_id: str,
                            at) -> tuple[SignupGrant, bool]:
        """`at` is the caller's clock; the ledger row takes the database's (`infrx.now()`).

        A grant is answered INSIDE the transaction, so one bound to another org rolls
        back; a denial is answered after it commits, so its recorded reason survives."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                status, = await (await conn.execute(
                    CLAIM, (identity.user_id, "", operation_id))).fetchone()
                row = await (await conn.execute(GRANT_ROW, (identity.user_id,))).fetchone()
                if status in (GRANTED, REPLAYED):
                    return answer(status, row, identity)
            return answer(status, row, identity)


def backfill(conn, campaign: str = "backfill", page: int = 500) -> Counter:
    """Existing individuals, through the same operation a first login calls: one
    transaction per individual, so one refusal never undoes another's grant. Re-running
    it is a no-op (`replayed`). `conn` is a sync psycopg connection in autocommit mode.

    Returns the count per status; a raised refusal counts as `error:<SQLSTATE>` and the
    run continues. Flag off (55000 maintenance) stops it: nothing can be granted then.
    """
    counts: Counter = Counter()
    after = _BEFORE_ALL
    while rows := conn.execute(PAGE, (after, page)).fetchall():
        if rows[-1][0] <= after:
            raise RuntimeError(f"backfill keyset did not advance past {after}")
        for (user_id,) in rows:
            try:
                with conn.transaction():
                    status, = conn.execute(CLAIM, (user_id, campaign, None)).fetchone()
            except Exception as failed:              # psycopg.Error; stays an import-free module
                state = getattr(failed, "sqlstate", None)
                if state is None or state == "55000" and "maintenance" in str(failed):
                    raise
                status = f"error:{state}"
            counts[status] += 1
        after = rows[-1][0]
    return counts
