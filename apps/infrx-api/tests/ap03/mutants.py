#!/usr/bin/env python3
"""R32/R83 for AP-03: one single-edit defect per decision the console/operator mutations make.

Two lists over the shared runner (`tests/contracts/mutants.py`): `MUTANTS` are killed by the
unit cases (`test_routes.py`, `test_actions.py`); `PG_MUTANTS` by the ap3 PostgreSQL cases
(`test_actions_pg.py`, the two-process races included), in copies that provision their own
container on the key's port - so run them in a process that has not started the harness
itself (`make api-mutants`' own line, `INFRX_D_TASK=ap3`).

    uv run --frozen pytest -q tests/ap03/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03/test_mutants.py
    uv run --frozen python -m tests.ap03.mutants --list
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner

API_DIR = pathlib.Path(__file__).resolve().parents[2]
UNIT_FILES = ("tests/ap03/test_routes.py", "tests/ap03/test_actions.py")
PG_FILE = "tests/ap03/test_actions_pg.py"
A = "console/actions.py"
C = "gateway/routes/console_actions.py"
OP = "gateway/routes/operator_actions.py"
R, U, P = "test_routes__", "test_actions__", "_pg__"


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting and rendering ----------------------------------------------------
    _m("console_mounted_without_a_repository", "default OFF: no repository, no console route",
       C, "    if repo is None:\n        return None\n    router",
       "    if False:\n        return None\n    router",
       R + "nothing_is_mounted_without_the_repository"),
    _m("operator_mounted_without_a_repository", "default OFF: no repository, no operator route",
       OP, "    if repo is None:\n        return None\n    router",
       "    if False:\n        return None\n    router",
       R + "nothing_is_mounted_without_the_repository"),
    _m("validation_left_to_fastapi", "an invalid body is the R270 422 envelope",
       C, "            except RequestValidationError as exc:\n"
          "                failed: Exception = errors.InvalidRequest(f\"{len(exc.errors())} "
          "invalid fields\")\n", "",
       R + "a_body_naming_authority_is_refused_in_the_envelope"),
    _m("refusal_flattened_to_500", "a domain refusal keeps its status",
       C, "                failed = exc\n", "                failed = errors.InternalError()\n",
       R + "a_repository_refusal_is_its_status_in_the_envelope"),
    # --- 03a keys --------------------------------------------------------------------
    _m("replay_answered_201", "a replay is a 200, distinguishable from a creation",
       C, "200 if created.replayed else 201", "201",
       R + "create_returns_201_once_no_store_with_the_secret_and_200_on_replay"),
    _m("create_without_idempotency_key", "a create carries its durable identity",
       C, "idempotency_key: str = Header(alias=IDEMPOTENCY)):\n        created",
       "idempotency_key: str | None = Header(None, alias=IDEMPOTENCY)):\n        created",
       R + "create_requires_an_idempotency_key"),
    _m("body_before_identity", "the actor is established before the body is validated",
       C, "    async def create_key(body: acts.KeyCreate, who: api.Actor = actor,\n"
          "                         idempotency_key: str = Header(alias=IDEMPOTENCY)):\n"
          "        created = await repo.create_key(who,",
       "    async def create_key(body: acts.KeyCreate, request: Request,\n"
       "                         idempotency_key: str = Header(alias=IDEMPOTENCY)):\n"
       "        created = await repo.create_key(await rt.actors.actor(request),",
       R + "identity_comes_before_the_body"),
    _m("revoke_drops_the_idempotency_key", "the revoke's key reaches the repository",
       C, "repo.revoke_key(who, str(key_id), idempotency_key)",
       "repo.revoke_key(who, str(key_id), None)",
       R + "revoke_passes_the_path_key_and_an_optional_idempotency_key"),
    _m("console_actor_any_audience", "a machine credential is not a console session",
       A, "    if actor.audience != \"session\" or not actor.user_id or not actor.org_id:",
       "    if not actor.user_id or not actor.org_id:",
       U + "console_writes_need_a_session_individual_with_an_account"),
    _m("idempotency_key_unbounded", "an oversized key never reaches the database",
       A, "if not value or len(value) > limit or", "if not value or",
       U + "keys_reasons_and_names_are_bounded"),
    _m("idempotency_key_unprintable", "a control character never reaches the database",
       A, " or not value.isprintable()", "",
       U + "keys_reasons_and_names_are_bounded"),
    _m("malformed_key_id_queried", "a malformed key id is not_found before any query",
       A, "        try:\n            key_id = str(uuid.UUID(key_id))\n        except ValueError:\n"
          "            raise errors.NotFound(\"no such key for this account\") from None\n", "",
       U + "a_malformed_key_id_is_not_found_without_a_query"),
    # --- 03b grant ---------------------------------------------------------------------
    _m("claim_takes_a_body", "the claim names no individual but the session's",
       C, "        if await request.body():\n", "        if False:\n",
       R + "the_claim_is_the_actors_and_takes_no_body_authority"),
    # --- 03c feedback --------------------------------------------------------------------
    _m("feedback_off_not_explicit", "feedback off is an explicit 503",
       A, "    if service is None:\n        raise errors.DependencyUnavailable",
       "    if False:\n        raise errors.DependencyUnavailable",
       R + "feedback_is_503_when_the_feature_is_off"),
    _m("feedback_author_is_the_org", "the author is the session's individual",
       A, "key_id=user_id, principal=user_id,", "key_id=user_id, principal=org_id,",
       R + "feedback_is_authored_by_the_actor_on_its_own_account"),
    _m("feedback_operation_unscoped", "feedback replays under 0038's operation",
       A, "operation=FEEDBACK_OPERATION,", "operation=\"feedback\",",
       R + "feedback_is_authored_by_the_actor_on_its_own_account"),
    _m("feedback_digest_ignores_the_value", "a changed signal is a different digest",
       A, "hashlib.sha256(codec.compact_bytes(submission))",
       "hashlib.sha256(submission.name.encode())",
       R + "feedback_is_authored_by_the_actor_on_its_own_account"),
    _m("feedback_body_accepts_provenance", "a provenance field is refused, not dropped",
       A, "class FeedbackCreate(api.Wire):", "class FeedbackCreate(api.Wire.__base__):",
       R + "feedback_refuses_provenance_and_a_missing_key"),
    # --- 03d operator --------------------------------------------------------------------
    _m("route_operator_check_dropped", "a non-operator is refused at the route",
       OP, "        if not actor.operator:\n", "        if False:\n",
       R + "operator_mutations_refuse_a_non_operator_before_the_repository"),
    _m("operator_idempotency_key_optional", "an operator write carries its key",
       OP, "    async def adjust(body: acts.CreditAdjustment, actor: api.Actor = who,\n"
          "                     idempotency_key: str = Header(alias=IDEMPOTENCY)):",
       "    async def adjust(body: acts.CreditAdjustment, actor: api.Actor = who,\n"
       "                     idempotency_key: str | None = Header(None, alias=IDEMPOTENCY)):",
       R + "operator_mutations_need_a_reason_and_an_idempotency_key"),
    _m("adjustment_replay_answered_201", "a replayed adjustment is a 200",
       OP, "200 if done.replayed else 201", "201",
       R + "operator_mutations_reach_the_repository_with_the_actor"),
    _m("repository_operator_check_dropped", "a non-operator is refused at the repository",
       A, "    if actor.audience != \"session\" or not actor.operator or not actor.user_id:",
       "    if actor.audience != \"session\" or not actor.user_id:",
       U + "a_consumer_actor_is_refused_every_operator_write"),
    _m("operator_key_taken_for_a_session", "an operator-audience key is not a session",
       A, "    if actor.audience != \"session\" or not actor.operator or not actor.user_id:",
       "    if not actor.operator or not actor.user_id:",
       U + "an_operator_key_is_not_an_operator_session"),
)

PG_MUTANTS: tuple[Mutant, ...] = (
    # --- 03a keys --------------------------------------------------------------------
    _m("stored_hash_not_the_gateways", "the stored hash is the gateway's sha256 hex",
       A, "                 hash_key(secret)))", "                 hash_key(secret.lower())))",
       "test_keys" + P + "the_first_create_reveals_the_secret_once_and_the_gateway_"
       "authenticates_it"),
    _m("prefix_not_the_apps", "the stored prefix is `sk-infrx-` + 8",
       A, "secret[:PREFIX_CHARS],", "secret[:PREFIX_CHARS + 1],",
       "test_keys" + P + "the_first_create_reveals_the_secret_once_and_the_gateway_"
       "authenticates_it"),
    _m("no_replay_lookup", "a retry answers the recorded key",
       A, "            if prior is not None:\n                if prior[0]",
       "            if False:\n                if prior[0]",
       "test_keys" + P + "a_replay_returns_the_same_key_without_the_secret"),
    _m("replay_ignores_the_body", "another body under a used key is a 409",
       A, "                if prior[0].get(\"request\") != request:\n",
       "                if False:\n",
       "test_keys" + P + "a_different_name_under_the_same_key_is_a_conflict"),
    _m("scope_not_the_account", "the idempotency scope is the account's",
       A, "scope = _scope(CREATE, org_id, ", "scope = _scope(CREATE, \"\", ",
       "test_keys" + P + "the_idempotency_key_is_scoped_to_the_account"),
    _m("no_advisory_lock", "racing instances serialize on the scope",
       A, "            await conn.execute(\"select pg_advisory_xact_lock(hashtextextended(%s, 0))\""
          ", (scope,))\n", "",
       "test_keys" + P + "two_processes_racing_one_idempotency_key_create_exactly_one_key"),
    _m("claims_not_transaction_bound", "the actor is bound inside the write's transaction",
       A, "async with rpc.connection(self._connect) as conn, conn.transaction():",
       "async with rpc.connection(self._connect) as conn:",
       "test_keys" + P + "two_processes_racing_one_idempotency_key_create_exactly_one_key"),
    _m("verification_not_checked", "an unverified individual cannot create a key",
       A, "            if not may[0]:\n", "            if False:\n",
       "test_keys" + P + "an_unverified_individual_cannot_create_a_key"),
    _m("ownership_not_checked", "the actor must own the account it mints into",
       A, "            if may is None or not may[1]:\n", "            if may is None:\n",
       "test_keys" + P + "an_organization_the_actor_does_not_own_is_refused"),
    _m("suspension_not_checked", "a suspended account receives no new key",
       A, "            if may[2]:\n", "            if False:\n",
       "test_keys" + P + "a_suspended_account_revokes_but_cannot_create"),
    _m("revoke_not_account_scoped", "another account's key is not_found",
       A, "            if await self._one(conn, _KEY + \" for update\", (key_id, org_id)) is None:\n"
          "                raise errors.NotFound(\"no such key for this account\")\n", "",
       "test_keys" + P + "revocation_is_idempotent_scoped_and_conflicts_on_a_reused_key"),
    _m("revoke_key_unscoped", "a revoke's Idempotency-Key is audited and conflicts on reuse",
       A, "        scope = None if idempotency_key is None else _scope(",
       "        scope = None if True else _scope(",
       "test_keys" + P + "revocation_is_idempotent_scoped_and_conflicts_on_a_reused_key"),
    _m("unique_violation_untyped", "a reused audit key is a typed 409",
       A, "    if state == \"23505\":", "    if False:",
       "test_keys" + P + "revocation_is_idempotent_scoped_and_conflicts_on_a_reused_key"),
    # --- 03b grant ---------------------------------------------------------------------
    _m("replayed_grant_without_credit", "a replayed claim answers the one grant",
       A, "if status not in (signup.GRANTED, signup.REPLAYED):",
       "if status not in (signup.GRANTED,):",
       "test_grant" + P + "a_claim_grants_10000_credit_once_and_replays",
       "test_grant" + P + "two_processes_claiming_concurrently_land_one_grant"),
    _m("campaign_not_the_apps", "the grant records the App's campaign",
       A, "CAMPAIGN = \"consumer-v1\"", "CAMPAIGN = \"\"",
       "test_grant" + P + "a_claim_grants_10000_credit_once_and_replays"),
    _m("denial_shown_as_credit", "a denied claim carries no credit",
       A, "            return GrantClaim(status=status)\n",
       "            return GrantClaim(status=status, credit=api.Money(amount=\"0\", "
       "unit=\"CREDIT\"))\n",
       "test_grant" + P + "an_unverified_individual_is_not_granted"),
    # --- 03d operator --------------------------------------------------------------------
    _m("operator_reason_rewritten", "the receipt keeps the operator's reason",
       A, "(*args, reason, key)", "(*args, reason.upper(), key)",
       "test_operator" + P + "an_adjustment_applies_once_and_records_actor_and_reason"),
    _m("adjustment_replay_hidden", "a duplicate adjustment says it replayed",
       A, "        return CreditAdjusted(replayed=doc[\"replayed\"],",
       "        return CreditAdjusted(replayed=False,",
       "test_operator" + P + "an_adjustment_applies_once_and_records_actor_and_reason"),
    _m("suspension_replay_hidden", "a duplicate suspension says it replayed",
       A, "suspended=suspended, replayed=doc[\"replayed\"])", "suspended=suspended, replayed=False)",
       "test_operator" + P + "suspension_and_revocation_apply_once_with_an_audit_receipt"),
    _m("operator_refusal_untyped", "the SQL guard's refusal is a typed 403",
       A, "    if state == \"42501\" and str(failed).startswith(\"forbidden:\"):",
       "    if False:",
       "test_operator" + P + "a_forged_operator_actor_is_refused_by_the_database"),
)


def case_names() -> set[str]:
    return {name for path in (*UNIT_FILES, PG_FILE)
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The repository's shape, so the PostgreSQL cases find the migrations: the package,
    tests and pyproject copied to `<tmp>/apps/infrx-api`, `<tmp>/apps/app` linked."""
    api = root / "apps" / "infrx-api"
    for name in (shared.PACKAGE, "tests"):
        shutil.copytree(API_DIR / name, api / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    (root / "apps" / "app").symlink_to(API_DIR.parent / "app")
    return api


RUNNER = Runner(name="ap3", targets=UNIT_FILES)
PG_RUNNER = Runner(name="ap3-pg", targets=(PG_FILE,), env=("INFRX_D_TASK",), layout=_layout,
                   timeout_s=600)


def run_mutant(mutant) -> Result:
    if not any(P in case for case in mutant.cases):
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


__all__ = ["MUTANTS", "PG_MUTANTS", "Mutant", "Outcome", "case_names", "run_mutant"]

if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-03's mutation list"))
