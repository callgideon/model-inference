"""Legacy research test ids -> the namespaced ids of 04-verification.md.

04 §1: "Retain existing research test cases, but namespace them as `SERV-*`, `TRACE-*`,
`JUDGE-*` or `CONSOLE-*` so repeated A1/Q1/I1 labels cannot collide. E2 publishes the
explicit legacy-to-new mapping in its evidence."

The point of the table is the **collisions**, not the prefixes. Two kinds exist, and both
are measured by `test_harness.py` rather than asserted here:

1. Between research documents: `Q1`-`Q11` name both a Redis-queue assertion
   (production-api §12.1) and a judge-selection assertion (traces/06 §5); `F1`-`F4` name
   both a gateway failure-matrix row and a feedback requirement. A reader who sees "Q3
   failed" cannot tell which suite it was.
2. Between a legacy test id and a **task id in this plan**: `E1` is a manual console
   checklist and also the benchmark task; `Q1`, `F1`, `I1`, `M1`, `T1`, `J1`, `C1`, `U1` and
   `G1` are all both. That is the worse pair, because a status line saying "F2 failed" reads
   as a task. `D1`-`D4` are deliberately NOT in this table: in production-api §1 they are
   design *decisions*, not test cases, and `test_harness.py` asserts their absence so nobody
   adds them by accident (r1 review: the docstring used to claim "D1 is both", which
   contradicted that assertion).

The mapping is pure data plus one resolver, and `test_harness.py` checks it against the
source documents: every legacy id must actually occur in the document it is attributed to,
no namespaced id may be issued twice, and any legacy id used by two documents must resolve
to two namespaced ids. A table nobody checks is a table that rots.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_DOCS = {
    "serv": "research/production-api/10-implementation-spec.md",
    "traces-req": "research/traces/01-requirements.md",
    "traces-gw": "research/traces/05-gateway-capture-spec.md",
    "judge": "research/traces/06-feedback-and-judge-spec.md",
    "console": "research/traces/07-console-spec.md",
    "drills": "research/traces/08-phases-and-test-plan.md",
}


@dataclass(frozen=True)
class Series:
    """One contiguous legacy id series from one document."""

    prefix: str          # the namespace it moves into: SERV / TRACE / JUDGE / CONSOLE
    letters: str         # the legacy letter(s): Q, A, M, I, L, F, G, H, U, E, T, J, C, O, NF
    numbers: tuple[int, ...]
    source: str          # key of REPO_DOCS
    where: str           # section, for a reader going back to the original
    scope: str           # what the series is about, so a collision reads unambiguously
    extra: tuple[str, ...] = ()   # ids in the series that are not "letters + number"

    def legacy_ids(self) -> tuple[str, ...]:
        return tuple(f"{self.letters}{n}" for n in self.numbers) + self.extra

    def namespaced(self, legacy: str) -> str:
        return f"{self.prefix}-{legacy}"


def _r(start: int, end: int) -> tuple[int, ...]:
    return tuple(range(start, end + 1))


SERIES: tuple[Series, ...] = (
    # ---- research/production-api/10-implementation-spec.md -> SERV-*
    Series("SERV", "Q", _r(1, 11), "serv", "§12.1 test_redis_queue.py",
           "queue order, leases, idempotent complete, depth counter"),
    Series("SERV", "A", _r(1, 8), "serv", "§12.1 test_admission.py",
           "response selection, jittered Retry-After, idempotency, ceilings"),
    Series("SERV", "M", _r(1, 13), "serv", "§12.1 test_media.py",
           "SSRF, redirects, size cap, transcode geometry, cache isolation"),
    Series("SERV", "I", _r(1, 15), "serv", "§12.2 integration against a fake vLLM",
           "gateway+worker+queue against a fake engine, kills and restarts"),
    Series("SERV", "L", _r(1, 7), "serv", "§12.3 loadtest/arrival.py",
           "open-loop arrival-rate load runs"),
    Series("SERV", "F", _r(1, 29), "serv", "§11 failure matrix",
           "one row per injected production failure and its client-visible answer"),
    # ---- research/traces/05-gateway-capture-spec.md -> TRACE-*
    Series("TRACE", "G", _r(1, 22), "traces-gw", "§11",
           "capture on the request path, spool, worker, feedback API"),
    # ---- research/traces/08-phases-and-test-plan.md -> TRACE-*
    Series("TRACE", "H", _r(1, 7), "drills", "§9 cross-cutting drills",
           "live A/B overhead, outage, restart, queue-full, rotation, cross-org, egress audit"),
    # ---- research/traces/01-requirements.md -> TRACE-* / JUDGE-* / CONSOLE-*
    Series("TRACE", "T", _r(1, 12), "traces-req", "§2 trace requirements",
           "what a trace must contain and how long it lives"),
    Series("TRACE", "F", _r(1, 4), "traces-req", "§2 feedback requirements",
           "feedback acceptance, ownership and provenance"),
    Series("TRACE", "O", _r(1, 5), "traces-req", "§2 operational requirements",
           "retention, deletion, storage and key handling"),
    Series("TRACE", "NF", _r(1, 11), "traces-req", "§2 non-functional requirements",
           "overhead, loss, duplication and cost bounds"),
    Series("JUDGE", "J", _r(1, 7), "traces-req", "§2 judge requirements",
           "consent, budget, rubric and egress rules"),
    Series("CONSOLE", "C", _r(1, 7), "traces-req", "§2 console requirements",
           "tenant-safe reads, content states and operator scope"),
    # ---- research/traces/06-feedback-and-judge-spec.md -> JUDGE-*
    Series("JUDGE", "Q", _r(1, 11), "judge", "§5 unit tests",
           "selection, sampling, budget, frames, prompt, schema, state machine, egress, cost",
           extra=("QI1",)),
    # ---- research/traces/07-console-spec.md -> CONSOLE-*
    Series("CONSOLE", "U", _r(1, 4), "console", "§9 test plan",
           "chQuery org binding, SigV4 vector, gzip/content guard, filter parsing"),
    Series("CONSOLE", "E", (1,), "console", "§9 manual end-to-end",
           "the 9-step manual release checklist (NOT plan task E1)"),
)


def mapping() -> dict[str, tuple[Series, ...]]:
    """legacy id -> every series that claims it. More than one means a real collision."""
    table: dict[str, list[Series]] = {}
    for series in SERIES:
        for legacy in series.legacy_ids():
            table.setdefault(legacy, []).append(series)
    return {legacy: tuple(found) for legacy, found in sorted(table.items())}


def collisions() -> dict[str, tuple[str, ...]]:
    """The legacy ids that are ambiguous on their own, with what they may mean."""
    return {legacy: tuple(s.namespaced(legacy) for s in found)
            for legacy, found in mapping().items() if len(found) > 1}


def plan_task_ids(repo_root) -> tuple[str, ...]:
    """The implementation plan's own task ids (`F1`, `D1`, `Q1`, `E1`, …), read from the
    manifest. Read-only: the manifest is coordinator-owned."""
    import json
    raw = json.loads((repo_root / "research" / "plan" / "tasks.json").read_text())
    return tuple(sorted(task["id"] for task in raw["tasks"]))


def task_id_clashes(repo_root) -> dict[str, tuple[str, ...]]:
    """The nastiest collisions of all: legacy test ids that are spelled exactly like a task
    in this plan. "F2 failed" could mean the foundation task or a gateway failure-matrix
    row; "E1" is both a manual console checklist and the benchmark task."""
    tasks = set(plan_task_ids(repo_root))
    return {legacy: tuple(s.namespaced(legacy) for s in found)
            for legacy, found in mapping().items() if legacy in tasks}


def namespaced_ids() -> tuple[str, ...]:
    return tuple(series.namespaced(legacy)
                 for series in SERIES for legacy in series.legacy_ids())


def resolve(namespaced_id: str) -> Series:
    """`SERV-Q3` -> its series, so a report can name the document and section."""
    for series in SERIES:
        for legacy in series.legacy_ids():
            if series.namespaced(legacy) == namespaced_id:
                return series
    raise KeyError(namespaced_id)


def markdown_table() -> str:
    """The table this task publishes in its evidence report."""
    lines = ["| Legacy id(s) | Source | Section | Scope | Namespaced |",
             "|---|---|---|---|---|"]
    for series in SERIES:
        ids = series.legacy_ids()
        span = ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1]}"
        if series.extra:
            span = f"{ids[0]}–{ids[len(ids) - len(series.extra) - 1]}, {', '.join(series.extra)}"
        lines.append(f"| {span} | `{REPO_DOCS[series.source]}` | {series.where} | "
                     f"{series.scope} | `{series.prefix}-{series.letters}*` |")
    lines.append("")
    lines.append("| Ambiguous legacy id | Resolves to |")
    lines.append("|---|---|")
    for legacy, options in collisions().items():
        lines.append(f"| `{legacy}` | {', '.join('`' + o + '`' for o in options)} |")
    try:
        clashes = task_id_clashes(Path(__file__).resolve().parents[2])
    except Exception:                      # noqa: BLE001 - the table prints without it
        clashes = {}
    if clashes:
        lines += ["", "| Legacy id that is also a plan TASK id | The test id(s) it may mean |",
                  "|---|---|"]
        for legacy, options in clashes.items():
            lines.append(f"| `{legacy}` (task {legacy}) | "
                         f"{', '.join('`' + o + '`' for o in options)} |")
    return "\n".join(lines)


if __name__ == "__main__":       # `python tests/integration/testids.py` prints the table
    print(markdown_table())
