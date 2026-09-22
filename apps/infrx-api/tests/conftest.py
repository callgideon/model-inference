"""Test-order isolation for the four top-level legacy tests.

E2R, from the S1 independent review: `pytest tests/test_media.py tests/test_inflight.py
tests/test_gateway_auth.py` fails 7 of `test_gateway_auth.py`'s cases with `(None, None)`
from `authenticate`, while the alphabetical order passes. Nothing is wrong with either test.

`test_gateway_auth.py` sets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and
`GATEWAY_API_KEY` in `os.environ` and *then* imports the `gateway` shim, which calls
`create_app()` at import time and reads the environment exactly once. `test_media.py` and
`test_inflight.py` import the same shim with neither variable set, deliberately - they
exercise the unauthenticated path. So whichever module pytest imports FIRST decides whether
the one shared runtime has a Supabase at all, and
`infrx/auth/keys.py::authenticate` short-circuits on `if not s.supabase_url`.

Setting the variables globally here would break the other two (an authenticating runtime
answers their unauthenticated requests with 401), and reloading the shim at import time only
moves the problem to the other order. So the settings are applied to the live runtime for
`test_gateway_auth.py`'s tests and put back afterwards, through the shim's own legacy
globals, which write through to `runtime.settings`. Neither order can now change what the
other modules see.

This is a wiring fix, not a behaviour change: no test file is touched, and the right
permanent answer is R48's - that module builds its own app with `create_app()` instead of
mutating shim globals, which is a coordinator-owned rewrite of a legacy test.
"""
from __future__ import annotations

import os

import pytest

#: The one module that needs an authenticating runtime, and the settings it needs.
LEGACY_AUTH_MODULE = "test_gateway_auth"
LEGACY_AUTH_SETTINGS = {
    "SUPABASE_URL": ("SUPABASE_URL", lambda value: value.rstrip("/")),
    "SUPABASE_KEY": ("SUPABASE_SERVICE_ROLE_KEY", lambda value: value),
    "LEGACY_KEY": ("GATEWAY_API_KEY", lambda value: value),
}


@pytest.fixture(autouse=True)
def legacy_gateway_settings(request):
    """Give `test_gateway_auth.py` the runtime its own `os.environ` asked for, whatever
    imported the shim first, and restore it for everybody else."""
    if request.module.__name__.rsplit(".", 1)[-1] != LEGACY_AUTH_MODULE:
        yield
        return
    import gateway                                  # noqa: PLC0415 - only for this module

    before = {name: getattr(gateway, name) for name in LEGACY_AUTH_SETTINGS}
    for name, (variable, normalise) in LEGACY_AUTH_SETTINGS.items():
        if variable in os.environ:
            setattr(gateway, name, normalise(os.environ[variable]))
    try:
        yield
    finally:
        for name, value in before.items():
            setattr(gateway, name, value)
