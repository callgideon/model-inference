#!/usr/bin/env python3
"""R32/R83 for L3: one single-edit defect per decision `tests/l/control` claims, through the
shared runner (`tests/contracts/mutants.py`).

`MUTANTS` run the fake world only (`-m "not pg"`, no Docker): the service's decisions
(`infrx/lab/control/__init__.py`) and the rules L3 leaves to lab-sql's store, which the fake
states for L3-SQL to match (`fakes.py`). `PG_MUTANTS` are the service's edits killed by the
same cases on PostgreSQL (`-m pg`, on the D harness of `INFRX_D_TASK=l3`); they need L3-SQL
merged (`infrx.state.lab_control`) and Docker, and skip visibly otherwise.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/control/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control/test_mutants.py
    uv run --frozen python tests/l/control/mutants.py --list

Left out on purpose: A3's resolution rules (`infrx/state/catalog.py`, restated by the fake)
and the ownership foreign keys the service checks first are killed by D's and G's own lists;
the positive-allocation and record validators are F2P's.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILE = "tests/l/control/test_control.py"
if str(API_DIR) not in sys.path:        # `python tests/l/control/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401
from tests.l.access import mutants as l2  # noqa: E402

C = "lab/control/__init__.py"
F = "lab/control/fakes.py"

SEAM = "test_lab_control__a_provider_never_mutates_another_providers_registry"
ROLES = "test_lab_control__roles_bound_every_operation"
ARTIFACT = "test_lab_control__only_a_pinned_supported_artifact_registers"
SMOKE = "test_lab_control__only_a_passed_smoke_makes_a_dev_revision_usable"
DISCOVERY = "test_lab_control__a_dev_revision_never_reaches_app_discovery"
KEY = "test_lab_control__a_dev_credential_is_scoped_to_its_endpoint"
WALLET = "test_lab_control__a_dev_wallet_starts_at_zero_and_only_audited_allocations_fund_it"
PINS = "test_lab_control__an_alias_switch_while_a_job_is_queued_keeps_its_pins"
CAS = "test_lab_control__publication_and_rollback_are_compare_and_set"
ROLLBACK = "test_lab_control__a_rollback_targets_an_earlier_servable_listing_only"
SHADOW = "test_lab_control__a_newer_unvalidated_revision_is_never_keyed_priced_or_served"

MUTANTS: tuple[Mutant, ...] = (
    # --- the service: who may ask, and what may be registered -------------------------
    _m("register_open_to_viewer", "registration needs developer+ (a viewer reads health only)",
       C, "ProviderCapability.manage_dev_deployment)\n        if serving.provider_org_id",
       "ProviderCapability.read_aggregate_health)\n        if serving.provider_org_id", ROLES),
    _m("register_row_of_another_provider", "a registered row names the caller's provider",
       C, "        if serving.provider_org_id != provider_org_id:\n",
       "        if False:\n", SEAM),
    _m("register_model_of_another_provider", "a registered row is of the caller's own model",
       C, "        if await self.store.model_provider(serving.model_id) != provider_org_id:\n",
       "        if False:\n", SEAM),
    _m("runtime_by_tag", "the runtime image is pinned by digest, never a moving tag",
       C, r'@sha256:[0-9a-f]{64}$")', r'(@sha256:[0-9a-f]{64}|:[\w.-]+)$")', ARTIFACT),
    _m("runtime_any_image", "only a supported runtime image runs (no uploaded code)",
       C, '    if runtime["repo"] not in SUPPORTED_RUNTIMES:\n', "    if False:\n",
       ARTIFACT),
    _m("schema_unchecked", "only schemas the gateway serves register",
       C, "not in SUPPORTED_SCHEMAS:", "in ():", ARTIFACT),
    _m("dev_of_another_providers_serving", "a dev revision serves the provider's own revision",
       C, "        if serving is None or serving.provider_org_id != provider_org_id:\n",
       "        if serving is None:\n", SEAM),
    _m("dev_revision_of_any_provider", "a dev revision is addressed in its own workspace only",
       C, "if (deployment is None or deployment.provider_org_id != provider_org_id",
       "if (deployment is None", SEAM),
    _m("dev_revision_of_any_environment", "a provider operates on dev revisions only",
       C, "                or deployment.environment is not Environment.dev):",
       "                ):", DISCOVERY),
    _m("smoke_skipped", "validation is the engine's smoke, not a formality",
       C, "passed = await self.engine.smoke(serving, deployment)", "passed = True", SMOKE),
    _m("smoke_inverted", "a failed smoke retires; a passed one is ready_private",
       C, "DeploymentState.ready_private if passed else DeploymentState.retired",
       "DeploymentState.retired if passed else DeploymentState.ready_private", SMOKE),
    _m("key_before_validation", "only a validated dev revision gets a credential",
       C, "        if deployment.state is not DeploymentState.ready_private:\n            raise errors.StateConflict(\"only a validated dev revision gets",
       "        if False:\n            raise errors.StateConflict(\"only a validated dev revision gets",
       SMOKE, SHADOW),
    _m("key_for_draft", "a draft or validating revision gets no credential (retired is not all)",
       C, "        if deployment.state is not DeploymentState.ready_private:\n            raise errors.StateConflict(\"only a validated dev revision gets",
       "        if deployment.state is DeploymentState.retired:\n            raise errors.StateConflict(\"only a validated dev revision gets",
       SHADOW),
    _m("price_unvalidated", "only a validated dev revision is priced",
       C, "        if deployment.state is not DeploymentState.ready_private:\n            raise errors.StateConflict(\"only a validated dev revision is priced",
       "        if False:\n            raise errors.StateConflict(\"only a validated dev revision is priced",
       SHADOW),
    _m("key_stores_the_secret", "the store keeps the secret's hash, never the secret",
       C, "key_hash=hash_key(secret)", "key_hash=secret", KEY),
    _m("proposal_by_developer", "publication is proposed by an administrator",
       C, "ProviderCapability.propose_publication)", "ProviderCapability.manage_dev_deployment)",
       ROLES),
    _m("history_open_to_viewer", "the control history (names members, operators) is developer+",
       C, "ProviderCapability.manage_dev_deployment)\n        return tuple(",
       "ProviderCapability.read_aggregate_health)\n        return tuple(", ROLES),
    _m("card_not_the_operators", "a card is approved by the operator who priced it",
       C, "approved_by=operator.principal)", 'approved_by="provisional - P-01 pending")',
       DISCOVERY),
    _m("internal_card_on_public", "an internal card prices a private dev revision only",
       C, "        if deployment.visibility is not Visibility.private:\n", "        if False:\n",
       DISCOVERY),
    _m("fund_new_operation_each_call", "one idempotency key is one allocation",
       C, 'operation_id=stable_id("lab_fund", idempotency_key)',
       "operation_id=str(uuid.uuid4())", WALLET),
    _m("operator_unnamed", "an operator write is audited under the operator's own name",
       C, "actor=operator.principal, reason=reason)", 'actor="operator", reason=reason)',
       WALLET, occurrences=3),
    # --- the store's rules (the fake states them for L3-SQL) ---------------------------
    _m("transition_without_cas", "a state move is a compare-and-set on the current state",
       F, "        if d.state is not expected or to not in MOVES.get(expected, ()):\n",
       "        if to not in MOVES.get(expected, ()):\n", SMOKE),
    _m("transition_unattributed", "each move is audited under the member who made it",
       F, '_event("lab_transition", actor,', '_event("lab_transition", "system",', SMOKE),
    _m("proposal_of_unvalidated", "only a ready_private dev revision is proposed",
       F, "        if (source.state is not S.ready_private\n", "        if (False\n", SMOKE),
    _m("listing_without_cas", "publication and rollback compare-and-set the listing version",
       F, "        if (current.version if current else None) != expected_version:\n",
       "        if False:\n", CAS),
    _m("publish_any_revision", "only a proposed public revision is published",
       F, "        if d.state is not S.proposed_public:\n", "        if False:\n", DISCOVERY),
    _m("publish_retires_previous", "a publication leaves the listed revision servable",
       F, "        self.deployments[d.deployment_revision_id] = d.model_copy(update={\"state\": "
       "S.active})\n",
       "        self.deployments[d.deployment_revision_id] = d.model_copy(update={\"state\": "
       "S.active})\n        if current:\n            self.deployments[current."
       "deployment_revision_id] = self.deployments[current.deployment_revision_id]."
       "model_copy(update={\"state\": S.retired})\n", PINS),
    _m("listing_overwritten", "a listing version is added, never edited",
       F, "        versions.append(listing)\n", "        versions[-1:] = [listing]\n", PINS),
    _m("rollback_to_any_version", "a rollback targets an EARLIER version",
       F, "        if not 1 <= to_version < current.version:\n",
       "        if not 1 <= to_version:\n", ROLLBACK),
    _m("rollback_to_retired", "a rollback targets a still active revision",
       F, "        if d.state is not S.active:\n            raise errors.StateConflict(\"the "
       "target", "        if False:\n            raise errors.StateConflict(\"the target",
       ROLLBACK),
    _m("dev_wallet_opens_with_a_grant", "a provider_dev wallet opens at 0 CREDIT",
       F, "            owner_provider_org_id=provider_org_id)",
       "            owner_provider_org_id=provider_org_id, "
       "ledger_total=v2.INITIAL_SIGNUP_GRANT, )", WALLET),
    _m("private_resolves_unvalidated", "a provider_dev key serves its endpoint's newest "
       "VALIDATED revision (A3 wiring WR-L3-5)",
       F, "d.state is S.ready_private", "d.state is not S.retired", SHADOW),
    _m("allocation_replayed_twice", "a replayed operation id appends nothing",
       F, "        if prior is not None:\n            return prior\n",
       "        if False:\n            return prior\n", WALLET),
)

#: The service's edits, killed by the same cases on PostgreSQL (after L3-SQL merges).
#: WR-LSQ-8: not `dev_revision_of_any_environment` - 0032's `lab_control_transition` and
#: `lab_control_propose` also refuse a prod source, so on PostgreSQL that edit is equivalent
#: (the store's rule is killed by lab-sql's own list; the fake list keeps the service's).
PG_EQUIVALENT = frozenset({"dev_revision_of_any_environment"})
PG_MUTANTS: tuple[Mutant, ...] = tuple(
    dataclasses.replace(m, name=f"pg_{m.name}") for m in MUTANTS
    if m.file == C and m.name not in PG_EQUIVALENT)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="l3", targets=(SUITE_FILE,), extra_args=("-m", "not pg"))
PG_RUNNER = Runner(name="l3-pg", targets=(SUITE_FILE,), extra_args=("-m", "pg"),
                   env=("INFRX_D_TASK",), layout=l2._pg_layout)


def run_mutant(mutant) -> Result:
    """The PostgreSQL list is not a module's `MUTANTS`, so the shared runner takes no
    baseline for it: its cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run L3's mutation list"))
