"""F3.b: the Lab state machines. A state not listed as a source is terminal.

The TypeScript half (`packages/shared/contracts/lab/index.ts` `TRANSITIONS`) declares
the same table; `tests/contracts/lab` fails if the two drift.

`external_run` is the one with an ambiguous state: a submit whose outcome is unknown
(timeout after the connector may have accepted it) becomes `ambiguous` and is then
reconciled by lookup to `submitted` or `failed`. It never goes back to `submitting`,
so no retry can submit (and pay for) the same work twice (PIPELINE-BUDGET).
"""
from __future__ import annotations

from .. import errors

TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "run": {"queued": frozenset({"running", "cancelled"}),
            "running": frozenset({"succeeded", "failed", "cancelled"})},
    # A lease that expires puts the case back to `pending`; its attempt is `expired`.
    "case": {"pending": frozenset({"leased", "skipped"}),
             "leased": frozenset({"pending", "done", "failed"})},
    "attempt": {"leased": frozenset({"succeeded", "failed", "expired"})},
    "harness": {"draft": frozenset({"frozen"}), "frozen": frozenset({"retired"})},
    "checkpoint": {"received": frozenset({"validated", "rejected"}),
                   "validated": frozenset({"evaluated", "rejected"})},
    "annotation": {"draft": frozenset({"submitted"}),
                   "submitted": frozenset({"accepted", "rejected"}),
                   "accepted": frozenset({"superseded"})},
    "external_run": {"prepared": frozenset({"submitting", "cancelled"}),
                     "submitting": frozenset({"submitted", "ambiguous", "failed"}),
                     "ambiguous": frozenset({"submitted", "failed"}),
                     "submitted": frozenset({"completed", "failed", "cancelled"})},
}

STATES: dict[str, tuple[str, ...]] = {
    kind: tuple(sorted(set(table) | {s for targets in table.values() for s in targets}))
    for kind, table in TRANSITIONS.items()}


def is_terminal(kind: str, state: str) -> bool:
    return state in STATES.get(kind, ()) and state not in TRANSITIONS[kind]


def transition(kind: str, current: str, target: str) -> str:
    """`target` if the move is declared, else `StateConflict` (never a silent no-op)."""
    if target not in TRANSITIONS.get(kind, {}).get(current, frozenset()):
        raise errors.StateConflict(f"{kind}: {current} -> {target} is not a declared transition")
    return target
