#!/usr/bin/env python3
"""R32/R40/R83 for G4T: one single-edit defect per invariant the trace export claims - the
route (`gateway/routes/trace_export.py`) and C2's `OwnedExport` / `ClickHouseTracePages`
(`content/__init__.py`), through the shared runner.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/trace_export/test_mutants.py
    uv run --frozen python tests/g/trace_export/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILE = "tests/g/trace_export/test_trace_export.py"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

R = "gateway/routes/trace_export.py"
C = "content/__init__.py"


def case(name: str) -> str:
    return f"test_trace_export__{name}"


OFF = case("no_export_mounts_no_route")
ON = case("the_route_mounts_over_the_runtime_export")
IDENTITY = case("identity_comes_before_any_read")
OPERATOR = case("an_operator_credential_owns_no_traces")
TENANT = case("an_org_exports_its_own_traces_only")
FOREIGN = case("a_foreign_row_from_the_store_is_dropped")
RETENTION = case("deleted_and_expired_data_is_not_exported")
NO_KEY = case("rows_carry_no_storage_key_and_no_content")
WALK = case("the_cursor_walks_every_row_once_in_order")
BOUNDED = case("the_page_is_bounded")
BAD_CURSOR = case("a_bad_cursor_is_a_400")
WINDOW = case("the_window_filters_by_start")
HANG = case("a_hanging_store_is_a_503_within_the_bound")
OUTAGE = case("a_store_outage_is_a_503")
CLICKHOUSE = case("the_clickhouse_page_binds_the_org_and_every_bound_as_parameters")

MUTANTS: tuple[Mutant, ...] = (
    # --- the route ----------------------------------------------------------------------
    _m("mounted_without_an_export", "no export, no route", R,
       "    if export is None:\n        return None", "    if False:\n        return None", OFF),
    _m("error_handlers_not_installed", "a standalone router answers in the envelope", R,
       "    install_error_handlers(app, new_request_id)\n", "", OFF),
    _m("runtime_export_ignored", "register(app, rt) mounts over rt.trace_export", R,
       'getattr(rt, "trace_export", None)', "None", ON),
    _m("inference_id_dropped", "every answer carries its Inference-Id", R,
       "                            headers={wire.HEADER_INFERENCE_ID: request_id})",
       "                            headers={})", ON),
    _m("identity_after_the_read", "the key is resolved before anything is read", R,
       "        context = await auth.context(request)\n", "", IDENTITY),
    _m("operator_exports", "an operator credential owns no traces", R,
       "        if context.audience not in OWNERS:", "        if False:", OPERATOR),
    _m("tenant_parameter_ignored", "an unknown parameter (an org) is a 400", R,
       "            if name not in PARAMETERS or len(", "            if len(", TENANT),
    _m("repeated_parameter_ignored", "a repeated parameter is a 400", R,
       " or len(query.getlist(name)) > 1:", ":", BOUNDED),
    _m("org_is_not_the_keys", "the organization is the key's", R,
       "export.page(context.org_id,", "export.page(context.key_id,", TENANT, WALK),
    _m("default_limit_is_the_cap", "the default page is 100 rows", R,
       "        return EXPORT_DEFAULT_LIMIT", "        return 1000", BOUNDED),
    _m("limit_not_a_number", "a non-integer limit is a 400, not a 500", R,
       "    if not value.isdigit():", "    if False:", BOUNDED),
    _m("naive_instant_accepted", "a window instant carries its zone", R,
       "    if at is None or at.tzinfo is None:", "    if at is None:", WINDOW),
    _m("store_call_unbounded", "the store call is bounded", R,
       "limit=_limit(query.get(\"limit\"))), intake.DEPENDENCY_BOUND_S)",
       "limit=_limit(query.get(\"limit\"))), 3600)", HANG),
    _m("no_next_cursor", "the page names where the next one starts", R,
       '                             "next_cursor": cursor},',
       '                             "next_cursor": None},', WALK),
    # --- C2's owned export --------------------------------------------------------------
    _m("foreign_rows_passed", "a row of another org is never exported", C,
       "limit=limit) if r.org_id == org_id]", "limit=limit)]", FOREIGN),
    _m("deleted_exported", "T3-deleted or metadata-expired rows are not exported", C,
       "            if verdict != DROP:", "            if True:", RETENTION),
    _m("content_expiry_ignored", "content T3 deleted or expired is expired", C,
       "row_state(row, verdict == NO_CONTENT)", "row_state(row, False)", RETENTION),
    _m("lost_reads_available", "an unstored object is lost", C,
       "    return ContentState.available if row.content_stored else ContentState.lost",
       "    return ContentState.available", NO_KEY),
    _m("metadata_only_unrecognised", "a trace without content is metadata_only", C,
       "    if not row.content_key:\n        return ContentState.metadata_only",
       "    if False:\n        return ContentState.metadata_only", NO_KEY),
    _m("limit_uncapped", "a page is at most 1000 rows", C,
       "        if not 1 <= limit <= EXPORT_MAX_LIMIT:", "        if not 1 <= limit:", BOUNDED),
    _m("cursor_past_the_end", "a short page is the end", C,
       "encode_cursor(rows[-1]) if len(rows) == limit else None",
       "encode_cursor(rows[-1]) if rows else None", WALK),
    _m("naive_cursor_accepted", "a cursor instant carries its zone", C,
       "        if at.tzinfo is None or not isinstance(trace_id, str):",
       "        if not isinstance(trace_id, str):", BAD_CURSOR),
    _m("garbage_cursor_crashes", "any undecodable cursor is invalid_cursor", C,
       "    except (ValueError, TypeError, binascii.Error):", "    except binascii.Error:",
       BAD_CURSOR),
    _m("clickhouse_org_inlined", "the org is a query parameter, never SQL text", C,
       '["org_id = {org:UUID}"]', "[f\"org_id = '{org_id}'\"]", CLICKHOUSE),
    _m("clickhouse_order_by_trace", "the page order is the cursor's (started_at, trace_id)", C,
       '"ORDER BY started_at, trace_id LIMIT', '"ORDER BY trace_id LIMIT', CLICKHOUSE),
    _m("clickhouse_since_exclusive", "the window starts inclusively", C,
       '"started_at >= toDateTime64({since:String}', '"started_at > toDateTime64({since:String}',
       CLICKHOUSE),
    _m("outage_swallowed", "a dependency failure is a 503, never an empty page", C,
       "        verdicts = await self.retention.verdicts(rows)       # T3's bounds and tombstones\n",
       "        try:\n            verdicts = await self.retention.verdicts(rows)\n"
       "        except Exception:\n            return [], None\n", OUTAGE),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="g4t", targets=(SUITE_FILE,))


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run G4T's trace export mutation list"))
