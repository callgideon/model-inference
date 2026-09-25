"""G8 points 2-3: the bounded, idempotent pilot transition between accounting regimes.

    python -m infrx.operations.cli credit-transition --dry-run --card <v> \\
        --input-rate <r> --output-rate <r>                     # read-only report
    python -m infrx.operations.cli credit-transition --card <v> --input-rate <r> \\
        --output-rate <r> --drain-timeout-s 600 --idempotency-key t-1 --reason "..."

What moves is three database flags (0006 `infrx.feature_flags`) and nothing else:

1. **Inventory** (read-only, one repeatable-read snapshot): flags; jobs in flight per
   regime; held/unknown holds per unit; the legacy USD statement of every organization
   with USD history and each USD wallet's available amount; CREDIT wallets, ledger kinds
   and entitlements; both reconciliation views' drift; every rate card with the jobs it
   pins and whether the effective catalog listing names it; every API key's metadata (no
   hash, no secret).
2. **Plan**: the flag changes, the environment the gateway and worker must be restarted
   with, and the blockers. Moving to `credit` requires the operator-approved card (P-01):
   published, not provisional, the card the public alias's effective listing names, at
   exactly the rates the operator restates. Any drift blocks either direction.
3. **Apply** (operator credential, audited once under its idempotency key): freeze the
   source regime's admission, wait (bounded) until every transaction open when the freeze
   committed has ended and then until none of the source's jobs is in flight, then
   enable the target's flags. A drain that does not finish leaves the freeze in place
   and exits with the report; re-running continues from where it stopped. Every flag
   write waits on the flag's row lock (an admission in flight holds it FOR SHARE, D10)
   no longer than the drain's remaining bound, then refuses with `open_transactions`.

Never: a balance converted, a USD row relabelled, a historical card rewritten, or a job
moved between regimes. Every accepted job settles in the regime and at the card or price
version it was admitted at (0006's regime CHECK; `terminalize` per regime). The
gateway's ACCOUNTING_REGIME/ACTIVE_RATE_CARD_VERSION are environment, not database: the
report names them for the restart, which the coordinator performs on the box.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Callable

from ..contracts import errors
from ..contracts.v2 import fixtures as v2fix
from ..contracts.v2.money_units import Credit

CREDIT, LEGACY = "credit", "legacy_usd"
TARGETS = (CREDIT, LEGACY)
#: The flag each regime's new admissions need (0006 `jobs_admission_guard`, 0011).
ADMISSION_FLAG = {CREDIT: "credit_admission", LEGACY: "legacy_usd_admission"}
#: What a target enables; a CREDIT consumer also needs the signup grant (A1).
ENABLE = {CREDIT: ("credit_admission", "signup_grant"), LEGACY: ("legacy_usd_admission",)}
#: F11 / F2C-C: the hosted seed's provisional card, and the id `marlin_release` mints.
SEED_PROVISIONAL = v2fix.RATE_CARD_VERSION
MINTED_SUFFIX = "_provisional_p01"
#: An approval that says it is not one (P-01 pending). Case-insensitive.
UNAPPROVED_MARKERS = ("p-01", "provisional", "pending")


#: What a flag write may run past its lock bound (the UPDATE and its triggers).
STATEMENT_MARGIN_MS = 1000


class FlagLocked(Exception):
    """A flag row stayed locked (an admission in flight holds it) past the bound."""


class TransitionBlocked(errors.StateConflict):
    """The procedure stopped; `report` says why and what already changed."""

    def __init__(self, report: dict) -> None:
        self.report = report
        codes = ", ".join(b["code"] for b in report["blockers"]) or "none"
        super().__init__(f"transition to {report['target']} blocked: {codes}")


def unapproved(approved_by: str, provisional: bool = False) -> str | None:
    """Why a card is not an operator-approved price, or None (P-01)."""
    text = (approved_by or "").strip().lower()
    if provisional or not text or any(m in text for m in UNAPPROVED_MARKERS):
        return f"approval {approved_by!r} is provisional or missing (P-01 pending)"
    return None


def _exact(value: str | None) -> str | None:
    """An operator-restated rate, canonical; None when not given."""
    if value is None:
        return None
    try:
        return str(Credit(value))
    except (ValueError, ArithmeticError, TypeError):
        raise errors.InvalidRequest(f"a rate is an exact CREDIT decimal, not {value!r}") \
            from None


# --------------------------------------------------------------------- inventory
_QUERIES = {
    "flags": "select name, enabled, updated_by, reason, updated_at from infrx.feature_flags "
             "order by name",
    "in_flight": "select accounting_regime, state, count(*) from infrx.jobs "
                 "where state in ('preparing', 'queued', 'running') group by 1, 2 order by 1, 2",
    "unknown_usage": "select accounting_regime, count(*) from infrx.jobs "
                     "where settlement_state = 'held_unknown' group by 1 order by 1",
    "holds": "select 'legacy_usd', state, count(*), sum(amount)::text from infrx.credit_holds "
             "where state in ('held', 'unknown') group by 2 union all "
             "select 'credit', state, count(*), sum(amount)::text from infrx.credit_wallet_holds "
             "where state in ('held', 'unknown') group by 2 order by 1, 2",
    # The legacy statement per organization with USD history (C0's own function: USD
    # stays USD, and a nonzero balance reads rollout_hold).
    "usd_statements": "select s.org_id::text, s.balance, s.entry_count, s.rollout_hold "
                      "from public.organizations o "
                      "cross join lateral public.console_legacy_usd_statement(o.id) s "
                      "where s.entry_count > 0 order by 1",
    "usd_wallets": "select org_id::text, available::text from infrx.wallets "
                   "where available <> 0 order by 1",
    "usd_usage_events": "select count(*) from public.usage_events "
                        "where accounting_regime = 'legacy_usd'",
    "credit_wallets": "select kind, count(*), coalesce(sum(available), 0)::text "
                      "from infrx.credit_wallets group by 1 order by 1",
    "credit_ledger": "select kind, count(*), sum(amount)::text from infrx.credit_ledger "
                     "group by 1 order by 1",
    "entitlements": "select count(*) from infrx.signup_entitlements",
    "drift": "select 'CREDIT', wallet_id::text, ledger_drift::text, reserved_drift::text "
             "from infrx.credit_wallet_reconciliation "
             "where ledger_drift <> 0 or reserved_drift <> 0 union all "
             "select 'USD', org_id::text, ledger_drift::text, reserved_drift::text "
             "from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0",
    "cards": "select c.rate_card_version, c.deployment_revision_id::text, "
             "c.input_rate_per_million::text, c.output_rate_per_million::text, c.effective_at, "
             "c.approved_by, c.provisional, "
             "(select count(*) from infrx.jobs j where j.rate_card_version = c.rate_card_version) "
             "from infrx.rate_card_versions c order by c.effective_at, c.rate_card_version",
    # What 0008 `resolve_admission_pins` pins for the bare alias (the public identity).
    "listing": "select l.version, l.deployment_revision_id::text, l.rate_card_version, "
               "l.effective_at from infrx.catalog_listings l "
               "where l.public_model_id = %(alias)s and l.effective_at <= infrx.now() "
               "order by l.version desc limit 1",
    "keys": "select id::text, prefix, audience, org_id::text, created_at, revoked_at, "
            "last_used_at from public.api_keys order by created_at, id",
    "now": "select infrx.now()",
}
#: The transactions open in this database now, by the virtual transaction id each holds
#: from its start (`pg_locks` and the pid, database and user of `pg_stat_activity` are
#: readable by any role; autovacuum has no user and admits nothing). Waiting until those
#: open at the freeze have ended is CREATE INDEX CONCURRENTLY's wait: before 0021,
#: `require_feature` read the flag without a lock, so an admission that read it before the
#: freeze committed could still commit its job (review G8-R1). From 0021 on it reads FOR
#: SHARE and the freeze waits for such admissions itself (V-G8TL-6); this wait then finds
#: nothing of theirs and matters only on a schema before 0021. The caller's own listing
#: drops out of the intersection: each listing is a new transaction.
_OPEN_TRANSACTIONS = (
    "select l.virtualxid from pg_catalog.pg_locks l "
    "join pg_catalog.pg_stat_activity a on a.pid = l.pid "
    "where l.locktype = 'virtualxid' and l.virtualxid = l.virtualtransaction "
    "and a.datname = current_database() and a.usesysid is not null")


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


class PgTransition:
    """The flags and the read-only inventory, as `service_role` (0004/0006 grants: SELECT
    on the relations, UPDATE of the flag columns). Nothing here writes money."""

    def __init__(self, connect) -> None:
        self._connect = connect

    async def inventory(self, alias: str = v2fix.PUBLIC_MODEL_ID) -> dict:
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute("set transaction isolation level repeatable read, read only")
                raw = {name: await (await conn.execute(
                    sql, {"alias": alias} if name == "listing" else None)).fetchall()
                       for name, sql in _QUERIES.items()}
        finally:
            await conn.close()
        return shape(raw, alias)

    async def open_transactions(self) -> frozenset[str]:
        conn = await self._connect()
        try:
            rows = await (await conn.execute(_OPEN_TRANSACTIONS)).fetchall()
        finally:
            await conn.close()
        return frozenset(vxid for vxid, in rows)

    async def set_flag(self, name: str, enabled: bool, actor: str, reason: str, *,
                       lock_timeout_s: float) -> bool:
        """True when this call changed it (attributed on the row); False when it already was.
        Raises `FlagLocked` when the row stays locked past `lock_timeout_s`: an admission
        holds it FOR SHARE until it commits (D10's `require_feature`), so an unbounded
        UPDATE would wait on a parked admission forever. `lock_timeout` bounds one wait;
        overlapping admissions chain waits (one MultiXact after another), so
        `statement_timeout` bounds the whole write (V-G8TL-1)."""
        # PostgreSQL reads 0 as "no limit": the floor keeps a spent bound bounded. The
        # statement's margin lets a 1 ms lock bound still run the UPDATE and its triggers.
        ms = max(1, int(lock_timeout_s * 1000))
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(f"set local lock_timeout = {ms}")
                await conn.execute(f"set local statement_timeout = {ms + STATEMENT_MARGIN_MS}")
                row = await (await conn.execute(
                    "update infrx.feature_flags set enabled = %s, updated_by = %s, reason = %s, "
                    "updated_at = infrx.now() where name = %s and enabled <> %s returning name",
                    (enabled, actor[:200], reason[:500], name, enabled))).fetchone()
        except Exception as exc:
            # lock_not_available, query_canceled (the statement bound)
            if getattr(exc, "sqlstate", None) in ("55P03", "57014"):
                raise FlagLocked(name) from exc
            raise
        finally:
            await conn.close()
        return row is not None


def shape(raw: dict, alias: str) -> dict:
    """The query rows as the report's JSON (exact decimal strings, ISO instants)."""
    listing = raw["listing"][0] if raw["listing"] else None
    named = listing[2] if listing else None
    return {
        "as_of": _iso(raw["now"][0][0]), "alias": alias,
        "flags": {n: {"enabled": e, "updated_by": by, "reason": why, "updated_at": _iso(at)}
                  for n, e, by, why, at in raw["flags"]},
        "in_flight": _nest(raw["in_flight"]),
        "unknown_usage": {regime: n for regime, n in raw["unknown_usage"]},
        "holds": {f"{regime}/{state}": {"count": n, "amount": amount,
                                        "unit": "USD" if regime == LEGACY else "CREDIT"}
                  for regime, state, n, amount in raw["holds"]},
        "usd": {"unit": "USD",
                "statements": [{"org_id": o, "balance": bal, "entries": n, "rollout_hold": hold}
                               for o, bal, n, hold in raw["usd_statements"]],
                "wallets_available": {o: amount for o, amount in raw["usd_wallets"]},
                "usage_events": raw["usd_usage_events"][0][0]},
        "credit": {"unit": "CREDIT",
                   "wallets": {k: {"count": n, "available": a} for k, n, a in raw["credit_wallets"]},
                   "ledger": {k: {"count": n, "amount": a} for k, n, a in raw["credit_ledger"]},
                   "signup_entitlements": raw["entitlements"][0][0]},
        "drift": [{"unit": u, "wallet": w, "ledger_drift": ld, "reserved_drift": rd}
                  for u, w, ld, rd in raw["drift"]],
        "listing": None if listing is None else {
            "version": listing[0], "deployment_revision_id": listing[1],
            "rate_card_version": named, "effective_at": _iso(listing[3])},
        "cards": [{"rate_card_version": v, "deployment_revision_id": d, "input_rate": i,
                   "output_rate": o, "effective_at": _iso(at), "approved_by": by,
                   "provisional": prov, "jobs_pinned": n, "named_by_listing": v == named}
                  for v, d, i, o, at, by, prov, n in raw["cards"]],
        "keys": [{"key_id": k, "prefix": p, "audience": a, "org_id": o, "created_at": _iso(c),
                  "revoked_at": _iso(r), "last_used_at": _iso(u)}
                 for k, p, a, o, c, r, u in raw["keys"]],
    }


def _nest(rows) -> dict:
    out: dict[str, dict[str, int]] = {}
    for regime, state, n in rows:
        out.setdefault(regime, {})[state] = n
    return out


# --------------------------------------------------------------------- plan
def plan(inv: dict, *, target: str, card: str | None = None, input_rate: str | None = None,
         output_rate: str | None = None) -> dict:
    """What a transition to `target` would change now, and what blocks it. Pure."""
    if target not in TARGETS:
        raise errors.InvalidRequest(f"the target regime is one of {TARGETS}")
    source = LEGACY if target == CREDIT else CREDIT
    blockers: list[dict] = []
    if target == CREDIT:
        blockers += _card_blockers(inv, card, _exact(input_rate), _exact(output_rate))
    if inv["drift"]:
        blockers.append({"code": "drift", "detail": f"{len(inv['drift'])} wallet(s) disagree "
                         "with their ledger or live holds; reconcile before any switch"})
    flying = inv["in_flight"].get(source, {})
    if sum(flying.values()):
        blockers.append({"code": "in_flight", "detail": f"{sum(flying.values())} {source} "
                         f"job(s) in flight {flying}: freeze {ADMISSION_FLAG[source]} and "
                         "drain (they settle in their own regime)"})
    flags = inv["flags"]
    changes = [{"flag": ADMISSION_FLAG[source], "enabled": False,
                "why": f"freeze new {source} admission"}] \
        if flags.get(ADMISSION_FLAG[source], {}).get("enabled") else []
    changes += [{"flag": f, "enabled": True, "why": f"{target} admission"}
                for f in ENABLE[target] if not flags.get(f, {}).get("enabled")]
    env = {"ACCOUNTING_REGIME": target}
    if target == CREDIT:
        env["ACTIVE_RATE_CARD_VERSION"] = card
    return {"target": target, "as_of": inv["as_of"], "blockers": blockers,
            "would_change": changes, "restart_with": env,
            "public_card": _public_card(inv, card) if target == CREDIT else None,
            "notes": _notes(inv, source), "inventory": inv}


def _card_blockers(inv, card, input_rate, output_rate) -> list[dict]:
    if not card or input_rate is None or output_rate is None:
        return [{"code": "card_missing", "detail": "name the approved card and restate its "
                 "input/output rates (--card, --input-rate, --output-rate): P-01"}]
    found = next((c for c in inv["cards"] if c["rate_card_version"] == card), None)
    if found is None:
        return [{"code": "card_missing", "detail": f"card {card} is not published"}]
    out = []
    why = unapproved(found["approved_by"], found["provisional"])
    if why:
        out.append({"code": "card_unapproved", "detail": f"card {card}: {why}"})
    if (found["input_rate"], found["output_rate"]) != (input_rate, output_rate):
        out.append({"code": "card_rates_mismatch",
                    "detail": f"card {card} prices {found['input_rate']}/{found['output_rate']} "
                              f"CREDIT per million input/output; the operator restated "
                              f"{input_rate}/{output_rate}"})
    if not found["named_by_listing"]:
        named = (inv["listing"] or {}).get("rate_card_version")
        out.append({"code": "card_not_listed",
                    "detail": f"the effective listing of {inv['alias']} names {named}, not "
                              f"{card}: publish it for the listed deployment (publish-card)"})
    return out


def _public_card(inv, card) -> dict:
    """F11 / F2C-C: which id is public after the switch, and what stays history."""
    named = (inv["listing"] or {}).get("rate_card_version")
    ids = {c["rate_card_version"] for c in inv["cards"]}
    return {"public": card, "unit": "CREDIT", "listing_names_now": named,
            "seed_provisional": {"id": SEED_PROVISIONAL, "present": SEED_PROVISIONAL in ids,
                                 "named_by_listing": named == SEED_PROVISIONAL},
            "minted_provisional": sorted(i for i in ids if i.endswith(MINTED_SUFFIX)),
            "history": sorted(i for i in ids if i != card),
            "rule": "cards are immutable; every job keeps the card it pinned (R78)"}


def _notes(inv, source) -> list[str]:
    notes = []
    held = [s for s in inv["usd"]["statements"] if s["rollout_hold"]]
    if held:
        notes.append(f"{len(held)} organization(s) keep a nonzero legacy USD balance on their "
                     "USD statement (rollout_hold, R72): never converted to CREDIT")
    for regime, n in inv["unknown_usage"].items():
        notes.append(f"{n} {regime} job(s) with unknown usage release under their own "
                     "regime's 24 h rule, never debited later")
    return notes


def in_flight(inv: dict, regime: str) -> int:
    return sum(inv["in_flight"].get(regime, {}).values())


# --------------------------------------------------------------------- apply
async def apply(op, store: PgTransition, *, target: str, card: str | None = None,
                input_rate: str | None = None, output_rate: str | None = None,
                idempotency_key: str, reason: str, drain_timeout_s: float = 0.0,
                poll_s: float = 2.0, sleep: Callable = asyncio.sleep,
                monotonic: Callable[[], float] = time.monotonic,
                freeze_only: bool = False) -> dict:
    """Freeze, drain (bounded), enable. `op` is an `OperatorSession`: the write is audited
    once under `idempotency_key`; a blocked run audits nothing and raises
    `TransitionBlocked` with the report (its freeze, if any, stays - rerun to continue).
    `freeze_only` stops after the drain with both regimes paused: the window in which a
    runtime is replaced (D10's `readiness_cutover_check` wants admission paused in both);
    a later run without it enables the target."""
    rates = {"card": card, "input_rate": input_rate, "output_rate": output_rate}
    source = LEGACY if target == CREDIT else CREDIT

    async def write(_operation_id: str):
        first = plan(await store.inventory(), target=target, **rates)
        if [b for b in first["blockers"] if b["code"] != "in_flight"]:
            raise TransitionBlocked({**first, "applied": []})
        applied = []
        deadline = monotonic() + drain_timeout_s

        async def open_transactions(n: int) -> TransitionBlocked:
            report = plan(await store.inventory(), target=target, **rates)
            report["blockers"].append({
                "code": "open_transactions",
                "detail": f"{n} transaction(s) open across the change of "
                          f"{ADMISSION_FLAG[source]} may still commit {source} work; "
                          "rerun once they have ended"})
            return TransitionBlocked({**report, "applied": applied})

        async def flag(name: str, enabled: bool) -> None:
            try:
                changed = await store.set_flag(name, enabled, op.principal, reason,
                                               lock_timeout_s=max(deadline - monotonic(), 0.0))
            except FlagLocked:
                # D10: an admission in flight holds the flag FOR SHARE; waited out to the bound.
                raise await open_transactions(1) from None
            if changed:
                applied.append({"flag": name, "enabled": enabled})

        await flag(ADMISSION_FLAG[source], False)
        # The freeze has committed; a transaction open now may have read the flag before it
        # and still commit a job the drain cannot see (a schema before D10's FOR SHARE read).
        # Wait until all of those have ended (one that begins later reads the flag frozen),
        # within the same bound.
        straddlers = await store.open_transactions()
        while straddlers := straddlers & await store.open_transactions():
            if deadline <= monotonic():
                raise await open_transactions(len(straddlers))
            await sleep(poll_s)
        while in_flight(inv := await store.inventory(), source):
            if monotonic() >= deadline:
                raise TransitionBlocked({**plan(inv, target=target, **rates),
                                         "applied": applied})
            await sleep(poll_s)
        for name in () if freeze_only else ENABLE[target]:
            await flag(name, True)
        final = plan(await store.inventory(), target=target, **rates)
        if final["blockers"]:
            # Belt and braces after the wait above: rerun (idempotent) until it clears.
            raise TransitionBlocked({**final, "applied": applied})
        return ({"flags": {f: v["enabled"] for f, v in first["inventory"]["flags"].items()}},
                {"target": target, "applied": applied, "restart_with": final["restart_with"],
                 "public_card": final["public_card"],
                 "flags": {f: v["enabled"] for f, v in final["inventory"]["flags"].items()}})

    result, _ = await op._once("transition", idempotency_key, reason, None,
                               {"target": target, **rates, "freeze_only": freeze_only}, write)
    return result


__all__ = ["FlagLocked", "PgTransition", "TransitionBlocked", "apply", "plan", "unapproved"]
