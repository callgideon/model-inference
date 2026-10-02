#!/usr/bin/env python3
"""R32/R83 for AP-02 (the console reads): one single-edit defect per decision, each killed by a
named case of `tests/ap02`. The PostgreSQL cases run on the ap2 key (`INFRX_D_TASK=ap2`, the D
harness); the copy sits at `apps/infrx-api` beside a link to the real `apps/app` (the migrations
`infrx.state.migrations` applies and the TS adapters the parity case runs).

    INFRX_MUTANTS=all INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_mutants.py
"""
from __future__ import annotations

import pathlib

from ..contracts import mutants as shared
from ..contracts.mutants import API_DIR, Mutant, Runner

C, R, ROUTES = "console/cursor.py", "console/reads.py", "gateway/routes/console_reads.py"
FILES = ("tests/ap02/test_cursor.py", "tests/ap02/test_units.py",
         "tests/ap02/test_credits_pg.py", "tests/ap02/test_requests_pg.py",
         "tests/ap02/test_projections_pg.py")


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, Runner(name="ap02-copy"))
    (root / "apps" / "app").symlink_to(API_DIR.parent / "app", target_is_directory=True)
    return api


RUNNER = Runner(name="ap02", targets=FILES[:2])
PG_RUNNER = Runner(name="ap02-pg", targets=FILES, env=("INFRX_D_TASK",), layout=_layout)


def _m(name: str, invariant: str, file: str, old: str, new: str, *cases: str,
       dies_by: tuple[str, ...] = ()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by)


SCOPES = "test_cursor__from_another_scope_is_invalid"
ACCESS = "test_units__result_access_is_the_read_outcome"
FILTERS = "test_units__request_filters_are_validated_and_canonical"
LIST = "test_requests__are_mine_newest_first_with_separate_states"
FILTERED = "test_requests__filters_narrow_and_scope_the_cursor"
RESULT = "test_result__served_only_when_available"
KEY_OUTAGE = "test_requests__refused_to_a_key_and_503_on_an_outage"
KEYS = "test_keys__the_orgs_newest_first_without_secrets"
MEMBERS = "test_members__the_accounts_own"
AUTHORITY = "test_operator__needs_authority_from_the_actor_and_the_database"
ACCOUNTS = "test_operator__accounts_are_exact_and_paged"
UNKNOWN_DRIFT = "test_operator__unknown_usage_and_drift"
AUDIT = "test_operator__audit_newest_first"
FOREIGN_CURSOR = "test_credit_ledger__a_cursor_from_another_actor_is_refused"

