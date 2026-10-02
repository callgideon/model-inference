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
OPS_FILE = "tests/l/control/test_operations.py"            # WR-LAB-API-2, WR-R2-2, WR-I2L-2
if str(API_DIR) not in sys.path:        # `python tests/l/control/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401
from tests.l.access import mutants as l2  # noqa: E402

C = "lab/control/__init__.py"
F = "lab/control/fakes.py"
OPS = "lab/control/operations.py"
APP = "lab/control/app.py"
ROUTE = "gateway/routes/lab_control.py"
STORE = "state/lab_control.py"

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
RETRY = "test_lab_control__a_proposal_retried_after_a_lost_answer_proposes_once"
SHADOW = "test_lab_control__a_newer_unvalidated_revision_is_never_keyed_priced_or_served"
O_ROWS = "test_operations__the_route_records_are_l3s_own_rows"
O_FOREIGN = "test_operations__another_providers_actor_sees_and_moves_nothing"
O_ACTOR = "test_operations__the_actor_is_rechecked_against_the_current_membership"
O_FAILED = "test_operations__a_failed_smoke_reads_failed_and_is_never_proposed"
O_PINNED = "test_operations__only_a_pinned_supported_registration_is_accepted"
O_APP_REG = "test_operations__the_lab_apps_registration_shape_registers"
O_SERVING = "test_serving_control__rollback_is_a_fenced_alias_cas_that_keeps_pins"
O_APP = "test_control_app__serves_readiness_and_no_consumer_route"
O_MOUNT = "test_control_app__mounts_only_the_lab_routers_on_its_own_settings"
O_REJECT = "test_operations__an_operator_rejects_a_proposal_and_it_publishes_nothing"
O_TERMINAL = "test_operations__a_retired_proposal_lists_as_a_terminal_row"
O_ROUTE = "test_control_route__only_an_operator_rejects_a_proposal"
O_LISTED = "test_operations__a_revision_reads_public_only_while_it_is_the_listing"
READ_GUARD = ("        await self.control.access.require(actor.user_id, actor.provider_org_id,\n"
              "                                          ProviderCapability.read_aggregate_health)\n")

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
    _m("proposal_retry_opens_a_second", "E3L-F2/R205: a retry of the same source answers "
       "the open proposal, never a second insert", F,
       "        for event in reversed(self.audit):\n"
       "            if event.action == \"lab_propose\" and event.after.get(\"source\") == "
       "source_revision_id:\n"
       "                existing = self.deployments.get(event.subject)\n"
       "                if existing is not None and existing.state is S.proposed_public:\n"
       "                    return existing\n                break\n",
       "        pass\n", RETRY),
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
    # --- WR-LAB-API-2: the route's port over LabControl (operations.py) -----------------
    _m("models_unguarded", "the model list is a current member's", OPS,
       READ_GUARD + "        return [_model(s)", "        return [_model(s)", O_ACTOR),
    _m("deployments_unguarded", "the deployment list is a current member's", OPS,
       READ_GUARD + "        return [await self._deployment(d)",
       "        return [await self._deployment(d)", O_ACTOR),
    _m("proposals_unguarded", "the proposal list is a current member's", OPS,
       READ_GUARD + "        return await self._proposals(", "        return await self._proposals(",
       O_ACTOR),
    _m("register_role_after_lookup", "a role without registration learns no model name", OPS,
       "        await self.control.access.require(user, provider, ProviderCapability.manage_dev_deployment)\n",
       "", O_ACTOR),
    _m("register_any_model_name", "a registration names one of the provider's own models", OPS,
       '                    if s.public_model_id.rpartition("/")[2] == registration.name]', "]",
       O_PINNED),
    _m("register_slug_qualified_name", "the name is the App's bare name (0-L3I-R1)", OPS,
       'if s.public_model_id.rpartition("/")[2] == registration.name]',
       "if s.public_model_id == registration.name]", O_APP_REG),
    _m("register_any_weights", "the artifact digest is one of the model's imported weights",
       OPS, "                    if registration.artifact_digest in s.weight_shard_digests]",
       "]", O_PINNED, O_APP_REG),
    _m("register_keeps_base_runtime", "the registered runtime is the one named, by digest", OPS,
       '"runtime_image_ref": registration.runtime,', "", O_ROWS, O_PINNED),
    _m("register_runtime_repinned_to_artifact", "the artifact digest never pins the image "
       "(0-L3I-R1)", OPS, '"runtime_image_ref": registration.runtime,',
       '"runtime_image_ref": registration.runtime.partition("@")[0] + "@" '
       '+ registration.artifact_digest,', O_ROWS, O_APP_REG),
    _m("register_image_digest_dropped", "the image digest is the runtime's own", OPS,
       '"runtime_image_digest": (registration.runtime.partition("@")[2]',
       '"runtime_image_digest": (None', O_ROWS),
    _m("model_shows_the_runtime_digest", "a model's artifact is its weights", OPS,
       "artifact_digest=serving.weight_shard_digests[0],",
       'artifact_digest=serving.runtime_image_ref.partition("@")[2],', O_ROWS),
    _m("register_keeps_base_schema", "the registered schema is the one named", OPS,
       '"capability": base.capability.model_copy(update={\n'
       '                "input_schema_ref": REQUEST + registration.schema_version,\n'
       '                "output_schema_ref": RESPONSE + registration.schema_version}),',
       '"capability": base.capability,', O_PINNED),
    _m("register_without_limits", "a revision's limits are the model's deployed ones", OPS,
       "        if not limits:\n            raise errors.InvalidRequest(\"this model has no "
       "deployed limits yet: an operator \"\n"
       "                                        \"deploys its first revision\")\n",
       "        limits = limits or [DeploymentRevision.model_construct(max_input_tokens=4096, "
       "max_output_tokens=512, created_at=base.created_at)]\n", O_PINNED),
    _m("register_limits_invented", "limits are copied, never invented", OPS,
       "max_input_tokens=latest.max_input_tokens", "max_input_tokens=4096", O_ROWS),
    _m("register_dev_endpoint_misnamed", "the dev endpoint is the model's own name", OPS,
       "endpoint_name=registration.name,", 'endpoint_name="lab",', O_ROWS),
    _m("smoke_failure_reads_passed", "a failed smoke never reads passed", OPS,
       'smoke = ("failed" if failed else', 'smoke = ("passed" if failed else', O_FAILED),
    _m("draft_reads_passed", "an unrun revision reads none", OPS,
       "d.state in (S.draft, S.validating, S.retired)", "d.state in (S.validating, S.retired)",
       O_FAILED),
    _m("retired_reads_active", "a retired revision reads retired", OPS,
       'state="retired" if d.state is S.retired else "active"', 'state="active"', O_FAILED),
    _m("rate_card_dropped", "a priced revision shows its card", OPS,
       "rate_card_version=card.rate_card_version if card else None", "rate_card_version=None",
       O_ROWS),
    _m("proposal_reads_approved_early", "a proposal is proposed until an operator lists it", OPS,
       '            state = ("proposed" if d.state is S.proposed_public',
       '            state = ("approved" if d.state is S.proposed_public', O_ROWS),
    _m("approval_unread", "an approved proposal reads approved with its instant", OPS,
       "        published = {e.after.get(\"deployment_revision_id\"): e.at for e in events\n"
       "                     if e.action == \"lab_publish\"}",
       "        published = {}", O_ROWS),
    _m("proposal_names_the_prod_revision", "a proposal names the dev revision proposed", OPS,
       'deployment_revision_id=e.after["source"]', "deployment_revision_id=e.subject", O_ROWS),
    _m("provider_rollback_proposal", "a provider proposes publication only", OPS,
       '        if kind != "publish":\n', "        if False:\n", O_PINNED),
    _m("proposal_to_another_endpoint", "a proposal targets the model's prod endpoint", OPS,
       'endpoint_name=serving.public_model_id.rpartition("/")[2] if serving else "-")',
       'endpoint_name="preview")', O_ROWS),
    # --- WR-R2-2: ServingControl -------------------------------------------------------
    _m("serving_reads_the_first_version", "serving is the alias's CURRENT listing", OPS,
       "        return await self._ref(versions[-1]), versions[-1].version",
       "        return await self._ref(versions[0]), versions[0].version", O_SERVING),
    _m("rollback_ignores_the_digest", "a ref names a revision AND its immutable serving", OPS,
       "            if await self._ref(listing) == to_serving_ref:",
       "            if (await self._ref(listing)).split(\"@\")[0] == to_serving_ref.split(\"@\")[0]:",
       O_SERVING),
    _m("rollback_unfenced", "the fence R2 read is the fence the CAS compares", OPS,
       "expected_version=fence,", "expected_version=versions[-1].version,", O_SERVING),
    # --- WR-I2L-2: the control factory -------------------------------------------------
    _m("readyz_always_ready", "readiness is the database answering", APP,
       '            return JSONResponse({"status": "unavailable"}, status_code=503)',
       '            return {"status": "ready"}', O_APP),
    _m("control_docs_served", "the control service publishes no schema or docs", APP,
       "FastAPI(docs_url=None, redoc_url=None, openapi_url=None)", "FastAPI()", O_APP),
    _m("control_routes_unmounted", "the unit serves L3's routes (WR-I2L-2b)", APP,
       "    lab_control.register(app, rt, control)\n", "", O_MOUNT),
    _m("lab_setting_waved_through", "a missing INFRX_LAB_* setting refuses startup", APP,
       "    if missing:\n        raise RuntimeMisconfigured", "    if False:\n        raise RuntimeMisconfigured",
       O_MOUNT),
    # --- WR-LSQ-9: the reads the fake states for lab-sql ----------------------------------
    _m("servings_of_every_provider", "a provider lists its own serving revisions", F,
       "if s.provider_org_id == provider_org_id]", "]", O_FOREIGN),
    _m("deployments_of_every_provider", "a provider lists its own deployment revisions", F,
       "if d.provider_org_id == provider_org_id]", "]", O_FOREIGN),
    _m("allocation_replayed_twice", "a replayed operation id appends nothing",
       F, "        if prior is not None:\n            return prior\n",
       "        if False:\n            return prior\n", WALLET),
    # --- E3L-F4: the operator's rejection ---------------------------------------------------
    _m("reject_unnamed", "a rejection is audited under the operator who made it", C,
       "        return await self.store.reject(deployment_revision_id, actor=operator.principal,",
       '        return await self.store.reject(deployment_revision_id, actor="operator",',
       O_REJECT),
    _m("reject_reason_dropped", "a rejection records the operator's reason", C,
       "        return await self.store.reject(deployment_revision_id, actor=operator.principal,\n"
       "                                       reason=reason)",
       "        return await self.store.reject(deployment_revision_id, actor=operator.principal,\n"
       '                                       reason="rejected")', O_REJECT),
    _m("reject_any_state", "only an open proposal is rejected (never twice, never a listed one)",
       F, "        if d.state is not S.proposed_public:    # never twice, never a listed",
       "        if False:    # never twice, never a listed", O_REJECT),
    _m("reject_without_reason", "a rejection states its reason", F,
       "        if not 1 <= len(reason.strip()) <= 500:\n", "        if False:\n", O_REJECT),
    _m("rejected_stays_public", "a retired revision is never public (PgControlStore's read)", F,
       '            "state": S.retired, "visibility": v2.Visibility.private})',
       '            "state": S.retired})', O_REJECT),
    _m("reject_unaudited_start", "the audit keeps the state a rejection moved from", F,
       '                    before={"state": S.proposed_public.value})',
       "                    before=None)", O_REJECT),
    _m("operator_bit_ignored", "only a platform operator is one", F,
       "        return user_id in self.operators", "        return True", O_REJECT),
    _m("operator_door_open", "the operator door refuses a non-operator", OPS,
       "        if not await self.control.store.operator(user_id):\n", "        if False:\n",
       O_REJECT, O_ROUTE),
    _m("operator_principal_claimed", "the audited actor is the operator's own user", OPS,
       'principal=f"operator:{user_id}")', 'principal="operator")', O_REJECT),
    _m("reject_anything", "only one of the Lab's proposals is rejected", OPS,
       "        if not mine:\n            raise errors.NotFound(\"no such proposal\")\n", "",
       O_REJECT),
    _m("rejection_undated", "a rejected proposal carries the operator's instant", OPS,
       '        rejected = {e.subject: e.at for e in events if e.action == "lab_transition"',
       '        rejected = {e.subject: e.at for e in events if False', O_REJECT, O_ROUTE),
    # AP-00: the route is typed; the operator is a dependency and an unreadable body is
    # withheld until it answers (`lab_auth.refusal_route`).
    _m("reject_body_before_operator", "the operator is checked before the body is read (R175)",
       "gateway/lab_auth.py", "                        except errors.DomainError as refused:\n"
       "                            request._body, withheld = b\"\", refused\n",
       "                        except errors.DomainError:\n                            raise\n",
       O_ROUTE),
    _m("reject_reason_optional", "a rejection body names its reason", ROUTE,
       "    reason: str = Field(min_length=1, max_length=500)",
       '    reason: str = "declined"', O_ROUTE),
    # --- E3L-F5: the record's visibility is the listing's truth (R207) -----------------------
    _m("visibility_from_the_row", "a revision reads public only while it is the listing", OPS,
       'visibility="public" if d.deployment_revision_id == listed else "private",',
       "visibility=d.visibility.value,", O_LISTED),
    _m("visibility_from_the_first_listing", "the listing is the alias's CURRENT version", OPS,
       "listed = versions[-1].deployment_revision_id if versions else None",
       "listed = versions[0].deployment_revision_id if versions else None", O_LISTED),
    _m("reject_unmounted", "the operator's rejection is served", ROUTE,
       '    route("POST", "/proposals/{proposal_id}/reject", reject, Proposal)\n',
       '    route("POST", "/proposals/{proposal_id}/reject-x", reject, Proposal)\n', O_ROUTE),
)

