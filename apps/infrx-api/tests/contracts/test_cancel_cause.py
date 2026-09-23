#!/usr/bin/env python3
"""F cancel-cause: `JobStore.cancel(org, handle, *, cause)` (R21; G2's D-new, D5 item 3).

The port and the set of causes a canceller may name. The settlement each cause gets is
proved by the exported conformance cases (`dur_settle__cancel_records_*`,
`credit_settle__cancel_records_*`); what is here is the shape every adapter shares.

    uv run --frozen pytest -q tests/contracts/test_cancel_cause.py
"""
from __future__ import annotations

import inspect

from infrx.contracts import ports
from infrx.contracts.records import CANCEL_CAUSES, TerminalCause

THE_THREE = {TerminalCause.client_cancelled, TerminalCause.client_disconnected,
             TerminalCause.sync_deadline}


def _cause(operation) -> inspect.Parameter:
    return inspect.signature(operation).parameters["cause"]


def test_dur_settle__cancel_takes_a_keyword_cause_that_defaults_to_client_cancelled():
    """Additive: keyword-only, defaulting to `client_cancelled`, so every existing
    `cancel(org, handle)` keeps its meaning; exactly the two client causes and the
    platform's synchronous deadline may be named."""
    cause = _cause(ports.JobStore.cancel)
    assert cause.kind is inspect.Parameter.KEYWORD_ONLY
    assert cause.default is TerminalCause.client_cancelled
    assert set(CANCEL_CAUSES) == THE_THREE
