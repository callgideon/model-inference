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
    # --- item 2: POST /v1/uploads ---------------------------------------------------
    _m("tenant_is_the_key_id", "the upload's org is the key row's org",
       U, "        created = await store.create_upload(context.org_id, body)",
       "        created = await store.create_upload(context.key_id, body)",
       "test_dur_rls__each_audience_creates_in_its_own_org"),
    _m("operator_owns_uploads", "an operator credential creates, writes and completes nothing",
       U, "        if context.audience not in CALLABLE:", "        if False:",
       "test_dur_rls__an_operator_key_owns_no_upload"),
    _m("create_reads_before_identity", "identity is resolved before the create body is read",
       U, "        context = await tenant(request)\n        intake.check_content_type(request)\n"
          "        body = await control_body(request)\n",
       "        intake.check_content_type(request)\n        body = await control_body(request)\n"
       "        context = await tenant(request)\n",
       "test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer_an_upload"),
    _m("tenant_from_the_body", "an org named in the body is refused, never honoured",
       U, "        created = await store.create_upload(context.org_id, body)",
       '        created = await store.create_upload(body.pop("org_id", context.org_id), body)',
       "test_dur_rls__the_body_names_no_org_and_nothing_the_contract_lacks"),
    _m("ticket_not_rendered_through_the_wire_model", "the ticket is the frozen wire form",
       U, '        return JSONResponse(wire.UploadCreated.model_validate(created).model_dump(mode="json"),',
       "        return JSONResponse(created,",
       "test_media_sec__no_store_field_outside_the_frozen_ticket_leaves"),
    _m("created_is_not_201", "a created upload is 201 (the section 7 default)",
       U, "status_code=201,", "status_code=200,",
       "test_media_sec__the_ticket_carries_exactly_the_frozen_fields"),
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
