"""The gateway's readiness probes: `Probe` and the journal's check (E4C run 2, register
row 94). Replaces `gateway/pilot.py`'s `Probe`, `PROBE_TIMEOUT_S` and `journal_check`,
which the composition root re-exports by their old names (WR-PROBE-1); `pilot._refresh`
and `PROBE_EVERY_S` (the lifetime's cadence) stay there.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("infrx.gateway")

#: One probe answer may take this long; past it the component reads unavailable.
PROBE_TIMEOUT_S = 10.0


class Probe:
    """One readiness answer that `ingress.component_state` can read: sync, and after its
    first answer never blocking - `/readyz` is async and must not nest an event loop.

    The lifetime task refreshes it (`refresh`). The first call comes from `assert_startup`
    inside `create_app`, where a sync caller may be inside a running loop (`uvicorn
    --factory`), so that one answer is asked on a thread of its own: `check` must therefore
    not depend on a loop-bound resource (the stores' probe paths use per-call connections).
    """

    def __init__(self, check, timeout_s: float = PROBE_TIMEOUT_S) -> None:
        self.check = check
        self.timeout_s = timeout_s
        self.value: bool | None = None

    def __call__(self) -> bool:
        if self.value is None:
            # `_answer` is bounded and never raises, so the thread always ends: a refusal
            # to start can finish (review C3) and no worker is left behind. The bound is
            # `wait_for`'s, so it holds for a check that awaits and lets its cancellation
            # through: one that blocks its thread, or suppresses or delays CancelledError
            # (3.12's wait_for waits for the cancelled check), holds this call with it
            # (review r2 COMP-N1; today's checks are cooperative).
            with ThreadPoolExecutor(1) as pool:
                self.value = pool.submit(asyncio.run, self._answer()).result()
        return self.value

    async def refresh(self) -> None:
        self.value = await self._answer()

    async def _answer(self) -> bool:
        """The check's answer within `timeout_s`; a check that hangs or fails reads False
        (review C2: a hung dependency never keeps a cached True)."""
        try:
            return bool(await asyncio.wait_for(self.check(), self.timeout_s))
        except Exception:
            log.warning("readiness probe failed or did not answer in %ss", self.timeout_s,
                        exc_info=True)
            return False


def journal_check(stream):
    """The journal is ready when its `ready()` answers: one primary-key lookup under a 2 s
    server bound (`state.journal.READY_SQL`). `usage()`'s four aggregates are the
    journal-bytes gauge's body, never the probe's (E4C run 2: every 5 s they missed the
    10 s bound on the slow hosted database and admission answered 503)."""
    async def check() -> bool:
        await stream.ready()
        return True
    return check


__all__ = ["PROBE_TIMEOUT_S", "Probe", "journal_check"]