#: The cursor's own decisions, killed by the unit cases (no PostgreSQL).
MUTANTS: tuple[Mutant, ...] = (
    # ---------------------------------------------------------------- cursors (02a)
    _m("ap02_cursor_scope_drops_the_actor", "a cursor is scoped to its actor", C,
       'who = f"{actor.audience}:{actor.user_id or \'\'}:{actor.org_id or \'\'}"', 'who = ""',
       SCOPES),
    _m("ap02_cursor_scope_drops_the_sort", "a cursor is scoped to its sort order", C,
       "|{json.dumps(entries, separators=(',', ':'))}|{sort}\"",
       "|{json.dumps(entries, separators=(',', ':'))}|\"", SCOPES),
    _m("ap02_cursor_scope_keeps_unset_filters", "an unset filter is absent from the scope", C,
       "for name, value in filters.items() if value is not None)",
       "for name, value in filters.items())", "test_cursor__scope_ignores_filter_order_and_unset_filters"),
    _m("ap02_cursor_scope_in_arrival_order", "the filter order does not change the scope", C,
       "entries = sorted((name, value)", "entries = list((name, value)",
       "test_cursor__scope_ignores_filter_order_and_unset_filters"),
    _m("ap02_cursor_mac_unchecked", "a disturbed or foreign cursor does not verify", C,
       "if not hmac.compare_digest(", "if False and not hmac.compare_digest(",
       "test_cursor__a_disturbed_or_foreign_cursor_is_invalid", SCOPES),
    _m("ap02_cursor_unbounded", "a cursor over 512 characters is refused before decoding", C,
       "if not 0 < len(token) <= MAX_CURSOR_CHARS:", "if not 0 < len(token):",
       "test_cursor__a_disturbed_or_foreign_cursor_is_invalid"),
    _m("ap02_cursor_short_secret", "a cursor secret has at least 16 bytes", C,
       "    if len(secret) < MIN_SECRET_BYTES:\n", "    if False:\n",
       "test_cursor__a_short_secret_is_refused"),
    _m("ap02_cursor_padding_lost", "a minted cursor decodes to its own key", C,
       'payload + "=" * (-len(payload) % 4)', "payload",
       "test_cursor__round_trips_its_key_for_the_same_scope"),
    # ---------------------------------------------------------------- the read outcome (02b)
    _m("ap02_access_nonterminal_served", "a request still running has no result yet", R,
       '    if state not in _TERMINAL:\n        return "pending"\n', "", ACCESS),
    _m("ap02_access_held_unknown_served", "a success whose usage is unknown is withheld", R,
       '    if settlement_state == "held_unknown":\n', "    if False:\n", ACCESS),
    _m("ap02_access_any_certainty", "only an authoritative success has a result", R,
       'if state != "succeeded" or usage_certainty != "authoritative":',
       'if state != "succeeded":', ACCESS),
    _m("ap02_access_any_state", "only a success has a result", R,
       'if state != "succeeded" or usage_certainty != "authoritative":',
       'if usage_certainty != "authoritative":', ACCESS),
    _m("ap02_access_no_expiry_served", "a result with no persisted expiry is unavailable", R,
       '    if expires_at is None:\n        return "unavailable"\n', "", ACCESS),
    _m("ap02_access_expiry_ignored", "the database's flag decides available vs expired", R,
       'return "available" if available else "expired"', 'return "available"', ACCESS),
    _m("ap02_page_default_max", "a page is 25 rows by default", R,
       "        return api.DEFAULT_PAGE_SIZE\n", "        return api.MAX_PAGE_SIZE\n",
       "test_units__page_size_defaults_to_25_and_refuses_outside_1_to_100"),
    _m("ap02_page_size_unbounded_unit", "a page size outside 1..100 is refused", R,
       "not 1 <= int(limit) <= api.MAX_PAGE_SIZE", "not 0 <= int(limit)",
       "test_units__page_size_defaults_to_25_and_refuses_outside_1_to_100"),
    _m("ap02_filter_naive_instant", "a filter instant needs a UTC offset", R,
       "    if parsed.tzinfo is None:\n", "    if False:\n", FILTERS),
    _m("ap02_filter_key_id_unchecked", "key_id must be a key id", R,
       "        if not _UUID.match(key_id):\n", "        if False:\n", FILTERS),
    _m("ap02_filter_key_id_not_canonical", "key_id is canonical (lowercase) in the scope", R,
       "        key_id = key_id.lower()\n", "", FILTERS),
    _m("ap02_filter_model_unbounded", "model is 1..200 characters", R,
       "if model is not None and not 0 < len(model) <= 200:", "if False:", FILTERS),
)