#: The service's edits, killed by the same cases on PostgreSQL (after L3-SQL merges).
#: WR-LSQ-8: not `dev_revision_of_any_environment` - 0032's `lab_control_transition` and
#: `lab_control_propose` also refuse a prod source, so on PostgreSQL that edit is equivalent
#: (the store's rule is killed by lab-sql's own list; the fake list keeps the service's).
PG_EQUIVALENT = frozenset({"dev_revision_of_any_environment"})
PG_MUTANTS: tuple[Mutant, ...] = tuple(
    dataclasses.replace(m, name=f"pg_{m.name}") for m in MUTANTS
    if (m.file == C and m.name not in PG_EQUIVALENT) or m.file == APP) + (
    # E3L-F4: PgControlStore's own decisions, killed by the same cases on PostgreSQL only
    _m("pg_store_retired_read_public", "a retired revision reads back private, never a "
       "ValidationError (E3L-F4)", STORE, '    if doc["state"] == "retired":\n',
       "    if False:\n", O_TERMINAL),
    _m("pg_store_reason_dropped", "the store sends the operator's reason", STORE,
       '"deployment_revision_id": deployment_revision_id, "actor": actor,\n'
       '            "reason": reason}))',
       '"deployment_revision_id": deployment_revision_id, "actor": actor,\n'
       '            "reason": ""}))', O_REJECT),
    _m("pg_store_operator_bit_ignored", "the store answers the profile's operator bit", STORE,
       '        return (await self._call("lab_control_operator", {"user_id": user_id}))'
       '["operator"]', "        return True", O_REJECT),
)


def case_names() -> set[str]:
    return {case for suite in (SUITE_FILE, OPS_FILE) for case in re.findall(
        r"^def (test_\w+)\(", (API_DIR / suite).read_text(), re.M)}


RUNNER = Runner(name="l3", targets=(SUITE_FILE, OPS_FILE), extra_args=("-m", "not pg"))
PG_RUNNER = Runner(name="l3-pg", targets=(SUITE_FILE, OPS_FILE), extra_args=("-m", "pg"),
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
