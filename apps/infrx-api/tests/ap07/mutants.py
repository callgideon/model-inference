#!/usr/bin/env python3
"""R32/R40/R83 for AP-07: one single-edit defect per decision the data-use controls and the
Lab trace projection claim, each killed by a named case of `tests/ap07`.

The shared runner, one mutant at a time in a throwaway copy after a pristine baseline. The
copies run the fake half of every `world` case; the PostgreSQL half (all of `test_data_use`)
runs there only on request (`INFRX_AP7_PG=1`, the copy then inheriting `INFRX_D_TASK`) - inside
a whole-suite run the parent process holds the key's container, so the PG-only mutants are
selected only then (`PG_ONLY`).

    uv run --frozen pytest -q tests/ap07/test_mutants.py                                  # subset
    INFRX_D_TASK=ap7 INFRX_AP7_PG=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap07/test_mutants.py
"""
from __future__ import annotations

import os
import pathlib
import re

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Result, Runner
from ..g.feedback.mutants import _layout

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILES = ("tests/ap07/test_data_use.py", "tests/ap07/test_trace_reads.py",
               "tests/ap07/test_eligible.py")
D = "console/data_use.py"
R = "gateway/routes/console_data_use.py"
L = "gateway/routes/lab_traces.py"
EL = "traces/eligible.py"
FILES = (D, R, L, EL)
U = "test_data_use__"
OWNER = U + "only_the_grantors_owner_decides"
CAPTURE = U + "capture_is_a_versioned_consent_the_gateway_reads"
HEAD = U + "the_head_is_the_highest_live_key_mode"
REPLAY = U + "a_replay_writes_nothing_and_a_stale_view_conflicts"
GRANTS = U + "grants_are_purpose_specific_versions"
EXPIRY = U + "a_grant_expires_on_the_databases_clock"
REVOKE = U + "revocation_is_the_grantors_and_holds_at_once"
T = "test_trace_reads__"
STATES = T + "each_row_states_why_its_content_is_or_is_not_there"
LAPSED = T + "a_lapsed_grant_is_revoked_not_never_granted"
BETWEEN = T + "a_revocation_between_list_and_detail_withholds_content"
FILTERED = T + "filters_run_server_side_and_bind_the_cursor"
E = "test_eligible__"
OFFERED = E + "only_full_stored_content_of_that_model_and_grantor_newest_first"
NO_GRANT = E + "no_current_judging_grant_offers_nothing"
VIDEO = E + "video_is_the_requests_admitted_video_source"
DELETED = E + "a_deleted_request_or_content_is_not_offered"
BOUND = E + "the_bound_is_the_judges"
END_TO_END = E + "a_captured_request_is_offered_end_to_end"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting and the envelope ------------------------------------------------------------
    _m("mounted_without_data_use", "no rt.data_use (the switch off): no route", R,
       "    if service is None:\n        return None", "    if False:\n        return None",
       U + "nothing_is_mounted_without_data_use"),
    _m("validation_not_enveloped", "every failure is the R270 envelope (control.R270Route)", R,
       "APIRouter(route_class=control.R270Route)", "APIRouter()",
       U + "evaluation_consent_needs_full_capture", U + "a_failure_is_an_envelope_never_a_trace"),
    _m("replayed_grant_is_created", "a new version is 201, a replay 200", R,
       "status = 201 if created else 200", "status = 201",
       GRANTS),
    # --- who decides --------------------------------------------------------------------------
    _m("key_audience_decides", "only a verified web session decides data use", D,
       'if actor.audience != "session" or not actor.user_id', "if not actor.user_id", OWNER),
    _m("any_member_decides", "the organization's owner decides, not any member", D,
       "if row is None or not row[1]:", "if row is None:", OWNER),
    _m("member_role_is_owner", "a plain member is not an owner", D,
       "and m.user_id = %s and m.role = 'owner')", "and m.user_id = %s and m.role <> 'x')",
       OWNER),
    _m("suspension_ignored", "a suspended organization writes nothing (R33)", D,
       "        if write and row[0]:\n", "        if False:\n",
       U + "a_suspended_organization_decides_nothing"),
    _m("another_orgs_key", "a key of another organization is not found", D,
       '"org_id = %s and revoked_at is null for update", (key_id, org))',
       '"%s::uuid is not null and revoked_at is null for update", (key_id, org))', OWNER),
    # --- capture: a versioned consent decision ------------------------------------------------
    _m("head_is_the_key_mode", "the head is the highest live key mode", D,
       "mode = max((body.mode, *(m for (m,) in others)), key=MODES.index)",
       "mode = body.mode", HEAD),
    _m("key_mode_not_written", "the key's own mode is set with the consent", D,
       'await conn.execute("update public.api_keys set trace_mode = %s where id = %s",',
       'await conn.execute("select %s::text, %s::uuid",', CAPTURE),
    _m("retention_not_recorded", "the consent carries the chosen retention", D,
       "(org, body.consent_version + 1, mode, body.retention_days,",
       "(org, body.consent_version + 1, mode, 30,", CAPTURE),
    _m("principal_is_the_org", "the consent names the owner who decided", D,
       "body.evaluation_consent, user))", "body.evaluation_consent, org))", CAPTURE),
    _m("effective_is_the_key_mode", "effective_mode is what the gateway admits", D,
       "effective_mode=effective(", "effective_mode=mode or 'off' or effective(", CAPTURE),
    _m("stale_view_writes", "a decision on a stale version is a 409", D,
       "elif (head[0] if head else 0) != body.consent_version:", "elif False:", REPLAY),
    _m("replay_rewrites", "the same decision again writes nothing", D,
       'if (key[0] or "off") == body.mode and head is not None',
       'if False and head is not None', REPLAY),
    _m("evaluation_without_full", "no inferred evaluation consent (0003)", D,
       'if self.evaluation_consent and self.mode != "full":', "if False:",
       U + "evaluation_consent_needs_full_capture"),
    # --- purpose grants -----------------------------------------------------------------------
    _m("every_purpose_granted", "a grant permits only its named purposes", D,
       '"purposes": [p.value for p in body.purposes]', '"purposes": [p.value for p in DataPurpose]',
       GRANTS),
    _m("models_not_forwarded", "the grant's models are the ones asked (0027 judges them)", D,
       '"model_ids": list(body.model_ids)', '"model_ids": []',
       U + "a_grant_names_only_the_recipients_models"),
    _m("grant_replay_rewrites", "the same grant again writes no version", D,
       "if current is not None and current.is_current(now) and same_scope(current, body):",
       "if False:", GRANTS),
    _m("stale_grant_writes", "a grant on a stale version is a 409", D,
       "if (current.version if current else 0) != body.grant_version:", "if False:", GRANTS),
    _m("scope_ignores_purposes", "a different purpose set is a different grant", D,
       "(set(g.model_ids), set(g.categories), set(g.purposes), g.retention_days,",
       "(set(g.model_ids), set(g.categories), set(body.purposes), g.retention_days,", GRANTS),
    _m("expired_reads_active", "a grant past its expiry is expired", D,
       '"active" if g.is_current(now)', '"active" if g.revoked_at is None', EXPIRY),
    _m("revoked_reads_expired", "a revoked grant says so", D,
       'else "revoked" if g.revoked_at is not None else "expired"', 'else "expired"', REVOKE),
    _m("older_version_listed", "a grant is its latest version", D,
       "g.version = (select max(h.version)", "g.version = (select min(h.version)", GRANTS),
    _m("every_grantors_grants", "the grants listed are the grantor's own", D,
       "where g.grantor_org_id = %s and g.version", "where %s::uuid is not null and g.version",
       U + "the_read_is_the_grantors_own"),
    _m("revoke_another_grantors", "another grantor's grant is not found", D,
       '"infrx.lab_access_grants where grant_id = %s and grantor_org_id = %s "',
       '"infrx.lab_access_grants where grant_id = %s and %s::uuid is not null "', REVOKE),
    _m("suspended_cannot_withdraw", "a suspended organization still withdraws a grant", D,
       "org, user = await self._grantor(actor)          # suspended or not",
       "org, user = await self._grantor(actor, write=True)  # suspended or not",
       U + "a_suspended_organization_still_withdraws_its_grant"),
    _m("withdraw_through_0027", "a withdrawal is 0066's revoke-only door, not 0027's revoke", D,
       '"lab_withdraw_access_grant", {', '"lab_revoke_access_grant", {',
       U + "a_suspended_organization_still_withdraws_its_grant"),
    _m("revoke_replay_rewrites", "a second DELETE answers the revoked grant", D,
       "if current is None or current.revoked_at is None:", "if True:", REVOKE),
    # --- the Lab projection: one access state per reason (AP-07c) ------------------------------
    _m("minimal_reads_captured", "a capture below full kept no content, grant or not", L,
       '    if row.mode != "full":\n        return "not_captured"',
       '    if False:\n        return "not_captured"', LAPSED),
    _m("lapse_reads_metadata", "a withdrawn grant is revoked, not never granted", L,
       'return "revoked" if lapsed else "metadata"', 'return "metadata"', LAPSED, BETWEEN),
    _m("never_granted_reads_revoked", "a grantor with no grant to this provider is metadata", L,
       "return bool(history) and not history[-1].is_current(",
       "return True or bool(history) and not history[-1].is_current(", STATES, LAPSED),
    _m("current_grant_reads_lapsed", "only a revoked or expired latest grant is withdrawn", L,
       "and not history[-1].is_current(", "and history[-1].is_current(", LAPSED),
    _m("oldest_grant_judged", "the latest version is the grant", L,
       "and not history[-1].is_current(", "and not history[0].is_current(", LAPSED),
    _m("lapse_not_read", "the grant history is read for a row without a grant", L,
       "withdrawn[key] = await lapsed(user_id, provider, row.org_id)",
       "withdrawn[key] = False", LAPSED),
    _m("unstored_reads_expired", "a granted capture that kept nothing is not_captured", L,
       '    if not row.content_stored:\n        return "not_captured"',
       '    if False:\n        return "not_captured"', STATES),
    _m("expired_reads_content", "deleted or past-bound content is expired", L,
       '    if not available:\n        return "expired"', '    if False:\n        return "expired"',
       STATES),
    _m("broken_reads_content", "an answer that broke off is partial", L,
       'return "content" if row.content_complete else "partial"', 'return "content"', STATES),
    _m("granted_state_not_derived", "a granted row's state is judged on its content", L,
       'item["access_state"] = access_state(row, True, False, item["content_available"])',
       'item["access_state"] = item["access_state"]', STATES),
    _m("unfinished_is_zero", "an unfinished capture has no elapsed time, not 0", L,
       "    if row.completed_at is None:\n        return None",
       "    if row.completed_at is None:\n        return 0", T + "timing_is_measured_or_absent_never_zero"),
    _m("elapsed_in_seconds", "elapsed_ms is milliseconds", L,
       ".total_seconds() * 1000)", ".total_seconds())", T + "timing_is_measured_or_absent_never_zero"),
    _m("pins_dropped", "every row names its price and trace schema version", L,
       '"price_version", "request_schema_version")', ")",
       T + "pins_name_the_admitted_revision_and_trace_schema"),
    _m("object_key_projected", "no object key leaves the route", L,
       '"price_version", "request_schema_version")',
       '"price_version", "request_schema_version", "content_key")',
       T + "no_object_key_or_storage_link_leaves_the_route"),
    _m("serving_filter_after_read", "a serving-version filter narrows what is asked", L,
       'if filters["serving_version_id"] in ("", version)\n', "if True\n", FILTERED),
    _m("model_filter_ignored", "a model filter narrows what is asked", L,
       'and filters["model_id"] in ("", model)}', "and True}", FILTERED),
    _m("cursor_crosses_filters", "a cursor is refused under another filter", L,
       "before = parse_cursor(unscoped(cursor, scope))",
       'before = parse_cursor(cursor.partition(".")[0])', FILTERED),
    _m("cursor_scope_not_minted", "a filtered list's cursor carries its filter", L,
       '"next_cursor": last and (f"{last}.{scope}" if scope else last)', '"next_cursor": last',
       FILTERED),
    _m("empty_filter_asks_everything", "a filter outside the provider's own asks nothing", L,
       "rows = await traces.rows.page(tuple(serving), before, limit) if serving else []",
       "rows = await traces.rows.page(tuple(serving), before, limit)", FILTERED),
    # --- the judge's eligible read (AP-08's start job; the stack: INFRX_AP7_STACK=1) ---------
    _m("el_any_purpose", "only an external_judging grant opens judging", EL,
       "purpose=DataPurpose.external_judging)", "purpose=DataPurpose.provider_sharing)",
       NO_GRANT),
    _m("el_one_category", "the grant covers request AND response content", EL,
       "is not None and all(grant.permits(", "is not None and any(grant.permits(", NO_GRANT),
    _m("el_grant_unchecked", "a grant that permits nothing opens nothing", EL,
       "if grant is not None and all(", "if grant is not None or all(", NO_GRANT),
    _m("el_any_mode", "a minimal trace is never offered", EL,
       "AND mode = 'full' ", "AND mode != '' ", OFFERED),
    _m("el_mode_misspelled", "the shipper's full traces are what is offered", EL,
       "AND mode = 'full' ", "AND mode = 'Full' ", OFFERED, END_TO_END),
    _m("el_content_not_stored", "content that was never stored is never offered", EL,
       "AND content_stored = 1 ", "", OFFERED),
    _m("el_any_org", "only the grantor's own traces", EL,
       "WHERE org_id = {{org:UUID}} ", "WHERE org_id = org_id ", OFFERED),
    _m("el_any_serving", "only the model's serving versions", EL,
       "AND serving_version_id IN {serving:Array(UUID)} ",
       "AND (serving_version_id IN {serving:Array(UUID)} OR 1) ", OFFERED),
    _m("el_content_bound_ignored", "content past T3's bound is not offered", EL,
       "since = t3.clock() - timedelta(days=t3.content_days)",
       "since = t3.clock() - timedelta(days=100 * t3.content_days)", OFFERED),
    _m("el_oldest_first", "newest first", EL,
       "ORDER BY started_at DESC, trace_id DESC", "ORDER BY started_at ASC, trace_id DESC",
       OFFERED),
    _m("el_limit_ignored", "at most `limit`", EL, '"limit": limit,', '"limit": 200,', OFFERED),
    _m("el_duplicates", "a request is offered once", EL,
       "ids = list(dict.fromkeys(str(r) for (r,) in result.result_rows))",
       "ids = [str(r) for (r,) in result.result_rows]", OFFERED),
    _m("el_tombstones_ignored", "a deleted request is not offered", EL,
       "ids = [r for r in ids if not {REQUEST, CONTENT} & set(stones.get((grantor, r), {}))]",
       "ids = [r for r in ids if True]", DELETED),
    _m("el_any_medium_is_video", "an image is not a video", EL,
       "and s.mime like 'video/%%'", "and s.mime is not null", VIDEO),
    _m("el_video_never", "a video request says so", EL,
       "return [(r, r in video) for r in ids]", "return [(r, False) for r in ids]", VIDEO),
    _m("el_unbounded", "the scan bound is the judge's", EL,
       "limit = scan_bound(limit)", "limit = max(limit, 0)", BOUND),
)


def case_names() -> set[str]:
    return {name for path in SUITE_FILES
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


PG = os.environ.get("INFRX_AP7_PG") == "1"
#: The eligible read's cases need the whole ap7 stack (PostgreSQL, ClickHouse, MinIO).
STACK = PG and os.environ.get("INFRX_AP7_STACK") == "1"
#: Every case they name is PostgreSQL's (`test_data_use` is `pg` throughout).
PG_ONLY = tuple(m.name for m in MUTANTS if all(c.startswith(U) for c in m.cases))
STACK_ONLY = tuple(m.name for m in MUTANTS if all(c.startswith(E) for c in m.cases))
RUNNER = Runner(name="ap07", targets=SUITE_FILES, layout=_layout,
                env=(("INFRX_D_TASK", "INFRX_D1_IMAGE") + (("INFRX_AP7_STACK",) if STACK else ()))
                if PG else (),
                extra_args=() if PG else ("-m", "not pg"))


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-07's mutation list"))
