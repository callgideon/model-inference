#!/usr/bin/env python3
"""R32/R40 for D5's Python half (`infrx/state/jobstore.py`: the settlement, the cancel
cause, WorkV2, the released record; `infrx/state/operations.py` and `catalog.py` join as
their items land).

Delegates to the one runner (`tests/contracts/mutants.py`, R83): each mutant is one edit to
a throwaway copy of the package, the named cases in the unit files run there (no Docker),
and only `killed` counts. The SQL has its own list (`migration_mutants.py`, the `d5_`
entries).

    uv run --frozen pytest -q tests/d/test_code_mutants_d5.py
    uv run --frozen python -m tests.d.code_mutants_d5 --list
"""
from __future__ import annotations

import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

J = "state/jobstore.py"
O = "state/operations.py"
C = "state/catalog.py"
RUNNER = Runner(name="d5", targets=("tests/d/test_settle_units.py",
                                    "tests/d/test_operations_units.py"))
TENANT = "test_tenant_store__binds_the_organization_and_reads_a_replay_as_one"
REVOKE = "test_tenant_store__revocation_keeps_the_first_instant_and_suspension_sends_the_code"
AUDIT = "test_audit_log__looks_up_by_its_own_key"
ACCOUNT = "test_account_view__each_row_keeps_its_unit_and_holds_are_credit_only"
LEDGER = "test_ledger__asks_for_an_operator_adjustment_and_answers_the_entry"
CATALOG = "test_catalog__a_private_deployment_only_for_its_provider_and_errors_raised"
SENDS = "test_complete__sends_the_proposal_the_regime_and_the_stores_ttls"
REFUSAL = "test_complete__a_committed_refusal_is_raised_as_its_type"
SETTLEMENT = "test_complete_credit__a_settlement_exactly_when_settled_at_the_recorded_charge"
CAUSE = "test_cancel__sends_the_cause_and_defaults_to_the_clients_own"
WORK = "test_load_work_credit__the_admitted_work_and_a_legacy_job_refused"
RELEASED = "test_recover__a_24h_release_is_reported_in_released"