#: Killed through the routes on the ap2 PostgreSQL (`PG_RUNNER`): run in a process of their own
#: (the D harness's host lock), `make api-mutants`' ap2 line.
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("ap02_pg_cursor_scope_drops_the_actor", "a cursor resumes only its own actor's list", C,
       'who = f"{actor.audience}:{actor.user_id or \'\'}:{actor.org_id or \'\'}"',
       'who = f"{actor.audience}"', FOREIGN_CURSOR),
    # ---------------------------------------------------------------- the actor session (02a)
    _m("ap02_session_as_the_platform", "a read runs as `authenticated`, never the platform role",
       R, '"set local role authenticated"', '"select 1"',
       "test_reads__the_session_is_the_actor_read_only",
       "test_legacy_statement__another_org_is_refused"),
    _m("ap02_session_writable", "a read runs in a read-only transaction", R,
       '"set transaction read only"', '"select 1"',
       "test_reads__the_session_is_the_actor_read_only"),
    _m("ap02_key_audience_reads", "only a verified web session reads the console", R,
       'if actor.audience != "session" or not actor.user_id', "if not actor.user_id",
       "test_reads__a_key_audience_or_no_session_is_refused", KEY_OUTAGE),
    _m("ap02_refusal_42501_unavailable", "the database refusing this actor is 403, not 503", R,
       'getattr(failed, "sqlstate", None) == "42501"', 'getattr(failed, "sqlstate", None) == "x"',
       "test_legacy_statement__another_org_is_refused"),
    _m("ap02_outage_raw", "an outage is a typed 503, never a raw 500", R,
       'return errors.DependencyUnavailable("console read failed", retry_after_s=5)',
       "return failed", "test_reads__a_database_outage_is_503_never_empty", KEY_OUTAGE,
       "test_projections__503_on_an_outage"),
    _m("ap02_router_mounted_without_its_port", "default OFF: no port, no routes", ROUTES,
       "    if reads is None:\n        return\n", "", "test_router__mounts_nothing_without_the_port",
       dies_by=("AttributeError",)),     # the defect IS mounting over a None port
    # ---------------------------------------------------------------- credits (02a)
    _m("ap02_spent_sign", "spent = what came in - what is left", R,
       "str(came_in - _credit(row, \"ledger_total\"))", "str(_credit(row, \"ledger_total\") - came_in)",
       "test_credits__are_the_individuals_exact_wallet"),
    _m("ap02_spent_without_a_wallet", "no wallet: spent is not computed (null, never a zero)", R,
       'if row["wallet_id"] is not None and len(ins) <= CREDITS_IN_BOUND:',
       "if len(ins) <= CREDITS_IN_BOUND:", "test_credits__no_wallet_reads_zero_and_no_spent"),
    _m("ap02_legacy_labelled_credit", "the legacy statement is USD, never CREDIT", R,
       'balance=_money(row["balance"], "USD")', 'balance=_money(row["balance"], "CREDIT")',
       "test_legacy_statement__is_usd_and_separate"),
    _m("ap02_page_cursor_after_the_unshown_row", "the next page resumes after the last row shown",
       R, "shown[-1][0]) if more", "found[-1][0]) if more",
       "test_credit_ledger__pages_without_loss_or_repeat"),
    _m("ap02_page_size_unbounded", "a page holds 1..100 rows or is refused", R,
       "not 1 <= int(limit) <= api.MAX_PAGE_SIZE", "not 0 <= int(limit)",
       "test_credit_ledger__a_limit_outside_the_page_bounds_is_refused"),
    _m("ap02_suspension_blocks_reads", "a suspended account keeps every read (R33)", R,
       "        user = _user(actor)\n",
       "        user = _user(actor)\n        if actor.org_id: raise errors.OrgSuspended('s')\n",
       "test_credits__a_suspended_account_still_reads"),
    # ---------------------------------------------------------------- requests (02b)
    _m("ap02_requests_key_filter_dropped", "the key filter reaches consumer_jobs", R,
       'filters["model"], filters["key_id"],', 'filters["model"], None,', FILTERED),
    _m("ap02_requests_window_swapped", "the window is [from, to)", R,
       'filters["from"], filters["to"]))', 'filters["to"], filters["from"]))', FILTERED),
    _m("ap02_requests_cursor_ignores_filters", "a cursor is scoped to its filters", R,
       '"created_at.desc,request_id.asc", filters,', '"created_at.desc,request_id.asc", {},',
       FILTERED),
    _m("ap02_page_cap_without_cursor", "a full page at the 100 cap still carries a cursor", R,
       " or (ask == size and len(found) == size)", "",
       "test_requests__25_by_default_and_a_full_page_at_the_cap_carries_a_cursor"),
    _m("ap02_page_cursor_after_the_unshown_row_requests",
       "a record created between pages is neither lost nor repeated", R,
       "shown[-1][0]) if more", "found[-1][0]) if more",
       "test_requests__a_record_created_between_pages_is_neither_lost_nor_repeated"),
    _m("ap02_request_id_not_canonical", "an id is matched case-insensitively", R,
       "    found = value.lower()\n", "    found = value\n",
       "test_request__mine_by_id_and_nothing_else"),
    _m("ap02_request_any_id", "one request is read by its id", R,
       "consumer_jobs(p_limit => 1, \"\n                                      \"p_request_id => %s::uuid)",
       "consumer_jobs(p_limit => 1, \"\n                                      \"p_model => %s)",
       "test_request__mine_by_id_and_nothing_else"),
    _m("ap02_request_hold_relabelled", "money is in the request's own unit", R,
       'hold=_opt_money(row["hold"], unit)', 'hold=_opt_money(row["hold"], "CREDIT")', LIST),
    _m("ap02_request_states_merged", "execution and settlement are separate fields", R,
       "settlement_state=settlement,", "settlement_state=state,", LIST),
    _m("ap02_result_gate_skipped", "content is served only for an available result", R,
       'if job.result_access != "available":', "if False:", RESULT),
    _m("ap02_result_pending_as_missing", "a result not ready is 409, not 404", R,
       '"pending": errors.ResultPending,', '"pending": errors.NotFound,', RESULT),
    _m("ap02_result_withheld_as_missing", "a withheld result is 409, not 404", R,
       '"held_unknown": errors.ResultPending,', '"held_unknown": errors.NotFound,', RESULT),
    _m("ap02_result_none_as_pending", "a request with no result is 404", R,
       '"no_result": errors.NotFound,', '"no_result": errors.ResultPending,', RESULT),
    _m("ap02_result_unavailable_as_gone", "no persisted expiry is 503, not 410", R,
       '"unavailable": errors.DependencyUnavailable,', '"unavailable": errors.ResultExpired,',
       "test_result__no_persisted_expiry_is_unavailable"),
    _m("ap02_result_expiry_from_the_flag_only", "an expired result is 410 and listed expired", R,
       'return "available" if available else "expired"', 'return "available"',
       "test_result__expired_by_the_database_clock_is_410"),
    # ---------------------------------------------------------------- projections (02c)
    _m("ap02_keys_any_org", "keys are the actor's org's only", R,
       "and org_id = %(org)s::uuid order by created_at desc", "and true order by created_at desc",
       KEYS),
    _m("ap02_keys_oldest_first", "keys are newest first", R,
       "order by created_at desc, id desc", "order by created_at, id", KEYS),
    _m("ap02_keys_extra_field", "a key summary carries no secret material", R,
       '    """Never the secret or its hash."""\n    id: str\n',
       '    """Never the secret or its hash."""\n    key_hash: str | None = None\n    id: str\n',
       KEYS),
    _m("ap02_members_any_org", "members are the actor's account's only", R,
       "and m.org_id = %(org)s::uuid order by", "and true order by", MEMBERS),
    _m("ap02_members_keyset_backwards", "the members page resumes after its last row", R,
       "(m.created_at, m.user_id) > (", "(m.created_at, m.user_id) < (", MEMBERS),
    _m("ap02_operator_actor_unchecked", "operator authority comes from the server actor", R,
       "    if not actor.operator:\n", "    if False:\n", AUTHORITY),
    _m("ap02_operator_database_unchecked", "operator authority is the database's too", R,
       "    if yes is not True:\n", "    if False:\n", AUTHORITY),
    _m("ap02_operator_accounts_open", "the accounts projection is operator-only", R,
       '"updated_at.desc,wallet_id.desc", {}, limit,\n                                token, fetch, operator=True)',
       '"updated_at.desc,wallet_id.desc", {}, limit,\n                                token, fetch)',
       AUTHORITY),
    _m("ap02_accounts_total_is_reserved", "an account's figures are its own fields", R,
       'ledger_total=_money(r["ledger_total"], "CREDIT"),',
       'ledger_total=_money(r["reserved_total"], "CREDIT"),', ACCOUNTS),
    _m("ap02_accounts_oldest_first", "accounts are most recently moved first", R,
       "order by w.updated_at desc, w.wallet_id desc", "order by w.updated_at, w.wallet_id",
       ACCOUNTS),
    _m("ap02_unknown_hold_relabelled", "an unknown-usage hold keeps its unit", R,
       'hold=_opt_money(r["hold"], unit))', 'hold=_opt_money(r["hold"], "USD"))', UNKNOWN_DRIFT),
    _m("ap02_drift_keyset_repeats", "the drift page resumes after its last row", R,
       "wallet_id > %(id)s::uuid", "wallet_id >= %(id)s::uuid", UNKNOWN_DRIFT),
    _m("ap02_audit_oldest_first", "the audit is newest first", R,
       "order by at desc, id desc", "order by at, id", AUDIT),
)


def run_mutant(mutant) -> shared.Result:
    """The PostgreSQL list has no module baseline (it is not `MUTANTS`), so its cases run
    unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-02's mutation list"))
