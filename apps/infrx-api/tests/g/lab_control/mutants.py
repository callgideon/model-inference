#!/usr/bin/env python3
"""R32/R40/R83 for WR-L4-1: one single-edit defect per invariant `/lab/v1/control` claims.

Mutants live in the router and, for the two seam decisions its route cases prove over HTTP
(consumer-only 403, foreign provider 404, expected refusals unlogged), in `lab_auth`.

    uv run --frozen pytest -q tests/g/lab_control/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_control/test_mutants.py
    uv run --frozen python -m tests.g.lab_control.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_control/test_lab_control.py",)
F = "gateway/routes/lab_control.py"
FILES = (F, auth.F)
C = "test_lab_control__"
ACTOR = ("        user_id = await lab_auth.authenticate(request, control.sessions)\n"
         "        membership = await lab_auth.member(\n"
         "            control.access, user_id, request.query_params.get(\"provider_org_id\", \"\"), "
         "capability)\n")
READ = "            who = await actor(request, Cap.read_aggregate_health)\n"


def _m(name, invariant, old, new, *cases, file=F) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting --------------------------------------------------------------------------
    _m("mounted_without_a_control", "LAB_CONTROL off (no rt.lab_control): no route",
       "    if control is None:\n        return None", "    if False:\n        return None",
       C + "nothing_is_mounted_without_a_control"),
    _m("runtime_control_ignored", "register(app, rt) mounts over rt.lab_control",
       'getattr(rt, "lab_control", None)', "None",
       C + "nothing_is_mounted_without_a_control"),
    # --- identity and the actor --------------------------------------------------------------
    _m("session_skipped", "every call is the forwarded session's user",
       "        user_id = await lab_auth.authenticate(request, control.sessions)\n",
       '        user_id = request.query_params.get("user_id", "")\n',
       C + "every_route_needs_the_session_before_anything_else"),
    _m("body_before_identity", "the body is read only after the session and membership",
       "        who = await actor(request, Cap.manage_dev_deployment)\n"
       "        registration = await body(request, Registration)\n",
       "        registration = await body(request, Registration)\n"
       "        who = await actor(request, Cap.manage_dev_deployment)\n",
       C + "every_route_needs_the_session_before_anything_else"),
    _m("actor_role_not_the_memberships", "the actor's role is the current membership's",
       "user_id=user_id,\n                     role=membership.role)",
       "user_id=user_id,\n                     role=ProviderRole.administrator)",
       C + "the_actor_is_the_sessions_membership_never_the_body"),
    _m("body_extras_ignored", "a body naming a provider, user or role is refused",
       '    model_config = ConfigDict(frozen=True, extra="forbid")',
       '    model_config = ConfigDict(frozen=True, extra="ignore")',
       C + "the_actor_is_the_sessions_membership_never_the_body"),
    _m("actor_cached", "nothing is cached: a revocation refuses the next call",
       ACTOR,
       "        user_id = await lab_auth.authenticate(request, control.sessions)\n"
       "        membership = actor.__dict__.get(user_id) or actor.__dict__.setdefault(\n"
       "            user_id, await lab_auth.member(\n"
       "            control.access, user_id, request.query_params.get(\"provider_org_id\", \"\"), "
       "capability))\n",
       C + "a_revoked_membership_is_refused_on_the_next_call"),
    _m("consumer_only_not_denied", "a consumer-only user is a 403 on every route",
       "    if not workspaces:\n", "    if False:\n",
       C + "a_consumer_only_user_is_denied_on_every_route", file=auth.F),
    _m("foreign_provider_is_forbidden", "another provider's id is a 404 on every route",
       'raise errors.NotFound("no such provider workspace")',
       'raise errors.Forbidden("no such provider workspace")',
       C + "another_providers_workspace_is_not_found_on_every_route", file=auth.F),
    # --- capabilities ------------------------------------------------------------------------
    _m("reads_need_development", "a viewer reads models, deployments and proposals",
       READ, READ.replace("read_aggregate_health", "manage_dev_deployment"),
       C + "each_operation_needs_its_capability"),
    _m("health_needs_development", "a viewer reads aggregate health",
       "        who = await actor(request, Cap.read_aggregate_health)\n        rows",
       "        who = await actor(request, Cap.manage_dev_deployment)\n        rows",
       C + "each_operation_needs_its_capability"),
    _m("viewer_registers", "registering needs manage_dev_deployment",
       "        who = await actor(request, Cap.manage_dev_deployment)\n        registration",
       "        who = await actor(request, Cap.read_aggregate_health)\n        registration",
       C + "each_operation_needs_its_capability"),
    _m("viewer_smokes", "a smoke run needs manage_dev_deployment",
       "        who = await actor(request, Cap.manage_dev_deployment)\n        tested",
       "        who = await actor(request, Cap.read_aggregate_health)\n        tested",
       C + "each_operation_needs_its_capability"),
    _m("developer_proposes", "only an administrator proposes a publication",
       "Cap.propose_publication", "Cap.manage_dev_deployment",
       C + "each_operation_needs_its_capability"),
    # --- the operations and their answers -----------------------------------------------------
    _m("every_listing_is_models", "each listing asks its own operation",
       "getattr(operations(), name)(who)", 'getattr(operations(), "models")(who)',
       C + "each_operation_needs_its_capability"),
    _m("smoke_id_from_the_query", "the smoked revision is the path's",
       'request.path_params["deployment_revision_id"]',
       'request.query_params.get("deployment_revision_id", "")',
       C + "each_operation_needs_its_capability"),
    _m("registered_with_200", "a registration is a 201",
       "lab_auth.ok(created.model_dump(mode=\"json\"), 201)",
       "lab_auth.ok(created.model_dump(mode=\"json\"))",
       C + "each_operation_needs_its_capability"),
    _m("proposed_with_200", "a proposal is a 201",
       "lab_auth.ok(proposal.model_dump(mode=\"json\"), 201)",
       "lab_auth.ok(proposal.model_dump(mode=\"json\"))",
       C + "each_operation_needs_its_capability"),
    _m("refusal_not_rendered", "L3's typed refusal is port.ts's reason, never a 5xx",
       "        except Exception as exc:                 # noqa: BLE001 - rendered, never re-raised\n"
       "            return refusal(exc)\n",
       "        except errors.DependencyUnavailable as exc:\n            return refusal(exc)\n",
       C + "operation_refusals_are_the_lab_ports_reasons", file=auth.F),
    _m("expected_refusal_logged", "an expected refusal (L3 unwired) is not logged as a bug",
       "    if not isinstance(exc, errors.DomainError):", "    if True:",
       C + "without_l3_wired_operations_are_unavailable_and_health_is_not", file=auth.F),
    _m("unwired_is_a_bug", "before L3 the operations are a typed 503, not an AttributeError",
       "        if control.operations is None:      # expected before L3 merges: a 503, not a bug\n",
       "        if False:\n",
       C + "without_l3_wired_operations_are_unavailable_and_health_is_not"),
    _m("health_from_the_operations", "aggregate health is L2's closed record",
       "        rows = await control.access.aggregates(who.user_id, who.provider_org_id)\n",
       "        rows = await operations().deployments(who)\n",
       C + "without_l3_wired_operations_are_unavailable_and_health_is_not",
       C + "aggregates_carry_no_customer_identity"),
    # --- the body ------------------------------------------------------------------------------
    _m("content_type_unchecked", "a body is application/json",
       "        intake.check_content_type(request)\n", "",
       C + "a_body_is_json_bounded_and_valid_before_the_operations"),
    _m("body_bounded_by_the_chat_cap", "a control body is bounded by its own cap",
       "max_bytes=MAX_BODY_BYTES", "max_bytes=limits.max_request_bytes",
       C + "a_body_is_json_bounded_and_valid_before_the_operations"),
    _m("invalid_body_escapes", "a body failing validation is a 422, never a 5xx",
       "        except ValidationError:\n", "        except KeyError:\n",
       C + "a_body_is_json_bounded_and_valid_before_the_operations"),
    _m("digest_unchecked", "an artifact digest is sha256:<64 hex>",
       "    artifact_digest: str = Field(pattern=DIGEST)", "    artifact_digest: str",
       C + "a_body_is_json_bounded_and_valid_before_the_operations"),
    _m("proposal_kind_open", "a proposal is publish or rollback",
       '    kind: Literal["publish", "rollback"]\n    deployment_revision_id: str = Field(',
       '    kind: str\n    deployment_revision_id: str = Field(',
       C + "a_body_is_json_bounded_and_valid_before_the_operations"),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-control", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-L4-1's control mutation list"))
