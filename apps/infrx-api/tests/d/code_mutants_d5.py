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
REGISTRY = "test_registry__the_alias_moves_at_the_deployments_newest_effective_card"
CONNECTIONS = "test_catalog__every_lookup_opens_and_closes_its_own_connection"
CATALOG = "test_catalog__a_private_deployment_only_for_its_provider_and_errors_raised"
SENDS = "test_complete__sends_the_proposal_the_regime_and_the_stores_ttls"
REFUSAL = "test_complete__a_committed_refusal_is_raised_as_its_type"
SETTLEMENT = "test_complete_credit__a_settlement_exactly_when_settled_at_the_recorded_charge"
CAUSE = "test_cancel__sends_the_cause_and_defaults_to_the_clients_own"
WORK = "test_load_work_credit__the_admitted_work_and_a_legacy_job_refused"
RELEASED = "test_recover__a_24h_release_is_reported_in_released"
LOOKUP = "test_lookup__answers_the_jobs_own_regime_and_sends_the_stores_ttl"


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
    _m("a_settlement_for_an_unsettled_outcome", "review N6: held back or platform-absorbed "
       "is no settlement",
       "    if outcome.settlement_state is not SettlementState.settled:",
       "    if outcome.settlement_state is SettlementState.released_free:", SETTLEMENT),
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
    # --- R91: the lookup -----------------------------------------------------------------
    _m("lookup_crosses_regimes", "the answer is the job's own regime's record",
       '        admission = admission_of(doc) if doc["accounting_regime"] == "legacy_usd" \\\n'
       '            else admission_v2_of(doc)',
       '        admission = admission_of(doc)', LOOKUP),
    _m("lookup_sends_the_default_ttl", "the store's own tombstone TTL decides expiry",
       '            "limits": {"idempotency_ttl_s": self.limits.idempotency_ttl_s}})\n'
       '        if doc is None:',
       '            "limits": {"idempotency_ttl_s": DEFAULTS.idempotency_ttl_s}})\n'
       '        if doc is None:', LOOKUP),
    _m("lookup_sends_the_scopes_org", "review N6: the caller's organization is sent (R10)",
       '        doc = await self._call("idempotency_lookup", {\n'
       '            "org_id": org_id, "idem": idem.model_dump(mode="json"),',
       '        doc = await self._call("idempotency_lookup", {\n'
       '            "org_id": idem.org_id, "idem": idem.model_dump(mode="json"),', LOOKUP),
    _m("lookup_drops_the_outcome", "a terminal mapping answers its committed outcome",
       '        return admission, _outcome(doc["outcome"])', '        return admission, None',
       LOOKUP),
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
    _m("adjust_sends_another_actor", "review CF-2: the operator is the adjustment's actor",
       '"amount": str(amount), "operation_id": operation_id, "actor": actor,',
       '"amount": str(amount), "operation_id": operation_id, "actor": "system",',
       LEDGER, file=O),
    _m("reconcile_sends_another_actor", "review CF-2: the operator is the reconcile's actor",
       '"actor": actor, "at": at.isoformat()})', '"actor": "system", "at": at.isoformat()})',
       LEDGER, file=O),
    _m("adjust_allocates", "an adjustment is an operator_adjustment",
       '"wallet_id": wallet.wallet_id, "kind": "operator_adjustment",',
       '"wallet_id": wallet.wallet_id, "kind": "operator_allocation",', LEDGER, file=O),
    _m("alias_moves_at_the_oldest_card", "review CF-1: the alias moves at the newest card",
       "                     order by c.effective_at desc, c.created_at desc limit 1) as card",
       "                     order by c.effective_at asc, c.created_at asc limit 1) as card",
       REGISTRY, file=O),
    # --- item 8: the catalog -----------------------------------------------------------
    _m("private_visible_to_consumer", "a private deployment only for its provider_dev key",
       "        if row is None and audience is CredentialAudience.provider_dev and endpoint_id:",
       "        if row is None and endpoint_id:", CATALOG, file=C),
    _m("public_resolves_an_inactive_deployment", "review CF-3: a public alias resolves "
       "only an ACTIVE deployment", "    and d.visibility = 'public' and d.state = 'active'",
       "    and d.visibility = 'public'", CATALOG, file=C),
    _m("private_resolves_a_retired_deployment", "review CF-3: a retired private deployment "
       "never resolves", " and d.visibility = 'private' and d.state <> 'retired'",
       " and d.visibility = 'private'", CATALOG, file=C),
    # D10 (F2C.c finding 2): "the card" is the listing's; the effective check moved with it.
    _m("card_not_effective_checked", "a card is active only once effective (DB clock)",
       "  where c.effective_at <= infrx.now() and c.rate_card_version = coalesce(",
       "  where c.rate_card_version = coalesce(", CATALOG, file=C),
    _m("a_connection_cached_across_calls", "review CF-4 (R09): a fresh connection per "
       "statement", "        conn = await self._connect()\n        try:\n            yield conn",
       '        conn = self.__dict__.get("_kept") or self.__dict__.setdefault('
       '"_kept", await self._connect())\n        try:\n            yield conn',
       CONNECTIONS, file=O),
    _m("a_connection_never_closed", "review CF-4: every statement's connection is closed",
       "        finally:\n            await conn.close()", "        finally:\n            pass",
       CONNECTIONS, file=O),
    _m("a_connection_leaks_on_error", "verifier V-N3: a statement that raises still "
       "closes its connection (the verifier's om6)",
       "        try:\n            yield conn\n        finally:\n            await conn.close()",
       "        yield conn\n        await conn.close()", CONNECTIONS, file=O),
    _m("errors_become_none", "a database error is raised, never answered as None",
       "                return await (await conn.execute(sql, params)).fetchall()\n"
       "            except Error as failed:\n                raise _typed(failed) from None",
       "                return await (await conn.execute(sql, params)).fetchall()\n"
       "            except Error as failed:\n                return []",
       CATALOG, file=O),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D5 adapter mutation list"))
