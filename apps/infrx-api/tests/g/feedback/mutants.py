#!/usr/bin/env python3
"""R32/R40/R83 for G4F: one single-edit defect per invariant the feedback route claims.

G4F's own list in its own directory (the `tests/g/uploads` precedent). The shared runner:
one mutant at a time in a throwaway copy, a pristine baseline first, and only an assertion
or a typed `DomainError` counts as a kill. The copies run the named `_pg` cases only on
request (`INFRX_G4F_PG=1` with `INFRX_D_TASK`, which the copy then inherits): inside a
whole-suite run the parent process holds the key's container, so a copy cannot take it.
Otherwise they skip in the copy and the route cases carry every kill.

    uv run --frozen pytest -q tests/g/feedback/test_mutants.py
    INFRX_G4F_PG=1 INFRX_D_TASK=g4f INFRX_MUTANTS=all \
        uv run --frozen pytest -q tests/g/feedback/test_mutants.py
    uv run --frozen python -m tests.g.feedback.mutants --list
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result, Runner

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILES = ("tests/g/feedback/test_feedback.py", "tests/g/feedback/test_feedback_pg.py")
F = "gateway/routes/feedback.py"

UNIT = "test_feedback_ack__"
PG = "test_feedback_ack_pg__"
ACCEPT = "        stored = await service.accept(context, submission.request_id, signal, idem)\n"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- enablement: off unless the composition put a service on the runtime ------
    _m("mounted_without_a_service", "no feedback service, no feedback route",
       "    if service is None:\n        return None", "    if False:\n        return None",
       UNIT + "no_service_mounts_no_feedback_route"),
    _m("error_handlers_not_installed", "a standalone router still answers in the envelope",
       "    install_error_handlers(app, new_request_id)\n", "",
       UNIT + "no_service_mounts_no_feedback_route"),
    _m("runtime_service_ignored", "register(app, rt) mounts over rt.feedback",
       'getattr(rt, "feedback", None)', "None",
       UNIT + "the_route_mounts_over_the_runtime_service"),
    _m("acknowledged_with_200", "the acknowledgment of a stored row is a 201",
       "status_code=201,", "status_code=200,",
       UNIT + "the_route_mounts_over_the_runtime_service"),
    # --- identity and provenance ---------------------------------------------------
    _m("identity_after_the_body", "the key is resolved before any body byte is parsed",
       "        context = await auth.context(request)\n", "",
       UNIT + "identity_comes_before_the_body"),
    _m("provenance_fields_dropped_silently", "a provenance field is refused, not ignored",
       "wire.FeedbackSubmission.model_validate(body)",
       "wire.FeedbackSubmission.model_validate("
       "{k: v for k, v in body.items() if k in wire.FeedbackSubmission.model_fields})",
       UNIT + "a_spoofed_provenance_field_is_refused_before_the_service"),
    _m("invalid_signal_passed_on", "an invalid signal never reaches the service",
       "wire.FeedbackSubmission.model_validate(body)",
       "wire.FeedbackSubmission.model_construct(**body)",
       UNIT + "an_invalid_signal_is_refused_before_the_service"),
    _m("content_type_unchecked", "the body is application/json or a 400 before the read",
       "        intake.check_content_type(request)\n", "",
       UNIT + "a_non_object_body_is_refused_before_the_service"),
    _m("request_id_is_the_http_id", "the feedback targets the body's request, not this call",
       ACCEPT, ACCEPT.replace("submission.request_id", "request_id"),
       UNIT + "the_author_and_org_are_the_keys_never_the_body",
       PG + "the_ack_is_a_committed_customer_row_before_any_trace",
       PG + "a_lost_ack_replays_the_one_row"),
    _m("signal_carries_the_request_id", "the service gets exactly {name, value, comment}",
       'exclude={"request_id"}', "exclude=set()",
       UNIT + "the_author_and_org_are_the_keys_never_the_body"),
    _m("inference_id_is_the_target", "Inference-Id is this call's id, not the target's",
       "headers={wire.HEADER_INFERENCE_ID: request_id}",
       "headers={wire.HEADER_INFERENCE_ID: submission.request_id}",
       UNIT + "the_author_and_org_are_the_keys_never_the_body"),
    # --- idempotency -----------------------------------------------------------------
    _m("operation_renamed", "the scope is D6F's `feedback` operation",
       'OPERATION = "feedback"', 'OPERATION = "feedback.accept"',
       UNIT + "the_author_and_org_are_the_keys_never_the_body"),
    _m("key_not_required", "every submission carries an Idempotency-Key",
       "        if request.headers.get(wire.HEADER_IDEMPOTENCY_KEY) is None:",
       "        if False:",
       UNIT + "the_idempotency_key_is_required_and_bounded"),
    _m("key_unbounded", "an empty or over-long key is a 400 naming the header",
       "        idem = idempotency(context, request.headers, digest, OPERATION)",
       "        idem = idempotency(context, {}, digest, OPERATION).model_copy("
       "update={'key': request.headers.get('idempotency-key')})",
       UNIT + "the_idempotency_key_is_required_and_bounded"),
    _m("digest_over_the_raw_bytes", "a retry in another spelling replays",
       "codec.compact_bytes(submission)", "raw",
       UNIT + "the_payload_digest_is_the_canonical_signal"),
    _m("digest_ignores_the_request", "a key reused for another request conflicts",
       "codec.compact_bytes(submission)",
       'codec.compact_bytes(submission.model_dump(mode="json", exclude={"request_id"}))',
       UNIT + "the_payload_digest_is_the_canonical_signal"),
    # --- bounds ------------------------------------------------------------------------
    _m("body_bounded_by_the_chat_cap", "a feedback body is bounded by its own cap",
       "max_bytes=MAX_FEEDBACK_BYTES", "max_bytes=limits.max_request_bytes",
       UNIT + "the_body_is_bounded"),
    _m("cap_below_the_largest_signal", "the largest legal signal fits under the cap",
       "MAX_FEEDBACK_BYTES = 131_072", "MAX_FEEDBACK_BYTES = 65_536",
       UNIT + "the_body_is_bounded"),
    # --- the acknowledgment --------------------------------------------------------------
    _m("ack_on_refusal", "no 201 unless the service accepted the row",
       ACCEPT,
       "        try:\n    " + ACCEPT + "        except errors.DomainError:\n"
       "            stored = wire.FeedbackAccepted(feedback_id='fb_pending', request_id="
       "submission.request_id, channel='api', author_role='customer', "
       "created_at='2026-09-27T00:00:00Z')\n",
       UNIT + "no_ack_before_the_service_accepted",
       PG + "another_orgs_request_is_not_found",
       PG + "the_disabled_flag_is_a_503_and_writes_nothing"),
    _m("stored_row_rendered", "only the frozen FeedbackAccepted leaves",
       'accepted.model_dump(mode="json")', 'stored.model_dump(mode="json")',
       UNIT + "only_the_frozen_acknowledgment_leaves"),
)


def case_names() -> set[str]:
    """Every case the two suite files define."""
    return {name for path in SUITE_FILES
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The repository's shape, so the `_pg` cases find the migrations: the package, tests
    and pyproject copied to `<tmp>/apps/infrx-api`, `<tmp>/apps/app` linked read only."""
    api = root / "apps" / "infrx-api"
    for name in (shared.PACKAGE, "tests"):
        shutil.copytree(API_DIR / name, api / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    (root / "apps" / "app").symlink_to(API_DIR.parent / "app")
    return api


RUNNER = Runner(name="g4f", targets=SUITE_FILES, layout=_layout,
                env=("INFRX_D_TASK",) if os.environ.get("INFRX_G4F_PG") else ())


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run G4F's feedback mutation list"))
