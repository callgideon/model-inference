"""Deterministic collaborators every fake takes by constructor injection.

No wall clock, no randomness, no sleeps: a conformance case advances time itself,
so a lease expiry test costs microseconds and never flakes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DEFAULT_START = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """The injected database clock. `advance` is the only way time passes."""

    def __init__(self, start: datetime = DEFAULT_START) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def at(self, seconds: float) -> datetime:
        return self._now + timedelta(seconds=seconds)


class SequentialIds:
    """Deterministic stand-ins with the real shapes: UUIDv4 text for internal
    identity, opaque prefixed handles for anything a customer can see."""

    def __init__(self, seed: int = 1) -> None:
        self._n = seed - 1

    def _next(self) -> int:
        self._n += 1
        return self._n

    def uuid(self) -> str:
        n = self._next()
        return f"{n:08x}-0000-4000-8000-{n:012x}"

    # distinct streams so an event id can never collide with a request id
    def event_id(self) -> str:
        return self.uuid()

    def _handle(self, prefix: str) -> str:
        return f"{prefix}fake{self._next():039d}"

    def job_handle(self) -> str:
        return self._handle("job_")

    def upload_handle(self) -> str:
        return self._handle("upl_")

    def feedback_id(self) -> str:
        return self._handle("fb_")


class CrashAfterCommit(RuntimeError):
    """The process died after the transaction committed and before it answered.
    The caller never learns the outcome; durable state already moved."""


@dataclass
class _Rule:
    operation: str
    on_call: int
    when: str                       # "before" | "after_commit"
    error: BaseException | None = None


@dataclass
class FailurePlan:
    """Deterministic fault injection by operation name and call ordinal."""

    rules: list[_Rule] = field(default_factory=list)
    calls: Counter = field(default_factory=Counter)

    def fail(self, operation: str, *, on_call: int = 1, error: BaseException | None = None) -> FailurePlan:
        """Fail before any state change (the caller's retry must be clean)."""
        self.rules.append(_Rule(operation, on_call, "before", error))
        return self

    def raise_(self, operation: str, error: BaseException, *, on_call: int = 1) -> FailurePlan:
        return self.fail(operation, on_call=on_call, error=error)

    def crash_after_commit(self, operation: str, *, on_call: int = 1) -> FailurePlan:
        """Commit, then die: the durable effect happened, the answer was lost."""
        self.rules.append(_Rule(operation, on_call, "after_commit", None))
        return self

    def before(self, operation: str) -> None:
        self.calls[operation] += 1
        self._maybe_raise(operation, "before")

    def after_commit(self, operation: str) -> None:
        self._maybe_raise(operation, "after_commit")

    def count(self, operation: str) -> int:
        return self.calls[operation]

    def _maybe_raise(self, operation: str, when: str) -> None:
        ordinal = self.calls[operation]
        for rule in self.rules:
            if rule.operation == operation and rule.when == when and rule.on_call == ordinal:
                self.rules.remove(rule)
                if when == "after_commit":
                    raise CrashAfterCommit(f"{operation} call {ordinal} crashed after commit")
                raise rule.error or RuntimeError(f"{operation} call {ordinal} failed before commit")


class NoFailures(FailurePlan):
    """The default: every call proceeds."""


def failure_hooks(plan: FailurePlan | None) -> FailurePlan:
    return plan if plan is not None else NoFailures()
