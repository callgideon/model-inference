"""E6L fixtures: the session's Lab world on the e6l stack, and where a case's artifacts live."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


@pytest.fixture
def workdir(request, tmp_path) -> Path:
    """`$INFRX_E6L_OUT/cases/<case>` under the runner (kept as the verdict's raw evidence),
    else pytest's own temporary directory."""
    out = os.environ.get("INFRX_E6L_OUT")
    if not out:
        return tmp_path
    path = Path(out) / "cases" / re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:120]
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def lab():
    """One Lab database and object prefix per session; BLOCKED without a provisioned stack."""
    import importlib.util
    import sys as _sys
    here = Path(__file__).resolve().parent
    key = f"{here.name}.lab_world"
    lab_world = _sys.modules.get(key)
    if lab_world is None:
        spec = importlib.util.spec_from_file_location(key, here / "lab_world.py")
        lab_world = importlib.util.module_from_spec(spec)
        _sys.modules[key] = lab_world
        spec.loader.exec_module(lab_world)
    if not lab_world.stack.has_stack():
        pytest.skip(f"BLOCKED[stack] no e6l stack: {lab_world.RERUN}")
    world = lab_world.Lab()
    yield world
    world.close()
