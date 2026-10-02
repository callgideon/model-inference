#!/usr/bin/env python3
"""R32/R83 for AP-06: one single-edit defect per decision the publication door makes.

Two lists over the shared runner (`tests/contracts/mutants.py`): `MUTANTS` are killed by the
unit cases (fake world); `PG_MUTANTS` by the ap6 PostgreSQL cases (`test_publication_pg.py`,
the two-process race included), in copies that provision their own container on the key's
port - so run them in a process that has not started the harness itself.

    uv run --frozen pytest -q tests/ap06/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=ap6 uv run --frozen pytest -q tests/ap06/test_mutants.py
    uv run --frozen python -m tests.ap06.mutants --list
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner

API_DIR = pathlib.Path(__file__).resolve().parents[2]
UNIT_FILES = ("tests/ap06/test_publication.py", "tests/ap06/test_dev_keys.py",
              "tests/ap06/test_catalog.py")
PG_FILE = "tests/ap06/test_publication_pg.py"
OP = "gateway/routes/operator_publication.py"
LC = "gateway/routes/lab_control.py"
MO = "gateway/routes/models.py"
PUB = "lab/publication/__init__.py"
LW = "lab/workers/__main__.py"
U, D, C = "test_publication__", "test_dev_keys__", "test_catalog__"
PG = "test_publication_pg__"


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting, rendering, authority ---------------------------------------------------
    _m("operator_routes_mounted_without_composition", "default OFF: no publication, no route",
       OP, "    if pub is None:\n        return None\n    decisions",
       "    if False:\n        return None\n    decisions",
       U + "nothing_is_mounted_without_a_publication"),
    _m("dev_routes_mounted_without_composition", "default OFF: no publication, no dev route",
       LC, "    if pub is None:\n        return\n    from fastapi import APIRouter",
       "    if False:\n        return\n    from fastapi import APIRouter",
       U + "nothing_is_mounted_without_a_publication"),
    _m("operator_not_reread_from_the_store", "profiles.is_operator decides, not the actor",
       OP, "        return await self.pub.operations.operator(session_operator(actor))",
       "        return OperatorSession(ops=None, principal=f\"operator:{actor.user_id}\")",
       U + "a_non_operator_is_refused_before_any_decision"),
    _m("operator_key_accepted_as_a_session", "an operator API key is not an operator session",
       OP, "operator(session_operator(actor))", "operator(actor.user_id or \"\")",
       U + "a_non_operator_is_refused_before_any_decision"),
    _m("validation_left_to_fastapi", "an invalid body is the R270 422 envelope",
       OP, "    router = APIRouter(route_class=control.R270Route, responses=ERRORS)",
       "    router = APIRouter(responses=ERRORS)",
       U + "an_invalid_body_or_missing_key_is_the_r270_422"),
    # --- 06b approval -----------------------------------------------------------------------
    _m("readiness_read_reported_unknown", "a receipt read is `configured`, never guessed",
       OP, '                availability = api.Availability(state="configured")',
       '                availability = api.Availability(state="unknown")',
       U + "an_approval_publishes_at_the_expected_version_with_a_readiness_receipt"),
    _m("readiness_gate_dropped", "no current readiness receipt, no publication",
       OP, "            await self._ready(proposal, candidate)\n", "",
       U + "a_stale_or_missing_readiness_receipt_refuses_approval",
       U + "readiness_is_required_when_none_is_composed"),
    _m("receipt_of_another_serving_revision_accepted", "a stale smoke receipt is refused",
       OP, "                or receipt.serving_version_id != candidate.serving_version_id):",
       "                ):",
       U + "a_stale_or_missing_readiness_receipt_refuses_approval"),
    _m("failed_receipt_accepted", "a failed readiness receipt is refused",
       OP, "                or not receipt.ready\n", "",
       U + "a_stale_or_missing_readiness_receipt_refuses_approval"),
    _m("retired_source_accepted", "a receipt of a since-retired source is stale",
       OP, "source.state is not DeploymentState.ready_private", "source.state is None",
       U + "a_stale_or_missing_readiness_receipt_refuses_approval"),
    _m("expected_version_replaced_by_the_current", "the client's expected version is the CAS",
       OP, "output_rate=body.output_rate,\n                expected_version=body.expected_version",
       "output_rate=body.output_rate,\n                expected_version=(await self.pub."
       "operations.reads.listing_versions(alias))[-1].version",
       U + "a_stale_expected_version_is_a_conflict_and_its_refusal_replays"),
    _m("refusal_not_recorded", "a key's refusal is its outcome (same key, same answer)",
       OP, "        if not api.status_of(refused)[2]:", "        if False:",
       U + "a_stale_expected_version_is_a_conflict_and_its_refusal_replays"),
    _m("replayed_refusal_changes_status", "a replayed refusal keeps its status",
       OP, "        raise _CODES.get(op.error.code, errors.Conflict)(\"replayed refusal\")",
       "        raise errors.Forbidden(\"replayed refusal\")",
       U + "a_stale_expected_version_is_a_conflict_and_its_refusal_replays"),
    _m("key_not_bound_to_the_body", "another body under a key is 409",
       OP, "    started = await ops.start(kind, actor, key, input_hash(body))",
       "    started = await ops.start(kind, actor, key, input_hash({}))",
       U + "an_approval_replays_under_its_key_and_conflicts_on_another_body"),
    _m("replay_not_flagged", "a replay says replayed",
       OP, "        return found, True", "        return found, False",
       U + "an_approval_replays_under_its_key_and_conflicts_on_another_body",
       U + "a_rollback_relists_an_earlier_version_and_admitted_jobs_keep_their_pins"),
    _m("crashed_attempt_repeated", "a committed effect is reconciled before any redo",
       OP, "        found = await done(op) if started.replayed else None",
       "        found = None",
       U + "a_first_attempt_that_died_after_committing_is_reconciled_not_repeated"),
    _m("non_lab_revision_treated_as_a_proposal", "only a lab_propose proposal is decided",
       OP, "        if not d or not found:", "        if not d:",
       U + "a_rejection_declines_an_open_proposal_once"),
    _m("alias_split_across_segments", "the alias keeps its slash in the rollback path",
       OP, '@router.post("/operator/v1/listings/{model_id:path}/rollback"',
       '@router.post("/operator/v1/listings/{model_id}/rollback"',
       U + "a_rollback_relists_an_earlier_version_and_admitted_jobs_keep_their_pins"),
    # --- 06a funding and pricing ---------------------------------------------------------------
    _m("fund_not_keyed_by_the_operation", "one allocation per Idempotency-Key",
       OP, "idempotency_key=op.operation_id, reason=body.reason)",
       "idempotency_key=str(uuid.uuid4()), reason=body.reason)",
       U + "a_dev_wallet_grant_is_exact_credit_once_per_key"),
    _m("fund_replay_reported_created", "a replayed allocation is 200, not another 201",
       OP, "        return control.ok(done, 200 if done.replayed else 201)",
       "        return control.ok(done, 201)",
       U + "a_dev_wallet_grant_is_exact_credit_once_per_key"),
    _m("dev_rate_replay_unreadable", "a priced revision's key answers its card again",
       OP, "            return card if card and card.rate_card_version == body.rate_card_version"
           " else None", "            return None",
       U + "a_dev_rate_prices_only_a_validated_private_revision"),
    # --- 06a dev keys -------------------------------------------------------------------------
    _m("dev_membership_checked_after_the_receipt", "a stranger never claims a key in A's scope",
       LC, "        await pub.operations.control.access.require(actor.user_id, provider_org_id, "
           "capability)\n", "",
       D + "only_a_member_developer_issues_and_a_stranger_claims_no_key_scope"),
    _m("api_key_accepted_at_the_lab_door", "only a verified session is a Lab caller",
       LC, '        if actor.audience != "session" or not actor.user_id:',
       "        if not actor.user_id:",
       D + "an_api_key_is_not_a_lab_session"),
    _m("dev_key_on_another_endpoint", "a key is issued for the named endpoint only",
       LC, "                 if d.endpoint_id == endpoint_id and d.environment is Environment.dev",
       "                 if d.environment is Environment.dev",
       D + "an_endpoint_without_a_validated_revision_has_no_key"),
    _m("dev_key_on_an_unvalidated_revision", "only a validated revision is keyed",
       LC, "                 and d.state is DeploymentState.ready_private]", "                 ]",
       D + "an_endpoint_without_a_validated_revision_has_no_key"),
    _m("dev_key_issue_not_recorded", "a replay names the issued key",
       LC, "            await pub.ops.advance(op.operation_id, op.fence,\n"
           "                                  f\"issued:{issued.key_id}:{issued.prefix}\")\n", "",
       D + "a_developer_issues_an_endpoint_scoped_key_once"),
    _m("dev_reads_absent_not_unavailable", "uncomposed reads are 503, never empty",
       LC, "        if pub.credentials is None:\n", "        if False:\n",
       D + "listing_revocation_and_wallet_are_unavailable_until_composed"),
    _m("viewer_reads_dev_keys", "dev keys are a developer's",
       LC, "    async def keys(request: Request, endpoint_id: str, provider_org_id: str = Query()):\n"
           "        await member(request, provider_org_id, Cap.manage_dev_deployment)",
       "    async def keys(request: Request, endpoint_id: str, provider_org_id: str = Query()):\n"
       "        await member(request, provider_org_id, Cap.read_aggregate_health)",
       D + "listing_revocation_and_wallet_are_unavailable_until_composed"),
    _m("dev_key_callable_at_public_listings", "a dev key reaches its private endpoint only",
       "gateway/routes/catalog.py",
       "CredentialAudience.provider_dev: (Visibility.private, DeploymentState.ready_private)",
       "CredentialAudience.provider_dev: (Visibility.public, DeploymentState.active)",
       D + "the_issued_key_calls_only_its_private_endpoint"),
    # --- 06c/06d discovery ------------------------------------------------------------------------
    _m("served_model_not_the_gateways_engine", "the served Marlin entry is byte-identical",
       MO, "    if served:\n        return SERVED", "    if served:\n        return \"ready\"",
       C + "the_served_marlin_document_is_byte_identical_to_b05eb6f4"),
    _m("unrouted_alias_listed", "no route, no listing",
       MO, "    if rows is None or route is None:", "    if rows is None:",
       C + "another_alias_is_hidden_without_a_route"),
    _m("down_route_listed_available", "a down route is explicitly unavailable",
       MO, " \\\n        and route in (SERVED, \"ready\")", "",
       C + "a_routed_approved_alias_is_listed_and_a_down_route_is_unavailable"),
    _m("route_outage_hidden", "a route table outage is unavailable, not absent",
       MO, "        return \"unavailable\"\n\n\ndef price_check", "        return None\n\n\n"
           "def price_check",
       C + "a_routed_approved_alias_is_listed_and_a_down_route_is_unavailable"),
    _m("env_card_pin_applied_to_every_alias", "another alias lists its own approved card",
       MO, "    if served and regime == CREDIT \\", "    if regime == CREDIT \\",
       C + "a_routed_approved_alias_is_listed_and_a_down_route_is_unavailable"),
    _m("other_alias_owned_by_nemostation", "an alias is owned by its provider slug",
       MO, "else serving.public_model_id.partition(\"/\")[0]", "else OWNED_BY",
       C + "a_routed_approved_alias_is_listed_and_a_down_route_is_unavailable"),
    _m("provisional_card_listed", "only an operator-approved card lists another alias",
       MO, "    if not served and provisional(card):", "    if False:",
       C + "a_provisional_card_or_a_private_revision_is_never_listed"),
)

PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pg_lost_cas_rebased_on_the_fresh_version", "two racing approvals: one winner, one 409",
       OP, "            return await self.control.approve(\n"
           "                operator, proposal_id, rate_card_version=body.rate_card_version,\n"
           "                input_rate=body.input_rate, output_rate=body.output_rate,\n"
           "                expected_version=body.expected_version, reason=body.reason)\n",
       "            for attempt in (1, 2):\n"
       "                try:\n"
       "                    return await self.control.approve(\n"
       "                        operator, proposal_id, rate_card_version=body.rate_card_version,\n"
       "                        input_rate=body.input_rate, output_rate=body.output_rate,\n"
       "                        expected_version=body.expected_version if attempt == 1 else (\n"
       "                            await self.pub.operations.reads.listing_versions(alias)\n"
       "                        )[-1].version, reason=body.reason)\n"
       "                except errors.StateConflict:\n"
       "                    if attempt == 2:\n"
       "                        raise\n",
       PG + "two_processes_racing_approvals_publish_one_listing"),
    _m("pg_replay_not_flagged", "0060's receipt replays the approval",
       OP, "        return found, True", "        return found, False",
       PG + "an_approval_replays_through_0060_and_rolls_back"),
    _m("pg_fund_not_keyed_by_the_operation", "grant_credit replays one entry per key",
       OP, "idempotency_key=op.operation_id, reason=body.reason)",
       "idempotency_key=str(uuid.uuid4()), reason=body.reason)",
       PG + "an_approval_replays_through_0060_and_rolls_back"),
    _m("pg_dev_key_issue_not_recorded", "a replay names the issued key (0060 phase)",
       LC, "            await pub.ops.advance(op.operation_id, op.fence,\n"
           "                                  f\"issued:{issued.key_id}:{issued.prefix}\")\n", "",
       PG + "a_dev_key_is_issued_once_on_postgres"),
    # --- WR-AS3-2: PgDevCredentials over 0068's doors ---------------------------------------
    _m("pg_dev_credentials_malformed_id_queried", "a malformed id is not_found, never a 500",
       PUB, "        if not all(_uuid(v)", "        if False and all(_uuid(v)",
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    _m("pg_dev_credentials_key_id_unguarded", "every id is checked, the key's included",
       PUB, 'if k.endswith("_id"))', 'if k.endswith("t_id"))',
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    _m("pg_dev_revoke_actor_dropped", "the revocation is audited as the session's member",
       PUB, '"actor": actor, "idempotency_key"', '"actor": "lab", "idempotency_key"',
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    _m("pg_dev_revoke_unkeyed", "the audit entry carries the request's Idempotency-Key",
       PUB, '"idempotency_key": idempotency_key}', '"idempotency_key": None}',
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    _m("pg_dev_wallet_always_opened", "an unopened dev wallet reads closed, not open at 0",
       PUB, 'opened=row["opened"]', "opened=True",
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    _m("pg_dev_wallet_in_usd", "the dev wallet is CREDIT, never USD",
       PUB, 'unit="CREDIT"', 'unit="USD"',
       PG + "dev_keys_and_the_wallet_answer_from_0068s_doors"),
    # --- WR-AS3-3: the Lab workers' connector -----------------------------------------------
    _m("pg_role_login_switches_role", "a role login (member of nothing) sets no role",
       LW, 'set_role=False if login_user(dsn).startswith("infrx_") else None)',
       "set_role=None)",
       PG + "a_role_login_connects_without_the_switch_and_the_service_login_switches"),
    _m("pg_service_login_never_switches", "the owner login still runs as service_role",
       LW, '.startswith("infrx_") else None)', '.startswith("infrx_") else False)',
       PG + "a_role_login_connects_without_the_switch_and_the_service_login_switches"),
    _m("pg_compose_ignores_the_role_login", "the composed worker uses the role-aware connector",
       LW, "    connect = lab_connector(values[DATABASE])\n",
       "    connect = connector(values[DATABASE])\n",
       PG + "a_role_login_connects_without_the_switch_and_the_service_login_switches"),
)


def case_names() -> set[str]:
    return {name for path in (*UNIT_FILES, PG_FILE)
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The repository's shape, so the PostgreSQL cases find the migrations."""
    api = root / "apps" / "infrx-api"
    for name in (shared.PACKAGE, "tests"):
        shutil.copytree(API_DIR / name, api / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    (root / "apps" / "app").symlink_to(API_DIR.parent / "app")
    return api


RUNNER = Runner(name="ap6", targets=UNIT_FILES)
PG_RUNNER = Runner(name="ap6-pg", targets=(PG_FILE,), env=("INFRX_D_TASK",), layout=_layout,
                   timeout_s=600)


def run_mutant(mutant) -> Result:
    if not any(PG in case for case in mutant.cases):
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


__all__ = ["MUTANTS", "PG_MUTANTS", "Mutant", "Outcome", "case_names", "run_mutant"]

if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-06's mutation list"))
