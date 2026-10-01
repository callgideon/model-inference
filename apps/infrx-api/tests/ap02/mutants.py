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
FILES = ("tests/ap02/test_cursor.py", "tests/ap02/test_credits_pg.py")


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, Runner(name="ap02-copy"))
    (root / "apps" / "app").symlink_to(API_DIR.parent / "app", target_is_directory=True)
    return api


UNIT = "tests/ap02/test_cursor.py"
RUNNER = Runner(name="ap02", targets=(UNIT,))
PG_RUNNER = Runner(name="ap02-pg", targets=FILES, env=("INFRX_D_TASK",), layout=_layout)


def _m(name: str, invariant: str, file: str, old: str, new: str, *cases: str,
       dies_by: tuple[str, ...] = ()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by)


SCOPES = "test_cursor__from_another_scope_is_invalid"
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
       "test_reads__a_key_audience_or_no_session_is_refused"),
    _m("ap02_refusal_42501_unavailable", "the database refusing this actor is 403, not 503", R,
       'getattr(failed, "sqlstate", None) == "42501"', 'getattr(failed, "sqlstate", None) == "x"',
       "test_legacy_statement__another_org_is_refused"),
    _m("ap02_outage_raw", "an outage is a typed 503, never a raw 500", R,
       'return errors.DependencyUnavailable("console read failed", retry_after_s=5)',
       "return failed", "test_reads__a_database_outage_is_503_never_empty"),
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
