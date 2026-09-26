"""E3C s15 (DUR-CAP): concurrent admissions across keys and organizations at the caps.

Two organizations (the journey's alpha and beta), two consumer keys each - the second issued
through the operator CLI - burst `POST /v1/jobs` all released at one barrier:

* **capacity**: through TWO gateway processes, 16 requests (4 per key) against the box's caps
  MAX_ACTIVE_JOBS 4, per organization 3, per key 2, with no worker running so every admitted
  job stays active. However the one admission lock orders them, exactly 4 are admitted and
  no scope is past its cap; the other 12 are the declared 429 `capacity_exhausted` with a
  Retry-After, and leave no job, hold, reservation or idempotency mapping.
* **balance**: alpha's wallet funded for exactly two more holds; 8 alpha requests race an
  operator debit of one hold (G6B's audited adjustment, D5's `grant_credit`, in-process so
  it really lands inside the burst) and beta's 4 requests. Alpha admits exactly what its
  wallet can hold (2, less the debit if it landed first); the refused ones are the declared
  402 `insufficient_credit` and hold nothing; available ends at exactly the half hold left,
  never negative; beta is untouched.

Both: a stable lock order - no 40P01 in any answer, in the gateways' logs, in the namespace's
PostgreSQL log or in `pg_stat_database.deadlocks` - and every admitted job then runs and
settles once, the books conserved per wallet (the operator adjustments counted)."""
from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

CAP, PER_ORG, PER_KEY = 4, 3, 2
CAPS = {"MAX_ACTIVE_JOBS": str(CAP), "MAX_ACTIVE_JOBS_PER_ORG": str(PER_ORG),
        "MAX_ACTIVE_JOBS_PER_KEY": str(PER_KEY)}
EIGHT_PLACES = Decimal("0.00000001")


def keyholders(trip, workdir: Path) -> list:
    """alpha and beta with their journey keys, then a second consumer key each, issued
    through the operator CLI (the secret file read once and removed)."""
    holders = [trip.world.alpha, trip.world.beta]
    for tenant in (trip.world.alpha, trip.world.beta):
        path = workdir / f"{tenant.name}-2.key"
        path.unlink(missing_ok=True)
        status, issued = world.cli(trip, "issue-key", "--user", tenant.user_id, "--name",
                                   f"{tenant.name} second key", "--secret-file", str(path),
                                   "--idempotency-key", f"k2-{tenant.name}",
                                   "--reason", "e3c s15 second key")
        assert status == 0 and issued["org_id"] == tenant.org_id, issued
        secret = path.read_text().strip()
        path.unlink()
        holders.append(SimpleNamespace(name=f"{tenant.name}-2", secret=secret,
                                       key_id=issued["key_id"], org_id=tenant.org_id,
                                       user_id=tenant.user_id, wallet=tenant.wallet))
    return holders


def burst(trip, urls, plan, race=None) -> tuple[list[dict], object]:
    """`plan` = [(holder, idempotency key)]: every `POST /v1/jobs` released at one barrier,
    the gateways taken in turn, and `race()` (if any) released with them. Each answer's
    status, code, Retry-After and request id; `race()`'s answer."""
    import httpx
    start = threading.Barrier(len(plan) + (race is not None))

    def send(n: int) -> dict:
        holder, key = plan[n]
        with httpx.Client(base_url=urls[n % len(urls)], timeout=120.0) as http:
            start.wait()
            answer = http.post("/v1/jobs", headers=trip.headers(holder, key),
                               json={"model": stack.CREDIT_ALIAS, "messages": world.TEXT})
        body = answer.json() if answer.status_code == 202 else {}
        return {"holder": holder.name, "org": holder.org_id, "key": key,
                "status": answer.status_code, "code": world.code(answer),
                "retry_after": answer.headers.get("retry-after"),
                "request_id": body.get("request_id")}

    def raced():
        start.wait()
        return race()
    with ThreadPoolExecutor(len(plan) + 1) as pool:
        other = pool.submit(raced) if race is not None else None
        answers = list(pool.map(send, range(len(plan))))
    return answers, other.result() if other is not None else None


def tally(answers: list[dict]) -> dict:
    counts: dict = {}
    for a in answers:
        slot = counts.setdefault(a["holder"], {})
        label = f"{a['status']} {a['code'] or ''}".strip()
        slot[label] = slot.get(label, 0) + 1
    return counts


def nothing_held(trip, refused: list[dict]) -> None:
    """A refused admission owns nothing: no job, hold, reservation, dispatch or mapping."""
    keys = [a["key"] for a in refused]
    left = trip.one(
        "select (select count(*) from infrx.jobs where idempotency_key = any(%s)), "
        "(select count(*) from infrx.idempotency where key = any(%s))", keys, keys)
    assert left == (0, 0), f"refused admissions left jobs/mappings behind: {left}"


