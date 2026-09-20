"""In-memory adapters implementing the real semantics of the durable protocols.

Development against these is the point: a track builds behind the same ports the
real service will implement, drives failures deterministically with `FailurePlan`,
and never needs PostgreSQL, Valkey, ClickHouse, S3 or a GPU to run its unit tests.

Green tests against a fake mean *implemented*, never integrated: the owning track
must run the same `conformance` suite against its real adapter.
"""
from __future__ import annotations

from .engine import DEFAULT_TEXT, SPLIT_REASONING, EngineExited, EngineFault, FakeEngine
from .factories import FACTORIES
from .feedback import FakeFeedbackService
from .judge import FakeJudgeCoordinator
from .media import FakeMediaStore, digest_of
from .scheduling import FakeScheduler
from .state import MAX_READ_LIMIT, FakeJobStore, FakeStreamStore
from .support import CrashAfterCommit, FailurePlan, FakeClock, NoFailures, SequentialIds
from .traces import FakeTraceSink

__all__ = [
    "CrashAfterCommit", "DEFAULT_TEXT", "EngineExited", "EngineFault", "FACTORIES",
    "FailurePlan", "FakeClock", "FakeEngine", "FakeFeedbackService", "FakeJobStore",
    "FakeJudgeCoordinator", "FakeMediaStore", "FakeScheduler", "FakeStreamStore",
    "FakeTraceSink", "MAX_READ_LIMIT", "NoFailures", "SPLIT_REASONING", "SequentialIds",
    "digest_of",
]
