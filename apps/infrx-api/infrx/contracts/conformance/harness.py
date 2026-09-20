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
    """The hook, or `MissingHook` - never a silent early return."""
    try:
        return harness.extra[name]
    except KeyError:
        raise MissingHook(name) from None


# Which hooks a port's suite may do without, i.e. exactly the ones that can raise
# `MissingHook`. The fakes provide all of them (`test_mutants.py` asserts that).
OPTIONAL_HOOKS: dict[str, frozenset[str]] = {
    "jobstore": frozenset({"publish", "revoke_key", "unrevoke_key", "suspend_org", "unentitle",
                           "entitle", "retune", "journal_bytes"}),
    "streamstore": frozenset({"jobs", "journal_bytes"}),
    "mediastore": frozenset({"put_object", "attach"}),
    "scheduler": frozenset({"jobs"}),
    "engine": frozenset({"text"}),
    "tracesink": frozenset({"queued", "crash", "content_budget"}),
    "feedback": frozenset({"jobs", "outbox", "audit"}),
    "judge": frozenset({"runs", "available", "set_consent", "revoke_consent", "audit"}),
}


@dataclass
class Harness:
    """What a factory hands a case: the adapter plus the hooks it needs.

    `extra` holds the named hooks a suite documents (for JobStore: `grant`,
    `balance`, `active_jobs`, `outbox`, `outbox_kinds`, and optionally `publish`,
    `revoke_key`, `unrevoke_key`, `suspend_org`, `unentitle`, `retune`,
    `journal_bytes`; for JudgeCoordinator: `available`, `runs`, `set_consent`,
    `revoke_consent`, `audit`). A missing optional hook makes the case return early
    rather than fail, so an adapter can adopt the suite in steps.

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


