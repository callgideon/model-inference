#!/usr/bin/env python3
"""R32/R40/R83 for WR-V1M-2: one single-edit defect per invariant `/lab/v1/traces` claims.

    uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py
    INFRX_LAB_API_PG=1 INFRX_D_TASK=l4 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py
    INFRX_LAB_API_STACK=1 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py     # + STACK_ONLY
    uv run --frozen python -m tests.g.lab_traces.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_traces/test_lab_traces.py",
               "tests/g/lab_traces/test_lab_traces_stack.py")
F = "gateway/routes/lab_traces.py"
FILES = (F,)
T = "test_lab_traces__"
STACK = T + "the_clickhouse_read_is_final_scoped_and_paged"
STACK_ONLY = ("final_dropped", "serving_unfiltered", "oldest_first", "cursor_inclusive",
              "cursor_ignored")
LIVE = "            if REQUEST in scopes or not t3.metadata_live(row.started_at, now):\n"
AVAILABLE = ('"content_available": row.content_stored and CONTENT not in scopes\n'
             "                         and t3.content_live(row.started_at, now)}")


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting ----------------------------------------------------------------------------
    _m("mounted_without_traces", "LAB_TRACES off (no rt.lab_traces): no route",
       "    if traces is None:\n        return None", "    if False:\n        return None",
       T + "nothing_is_mounted_without_traces"),
    _m("runtime_traces_ignored", "register(app, rt) mounts over rt.lab_traces",
       'getattr(rt, "lab_traces", None)', "None", T + "nothing_is_mounted_without_traces"),
    # --- LAB-ACCESS: the session and a developer+ membership before any row ------------------
    _m("membership_skipped", "no row before a current membership of the named provider",
       "        await lab_auth.member(traces.access, user_id, provider, Cap.manage_dev_deployment)\n",
       "", T + "identity_and_membership_before_any_row"),
    _m("viewer_reads_requests", "aggregate health alone never yields per-request rows",
       "Cap.manage_dev_deployment", "Cap.read_aggregate_health",
       T + "identity_and_membership_before_any_row"),
    # --- TRACE-TENANT: the provider's own deployments, T3 applied -----------------------------
    _m("serving_of_another_id", "the serving versions are the named provider's",
       "await traces.serving.serving(provider)", "await traces.serving.serving(user_id)",
       T + "a_provider_reads_its_own_deployments_requests_only"),
    _m("deleted_request_listed", "a request tombstone hides the request at once",
       LIVE, "            if not t3.metadata_live(row.started_at, now):\n",
       T + "a_provider_reads_its_own_deployments_requests_only",
       T + "deleted_and_expired_content_stay_gone"),
    _m("metadata_bound_ignored", "past 13 months a request is gone, TTL run or not",
       LIVE, "            if REQUEST in scopes:\n",
       T + "a_provider_reads_its_own_deployments_requests_only",
       T + "a_detail_outside_the_providers_deployments_is_not_found"),
    _m("times_not_rendered", "every item is JSON (times as ISO 8601)",
       "            out.append((row, _json(item)))", "            out.append((row, item))",
       T + "a_provider_reads_its_own_deployments_requests_only"),
    _m("non_uuid_queried", "a malformed request id is a 404, never a query",
       "            uuid.UUID(request_id)\n        except ValueError:\n",
       "            pass\n        except ValueError:\n",
       T + "a_detail_outside_the_providers_deployments_is_not_found"),
    # --- LAB-ACCESS: content only under the grantor's current grant ---------------------------
    _m("organization_in_metadata", "a metadata row names no organization",
       'METADATA = ("request_id",', 'METADATA = ("org_id", "request_id",',
       T + "without_a_content_grant_rows_are_metadata_only"),
    _m("one_category_releases_the_blob", "the stored object holds request AND response",
       "CATEGORIES = (DataCategory.request_content, DataCategory.response_content)",
       "CATEGORIES = (DataCategory.request_content,)",
       T + "without_a_content_grant_rows_are_metadata_only"),
    _m("content_without_a_grant", "the detail's content is the granted rows' only",
       '        if item["access"] == "content":\n', "        if True:\n",
       T + "without_a_content_grant_rows_are_metadata_only"),
    _m("wrong_purpose", "provider reads are the provider_sharing purpose",
       "purpose=DataPurpose.provider_sharing", "purpose=DataPurpose.capture",
       T + "a_current_grant_releases_content_per_grantor"),
    _m("grantor_is_the_provider", "the grant is the request's organization's",
       "grantor_org_id=org_id,", "grantor_org_id=provider,",
       T + "a_current_grant_releases_content_per_grantor"),
    _m("grant_shared_across_grantors", "each grantor organization is judged on its own grant",
       "            key = (row.org_id, model_id)\n", "            key = model_id\n",
       T + "a_current_grant_releases_content_per_grantor"),
    _m("grants_cached_across_calls", "nothing is cached: a revocation holds on the next call",
       "        grants: dict[tuple[str, str], bool] = {}\n",
       "        grants = visible.__dict__.setdefault('grants', {})\n",
       T + "a_revoked_grant_is_metadata_only_on_the_next_call"),
    # --- T3 on content ---------------------------------------------------------------------------
    _m("content_bound_ignored", "content past its bound is unavailable",
       AVAILABLE, '"content_available": row.content_stored and CONTENT not in scopes}',
       T + "deleted_and_expired_content_stay_gone"),
    _m("unstored_content_available", "lost capture has no content to offer",
       '"content_available": row.content_stored and CONTENT', '"content_available": CONTENT',
       T + "deleted_and_expired_content_stay_gone"),
    # --- pages ------------------------------------------------------------------------------------
    _m("cursor_after_a_short_page", "the last page has no cursor",
       "if len(rows) == limit else None", "if rows else None",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
    _m("cursor_names_the_organization", "a cursor carries a position, never an organization",
       "json.dumps([row.started_at.isoformat(), row.trace_id])",
       "json.dumps([row.started_at.isoformat(), row.trace_id, row.org_id])",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
    _m("naive_cursor_accepted", "a cursor's instant is timezone-aware",
       "        if position[0].tzinfo is None or not isinstance(trace_id, str):\n",
       "        if not isinstance(trace_id, str):\n",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
    _m("garbled_cursor_escapes", "any undecodable cursor is a 422",
       "    except (ValueError, TypeError, binascii.Error):", "    except binascii.Error:",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
    _m("zero_limit_accepted", "a page holds at least one row",
       "    if not 1 <= limit <= MAX_LIMIT:", "    if not 0 <= limit <= MAX_LIMIT:",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
    # --- the ClickHouse read (STACK_ONLY: killed only with a stack up) --------------------------
    _m("final_dropped", "a replayed row is read once, its stored version",
       "FROM {TABLE} FINAL \"", "FROM {TABLE} \"", STACK),
    _m("serving_unfiltered", "only the given serving versions' rows are read",
       '"WHERE serving_version_id IN {serving:Array(UUID)} AND " + where', '"WHERE " + where',
       STACK),
    _m("oldest_first", "pages are newest first",
       "ORDER BY started_at DESC, trace_id DESC", "ORDER BY started_at, trace_id", STACK),
    _m("cursor_inclusive", "a page starts strictly after the cursor's row",
       '"(started_at, trace_id) < (toDateTime64', '"(started_at, trace_id) <= (toDateTime64',
       STACK),
    _m("cursor_ignored", "the cursor moves the page",
       "        if before is None:\n            return await self._select(",
       "        if True:\n            return await self._select(", STACK),
    _m("limit_unbounded", "a page holds at most MAX_LIMIT rows",
       "    if not 1 <= limit <= MAX_LIMIT:", "    if not 1 <= limit:",
       T + "pages_are_bounded_and_cursors_are_the_servers"),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-traces", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-V1M-2's traces mutation list"))
