"""AP-08 08c: the judge worker's START step - a run the Lab queued becomes J2's `submit`.

Until this step nothing turned a queued request (0037 `lab_judge_requests`, written by
`POST /lab/v1/judge/runs`) into a run: the judge role only swept, reconciled and collected
(WR-LSQ-C2A). `start_pass` reads the requests with no ledger run yet, or a reserved one never
sent (0064 `lab_judge_queued`; never a cancelled one), freezes a sample of the eligible traces
and calls `submit` with the REQUESTER's identity - so the permission checked is that user's
current one, and J2's protocol is unchanged: the mode must be live, the worst case is reserved
against the named payer before anything leaves, one submit intent, the permission rechecked
immediately before egress, an unknown outcome quarantined and never resent.

The sample is frozen by the reservation: 0036's reserve answers the stored row on a replay, so
a restarted worker re-reads the same ids whatever the trace store answers now.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any
from collections.abc import Awaitable, Callable, Sequence

from ..contracts import errors
from .dryrun import MAX_CANDIDATES
from .rubric import RUBRICS, Rubric, from_definition
from .submit import JudgeJob, JudgeWiring, LedgerRun, RubricOf, submit

log = logging.getLogger(__name__)
#: (grantor org, grantor model, limit) -> that model's traces eligible for judging, as
#: (request id, has video) - the trace store's read; the worker composes it (WIRING REQUEST).
Eligible = Callable[[str, str, int], Awaitable[Sequence[tuple[str, bool]]]]
Queued = Callable[[int], Awaitable[list[dict[str, Any]]]]
START_BATCH = 20


def graded(version: int, definition: dict[str, Any] | None) -> Rubric | None:
    """The rubric a version names: its stored definition, else the code registry's."""
    return RUBRICS.get(version) if definition is None else from_definition(definition)


def frozen_sample(run_id: str, rows: Sequence[tuple[str, bool]],
                  size: int) -> tuple[tuple[str, ...], frozenset[str]]:
    """`size` of the eligible ids, ordered by a hash seeded with the run id (the same rows
    always give the same sample), and those of them with video.
    ponytail: a uniform seeded draw; J1's stratified calibration design (`sampling.select`)
    is the calibration workflow's - use it here if online runs need strata."""
    media = dict(rows)
    ranked = sorted(media, key=lambda r: hashlib.sha256(f"{run_id}:{r}".encode()).digest())
    chosen = tuple(ranked[:size])
    return chosen, frozenset(r for r in chosen if media[r])


async def start(request: dict[str, Any], *, wiring: JudgeWiring,
                eligible: Eligible) -> LedgerRun | None:
    """One queued request -> `submit` (None: nothing eligible yet, it stays queued)."""
    rubric = RUBRICS.get(request["rubric_version"]) if wiring.rubric_of is None else \
        await wiring.rubric_of(request["run_id"])
    if rubric is None or rubric.version != request["rubric_version"]:
        raise errors.InvalidRequest("the worker grades no such rubric version")
    rows = await eligible(request["grantor_org_id"], request["model_id"], MAX_CANDIDATES)
    ids, media = frozen_sample(request["run_id"], rows, request["sample_size"])
    if not ids:
        return None
    job = JudgeJob(run_id=request["run_id"], provider_org_id=request["provider_org_id"],
                   grantor_org_id=request["grantor_org_id"], model_id=request["model_id"],
                   judge_model=request["judge_model"], payer_ref=request["payer_ref"],
                   request_ids=ids, media_ids=media, rubric=rubric)
    return await submit(job, user_id=request["requested_by"], wiring=wiring)


async def start_pass(queued: Queued, wiring: JudgeWiring, eligible: Eligible,
                     limit: int = START_BATCH) -> dict[str, int]:
    """Every queued request once. A refusal (dry-run, budget, permission) sends nothing and
    leaves the request queued; one request's failure is counted and the next still runs."""
    done = {"started": 0, "waiting": 0, "refused": 0, "failed": 0}
    for request in await queued(limit):
        try:
            run = await start(request, wiring=wiring, eligible=eligible)
            done["started" if run is not None else "waiting"] += 1
        except errors.DomainError as refused:
            log.info("judge start refused: %s", refused.code)
            done["refused"] += 1
        except Exception:                     # noqa: BLE001 - the next request still runs
            log.exception("judge start failed for one request")
            done["failed"] += 1
    return done


def eligible_read(limits) -> Eligible | None:
    """AP-07's `infrx.traces.eligible` read over the trace projection (published by
    api-traces-2), for the judge role's start job. None until that read is on this base: the
    role then composes no start job (WIRING REQUEST WR-AP08-2b binds it at merge)."""
    return None


def pg_queued(connect) -> Queued:
    """0064's `infrx.lab_judge_queued` on the worker's login."""
    from ..state import rpc
    from ..state.jobstore import domain_error

    async def queued(limit: int) -> list[dict[str, Any]]:
        rows = await rpc.call(connect, "lab_judge_queued", {"limit": limit}, error=domain_error)
        return [{k: (str(v) if k.endswith(("_id", "_by", "_org_id")) else v)
                 for k, v in row.items()} for row in rows]
    return queued


def pg_rubric_of(connect) -> RubricOf:
    """SR-AP08-1's `infrx.lab_judge_rubric_of` on the worker's login."""
    from ..state import rpc
    from ..state.jobstore import domain_error

    async def rubric_of(run_id: str) -> Rubric | None:
        row = await rpc.call(connect, "lab_judge_rubric_of", {"run_id": run_id},
                             error=domain_error)
        return None if row is None else graded(row["rubric_version"], row["definition"])
    return rubric_of
