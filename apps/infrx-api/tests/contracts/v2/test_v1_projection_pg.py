#!/usr/bin/env python3
"""F2P wire-in item 10, Python half: the v1 -> v2 read projection over REAL pre-cutover rows.

`project_v1_usage` proves a shape; this proves the projection against what 0001-0005 actually
wrote. It builds D1R's `upgrade05` database in the D harness (0001-0002, legacy USD history with
negative, sign-violating and key-less rows, 0003-0005, accepted pilot-regime jobs with a settled
USD usage row, then everything D1R adds) and reads that history through the v2 records:

* every `usage_events` row, through D1R's `infrx.usage_records` seam, is a `UsageRecordV2` in the
  `legacy_usd` regime, in USD, carrying `cost_usd` to the digit, with none of the CREDIT fields
  invented, and the history totals one USD figure equal to the table's own sum;
* `project_v1_usage` reads the same raw rows to the same DTOs;
* the legacy statement is a `LegacyUsdStatement` equal to the ledger sum, flagged as a rollout
  hold when nonzero, and never a CREDIT figure.

Needs Docker (the D harness, one shared port): a `HarnessBusy` refusal means another checkout
holds the port - retry, never remove it. Without Docker this module skips, visibly; a skip is
never a pass.

    uv run --frozen pytest -q tests/contracts/v2/test_v1_projection_pg.py
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from infrx.contracts import records as v1
from infrx.contracts.v2 import money_units as mu, records as v2
from infrx.state import migrations

from tests.d import checks, checks_credit, pgharness

DATABASE = f"{pgharness.DATABASE}_f2p_projection"

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_state: dict = {}


def _upgraded():
    if "conn" not in _state:
        pgharness.ensure()
        conn, before = checks_credit.upgrade05(
            pgharness, DATABASE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        _state.update(conn=conn, orgs=(before["legacy"]["org_a"], before["legacy"]["org_b"]))
    return _state["conn"], _state["orgs"]


_SEAM_FIELDS = ("request_id", "org_id", "accounting_regime", "unit", "charged_amount",
                "prompt_tokens", "completion_tokens", "usage_certainty", "outcome",
                "rate_card_version", "serving_version_id", "deployment_revision_id",
                "price_version", "settled_at")


def _seam_rows(conn, org_id: str) -> list[dict]:
    rows = conn.execute("select * from infrx.usage_records(%s, null, null, 500)",
                        (org_id,)).fetchall()
    return [dict(zip(_SEAM_FIELDS, row)) for row in rows]


def _raw_rows(conn, org_id: str) -> list[dict]:
    """The pre-cutover `usage_events` rows exactly as 0001-0005 wrote them."""
    cursor = conn.execute("select id::text as request_id, cost_usd, prompt_tokens, "
                          "completion_tokens, usage_certainty, settlement_state, price_version, "
                          "created_at as settled_at from public.usage_events "
                          "where org_id = %s order by created_at desc, id desc", (org_id,))
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _dto(row: dict) -> v2.UsageRecordV2:
    """The seam's row as the v2 DTO. Nothing is defaulted: a NULL stays absent."""
    usage = None
    if row["prompt_tokens"] is not None and row["completion_tokens"] is not None:
        usage = v1.Usage.of(row["prompt_tokens"], row["completion_tokens"],
                            v1.UsageCertainty(row["usage_certainty"] or "authoritative"))
    return v2.UsageRecordV2(
        request_id=str(row["request_id"]), org_id=str(row["org_id"]),
        accounting_regime=row["accounting_regime"], unit=row["unit"],
        charged_amount=row["charged_amount"], usage=usage,
        outcome=row["outcome"], rate_card_version=row["rate_card_version"],
        serving_version_id=row["serving_version_id"],
        deployment_revision_id=row["deployment_revision_id"],
        price_version=row["price_version"], settled_at=row["settled_at"])


def test_every_pre_cutover_usage_row_reads_as_a_legacy_usd_dto():
    conn, orgs = _upgraded()
    seen = 0
    for org_id in orgs:
        rows = _seam_rows(conn, org_id)
        count, total = conn.execute(
            "select count(*), coalesce(sum(cost_usd), 0)::numeric(20,8)::text "
            "from public.usage_events where org_id = %s", (org_id,)).fetchone()
        assert len(rows) == count > 0, (org_id, len(rows), count)
        costs = dict(conn.execute("select id::text, cost_usd from public.usage_events "
                                  "where org_id = %s", (org_id,)).fetchall())
        entries = []
        for row in rows:
            dto = _dto(row)
            assert dto.accounting_regime is v2.AccountingRegime.legacy_usd
            assert dto.unit == mu.USD
            assert dto.amount() == mu.Usd(Decimal(costs[dto.request_id])), dto
            assert (dto.rate_card_version, dto.serving_version_id,
                    dto.deployment_revision_id) == (None, None, None)
            entries.append(dto)
        history = v2.UsageHistory(org_id=org_id, entries=tuple(entries))
        assert history.totals() == {mu.USD: total}, (history.totals(), total)
        seen += len(rows)
    assert seen >= 4, f"the upgrade fixture carried only {seen} usage rows"


def test_project_v1_usage_reads_the_raw_rows_to_the_same_dtos():
    conn, orgs = _upgraded()
    for org_id in orgs:
        by_seam = {dto.request_id: dto for dto in map(_dto, _seam_rows(conn, org_id))}
        for row in _raw_rows(conn, org_id):
            projected = v2.project_v1_usage(row, org_id=org_id)
            assert projected == by_seam[projected.request_id], (row, projected)


def test_the_legacy_statement_is_a_rollout_hold_never_a_credit_figure():
    conn, orgs = _upgraded()
    for org_id in orgs:
        (_, regime, unit, balance, entry_count, as_of, hold), = conn.execute(
            "select * from public.console_legacy_usd_statement(%s)", (org_id,)).fetchall()
        assert (regime, unit) == (mu.LEGACY_USD_REGIME, mu.USD)
        statement = v2.LegacyUsdStatement(org_id=org_id, balance=balance,
                                          entry_count=entry_count, as_of=as_of,
                                          rollout_hold=hold)
        ledger, rows = conn.execute(
            "select coalesce(sum(delta_usd), 0)::numeric(20,8)::text, count(*) "
            "from public.credit_ledger where org_id = %s", (org_id,)).fetchone()
        assert str(statement.balance) == ledger and statement.entry_count == rows
        assert statement.rollout_hold is (not statement.balance.is_zero)
    # And no CREDIT wallet, ledger row or grant was made from any of it.
    assert conn.execute("select (select count(*) from infrx.credit_wallets) + "
                        "(select count(*) from infrx.credit_ledger)").fetchone()[0] == 0
    assert checks.KEY_A        # the fixture's key is the legacy key the rows reference