def owned(trip) -> tuple:
    """Everything live admissions hold: active jobs, held CREDIT holds, active reservations,
    dispatch rows, idempotency mappings."""
    return trip.one(
        "select (select count(*) from infrx.jobs where state in "
        "('preparing', 'queued', 'running')), "
        "(select count(*) from infrx.credit_wallet_holds where state = 'held'), "
        "(select count(*) from infrx.capacity_reservations where active), "
        "(select count(*) from infrx.outbox where kind = 'prepare_dispatch'), "
        "(select count(*) from infrx.idempotency)")


def wallet(trip, tenant) -> tuple:
    return trip.one("select ledger_total, reserved_total, available from "
                    "infrx.credit_wallets where wallet_id = %s", tenant.wallet.wallet_id)


def marks(trip) -> tuple[str, int]:
    """Where the deadlock evidence starts: the log instant and the database's counter."""
    return (datetime.now(timezone.utc).isoformat(),
            trip.one("select deadlocks from pg_stat_database where datname = "
                     "current_database()")[0])


def no_deadlock(trip, since: str, before: int, boxes) -> None:
    """Stable lock order: no `deadlock detected` / 40P01 in the namespace's PostgreSQL log
    since `since` (the container is this namespace's own, `assert_ours`) or in the gateways'
    current logs, and the clone's `pg_stat_database.deadlocks` unmoved."""
    harness = world.harness
    container = harness.assert_ours(harness.container_of("postgres"))
    logs = harness.run(["docker", "logs", "--since", since, container], check=False,
                       timeout=60)
    text = (logs.stdout or "") + (logs.stderr or "")
    for box in boxes:
        path = box.workdir / f"gateway-{box.starts['gateway']}.log"
        text += path.read_text(errors="replace") if path.exists() else ""
    lines = [line for line in text.splitlines()
             if "40P01" in line or "deadlock detected" in line.lower()]
    after, = trip.one("select deadlocks from pg_stat_database where datname = "
                      "current_database()")
    assert not lines and after == before, \
        f"a deadlock under the burst: {after - before} counted, log lines {lines[:5]}"


def books(trip, tenant) -> None:
    """Journey.conserved with the wallet's operator adjustments counted: ledger = the one
    grant + adjustments - every settled charge (each its ADMITTED card x usage), reserved =
    the holds still held or unknown, available never negative."""
    from infrx.contracts.v2 import records as v2
    wallet_id = tenant.wallet.wallet_id
    ledger, reserved, available = wallet(trip, tenant)
    adjusted, = trip.one("select coalesce(sum(amount), 0) from infrx.credit_ledger where "
                         "wallet_id = %s and kind = 'operator_adjustment'", wallet_id)
    charged = Decimal(0)
    for card, prompt, completion, amount in trip.db(
            "select infrx.job_admission(j.request_id)->'rate_card', u.prompt_tokens, "
            "u.completion_tokens, u.charged_credits from infrx.jobs j join "
            "public.usage_events u on u.id = j.request_id where j.wallet_id = %s and "
            "u.accounting_regime = 'credit'", wallet_id):
        due = v2.RateCardSnapshot.model_validate(card).debit(prompt, completion).raw("CREDIT")
        assert amount == due, f"{tenant.name}: charged {amount}, card x usage {due}"
        charged += due
    held, = trip.one("select coalesce(sum(amount), 0) from infrx.credit_wallet_holds where "
                     "wallet_id = %s and state in ('held', 'unknown')", wallet_id)
    assert ledger == stack.SIGNUP_GRANT + adjusted - charged, (tenant.name, ledger, adjusted,
                                                              charged)
    assert reserved == held and available >= 0, (tenant.name, reserved, held, available)


def drained(trip, request_ids) -> None:
    """Start the worker: every admitted job runs and settles once."""
    trip.box.start("worker")
    for request_id in request_ids:
        assert world.terminal(trip, request_id, timeout=90.0) == "succeeded", \
            world.diagnose(trip, request_id)
        world.settled_once(trip, request_id)


