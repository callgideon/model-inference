#!/usr/bin/env python3
"""D4 item 6: the journal races under REAL PostgreSQL transactions (both images).

As D3's `test_lease_races.py`: LOCK-STEP, both orders - the first transaction runs its call
and stays open, the second is seen WAITING on a lock (or, for the pruner, returns at once:
it takes job rows SKIP LOCKED), then the first commits - and STRESS: callers released
together by a barrier, several rounds. Every call is the boundary as `service_role` with the
adapter's arguments.

    INFRX_D_TASK=d4 uv run --frozen pytest -q tests/d/test_journal_races.py
"""
from __future__ import annotations

import pytest

from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Lease

from . import checks_journal as cj
from . import checks_leases as cl
from . import pgharness
from .checks_leases import lockstep, rpc
from .test_lease_races import CALLERS, ROUNDS, Rig, together

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
TTL = DEFAULTS.lease_ttl_s


def append(lease: Lease, *contents: str, limits=DEFAULTS):
    return lambda c: rpc(c, "append", cj.args(lease, b.events(*contents), limits))


def cancel(rig: Rig, job_id: str):
    return lambda c: rpc(c, "cancel", {"org_id": b.ORG_A, "job_handle": rig.handle(job_id),
                                       "limits": cl.LIMITS})


def journal(rig: Rig, job_id: str) -> list[tuple[int, int, str]]:
    return cj.cursors(cj.journal(rig.owner, job_id))


def test_race__two_appends_on_one_lease_are_contiguous() -> None:
    """DUR-OUTPUT: appends on one lease serialize on the job row. Lock-step, the second
    waits and continues the sequence; under a barrier, every batch commits, the cursors are
    1..N with no duplicate and no hole, and the stored bytes are the journal's."""
    rig = Rig()
    lease = rig.running()
    first, second = lockstep(rig.owner, (rig.service(), append(lease, "a", "b")),
                             (rig.service(), append(lease, "c")))
    assert first[0] is None and second[0] is None, (first, second)
    assert [c["sequence"] for c in second[1]["chunks"]] == [3], second
    for _ in range(ROUNDS):
        lease = rig.running()
        answers = together([(rig.service(), append(lease, f"{n}-x", f"{n}-y"))
                            for n in range(CALLERS)])
        assert all(code is None for code, _ in answers), answers
        minted = sorted(c["sequence"] for _, a in answers for c in a["chunks"])
        assert minted == list(range(1, 2 * CALLERS + 1)), minted
        assert journal(rig, lease.job_id) == [(1, n, "delta") for n in minted]
        rows = cj.journal(rig.owner, lease.job_id)
        assert rig.job(lease.job_id)["journal_stored_bytes"] == sum(r["bytes"] for r in rows)
        rpc(rig.service(), "cancel", {"org_id": b.ORG_A, "job_handle": rig.handle(lease.job_id),
                                      "limits": cl.LIMITS})


def test_race__append_and_cancel_have_one_order() -> None:
    """DUR-OUTPUT / DUR-SETTLE: cancel first -> the append waits, then `already_terminal`,
    nothing stored, the journal is only the terminal event (released_free). Append first ->
    the cancel waits, then answers `held_unknown` with debit 0, the terminal event last."""
    rig = Rig()
    lease = rig.running()
    first, second = lockstep(rig.owner, (rig.service(), cancel(rig, lease.job_id)),
                             (rig.service(), append(lease, "late")))
    assert first[0] is None and first[1]["settlement_state"] == "released_free", first
    assert second[0] == "already_terminal", second
    assert journal(rig, lease.job_id) == [(1, 1, "terminal")]
    lease = rig.running()
    first, second = lockstep(rig.owner, (rig.service(), append(lease, "shown")),
                             (rig.service(), cancel(rig, lease.job_id)))
    assert first[0] is None and second[0] is None, (first, second)
    assert second[1]["settlement_state"] == "held_unknown" and \
        float(second[1]["debit"]) == 0, second
    assert journal(rig, lease.job_id) == [(1, 1, "delta"), (1, 2, "terminal")]
    cj.one_terminal_last(rig.owner, lease.job_id, "append then cancel")


