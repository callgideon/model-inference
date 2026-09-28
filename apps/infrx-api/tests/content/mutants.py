#!/usr/bin/env python3
"""R32/R83 for C2: one single-edit defect per decision `tests/content` claims, through the
shared runner (`tests/contracts/mutants.py`). The service (`infrx/content/__init__.py`) and
the C2-RPC contract fake (`infrx/content/fakes.py`, the rules lab-sql's RPC must answer)
both.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/content/test_mutants.py
    uv run --frozen python tests/content/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/content/test_content.py"
if str(API_DIR) not in sys.path:        # `python tests/content/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

C = "content/__init__.py"
F = "content/fakes.py"


def case(name: str) -> str:
    return f"test_content__{name}"


SEAM = case("a_cross_provider_ref_or_grant_exposes_nothing")
FORGED = case("a_forged_grant_ref_is_not_found")
USER = case("a_ref_is_redeemable_by_the_user_it_was_issued_to_only")
RECIPIENT = case("a_ref_is_bound_to_its_recipient_provider")
REVOKED = case("revocation_fails_an_existing_ref_closed")
MEMBER = case("a_revoked_membership_fails_an_existing_ref_closed")
NARROWED = case("a_narrowed_grant_fails_an_existing_ref_closed")
TTL = case("the_ref_expires_at_its_ttl")
BOUNDS = case("the_grant_expiry_and_retention_bound_the_ref")
SHRUNK = case("retention_shrunk_after_issue_fails_the_ref_closed")
PURPOSE = case("each_purpose_is_its_own_permission")
CATEGORIES = case("both_content_categories_are_required")
MODEL = case("the_model_is_the_jobs_never_the_callers")
VIEWER = case("a_viewer_issues_no_ref")
KEY = case("a_storage_key_is_never_a_ref")
DIGEST = case("the_database_never_sees_the_handle")
RPC_INPUT = case("the_rpc_refuses_a_reused_handle_an_unbounded_ttl_or_no_category")
TTL_BOUND = case("the_ttl_is_bounded")
DELETED = case("a_deleted_request_is_expired_despite_the_object")
PAST = case("content_past_its_bound_is_expired_despite_the_object")
MISSING = case("missing_content_is_a_state_never_a_crash")
HOLDS = case("a_live_ref_holds_the_object_against_the_sweep")
VERSION = case("the_grant_ref_names_a_version_and_rights_follow_the_current_one")

REDEEM_CURRENT = ("        grant, retained_until = self._current(row, now)\n"
                  "        if now >= retained_until:")

MUTANTS: tuple[Mutant, ...] = (
    # --- the service --------------------------------------------------------------------
    _m("handle_grammar_unchecked", "a storage key or path is never looked up as a ref",
       C, "        if not isinstance(handle, str) or not ids.TRACE_CONTENT_HANDLE_RE"
          ".fullmatch(handle):", "        if handle is None:", KEY),
    _m("raw_handle_to_the_database", "the RPC is given the handle's SHA-256, never the handle",
       C, "            handle_sha256=handle_digest(handle), user_id=user_id,",
       "            handle_sha256=handle, user_id=user_id,", DIGEST),
    _m("digest_is_reversible", "the stored form is a one-way SHA-256",
       C, "hashlib.sha256(handle.encode()).hexdigest()", "handle.encode().hex()", DIGEST),
    _m("one_category_suffices", "trace content needs request AND response content",
       C, "CATEGORIES = (DataCategory.request_content, DataCategory.response_content)",
       "CATEGORIES = (DataCategory.response_content,)", CATEGORIES),
    _m("purpose_fixed", "the caller's purpose is the one checked",
       C, "purpose=DataPurpose(purpose), categories=CATEGORIES",
       "purpose=DataPurpose.provider_sharing, categories=CATEGORIES", PURPOSE),
    _m("ttl_unbounded", "a ref lives 1..MAX_TTL_S seconds",
       C, "        if not 1 <= ttl_s <= MAX_TTL_S:\n            raise ValueError",
       "        if False:\n            raise ValueError", TTL_BOUND),
    _m("deleted_reads_as_pending", "a deleted request is expired, a missing one pending",
       C, "ContentState.expired if deleted else ContentState.pending",
       "ContentState.pending if deleted else ContentState.expired", DELETED, MISSING),
    _m("metadata_only_unrecognised", "a trace without content is metadata_only",
       C, "        if not any(r.content_key for r in rows):", "        if False:", MISSING),
    _m("content_bound_unchecked", "content past T3's bound is expired, not lost",
       C, "        elif not all(self.retention.content_live(r.started_at, now) for r in rows):",
       "        elif False:", PAST),
    _m("content_unvalidated", "only a valid TraceContentBody is rendered",
       C, "wire.TraceContentBody.model_validate_json(data) if data else None",
       "wire.TraceContentBody.model_construct() if data else None", MISSING),
    _m("missing_object_available", "no object, no available state",
       C, "            state = ContentState.lost if content is None else ContentState.available",
       "            state = ContentState.available", MISSING),
    _m("content_of_the_callers_org", "the content is the grantor's, the binding's org",
       C, "        org, request = binding.grantor_org_id, binding.request_id",
       "        org, request = provider_org_id, binding.request_id", SEAM),
    _m("holds_nothing", "a live ref holds the object (T3 holds)",
       C, "        return await self.refs.held(org_id, request_id)", "        return False",
       HOLDS),
    # --- the C2-RPC contract (what lab-sql's RPC must answer) --------------------------
    _m("rpc_foreign_grant_resolved", "a grant ref resolves only for its recipient provider",
       F, "g.recipient_provider_org_id\n                       == provider_org_id and ", "",
       SEAM, FORGED),
    _m("rpc_foreign_request", "the request must be the grantor's own job",
       F, "        if pinned is None or (pinned.grantor_org_id, request_id) not in self.jobs:",
       "        if pinned is None:", FORGED),
    _m("rpc_forged_ref_resolved", "the ref's digest must be the grant version's",
       F, "and ref_of(g) == grant_ref), None)",
       "and g.grant_id in grant_ref), None)", FORGED),
    _m("rpc_last_category_only", "every category of the ref is checked",
       F, 'for category in row["categories"]:', 'for category in row["categories"][-1:]:',
       CATEGORIES),
    _m("rpc_model_from_the_grant", "the model is the job's, never assumed in scope",
       F, "provider_org_id=row[\"provider_org_id\"], model_id=model_id,",
       "provider_org_id=row[\"provider_org_id\"], model_id=grant.model_ids[0] if grant "
       "else model_id,", MODEL),
    _m("rpc_revoked_member_redeems", "the user's membership is judged current",
       F, "authorize_content_read(membership=membership,",
       "authorize_content_read(membership=membership and membership.model_copy("
       "update={'revoked_at': None}),", MEMBER),
    _m("rpc_viewer_reads_content", "a viewer's membership reads no content",
       F, "authorize_content_read(membership=membership,",
       "authorize_content_read(membership=membership and membership.model_copy("
       "update={'role': 'developer'}),", VIEWER),
    _m("rpc_retention_fixed", "retention is the grant's retention_days",
       F, "created_at + timedelta(days=grant.retention_days)",
       "created_at + timedelta(days=90)", BOUNDS, SHRUNK),
    _m("rpc_grant_expiry_ignored", "a ref never outlives its grant",
       F, ",\n                         grant.expires_at or retained_until)", ")", BOUNDS),
    _m("rpc_issues_past_retention", "no ref is issued past retention",
       F, "        if expires_at <= now:", "        if False:", BOUNDS),
    _m("rpc_handle_reissued", "a handle digest is issued once",
       F, "        if handle_sha256 in self.refs:", "        if False:", RPC_INPUT),
    _m("rpc_input_unbounded", "the RPC bounds the ttl and needs a category",
       F, "        if not 1 <= ttl_s <= MAX_TTL_S or not categories:", "        if False:",
       RPC_INPUT),
    _m("rpc_any_user_redeems", "a ref is redeemed by the user it was issued to",
       F, '                or row["user_id"] != user_id:', "                or False:", USER),
    _m("rpc_any_provider_redeems", "a ref is redeemed by its recipient provider",
       F, 'if row is None or row["provider_org_id"] != provider_org_id \\',
       "if row is None \\", RECIPIENT),
    _m("rpc_ref_never_expires", "a ref past its expiry is gone",
       F, '        if now >= row["expires_at"]:\n            raise errors.Gone',
       "        if False:\n            raise errors.Gone", TTL),
    _m("rpc_redeem_past_retention", "a ref past its grant's retention is gone",
       F, REDEEM_CURRENT, REDEEM_CURRENT.replace("if now >= retained_until:", "if False:"),
       SHRUNK),
    _m("rpc_redeem_trusts_the_issue", "every redeem re-checks the current grant and membership",
       F, REDEEM_CURRENT,
       "        grant, retained_until = self.access.history[-1], row[\"expires_at\"]\n"
       "        if now >= retained_until:", REVOKED, MEMBER, NARROWED),
    _m("rpc_expired_ref_holds", "an expired ref holds nothing",
       F, ' != (org_id, request_id) \\\n                    or now >= row["expires_at"]:',
       " != (org_id, request_id):", TTL),
    _m("rpc_revoked_ref_holds", "a ref its recipient can no longer redeem holds nothing",
       F, "                if now < self._current(row, now)[1]:", "                if True:",
       REVOKED),
    _m("rpc_binding_names_the_pinned_version", "the binding is the grant's current version",
       F, "RefBinding(grant_id=grant.grant_id, grant_version=grant.version,",
       "RefBinding(grant_id=grant.grant_id, grant_version=1,", VERSION),
    _m("rpc_hold_ignores_the_request", "a ref holds its own request only",
       F, 'if (row["grantor_org_id"], row["request_id"]) != (org_id, request_id)',
       'if row["grantor_org_id"] != org_id', TTL),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


# The copies run the in-memory world; the MinIO (`-m s3`) and PostgreSQL (`-m pg`, WR-C2-5)
# halves are the suite's own runs.
RUNNER = Runner(name="c2", targets=(SUITE_FILE,), extra_args=("-m", "not s3 and not pg"))


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run C2's mutation list"))
