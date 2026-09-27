#!/usr/bin/env python3
"""R32/R40/R83 for T2I: one single-edit defect per decision `tests/t/ship` claims.

The runner is the shared one (`tests/contracts/mutants.py`). `INFRX_T2I_STACK` passes into
the copies, so the stack half of each case runs there too; the mutants only the real
ClickHouse can see (`NEEDS_STACK`) skip, naming their owner, without it. `schema.sql` is not
mutable by this runner (it compiles every mutated file as Python): its engine and key are
proved by the stack cases through `find`, whose FINAL and org binding are mutated here.

    uv run --frozen pytest -q tests/t/ship/test_mutants.py                     # subset
    INFRX_T2I_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/ship/test_mutants.py
    uv run --frozen python -m tests.t.ship.mutants --list
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/t/ship/test_ship.py"

if str(API_DIR) not in sys.path:        # `python tests/t/ship/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

S = "traces/ship/shipper.py"

FLAG = "test_shipping_is_off_unless_the_spool_the_projection_and_the_bucket_are_all_set"
CRASH = "test_a_crash_before_fsync_ships_only_the_promised_records"
UNSYNCED = "test_an_unsynced_record_in_the_active_segment_is_never_shipped"
DUP = "test_duplicate_shipping_after_a_lost_ack_is_one_logical_trace"
OUTAGE = "test_a_projection_outage_leaves_the_segment_for_the_retry"
METADATA = "test_metadata_ships_while_the_content_store_is_down_and_content_follows"
VERSIONS = "test_the_projection_carries_the_versions_and_the_loss_state"
PINS_OUTAGE = "test_a_pins_lookup_outage_holds_the_segment"
UNREADABLE = "test_a_segment_this_reader_cannot_read_is_never_acked"
TENANT = "test_a_tenant_reads_only_its_own_content"
FORGED = "test_a_row_pointing_at_another_tenants_object_is_never_followed"
BUILD = "test_no_shipper_is_built_unless_shipping_is_enabled"


def _m(name, invariant, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=S, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- the flag: default OFF --------------------------------------------------------
    _m("flag_any_setting_enables", "all three settings, not any one",
       "    return all(value.strip() for value", "    return any(value.strip() for value",
       FLAG),
    _m("flag_ignores_the_bucket", "no bucket, no shipping",
       "limits.clickhouse_url,\n                                           limits.s3_trace_bucket))",
       "limits.clickhouse_url))", FLAG),
    _m("build_ignores_the_flag", "nothing is built (nothing connects) while shipping is off",
       "    if not shipping_enabled(limits):\n        return None",
       "    if False:\n        return None", BUILD),
    _m("build_without_retention", "the production shipper consults T3's tombstones",
       "    return Shipper(spool, traces, objects, pins=PgPins(connector(limits.database_url)),\n"
       "                   retention=retention)",
       "    return Shipper(spool, traces, objects, pins=PgPins(connector(limits.database_url)))",
       BUILD),
    # --- TRACE-RECOVER ----------------------------------------------------------------
    _m("active_segment_shipped", "only sealed (fsynced) segments ship",
       "            if not view.sealed:\n                continue",
       "            if False:\n                continue", UNSYNCED),
    _m("rows_never_inserted", "the promised prefix reaches the projection",
       "            await self.projection.insert(rows)", "            pass", CRASH),
    _m("torn_tail_not_reported", "a torn tail is counted, not silent",
       "        report.torn += scan.torn", "        report.torn += 0", CRASH),
    _m("shipped_segment_never_acked", "a shipped segment is acked whole",
       "                if reason is None and not await self.spool.ack(view.name):",
       "                if False:", CRASH, DUP),
    _m("segment_failure_escapes", "an outage holds the segment, it does not end the pass",
       "            except Exception as failure:          # noqa: BLE001 - held, retried next pass",
       "            except ZeroDivisionError as failure:  # noqa: BLE001 - held, retried next pass",
       OUTAGE, PINS_OUTAGE, dies_by=("DependencyUnavailable",)),
    _m("acked_without_its_content", "a segment whose content did not land is held",
       '        return None if all(row.content_stored or row.content_key is None for row in rows) \\\n'
       '            else "content not stored"',
       "        return None", METADATA),
    _m("a_failed_put_claims_stored", "a row never claims content that did not land",
       "            except Exception:                    # noqa: BLE001 - metadata ships regardless\n"
       "                stored = False",
       "            except Exception:                    # noqa: BLE001 - metadata ships regardless\n"
       "                stored = True", METADATA),
    _m("poison_segment_acked", "a poison frame is kept, never deleted as shipped",
       "        if scan.unreadable or scan.poison:", "        if scan.unreadable:", UNREADABLE),
    _m("unreadable_segment_acked", "an unknown format is kept for a reader that knows it",
       "        if scan.unreadable or scan.poison:", "        if scan.poison:", UNREADABLE),
    _m("find_without_final", "a replayed row is one logical trace (ReplacingMergeTree FINAL)",
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} FINAL "',
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} "', DUP),
    # --- versions and loss state -------------------------------------------------------
    _m("pins_looked_up_under_the_key_not_the_org", "pins are the envelope org's request's",
       "await self.pins(envelope.org_id, envelope.request_id)",
       "await self.pins(envelope.key_id, envelope.request_id)", VERSIONS),
    _m("loss_state_dropped", "the envelope's loss state is carried",
       "loss_reason=envelope.loss_reason.value", 'loss_reason="none"', VERSIONS),
    _m("rate_card_version_dropped", "the rate version is carried",
       "rate_card_version=pins.rate_card_version if pins else None",
       "rate_card_version=None", VERSIONS),
    # --- TRACE-TENANT ------------------------------------------------------------------
    _m("key_from_the_envelope_ref", "the object key is derived server-side, per org",
       "            key = content_key(envelope.org_id, trace_id)",
       "            key = envelope.content_ref", TENANT),
    _m("find_ignores_the_org", "the organization is bound in the query",
       '"WHERE org_id = {org:UUID} AND request_id = {request:UUID} ORDER BY trace_id"',
       '"WHERE request_id = {request:UUID} ORDER BY trace_id"', TENANT),
    _m("cross_org_ref_followed", "a key outside the org's prefix is never read",
       "        if row.content_stored and row.content_key \\\n"
       "                and row.content_key.startswith(content_key(org_id, \"\")):",
       "        if row.content_stored and row.content_key:", FORGED),
    _m("unstored_content_followed", "a row that does not claim its content is not followed",
       "        if row.content_stored and row.content_key \\\n",
       "        if row.content_key \\\n", METADATA),
)

#: Only the real ClickHouse sees these: the in-memory projection has no query text.
NEEDS_STACK = frozenset({"find_without_final", "find_ignores_the_org"})
RUNNER = Runner(name="t2i", targets=(SUITE,), env=("INFRX_T2I_STACK",))


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run T2I's mutation list"))