def _m(name, invariant, old, new, *cases, file=J) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    _m("complete_raises_the_refusal_inside_the_transaction",
       "R39: the settlement goes through the fenced call, so a committed refusal is raised "
       "as its type after the commit, never read as an outcome",
       '        return await self._fenced("terminalize", lease, regime=regime,\n'
       '                                  outcome=outcome.model_dump(mode="json"),\n'
       '                                  limits={**self._lease_limits(),\n'
       '                                          "result_ttl_s": self.limits.result_ttl_s,\n'
       '                                          "idempotency_ttl_s": '
       'self.limits.idempotency_ttl_s})',
       '        return await self._call("terminalize", {"lease": lease.model_dump(mode="json"),\n'
       '                                  "regime": regime,\n'
       '                                  "outcome": outcome.model_dump(mode="json"),\n'
       '                                  "limits": {**self._lease_limits(),\n'
       '                                          "result_ttl_s": self.limits.result_ttl_s,\n'
       '                                          "idempotency_ttl_s": '
       'self.limits.idempotency_ttl_s}})', REFUSAL),
    _m("the_proposal_is_not_sent", "the store settles the worker's proposal, not an empty one",
       '                                  outcome=outcome.model_dump(mode="json"),',
       '                                  outcome={},', SENDS),
    _m("the_default_result_ttl_is_sent", "the store's retuned result TTL, never the default",
       '"result_ttl_s": self.limits.result_ttl_s,', '"result_ttl_s": DEFAULTS.result_ttl_s,',
       SENDS),
    _m("the_default_tombstone_ttl_is_sent", "the store's retuned idempotency TTL",
       '"idempotency_ttl_s": self.limits.idempotency_ttl_s})',
       '"idempotency_ttl_s": DEFAULTS.idempotency_ttl_s})', SENDS),
    _m("complete_settles_in_the_credit_regime", "v1 complete is the legacy USD door (R64)",
       '(await self._terminalize(lease, outcome, "legacy_usd"))["outcome"]',
       '(await self._terminalize(lease, outcome, "credit"))["outcome"]', SENDS),
    _m("complete_credit_settles_in_the_legacy_regime", "complete_credit is the CREDIT door",
       '        doc = await self._terminalize(lease, outcome, "credit")',
       '        doc = await self._terminalize(lease, outcome, "legacy_usd")', SETTLEMENT),
    _m("a_settlement_for_every_outcome", "a SettlementV2 exists exactly when settled",
       "    if outcome.settlement_state is not SettlementState.settled:\n        return None",
       "    if False:\n        return None", SETTLEMENT),
    _m("the_charge_is_the_v1_debit", "the charge is the one the inference debit recorded",
       '"charged": doc["charged_credits"],', '"charged": str(outcome.debit),', SETTLEMENT),
    _m("the_cause_is_not_sent", "R21: the cancel cause reaches the store",
       '"org_id": org_id, "job_handle": job_handle, "cause": str(cause),',
       '"org_id": org_id, "job_handle": job_handle, "cause": "client_cancelled",', CAUSE),
    _m("load_work_credit_serves_a_legacy_job", "a legacy job's work is not WorkV2",
       '        if admission["accounting_regime"] != "credit":\n'
       '            raise errors.NotFound(f"job {lease.job_id} is not a CREDIT job',
       '        if False:\n'
       '            raise errors.NotFound(f"job {lease.job_id} is not a CREDIT job', WORK),
    _m("load_work_credit_policy_rewritten", "the pinned policy reaches the worker as stored",
       '                policy=DataAccessPolicyRef.model_validate(doc["policy"])),',
       '                policy=DataAccessPolicyRef.model_validate({**doc["policy"], '
       '"trace_mode": "full"})),', WORK),
    _m("a_release_read_as_a_terminalization", "I3B 5: a 24 h release is reported as released",
       '                released.append(produced[-1].job_id)', '                pass', RELEASED),
    _m("released_never_cleared", "each sweep reports its own releases",
       "        self.released = tuple(released)", "        self.released += tuple(released)",
       RELEASED),
    # --- item 7: the operator adapters -------------------------------------------------
    _m("key_lookup_ignores_org", "a key is read only within its own organization",
       '_KEY + "id = %s and org_id = %s", (key_id, org_id))', '_KEY + "id = %s", (key_id,))',
       TENANT, file=O),
    _m("insert_key_duplicates", "a replayed key insert writes nothing",
       ' on conflict (id) do nothing returning id",', ' returning id",', TENANT, file=O),
    _m("insert_key_reports_every_call_written", "a replay is reported as a replay",
       "        return written is not None", "        return True", TENANT, file=O),
    _m("revoke_rewrites_revoked_at", "a revocation keeps its first instant",
       '"update public.api_keys set revoked_at = coalesce(revoked_at, infrx.now()) "',
       '"update public.api_keys set revoked_at = infrx.now() "', REVOKE, file=O),
    _m("suspension_lift_suspends", "lifting sends suspended = false",
       "            (org_id, reason is not None, reason,", "            (org_id, True, reason,",
       REVOKE, file=O),
    _m("audit_lookup_by_any_key", "an audit row answers only its own idempotency key",
       '"infrx.audit_by_idempotency_key(%s)", (key,))',
       '"infrx.audit_by_idempotency_key(%s)", ("",))', AUDIT, file=O),
    _m("usage_totals_merge_units", "R73: each usage row keeps its own unit",
       "        request_id=str(request_id), org_id=str(org_id), accounting_regime=regime, "
       "unit=unit,", "        request_id=str(request_id), org_id=str(org_id), "
       "accounting_regime=regime, unit=\"CREDIT\",", ACCOUNT, file=O),
    _m("holds_read_usd_as_credit", "a USD hold is never read as a CREDIT hold",
       '"select request_id, state, amount from infrx.active_holds(%s) "\n'
       '            "where accounting_regime = \'credit\'"',
       '"select request_id, state, amount from infrx.active_holds(%s) "', ACCOUNT, file=O),
    _m("adjust_allocates", "an adjustment is an operator_adjustment",
       '"wallet_id": wallet.wallet_id, "kind": "operator_adjustment",',
       '"wallet_id": wallet.wallet_id, "kind": "operator_allocation",', LEDGER, file=O),
    # --- item 8: the catalog -----------------------------------------------------------
    _m("private_visible_to_consumer", "a private deployment only for its provider_dev key",
       "        if row is None and audience is CredentialAudience.provider_dev and endpoint_id:",
       "        if row is None and endpoint_id:", CATALOG, file=C),
    _m("card_not_effective_checked", "a card is active only once effective (DB clock)",
       "  where deployment_revision_id = %s and effective_at <= infrx.now()",
       "  where deployment_revision_id = %s", CATALOG, file=C),
    _m("errors_become_none", "a database error is raised, never answered as None",
       "                return await (await conn.execute(sql, params)).fetchall()\n"
       "            except Error as failed:\n                raise _typed(failed) from None",
       "                return await (await conn.execute(sql, params)).fetchall()\n"
       "            except Error as failed:\n                return []",
       CATALOG, file=O),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D5 adapter mutation list"))
