"""The harness contract every conformance case and adapter factory shares.

Split out of `__init__` so a case module can import it without importing the suites
back (there is no cycle here, and nothing in this file knows about any port).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

class MissingHook(Exception):
    """r1 R32: a case cannot run because the factory did not supply an optional hook.

    It is never a pass. The pytest wrapper turns it into a *skip* naming the hook, and
    `run_cases` only tolerates it when the caller passes a list to collect them, so an
    adapter adopting the suite in steps has to report what it skipped.
    """

    def __init__(self, hook: str, case: str = "") -> None:
        self.hook = hook
        self.case = case
        super().__init__(f"missing optional hook {hook!r}" + (f" for {case}" if case else ""))


def hook(harness: "Harness", name: str):
    """The hook, or `MissingHook` - never a silent early return, and never a `None`
    a case then quietly skips assertions around.

    `failures` is a `Harness` field rather than an `extra` entry, and a factory
    without fault injection leaves it `None`; it is requested by the same name so a
    fault case is skipped rather than silently weakened.
    """
    if name == "failures":
        if harness.failures is None:
            raise MissingHook("failures")
        return harness.failures
    value = harness.extra.get(name)
    if value is None:
        raise MissingHook(name)
    return value


# Which hooks a port's suite may do without, i.e. exactly the ones that can raise
# `MissingHook`. The fakes provide all of them (`test_mutants.py` asserts that).
OPTIONAL_HOOKS: dict[str, frozenset[str]] = {
    "jobstore": frozenset({"publish", "revoke_key", "unrevoke_key", "suspend_org", "unentitle",
                           "entitle", "retune", "journal_bytes", "failures", "stream",
                           "unsettleable"}),
    "streamstore": frozenset({"jobs", "journal_bytes", "failures"}),
    "mediastore": frozenset({"put_object", "attach"}),
    "scheduler": frozenset({"jobs"}),
    "engine": frozenset({"text"}),
    "tracesink": frozenset({"queued", "crash", "content_budget", "reap"}),
    # r1 R33: `suspend_org` is the same injectable suspension source the JobStore uses,
    # so one organization cannot be suspended for admission and live for feedback.
    "feedback": frozenset({"jobs", "outbox", "audit", "suspend_org"}),
    "judge": frozenset({"runs", "available", "set_consent", "revoke_consent", "audit"}),
}


@dataclass
class Harness:
    """What a factory hands a case: the adapter plus the hooks it needs.

    `extra` holds the named hooks a suite documents. The required ones (for JobStore:
    `grant`, `balance`, `active_jobs`, `outbox`, `outbox_kinds`) are read directly; the
    optional ones - `OPTIONAL_HOOKS` below, plus the `failures` field - are read
    through `hook()`, which raises `MissingHook`. A case that cannot be driven is
    **skipped, naming the hook**, and never counted as a pass (r1 R32), so an adapter
    can adopt the suite in steps without its evidence claiming more than it ran.

    Hook signatures a case relies on: `reap(grace_s: float = 60.0) -> int` is called both
    as `reap()` and as `reap(-100.0)`, so it must accept an optional grace period and
    clamp a negative one; `journal_bytes() -> int`; `balance(org_id) -> dict` with
    `ledger`/`reserved`/`available`; `retune(**limit_changes)`; `unsettleable() -> dict`
    of job id to error code; `content_budget() -> int`; `queued() -> list`.

    The streamstore, scheduler and feedback factories also publish `extra["jobs"]`,
    the JobStore a case needs to admit a job first. The cases only ever call *port*
    operations on it, so a real adapter can pass its own JobStore there; nothing in
    a suite reads a fake's attributes.
    """

    port: Any
    clock: Any
    ids: Any
    failures: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


