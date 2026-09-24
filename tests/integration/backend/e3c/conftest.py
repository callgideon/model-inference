"""E3C fixtures: where a case's box logs, barrier markers and CLI secret files live."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


@pytest.fixture
def workdir(request, tmp_path) -> Path:
    """`$INFRX_E3C_OUT/cases/<case>` under the runner (kept as the verdict's raw evidence),
    else pytest's own temporary directory."""
    out = os.environ.get("INFRX_E3C_OUT")
    if not out:
        return tmp_path
    path = Path(out) / "cases" / re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:120]
    path.mkdir(parents=True, exist_ok=True)
    return path
