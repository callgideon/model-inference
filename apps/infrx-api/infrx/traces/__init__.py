"""T: trace capture and spooling (T1), shipping and retention (T2, T3).

`SpoolTraceSink` is the real `ports.TraceSink`: bounded, nonblocking capture on the
request path and a durable checksummed spool behind a dedicated writer thread. `recover`
and `scan_segment` are the replay half, and `SpoolIO` is the filesystem seam the failure
drills inject.
"""
from .spool import (SEGMENT_MAX_BYTES, SEGMENT_MAGIC, SEGMENT_VERSION, Scan, SegmentView,
                    SpoolCapture, SpoolIO, SpoolTraceSink, recover, scan_segment,
                    segment_names)

__all__ = ["SEGMENT_MAGIC", "SEGMENT_MAX_BYTES", "SEGMENT_VERSION", "Scan", "SegmentView",
           "SpoolCapture", "SpoolIO", "SpoolTraceSink", "recover", "scan_segment",
           "segment_names"]
