"""Q: the scheduling index. An index, never an authorization to execute."""
from __future__ import annotations

from .memory import MAX_INDEX_BYTES, MAX_INDEX_ITEMS, MemoryScheduler
from .valkey import ValkeyScheduler, connect

# `valkey.py` imports the client package inside `connect`, so importing this package
# still pulls no heavy dependency (`08` §4).
__all__ = ["MAX_INDEX_BYTES", "MAX_INDEX_ITEMS", "MemoryScheduler", "ValkeyScheduler",
           "connect"]
