#!/usr/bin/env python3
"""R32/R40/R83 for T2F (and T2I's WR-3 pins lookup): one single-edit defect per decision.

Two kinds, one list:

* `MUTANTS` edit the projector (`traces/feedback/projector.py`) and run through the shared
  runner (`tests/contracts/mutants.py`) against `test_feedback.py`: memory always, the
  ClickHouse half with `INFRX_T2F_STACK=1` (`NEEDS_STACK` mutants skip, naming their owner,
  without it).
* `PG_MUTANTS` edit the PostgreSQL adapters (`traces/feedback/pg.py`, `traces/ship/pins.py`)
  and are killed **in-process** (`kill_in_process`): the named `check_*` of
  `test_feedback_pg.py` / `tests/t/ship/test_pins_pg.py` runs against a mutated copy of the
  module on a fresh `pgworld` database. In-process because a PostgreSQL case in a runner copy
  would contend for the harness port's lock with the process running the list. Only an
  assertion is a kill; any other exception is a broken run.

    uv run --frozen pytest -q tests/t/feedback/test_mutants.py                       # subset
    INFRX_D_TASK=t2f INFRX_T2F_STACK=1 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/t/feedback/test_mutants.py
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/t/feedback/test_feedback.py"

if str(API_DIR) not in sys.path:        # `python tests/t/feedback/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

P = "traces/feedback/projector.py"
PG = "traces/feedback/pg.py"
PINS = "traces/ship/pins.py"

DURABLE = "test_a_projected_row_is_the_durable_row_with_its_author_and_authority"
OUTAGE = "test_a_projection_outage_acknowledges_nothing_and_the_retry_projects_once"
DUP = "test_a_redelivery_after_a_lost_ack_is_one_logical_row"
ORPHAN = "test_an_event_without_its_durable_row_is_never_acknowledged"
LATE = "test_late_feedback_joins_its_trace_by_the_durable_request_id"

RELAY = "check_each_signal_is_projected_once_as_its_durable_row"
REDELIVERY = "check_a_lost_ack_is_redelivered_after_the_window_and_acked_by_its_claimer"
LAG = "check_lag_counts_the_unacknowledged_feedback_events"
FAILURE = "check_a_postgresql_failure_is_a_dependency_outage"
VERSIONS = "check_the_versions_are_the_ones_postgresql_admitted"
PINS_FAILURE = "check_a_postgresql_failure_holds_the_segment"


def _m(name, invariant, old, new, file, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


_INSERT = "            await self.projection.insert([e.feedback for e in owned])"
MUTANTS: tuple[Mutant, ...] = (
    _m("rows_never_inserted", "the durable rows reach the projection",
       _INSERT, "            pass", P, DURABLE),
    _m("outage_swallowed", "an insert failure acknowledges nothing",
       _INSERT, "            try:\n    " + _INSERT + "\n            except Exception:\n"
       "                pass", P, OUTAGE),
    _m("orphan_acknowledged", "an event without its row is never acknowledged",
       "            [e.event_id for e in owned], worker_id=self.worker_id)",
       "            [e.event_id for e in events], worker_id=self.worker_id)", P, ORPHAN),
    _m("orphan_not_reported", "an orphan is counted, not silent",
       '"orphaned": len(events) - len(owned)}', '"orphaned": 0}', P, ORPHAN),
    _m("ack_as_another_relay", "the relay acknowledges as the claimer",
       "worker_id=self.worker_id) if owned else 0", 'worker_id="relay") if owned else 0',
       P, DURABLE),
    _m("find_without_final", "a redelivered row is one logical row (FINAL)",
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} FINAL "',
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} "', P, DUP),
    _m("find_ignores_the_org", "the organization is bound in the query",
       '"WHERE org_id = {org:UUID} AND request_id = {request:UUID} "',
       '"WHERE request_id = {request:UUID} "', P, LATE),
    _m("find_ignores_the_request", "late feedback joins by its own request id",
       '"WHERE org_id = {org:UUID} AND request_id = {request:UUID} "',
       '"WHERE org_id = {org:UUID} "', P, LATE),
)

#: Only the real ClickHouse sees these: the in-memory projection has no query text.
NEEDS_STACK = frozenset({"find_without_final", "find_ignores_the_org",
                         "find_ignores_the_request"})
RUNNER = Runner(name="t2f", targets=(SUITE,), env=("INFRX_T2F_STACK",))

PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pending_takes_any_kind", "the relay claims only feedback events",
       "    where x.kind = 'feedback_projection' and x.acknowledged_at is null",
       "    where x.acknowledged_at is null", PG, RELAY),
    _m("pending_redelivers_acknowledged", "an acknowledged event is never handed out again",
       "    where x.kind = 'feedback_projection' and x.acknowledged_at is null",
       "    where x.kind = 'feedback_projection'", PG, REDELIVERY),
    _m("claim_never_lapses", "a lost ack is redelivered once the window has passed",
       "           or x.claimed_at <= infrx.now() - make_interval(secs => %(redelivery)s))",
       "           or %(redelivery)s < 0)", PG, REDELIVERY),
    _m("claim_ignores_the_window", "a live claim is nobody else's",
       "      and (x.claimed_at is null\n", "      and (true\n", PG, REDELIVERY),
    _m("row_not_bound_to_the_event_org", "an event projects only its organization's row",
       "    where f.feedback_id = o.payload->>'feedback_id' and f.org_id = o.org_id)",
       "    where f.feedback_id = o.payload->>'feedback_id')", PG, RELAY),
    _m("ack_by_anyone", "only the claimer's acknowledgment lands",
       "and acknowledged_at is null and claimed_by = %s",
       "and acknowledged_at is null and %s::text is not null", PG, REDELIVERY),
    _m("lag_counts_every_kind", "dispatch events are not feedback lag",
       "from infrx.outbox where kind = 'feedback_projection' and acknowledged_at is null",
       "from infrx.outbox where acknowledged_at is null", PG, LAG),
    _m("failure_reads_as_empty", "a PostgreSQL failure is an outage, never an answer",
       '        raise errors.DependencyUnavailable(f"postgres: {type(failed).__name__}") '
       'from None', "        return []", PINS, FAILURE, PINS_FAILURE),
    _m("pins_ignore_the_org", "pins are the owning organization's request's",
       '"where org_id = %s and request_id = %s', '"where %s::uuid is not null and request_id = %s',
       PINS, VERSIONS),
    _m("pins_ignore_the_regime", "a legacy USD job has no pins",
       " and accounting_regime = 'credit'", "", PINS, VERSIONS),
    _m("pins_never_answer", "an admitted CREDIT request's pins are found",
       "        if not rows:\n            return None", "        if True:\n            return None",
       PINS, VERSIONS),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def pg_checks() -> dict:
    """check name -> (check, world factory, the module file it is handed)."""
    from tests.g.ops import pgworld
    from tests.t.feedback import test_feedback_pg as relay
    from tests.t.ship import test_pins_pg as pins
    return {**{name: (check, relay.world, PG) for name, check in relay.CHECKS.items()},
            **{name: (check, lambda: pgworld.world("pins"), PINS)
               for name, check in pins.CHECKS.items()}}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def _module(file: str, source: str, name: str) -> types.ModuleType:
    path = API_DIR / "infrx" / file
    module = types.ModuleType(name)
    module.__package__ = "infrx." + str(pathlib.Path(file).parent).replace("/", ".")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def kill_in_process(mutant: Mutant) -> Result:
    """The named checks against a mutated copy of the module. A check handed another module
    (the relay, when `pg_rows` in `pins.py` is mutated) gets a fresh copy of it whose names
    imported from the mutated module are rebound to the mutated ones."""
    source = (API_DIR / "infrx" / mutant.file).read_text()
    if source.count(mutant.old) != 1:
        return Result(Outcome.misdeclared, f"anchor appears {source.count(mutant.old)} times")
    mutated = _module(mutant.file, source.replace(mutant.old, mutant.new),
                      f"infrx_mutant_{mutant.name}")
    origin = "infrx." + mutant.file.removesuffix(".py").replace("/", ".")
    checks = pg_checks()
    for case in mutant.cases:
        check, make_world, file = checks[case]
        target = mutated
        if file != mutant.file:
            target = _module(file, (API_DIR / "infrx" / file).read_text(), f"infrx_copy_{case}")
            for name, value in list(vars(target).items()):
                if getattr(value, "__module__", None) == origin:
                    setattr(target, name, getattr(mutated, name))
        w = make_world()
        try:
            check(target, w)
        except AssertionError as noticed:
            return Result(Outcome.killed, f"{case}: {str(noticed)[:200]}")
        except Exception as crashed:              # noqa: BLE001 - reported, never a kill
            return Result(Outcome.broken_runner, f"{case}: {type(crashed).__name__}: {crashed}")
        finally:
            w.owner.close()
    return Result(Outcome.survived, f"{', '.join(mutant.cases)} passed")


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run T2F's mutation list"))