def test_s15_a_burst_across_keys_and_orgs_admits_exactly_the_capacity(workdir,
                                                                      record_property):
    with world.composed(workdir, start=("gateway",), **CAPS) as trip:
        holders = keyholders(trip, workdir)
        other = world.second_gateway(trip.box)
        other.start("gateway")
        try:
            since, before = marks(trip)
            owned_before = owned(trip)
            plan = [(holder, f"e3c-s15-cap-{holder.name}-{n}") for holder in holders
                    for n in range(4)]
            answers, _ = burst(trip, (trip.box.url, other.url), plan)
        finally:
            other.stop("gateway")
        record_property("burst", tally(answers))
        admitted = [a for a in answers if a["status"] == 202]
        refused = [a for a in answers if a["status"] != 202]
        wrong = [a for a in refused if (a["status"], a["code"]) != (429, "capacity_exhausted")
                 or not (a["retry_after"] or "").isdigit()]
        assert not wrong, f"not the declared capacity refusal: {wrong}"
        assert len(admitted) == CAP, \
            f"{len(admitted)} of {len(plan)} admitted at MAX_ACTIVE_JOBS {CAP} (DUR-CAP): " \
            f"{tally(answers)}"
        scopes = trip.db("select org_id::text, key_id::text, count(*) from infrx.jobs where "
                         "state in ('preparing', 'queued', 'running') group by 1, 2")
        per_org: dict = {}
        for org, _key, count in scopes:
            per_org[org] = per_org.get(org, 0) + count
        assert max(per_org.values()) <= PER_ORG and \
            max(count for *_, count in scopes) <= PER_KEY, f"a scope past its cap: {scopes}"
        nothing_held(trip, refused)
        grew = tuple(after - before for after, before in zip(owned(trip), owned_before))
        assert grew == (CAP, CAP, 3 * CAP, CAP, CAP), \
            f"(jobs, holds, reservations, dispatches, mappings) grew {grew} for {CAP} jobs"
        for tenant in (trip.world.alpha, trip.world.beta):
            _, reserved, available = wallet(trip, tenant)
            assert available >= 0, (tenant.name, available)
        drained(trip, [a["request_id"] for a in admitted])
        again = trip.send(trip.world.alpha, "async", world.TEXT, "e3c-s15-cap-after")
        assert again.status_code == 202, f"capacity not released: {again.text[:200]}"
        assert world.terminal(trip, again.json()["request_id"], timeout=90.0) == "succeeded"
        for tenant in (trip.world.alpha, trip.world.beta):
            trip.conserved(tenant)
        no_deadlock(trip, since, before, (trip.box, other))


def adjust(trip, tenant, amount: Decimal, key: str) -> str | None:
    """An audited operator adjustment of `amount` CREDIT on the tenant's wallet (G6B's
    `OperatorSession.adjust` over D5's `grant_credit`): None when applied, else the code."""
    from infrx.contracts import errors

    async def run():
        operator = await trip.world.ops.operator(trip.world.operator_secret)
        return await operator.adjust(tenant.user_id, str(amount.quantize(EIGHT_PLACES)),
                                     idempotency_key=key, reason="e3c s15 balance cap")
    try:
        asyncio.run(run())
        return None
    except errors.DomainError as refused:
        return refused.code


def test_s15_a_burst_past_the_balance_admits_only_what_the_wallet_holds(workdir,
                                                                       record_property):
    with world.composed(workdir, start=("gateway",)) as trip:
        alpha, beta = trip.world.alpha, trip.world.beta
        holders = keyholders(trip, workdir)
        first = trip.send(alpha, "async", world.TEXT, "e3c-s15-bal-first")
        assert first.status_code == 202, first.text
        hold, = trip.one("select amount from infrx.credit_wallet_holds where request_id = %s",
                         first.json()["request_id"])
        # alpha keeps exactly two more holds and a half: the half is what must be left
        funded = (2 * hold + hold / 2).quantize(EIGHT_PLACES)
        assert adjust(trip, alpha, funded - wallet(trip, alpha)[2], "a-s15-fund") is None
        assert wallet(trip, alpha)[2] == funded, wallet(trip, alpha)
        before_beta = wallet(trip, beta)
        since, before = marks(trip)
        plan = [(holder, f"e3c-s15-bal-{holder.name}-{n}") for holder in holders
                for n in range(4 if holder.org_id == alpha.org_id else 2)]
        answers, debit = burst(trip, (trip.box.url,), plan,
                               race=lambda: adjust(trip, alpha, -hold, "a-s15-race"))
        record_property("burst", {**tally(answers), "operator debit": debit or "applied"})
        mine = [a for a in answers if a["org"] == alpha.org_id]
        theirs = [a for a in answers if a["org"] == beta.org_id]
        admitted = [a for a in mine if a["status"] == 202]
        refused = [a for a in mine if a["status"] != 202]
        assert debit in (None, "invalid_request"), f"the racing debit answered {debit}"
        applied = debit is None
        wrong = [a for a in refused if (a["status"], a["code"]) != (402, "insufficient_credit")]
        assert not wrong, f"not the declared balance refusal: {wrong}"
        assert len(admitted) + applied == 2, \
            f"alpha admitted {len(admitted)} with the debit {'applied' if applied else 'refused'}" \
            f": the wallet held 2.5 holds (DUR-CAP): {tally(answers)}"
        assert all(a["status"] == 202 for a in theirs), f"beta refused: {tally(theirs)}"
        ledger, reserved, available = wallet(trip, alpha)
        assert available == (hold / 2).quantize(EIGHT_PLACES) and available >= 0, \
            f"alpha available {available}, reserved {reserved}, ledger {ledger}"
        assert reserved == (1 + len(admitted)) * hold, (reserved, len(admitted), hold)
        nothing_held(trip, refused)
        assert wallet(trip, beta)[0] == before_beta[0], "beta's ledger moved"
        drained(trip, [first.json()["request_id"],
                       *(a["request_id"] for a in admitted + theirs)])
        books(trip, alpha)
        trip.conserved(beta)
        no_deadlock(trip, since, before, (trip.box,))
