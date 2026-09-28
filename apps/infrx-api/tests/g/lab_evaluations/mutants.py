#!/usr/bin/env python3
"""R32/R40/R83 for WR-B4-1: one single-edit defect per invariant `/lab/v1/evaluations` claims.

Mutants live in the router and, for the two seam decisions its route cases prove over HTTP
(consumer-only 403, foreign provider 404), in `lab_auth` (lab-api, batch #4).

    uv run --frozen pytest -q tests/g/lab_evaluations/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_evaluations/test_mutants.py
    uv run --frozen python -m tests.g.lab_evaluations.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_evaluations/test_lab_evaluations.py",)
F = "gateway/routes/lab_evaluations.py"
FILES = (F, auth.F)
C = "test_lab_evaluations__"
MOUNT, SESSION = C + "nothing_is_mounted_without_the_switch", \
    C + "every_route_needs_the_session_before_anything_else"
ACCESS, ROLES = C + "a_consumer_only_user_is_denied_and_another_provider_is_not_found", \
    C + "every_role_reads_and_only_run_evaluation_writes"
LAUNCH, RESUME = C + "a_launch_is_two_d7_runs_and_a_resubmit_is_the_same_runs", \
    C + "a_launch_interrupted_between_the_freezes_resumes_as_the_same_runs"
CHECKED, CANCEL = C + "a_launch_is_checked_before_anything_is_written", \
    C + "cancel_stops_a_live_run_and_a_finished_run_is_a_conflict"
RUNS, REPORT = C + "runs_are_the_providers_experiment_and_subscription_runs_verbatim", \
    C + "an_experiment_carries_b2s_report_verbatim"
SUBSCRIBE = C + "subscribe_is_b3s_with_the_catalogs_evaluator_and_the_session_owner"
UNWIRED, BODY = C + "an_unwired_port_is_unavailable_after_the_access_checks", \
    C + "a_body_is_json_bounded_and_valid_before_the_backends"
LAUNCH_ROUTE = ("        who = await actor(request, Cap.run_evaluation)\n"
                "        return lab_auth.ok(await launch(")


def _m(name, invariant, old, new, *cases, file=F) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting ----------------------------------------------------------------------------
    _m("mounted_without_the_switch", "LAB_EVALS off (no rt.lab_evaluations): no route",
       "    if x is None:\n        return None", "    if False:\n        return None", MOUNT),
    _m("runtime_evaluations_ignored", "register(app, rt) mounts over rt.lab_evaluations",
       'getattr(rt, "lab_evaluations", None)', "None", MOUNT),
    # --- identity, access, capability --------------------------------------------------------
    _m("session_skipped", "every call is the forwarded session's user",
       "    user_id = await lab_auth.authenticate(request, sessions)\n",
       '    user_id = request.query_params.get("user_id", "")\n', SESSION),
    _m("launch_body_before_identity", "a launch body is read after the session and membership",
       LAUNCH_ROUTE, "        wanted = await lab_body(request, rt, Launch)\n" + LAUNCH_ROUTE,
       SESSION),
    _m("subscription_body_before_identity", "a subscription body is read after the session",
       "        who = await actor(request, Cap.run_evaluation)\n"
       "        wanted = await lab_body(request, rt, SubscriptionRequest)\n",
       "        wanted = await lab_body(request, rt, SubscriptionRequest)\n"
       "        who = await actor(request, Cap.run_evaluation)\n", SESSION),
    _m("consumer_only_not_denied", "a consumer-only user is a 403 on every route",
       "    if not workspaces:\n", "    if False:\n", ACCESS, file=auth.F),
    _m("foreign_provider_is_forbidden", "another provider's id is a 404 on every route",
       'raise errors.NotFound("no such provider workspace")',
       'raise errors.Forbidden("no such provider workspace")', ACCESS, file=auth.F),
    _m("reads_need_run_evaluation", "every role reads the four listings",
       "            who = await actor(request, Cap.read_aggregate_health)\n",
       "            who = await actor(request, Cap.run_evaluation)\n", ROLES),
    _m("viewer_launches", "a launch needs run_evaluation", LAUNCH_ROUTE,
       LAUNCH_ROUTE.replace("run_evaluation", "read_aggregate_health"), ROLES),
    _m("viewer_cancels", "a cancel needs run_evaluation",
       "        who = await actor(request, Cap.run_evaluation)\n"
       "        return lab_auth.ok(await cancel(",
       "        who = await actor(request, Cap.read_aggregate_health)\n"
       "        return lab_auth.ok(await cancel(", ROLES),
    _m("viewer_subscribes", "a subscription needs run_evaluation",
       "        who = await actor(request, Cap.run_evaluation)\n        wanted = await lab_body(",
       "        who = await actor(request, Cap.read_aggregate_health)\n"
       "        wanted = await lab_body(",
       ROLES),
    # --- EVAL-DURABLE: the launch ------------------------------------------------------------
    _m("run_ids_not_derived", "a run id is derived from (experiment, arm)",
       "    return checkpoints.run_id_of(experiment_id, arm)",
       '    return checkpoints.run_id_of(__import__("uuid").uuid4().hex, arm)', LAUNCH),
    _m("one_serving_for_both_arms", "each arm runs its own serving",
       'getattr(launch, f"{arm}_serving_ref")', "launch.baseline_serving_ref", LAUNCH),
    _m("candidate_not_frozen", "a launch creates both runs",
       "    for arm in ARMS:\n        await runner.freeze(",
       "    for arm in ARMS[:1]:\n        await runner.freeze(", LAUNCH),
    _m("launch_answered_200", "a launch is accepted (202), not done",
       "await lab_body(request, rt, Launch)), 202)", "await lab_body(request, rt, Launch)))",
       LAUNCH),
    _m("freeze_not_as_the_session_user", "B1 schedules as the session's user",
       "evaluator=spec, access=x.access, user_id=who.user_id,",
       "evaluator=spec, access=x.access, user_id=who.provider_org_id,", LAUNCH),
    _m("created_at_not_the_experiments", "both runs carry the experiment's first created_at",
       "                                                row[\"created_at\"]),",
       "                                                now.strftime(\"%Y-%m-%dT%H:%M:%SZ\")),",
       RESUME),
    _m("half_launch_listed", "an experiment without both D7 runs is not listed",
       "    if None in runs:\n        return None", "    if False:\n        return None", RESUME),
    _m("missing_runs_listed", "a run D7 does not hold is not listed",
       "    return [run for run in found if run is not None]", "    return found", RESUME),
    _m("usd_run_limit", "a launch spends CREDIT: another unit is refused before any write",
       '    unit: Literal["CREDIT"]', "    unit: str", CHECKED),
    # --- cancel ------------------------------------------------------------------------------
    _m("finished_run_cancelled", "a finished run is a 409, never flipped to cancelled",
       '    if status["state"] not in LIVE:\n', "    if False:\n", CANCEL),
    _m("running_run_not_cancellable", "a running run can be cancelled",
       'LIVE = ("queued", "running")', 'LIVE = ("queued",)', CANCEL),
    _m("cancel_id_from_the_query", "the cancelled run is the path's",
       'request.path_params["run_id"]', 'request.query_params.get("run_id", "")', CANCEL),
    # --- the listings ------------------------------------------------------------------------
    _m("runs_are_the_experiments", "each listing asks its own operation",
       '("runs", runs)', '("runs", experiments)', RUNS),
    _m("subscription_runs_omitted", "runs include the subscription decisions' runs",
       '    ids += [d["run_id"] for sub in await x.port("ledger").listing(provider)\n'
       '            for d in sub["decisions"] if d["run_id"]]\n', "", RUNS),
    _m("runs_repeated", "a run named twice is listed once",
       "for rid in dict.fromkeys(ids)]", "for rid in ids]", RUNS),
    _m("report_withheld", "B2's stored report is served verbatim",
       '            "report": row["report"]}', '            "report": None}', REPORT),
    # --- subscriptions -----------------------------------------------------------------------
    _m("private_fields_served", "B3's evaluator spec and owner never reach the Lab",
       'PRIVATE = ("evaluator", "owner_user_id")', "PRIVATE = ()", SUBSCRIBE),
    _m("subscription_conflict_unchecked", "another body under a subscription id is a 409",
       '    if stored.model_dump(mode="json", exclude={"owner_user_id"}) != asked:\n',
       "    if False:\n", SUBSCRIBE),
    _m("evaluator_not_the_catalogs", "the subscription's evaluator spec is the catalog's",
       '             "evaluator": spec}', '             "evaluator": {}}', SUBSCRIBE),
    _m("subscribed_with_200", "a subscription is a 201",
       "return lab_auth.ok(await subscribe(x, who, wanted), 201)",
       "return lab_auth.ok(await subscribe(x, who, wanted))", SUBSCRIBE),
    # --- the ports and the body --------------------------------------------------------------
    _m("unwired_is_a_bug", "a port not merged yet is a typed 503, not an AttributeError",
       "        if value is None:                   # expected until its table merges: a 503\n",
       "        if False:\n", UNWIRED),
    _m("content_type_unchecked", "a body is application/json",
       "    intake.check_content_type(request)\n", "", BODY),
    _m("body_unbounded", "a body is bounded by the Lab cap",
       "max_bytes: int = MAX_BODY_BYTES", "max_bytes: int = 1 << 30", BODY),
    _m("invalid_body_escapes", "a body failing validation is a 422, never a 5xx",
       "    except ValidationError:\n", "    except KeyError:\n", BODY),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-evaluations", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-B4-1's evaluations mutation list"))