def test_race__append_and_recover_never_regenerate_published_output() -> None:
    """DUR-OUTPUT: an append in flight while its lease lapses - the reaper skips the locked
    job (never waits, never requeues under it); once the append commits, the lapsed lease is
    `lost_after_publication`, never a requeue, and its terminal event follows the output."""
    rig = Rig()
    lease = rig.running()
    rig.advance(TTL - 1)
    writer = rig.service()
    writer.execute("begin")
    assert append(lease, "last words")(writer)[0] is None
    rig.advance(1)                                          # the lease lapses meanwhile
    reaper = rig.service()
    assert cj.returns_without_waiting(writer, reaper, lambda c: rpc(
        c, "recover", {"limits": cl.LIMITS})) == (None, []), "the reaper acted on it"
    code, produced = rpc(rig.service(), "recover", {"limits": cl.LIMITS})
    assert [i["outcome"]["cause"] for i in produced] == ["lost_after_publication"], produced
    assert cl.kinds(rig.owner, lease.job_id).count("inference_dispatch") == 1, \
        "published output was redispatched"
    assert journal(rig, lease.job_id) == [(1, 1, "delta"), (1, 2, "terminal")]


def test_race__append_and_heartbeat_serialize_on_the_job_row() -> None:
    """DUR-FENCE: both fence the same lease; either order, the second waits for the first's
    row lock and then proceeds on the committed state - no lost renewal, no lost chunk."""
    rig = Rig()
    lease = rig.running()
    heartbeat = lambda c: rpc(c, "heartbeat", {"lease": lease.model_dump(mode="json"),  # noqa: E731
                                               "limits": cl.LIMITS})
    for order in ((append(lease, "a"), heartbeat), (heartbeat, append(lease, "b"))):
        first, second = lockstep(rig.owner, (rig.service(), order[0]),
                                 (rig.service(), order[1]))
        assert first[0] is None and second[0] is None, (first, second)
    assert journal(rig, lease.job_id) == [(1, 1, "delta"), (1, 2, "delta")]


def test_race__a_stale_generation_after_a_requeue_cannot_append() -> None:
    """DUR-FENCE (E3B dr05): generation 1 loses its lease and the job is requeued; the SAME
    worker id claims generation 2. An append of generation 1 that waited behind the requeue
    is `stale_lease`, stores nothing and publishes nothing; generation 2 appends at (2, 1)."""
    rig = Rig()
    old = rig.running("w1")
    rig.advance(TTL)
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(
        c, "recover", {"limits": cl.LIMITS})), (rig.service(), append(old, "stale")))
    assert second[0] == "stale_lease", second
    assert not rig.job(old.job_id)["published"], "a stale append published"
    code, answer = rpc(rig.service(), "claim", {"job_id": old.job_id, "worker_id": "w1",
                                                "limits": cl.LIMITS})
    new = Lease.model_validate(answer["lease"])
    assert (code, new.generation) == (None, 2), (code, answer)
    assert append(old, "stale again")(rig.service())[0] == "stale_lease"
    assert append(new, "fresh")(rig.service())[0] is None
    assert journal(rig, old.job_id) == [(2, 1, "delta")]


def test_race__expire_never_waits_on_an_append_and_an_append_waits_for_a_prune() -> None:
    """DUR-OUTPUT: the pruner takes job rows SKIP LOCKED - with an append in flight it
    answers at once and prunes that job on its next pass. An append behind a prune in flight
    waits, then continues past the new watermark (never a pruned cursor)."""
    rig = Rig()
    lease = rig.running()
    short = cj.SHORT
    assert append(lease, "old", limits=short)(rig.service())[0] is None
    rig.advance(short.journal_chunk_ttl_s + 1)
    holder = rig.service()
    holder.execute("begin")
    assert append(lease, "new", limits=short)(holder)[0] is None
    assert cj.returns_without_waiting(holder, rig.service(), lambda c: rpc(
        c, "expire_journal", {"now": None})) == (None, 0), "the pruner took a locked job"
    assert rpc(rig.service(), "expire_journal", {"now": None}) == (None, 1)
    rig.advance(short.journal_chunk_ttl_s + 1)
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(
        c, "expire_journal", {"now": None})), (rig.service(), append(lease, "after")))
    assert first == (None, 1) and second[0] is None, (first, second)
    assert [c["sequence"] for c in second[1]["chunks"]] == [3], \
        f"an append behind a prune reissued a pruned cursor: {second}"
    assert journal(rig, lease.job_id) == [(1, 3, "delta")]


def test_race__a_stale_pruner_never_resets_a_pruned_jobs_watermark() -> None:
    """DUR-OUTPUT (review J1): pruner B blocks inside job 1 while pruner A prunes job 2; when
    B reaches job 2 with its older candidate list it finds nothing to prune and leaves the
    watermark, the bytes and the rows alone - a cursor-less replay is still `replay_gap`."""
    rig = Rig()
    print(cj.stale_pruner_race(rig.owner, rig.service, pgharness.connect(rig.db), rig.world))
