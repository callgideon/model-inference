"""W: engine execution and lifecycle. W1 is the engine adapter and its fakes.

    from infrx.worker import VllmEngine, prepared_request

W2 is the lease/cancellation/completion loop: `AttemptRunner` runs one fenced attempt,
`WorkerLoop` claims candidates and drains. Both reach the JobStore and the StreamStore
through their ports only, and neither touches the wallet - settlement is the store's
single transaction.
"""
from .attempt import AttemptResult, AttemptRunner
from .engine import (EngineError, EngineFailure, EngineIncomplete, EngineProtocolViolation,
                     EngineStream, EngineTransportError, EngineUnsupported, VllmEngine,
                     cache_salt, prepared_request)
from .loop import DrainReport, WorkerLoop
from .reasoning import ReasoningFilter, filter_text

__all__ = ["AttemptResult", "AttemptRunner", "DrainReport", "EngineError", "EngineFailure",
           "EngineIncomplete", "EngineProtocolViolation", "EngineStream",
           "EngineTransportError", "EngineUnsupported", "ReasoningFilter", "VllmEngine",
           "WorkerLoop", "cache_salt", "filter_text", "prepared_request"]
