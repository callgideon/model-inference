#!/usr/bin/env python3
"""R32/R40/R83 for G4U: one single-edit defect per invariant `test_uploads.py` claims.

G4U's own list, beside `tests/g/mutants.py` rather than in it (the G2 lane edits that one
in parallel), on the same shared runner: one mutant at a time in a throwaway copy, the
named cases run there, a pristine baseline first, and only an assertion or a typed
`DomainError` counts as a kill unless the mutant declares its death in `dies_by`.

    uv run --frozen pytest -q tests/g/test_uploads_mutants.py
    uv run --frozen python -m tests.g.uploads_mutants --list
"""
from __future__ import annotations

import pathlib
import re

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner   # noqa: F401

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/g/test_uploads.py"
U = "gateway/routes/uploads.py"


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- item 1: enabled only when the store exists ----------------------------
    _m("mounted_without_a_store", "no media store, no upload route",
       U, "    if store is None:\n        return None", "    if False:\n        return None",
       "test_media_sec__no_store_mounts_no_upload_route"),
    _m("error_handlers_not_installed", "a standalone router still answers in the envelope",
       U, "    install_error_handlers(app, new_request_id)\n", "",
       "test_media_sec__no_store_mounts_no_upload_route"),
    _m("runtime_store_ignored", "register(app, rt) mounts over rt.media_store",
       U, 'getattr(rt, "media_store", None)', "None",
       "test_media_sec__the_routes_mount_over_the_runtime_store_and_its_shared_slots"),
    _m("runtime_slots_ignored", "uploads share the runtime's large-body bound with chat",
       U, 'getattr(rt, "large_bodies", None)', "None",
       "test_media_sec__the_routes_mount_over_the_runtime_store_and_its_shared_slots"),
)


def case_names() -> set[str]:
    """Every case `test_uploads.py` defines."""
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


#: The shared runner's default copy (package, tests, pyproject) is all these cases need.
RUNNER = Runner(name="g4u", targets=(SUITE_FILE,))


def run_mutant(mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names."""
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run G4U's upload mutation list"))
