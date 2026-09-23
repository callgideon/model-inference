#!/usr/bin/env python3
"""R32/R40/R83 for G4U: one single-edit defect per invariant `test_uploads.py` claims.

G4U's own list, in its own directory rather than in `tests/g/mutants.py`: the G2 lane
edits that file in parallel, and its coverage rule claims every `tests/g/test_*.py` case
(the `tests/g/ops` precedent). Same shared runner: one mutant at a time in a throwaway
copy, the named cases run there, a pristine baseline first, and only an assertion or a
typed `DomainError` counts as a kill unless the mutant declares its death in `dies_by`.

    uv run --frozen pytest -q tests/g/uploads/test_uploads_mutants.py
    uv run --frozen python -m tests.g.uploads.uploads_mutants --list
"""
from __future__ import annotations

import pathlib
import re

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Outcome, Result, Runner   # noqa: F401

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILE = "tests/g/uploads/test_uploads.py"
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
    _m("ingress_pool_ignored", "without a runtime pool, uploads share the ingress's pool",
       U, '             or getattr(getattr(rt, "ingress", None), "large_bodies", None)\n', "",
       "test_media_sec__without_a_runtime_pool_uploads_count_against_the_ingress_pool"),
    # --- item 2: POST /v1/uploads ---------------------------------------------------
    _m("tenant_is_the_key_id", "the upload's org is the key row's org",
       U, "        created = await store.create_upload(context.org_id, body)",
       "        created = await store.create_upload(context.key_id, body)",
       "test_dur_rls__each_audience_creates_in_its_own_org",
       "test_dur_rls__a_completed_upload_is_usable_only_by_its_org"),
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
       U, '        ticket = wire.UploadCreated.model_validate(created).model_dump(mode="json")',
       "        ticket = created",
       "test_media_sec__no_store_field_outside_the_frozen_ticket_leaves"),
    _m("ticket_destination_unchecked", "the destination is exactly the scheme + the handle",
       U, '                or ticket["destination_ref"] != UPLOAD_SCHEME + handle:',
       "                or False:",
       "test_media_sec__no_store_value_outside_the_frozen_ticket_leaves"),
    _m("ticket_handle_unchecked", "the issued handle is upl_ + 22..64 url-safe characters",
       U, "        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle) \\\n                or ticket",
       "        if False \\\n                or ticket",
       "test_media_sec__no_store_value_outside_the_frozen_ticket_leaves"),
    # T4: an org read from anywhere but the key. Each adds a source the cases must refuse.
    _m("create_org_from_query", "a query parameter never names the org",
       U, "        created = await store.create_upload(context.org_id, body)",
       '        created = await store.create_upload(request.query_params.get("org_id")'
       " or context.org_id, body)",
       "test_dur_rls__the_body_names_no_org_and_nothing_the_contract_lacks",
       "test_dur_rls__the_router_reads_no_org_from_the_query_or_headers"),
    _m("put_org_from_header", "a header never names the org of a write",
       U, "store.put_upload(context.org_id, handle, data, mime)",
       'store.put_upload(request.headers.get("x-org-id") or context.org_id, handle, data, mime)',
       "test_dur_rls__another_orgs_upload_is_the_unknown_handles_404",
       "test_dur_rls__the_router_reads_no_org_from_the_query_or_headers"),
    _m("complete_org_from_header", "a header never names the org of a completion",
       U, "        ref = await store.finalize_upload(context.org_id, handle)",
       '        ref = await store.finalize_upload(request.headers.get("x-infrx-org")'
       " or context.org_id, handle)",
       "test_dur_rls__another_orgs_upload_is_the_unknown_handles_404",
       "test_dur_rls__the_router_reads_no_org_from_the_query_or_headers"),
    # Confirmation C1/G1/G2: the weaker checks a reviewer tried, not only their removal.
    _m("ticket_destination_prefix_only", "the destination equals the scheme + handle exactly",
       U, '                or ticket["destination_ref"] != UPLOAD_SCHEME + handle:',
       '                or not ticket["destination_ref"].startswith(UPLOAD_SCHEME):',
       "test_media_sec__no_store_value_outside_the_frozen_ticket_leaves"),
    _m("ticket_handle_prefix_match", "the issued handle matches the grammar in full",
       U, "        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle) \\\n",
       "        if not ids.UPLOAD_HANDLE_RE.match(handle) \\\n",
       "test_media_sec__no_store_value_outside_the_frozen_ticket_leaves"),
    _m("created_is_not_201", "a created upload is 201 (the section 7 default)",
       U, "status_code=201,", "status_code=200,",
       "test_media_sec__the_ticket_carries_exactly_the_frozen_fields"),
    _m("control_body_unbounded", "create and complete read at most MAX_CONTROL_BYTES",
       U, "max_bytes=MAX_CONTROL_BYTES,", "max_bytes=limits.max_request_bytes,",
       "test_media_sec__a_control_body_is_bounded"),
    _m("control_deadline_stretched", "create and complete read under the intake deadline",
       U, "timeout_s=limits.intake_timeout_s, clock=rt.clock)",
       "timeout_s=limits.intake_timeout_s * 100, clock=rt.clock)",
       "test_media_sec__a_slow_control_body_is_cut_at_the_deadline"),
    # R83: the translation itself goes, the request id stays - so the store's typed refusal
    # escapes the route (a `DomainError` death) instead of a changed signature answering 422.
    _m("refusals_not_translated", "the store's refusals leave as the contract's envelope",
       U, "        translated = guard(handler)",
       "        translated = lambda request: handler(request, ids.new_request_id())  # noqa",
       "test_media_sec__the_destination_is_write_once_over_http",
       "test_media_sec__completion_refusals_leave_in_the_envelope"),
    _m("refusals_keep_the_connection", "every upload refusal closes the connection",
       U, '                answer.headers["Connection"] = "close"', "                pass",
       "test_media_sec__every_refusal_closes_the_connection",
       "test_media_sec__no_store_value_outside_the_frozen_ticket_leaves"),
    _m("successes_close_the_connection", "an answer that succeeded keeps the connection",
       U, "            if answer.status_code >= 400:", "            if True:",
       "test_media_sec__every_refusal_closes_the_connection"),
    # --- item 3: PUT /v1/uploads/{handle}, the constrained destination -----------
    _m("destination_cap_is_the_request_cap", "the destination reads at most MAX_MEDIA_BYTES",
       U, "max_bytes=limits.max_media_bytes,", "max_bytes=limits.max_request_bytes,",
       "test_media_sec__a_chunked_upload_over_the_cap_stops_reading"),
    _m("destination_deadline_stretched", "the destination is under the intake deadline",
       U, "timeout_s=limits.intake_timeout_s, clock=rt.clock,\n                                          large=slot)",
       "timeout_s=limits.intake_timeout_s * 100, clock=rt.clock,\n                                          large=slot)",
       "test_media_sec__a_slow_upload_is_cut_at_the_deadline"),
    _m("destination_claims_no_slot", "a large upload claims a shared large-body slot",
       U, "clock=rt.clock,\n                                          large=slot)", "clock=rt.clock)",
       "test_media_sec__large_uploads_hold_a_shared_slot_until_stored"),
    _m("slot_never_released", "every exit gives the slot back, refusals included",
       U, "            slot.release()", "            pass",
       "test_media_sec__large_uploads_hold_a_shared_slot_until_stored",
       "test_media_sec__a_chunked_upload_over_the_cap_stops_reading",
       "test_media_sec__a_slow_upload_is_cut_at_the_deadline"),
    # The brief's order: the slot goes back before the store call, which still runs.
    _m("slot_released_before_the_store", "the slot is held until the store has the bytes",
       U, "large=slot)\n", "large=slot)\n            slot.release()\n",
       "test_media_sec__large_uploads_hold_a_shared_slot_until_stored"),
    _m("store_call_undeadlined", "the store call is under the intake deadline too",
       U, "            await asyncio.wait_for(store.put_upload(context.org_id, handle, data, mime),\n"
          "                                   limits.intake_timeout_s)",
       "            await store.put_upload(context.org_id, handle, data, mime)",
       "test_media_sec__a_hung_store_is_cut_at_the_deadline"),
    _m("handle_grammar_unchecked", "a malformed handle is not_found before any byte",
       U, "        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle):", "        if False:",
       "test_media_sec__a_malformed_handle_is_not_found_before_any_byte"),
    _m("destination_type_unchecked", "the destination stores only an allowed media type",
       U, "        if mime not in store.fetcher.allowed_mime:", "        if False:",
       "test_media_sec__the_destination_takes_only_media_types"),
    _m("destination_reads_before_identity", "identity is resolved before the bytes are read",
       U, "        context = await tenant(request)\n        handle = handle_of(request)\n        mime",
       "        handle = handle_of(request)\n        mime",
       "test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer_an_upload"),
    _m("stored_is_not_204", "stored bytes are 204 (the section 7 default)",
       U, "Response(status_code=204,", "Response(status_code=200,",
       "test_media_sec__the_destination_is_write_once_over_http"),
    _m("destination_under_another_id", "the destination is scoped by the key's org",
       U, "store.put_upload(context.org_id, handle, data, mime)",
       "store.put_upload(context.key_id, handle, data, mime)",
       "test_dur_rls__another_orgs_upload_is_the_unknown_handles_404"),
    # --- item 4: POST /v1/uploads/{handle}/complete ---------------------------------
    _m("completion_renders_the_ref", "completion answers the projection, never the ref (R47)",
       U, '        return JSONResponse(completed.model_dump(mode="json"),',
       '        return JSONResponse(ref.model_dump(mode="json"),',
       "test_media_sec__completion_projects_the_ref"),
    _m("completion_fields_ignored", "completion takes no fields (R17)",
       U, "        if await control_body(request) != {}:",
       "        if await control_body(request) is None:",
       "test_media_sec__completion_takes_no_fields"),
    _m("completion_reads_before_identity", "identity is resolved before completion reads a body",
       U, "        context = await tenant(request)\n        handle = handle_of(request)\n"
          "        if await control_body",
       "        handle = handle_of(request)\n        if await control_body",
       "test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer_an_upload"),
    _m("completion_under_another_id", "completion is scoped by the key's org",
       U, "        ref = await store.finalize_upload(context.org_id, handle)",
       "        ref = await store.finalize_upload(context.key_id, handle)",
       "test_dur_rls__another_orgs_upload_is_the_unknown_handles_404",
       "test_dur_rls__a_completed_upload_is_usable_only_by_its_org"),
    # --- item 6: the declared paths --------------------------------------------------
    _m("undeclared_path", "the routes are at the frozen contract's paths",
       U, 'UPLOADS_PATH = "/v1/uploads"', 'UPLOADS_PATH = "/v1/upload"',
       "test_media_sec__the_upload_handshake_uses_only_the_frozen_names"),
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
