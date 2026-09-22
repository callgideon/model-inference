"""Scripted engine with the fault modes 04-verification.md requires.

The engine is an external process, so its failures are not `DomainError`s: an
abrupt exit raises `EngineExited`. Stalls are expressed as clock advances between
yields, because the timeout policy belongs to W, not to the engine: a case asserts
how much time passed before the next event, never that the engine timed out.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..limits import DEFAULTS, PilotSettings
from ..records import ChunkEventType, EngineEvent, Lease, PreparedRequest, Usage
from .support import FailurePlan, FakeClock, failure_hooks

DEFAULT_TEXT = "Two people unload boxes from a van onto a trolley."
# Reasoning delimiters deliberately split across chunk boundaries: a parser that
# looks for "<think>" inside one chunk fails here, which is the point (API-STREAM).
SPLIT_REASONING = ("<th", "ink>the van is", " stationary</thi", "nk>Two people unload boxes.")
# What the customer reads of each piece (r1 R58 `visible`): nothing until the block
# closes. Scripted rather than computed, so the fake needs no reasoning filter of its own.
SPLIT_REASONING_VISIBLE = ("", "", "", "Two people unload boxes.")


class EngineFault(enum.StrEnum):
    none = "none"
    prefill_stall = "prefill_stall"
    midstream_stall = "midstream_stall"
    malformed_usage = "malformed_usage"
    missing_usage = "missing_usage"
    cancellation_race = "cancellation_race"
    abrupt_exit = "abrupt_exit"
    split_reasoning_delimiters = "split_reasoning_delimiters"


class EngineExited(RuntimeError):
    """The engine process died mid-request. Not a domain error: W decides."""


@dataclass
class _Step:
    event: EngineEvent | None = None
    advance_s: float = 0.0
    raises: BaseException | None = None
    stop: bool = False


@dataclass
class FakeEngine:
    """`ports.Engine`. One fault per instance keeps cases readable."""

    clock: FakeClock = field(default_factory=FakeClock)
    limits: PilotSettings = DEFAULTS
    fault: EngineFault = EngineFault.none
    text: str = DEFAULT_TEXT
    prompt_tokens: int = 1200
    chunk_size: int = 12
    failures: FailurePlan | None = None
    cancelled: set = field(default_factory=set)
    drained: bool = False
    emitted: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.failures = failure_hooks(self.failures)

    def deltas(self) -> tuple[str, ...]:
        if self.fault is EngineFault.split_reasoning_delimiters:
            return SPLIT_REASONING
        return tuple(self.text[i:i + self.chunk_size]
                     for i in range(0, len(self.text), self.chunk_size))

    def visibles(self) -> tuple[str, ...]:
        """The customer's part of each delta; `self.text` carries no reasoning block."""
        if self.fault is EngineFault.split_reasoning_delimiters:
            return SPLIT_REASONING_VISIBLE
        return self.deltas()

    def _usage(self, pieces: int) -> Usage:
        return Usage.of(self.prompt_tokens, max(pieces, 0))

    def _script(self) -> list[_Step]:
        progress = _Step(EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"}))
        pieces = self.deltas()
        # r1 R58 / R64: a delta payload is exactly `{visible, raw}`.
        deltas = [_Step(EngineEvent(type=ChunkEventType.delta,
                                    payload={"visible": visible, "raw": raw}))
                  for raw, visible in zip(pieces, self.visibles(), strict=True)]
        usage = _Step(EngineEvent(type=ChunkEventType.usage, payload={},
                                  usage=self._usage(len(pieces))))
        if self.fault is EngineFault.prefill_stall:
            # Nothing after the progress event; the clock passes the TTFT budget.
            return [progress, _Step(advance_s=self.limits.ttft_timeout_s + 1, stop=True)]
        if self.fault is EngineFault.midstream_stall:
            return [progress, deltas[0], _Step(advance_s=self.limits.tpot_stall_s + 1, stop=True)]
        if self.fault is EngineFault.abrupt_exit:
            return [progress, deltas[0], _Step(raises=EngineExited("engine process exited"))]
        if self.fault is EngineFault.missing_usage:
            return [progress, *deltas]
        if self.fault is EngineFault.malformed_usage:
            # Structurally wrong: token counts as strings, no usage record at all.
            malformed = EngineEvent(type=ChunkEventType.usage,
                                    payload={"usage": {"prompt_tokens": "1200",
                                                       "completion_tokens": None}})
            return [progress, *deltas, _Step(malformed)]
        return [progress, *deltas, usage]

    async def generate(self, lease: Lease, prepared: PreparedRequest) -> AsyncIterator[EngineEvent]:
        self.failures.before("generate")
        produced = 0
        for step in self._script():
            if step.advance_s:
                self.clock.advance(step.advance_s)
            if step.raises is not None:
                raise step.raises
            if step.stop:
                return
            if self.fault is EngineFault.cancellation_race and lease.job_id in self.cancelled:
                # Cancelled mid-stream: report what was really produced, once.
                yield EngineEvent(type=ChunkEventType.usage, payload={},
                                  usage=self._usage(produced))
                return
            if step.event is not None:
                if step.event.type is ChunkEventType.delta:
                    produced += 1
                self.emitted.append(step.event)
                yield step.event

    async def cancel(self, lease: Lease) -> bool:
        self.cancelled.add(lease.job_id)
        return True

    async def health(self) -> dict[str, object]:
        return {"ready": not self.drained, "max_num_seqs": self.limits.engine_max_num_seqs,
                "fault": self.fault.value}

    async def drain(self) -> None:
        self.drained = True
