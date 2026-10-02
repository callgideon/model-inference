#!/usr/bin/env python3
"""R32/R83 for AP-05 (API-DEPLOY): one single-edit defect per decision `tests/ap05` claims,
through the shared runner (`tests/contracts/mutants.py`).

`MUTANTS` run the fake world (`-m "not pg"`, no Docker; the box step's in a sandbox).
`PG_MUTANTS` edit what only PostgreSQL and a real engine process exercise (the store's fence,
the receipt order, the local launcher's ownership and stop) and are killed on ap5 (`-m pg`,
`INFRX_D_TASK=ap5`). 0062's own SQL decisions are `tests/d/test_upgrade_0062_mutants.py`.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap05/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/ap05/test_mutants.py
    uv run --frozen python tests/ap05/mutants.py --list
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/ap05/test_hosting.py"
if str(API_DIR) not in sys.path:        # `python tests/ap05/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

H = "lab/hosting/__init__.py"
ST = "lab/hosting/store.py"
EN = "lab/hosting/engine.py"
CT = "lab/hosting/controller.py"
RT = "gateway/routes/lab_deployments.py"
WINDOW = "../../../infra/lab/hosting/window.sh"     # outside the package: the box step


def c(name: str) -> str:
    return f"test_ap05__{name}"


ONE_DRAFT = c("a_deploy_request_is_one_operation_and_one_draft")
DOORS = c("membership_and_role_decide_every_door")
REASONS = c("an_unsupported_request_names_its_reasons")
QUEUED = c("a_queued_deployment_cancels_and_the_controller_retires_it")
RUNNING = c("a_running_deployment_cancelled_is_reconciled_and_torn_down")
CANCEL_SCOPE = c("operations_are_cancelled_only_inside_their_workspace")
RESUME = c("a_controller_stopped_at_each_boundary_resumes_once")
STALE = c("a_stale_fence_writes_nothing")
CAPACITY = c("a_taken_slot_is_capacity_unavailable_never_an_eviction")
FOREIGN = c("foreign_processes_are_never_stopped_or_adopted")
BOX = c("the_box_launcher_runs_only_its_own_unit")
WINDOW_CASE = c("the_box_window_never_runs_two_engines_on_the_gpu")
IDENTITY = c("the_engine_serves_exactly_the_requested_revision")
MISMATCH = c("a_mismatched_identity_never_becomes_ready")
SOURCE = c("the_source_must_hold_the_verified_bytes")
PROFILE = c("the_profile_is_the_measured_marlin_serving_version")
PROMOTES = c("a_passing_smoke_promotes_the_exact_deployment")
SMOKE_FAILS = c("a_failing_smoke_never_promotes")
SWAPPED = c("an_unreachable_or_swapped_engine_never_promotes")
SMOKE_STATE = c("a_smoke_needs_a_validating_deployment_with_nothing_running")
SMOKE_ONCE = c("a_controller_stopped_mid_smoke_never_smokes_twice")
HEALTH = c("a_lost_engine_or_a_stale_check_makes_a_ready_deployment_unavailable")
EXPIRY = c("expired_stuck_and_abandoned_deployments_are_retired")
TERMINAL = c("an_engine_that_never_answers_or_a_late_operation_fails_terminally")
RETIRE = c("retire_waits_for_running_work_and_in_flight_requests")
BOUNDED = c("a_drain_is_bounded")
REAL_DRAIN = c("a_real_in_flight_request_finishes_before_its_engine_stops")
KILLED = c("a_controller_process_killed_at_each_boundary_resumes_once")
ROLE = c("the_hosting_role_hosts_only_its_configured_slot_on_the_box_launcher")
NO_TARGET = c("without_a_target_nothing_is_queued_and_unwired_nothing_mounts")
PORT_HELD = c("a_port_another_process_holds_is_never_shared")
SUPERSEDED = c("a_newer_failed_check_supersedes_an_interrupted_smoke")

MUTANTS: tuple[Mutant, ...] = (
    # --- 05a: the request, the doors, the operation ---------------------------------------
    _m("deploy_without_access", "only a provider developer requests a deployment",
       H, "        provider = await self._member(actor, C.manage_dev_deployment)\n"
       "        if self.target is None:", "        provider = actor.provider_org_id or ''\n"
       "        if self.target is None:", DOORS),
    _m("cancel_without_access", "only a provider developer cancels",
       H, "        await self._member(actor, C.manage_dev_deployment)\n        return (await "
       "self.ops.cancel(", "        return (await self.ops.cancel(", CANCEL_SCOPE),
    _m("reads_without_scope", "a deployment is read only inside its own workspace",
       H, "        if hosting is None or hosting.provider_org_id != provider:",
       "        if hosting is None:", DOORS),
    _m("foreign_serving_deployed", "a provider deploys only its own serving revisions",
       H, "        if serving is None or serving.provider_org_id != provider:",
       "        if serving is None:", REASONS),
    _m("limits_unchecked", "input + output tokens fit the profile's context",
       H, "        if body.max_input_tokens + body.max_output_tokens > PROFILE.max_model_len:",
       "        if False:", REASONS),
    _m("pins_unchecked", "the serving revision pins the profile's runtime and options",
       H, "for name, (have, want) in pins.items() if have != want]",
       "for name, (have, want) in pins.items() if False]", REASONS),
    _m("artifact_unchecked", "only a revision with a verified artifact is installable",
       H, "        if await self.artifact(serving) is None:", "        if False:", REASONS),
    _m("queued_without_target", "no operation is queued that no controller can run",
       H, "        if self.target is None:\n            raise errors.DependencyUnavailable",
       "        if False:\n            raise errors.DependencyUnavailable", NO_TARGET),
    _m("profile_always_configured", "the profile states whether a hosting target exists",
       H, '        state = "configured" if self.target is not None else "unavailable"',
       '        state = "configured"', NO_TARGET),
    _m("smoke_any_state", "a smoke starts only on a validating deployment",
       H, "        if deployment.state is not S.validating:\n            raise",
       "        if False:\n            raise", SMOKE_STATE),
    _m("actions_ignore_running_work", "a smoke is offered only when nothing runs",
       H, '(("smoke",) if deployment.state is S.validating and not active else ())',
       '(("smoke",) if deployment.state is S.validating else ())', SMOKE_STATE),
    _m("readiness_ignores_state", "ready needs the promoted state, not only passing checks",
       H, "        if deployment.state is not S.ready_private:\n            reasons.insert",
       "        if False:\n            reasons.insert", SMOKE_ONCE),
    _m("readiness_ignores_expiry", "an expired deployment is not ready",
       H, '        if hosting.expires_at <= now:\n            reasons.append("expired")',
       '        if False:\n            reasons.append("expired")', EXPIRY),
    _m("unkeyed_deploy", "every mutation takes an Idempotency-Key",
       RT, "    async def deploy(request: Request, body: DeploymentRequest, key: IdempotencyKey):",
       "    async def deploy(request: Request, body: DeploymentRequest, key: str = 'k'):",
       REASONS),
    _m("mounted_unwired", "nothing mounts while rt.lab_hosting is None",
       RT, "    if h is None:\n        return\n", "", NO_TARGET),
    # --- the fake store's rules (0062's in memory; the SQL ones are tests/d's) -------------
    _m("fake_replay_writes", "a replayed request writes no second draft",
       ST, "        if not started.replayed:\n", "        if True:\n", ONE_DRAFT),
    _m("fake_fence_ignored", "a write needs the live lease's fence",
       ST, "        if row.op.fence != hold.fence or row.lease_until is None \\\n",
       "        if row.lease_until is None \\\n", STALE),
    _m("fake_fence_any_deployment", "a lease writes only its own deployment",
       ST, "        if row is None or row.op.resource_kind != \"deployment\" \\\n"
       "                or row.op.resource_id != hold.deployment_revision_id:",
       "        if row is None or row.op.resource_kind != \"deployment\":", STALE),
    _m("fake_slot_shared", "one unreleased allocation per slot",
       ST, "            raise errors.CapacityUnavailable(f\"slot {allocation.slot} is taken\")",
       "            pass", CAPACITY),
    # --- 05b: the controller's allocation and launch ---------------------------------------
    _m("busy_port_shared", "a held port fails the create before anything starts",
       CT, "                    return await self._fail(op, hold, \"port_in_use\", \"another "
       "process holds \"\n                                            \"the candidate's port\", "
       "retryable=True)", "                    return False", PORT_HELD),
    _m("second_engine_started", "a resumed controller finds its engine by tag",
       CT, "            if await self.launcher.inspect(allocation) is None:\n"
       "                try:", "            if True:\n                try:", RESUME),
    _m("promotes_elsewhere", "a passing smoke promotes the revision to ready_private",
       CT, '"ready_private", "hosting: identity and smoke passed")',
       '"retired", "hosting: identity and smoke passed")', PROMOTES),
    _m("cancel_not_reconciled", "a cancel_requested create tears down what it made",
       CT, '        if op.state == "cancel_requested" or d.state is S.retired:',
       "        if d.state is S.retired:", RUNNING),
    _m("capacity_not_retryable", "capacity_unavailable is retryable",
       CT, '"the hosting slot holds another deployment",\n'
       "                                        retryable=True)",
       '"the hosting slot holds another deployment",\n'
       "                                        retryable=False)", CAPACITY),
    _m("failure_keeps_resources", "a failed create releases its slot and retires",
       CT, "        if not keep:\n            await self._teardown(hold)",
       "        if False:\n            await self._teardown(hold)", MISMATCH),
    _m("teardown_keeps_engine", "teardown stops the deployment's engine",
       CT, "        await self.launcher.stop(allocation)\n        self.boundary(\"stopped\")",
       "        self.boundary(\"stopped\")", RUNNING),
    _m("teardown_keeps_model", "teardown removes the installed model",
       CT, "        await asyncio.to_thread(shutil.rmtree, self._dir(allocation), True)\n",
       "", MISMATCH),
    # --- 05c: install and identity ---------------------------------------------------------
    _m("install_trusts_source", "an install checks every copied file's digest",
       EN, "        if sha256(part) != digest:", "        if False:", SOURCE),
    _m("install_keeps_strays", "an install leaves nothing but the manifest's files",
       EN, "        if wanted.get(str(path.relative_to(dest))) is None:\n            path.unlink()",
       "        if wanted.get(str(path.relative_to(dest))) is None:\n            pass", IDENTITY),
    _m("identity_skips_files", "the identity measures the installed bytes",
       CT, "                             files=files, manifest=artifact.files if artifact else ())",
       "                             files=None, manifest=artifact.files if artifact else ())",
       MISMATCH),
    _m("identity_mismatch_succeeds", "an identity mismatch fails the create",
       CT, "        if not receipt.passed:", "        if False:", MISMATCH),
    _m("image_unchecked", "the engine's image digest is the revision's",
       EN, "    if runtime.image != (serving.runtime_image_digest",
       "    if False and runtime.image != (serving.runtime_image_digest", MISMATCH),
    _m("options_unchecked", "the engine's options are the revision's",
       EN, "    if options_digest(runtime.flags) != serving.engine_options_digest:",
       "    if False:", MISMATCH),
    _m("served_name_unchecked", "the engine serves the profile's model name",
       EN, "    if models is None or served_model not in models:", "    if False:", MISMATCH),
    _m("model_dir_unchecked", "the engine loaded the installed directory",
       EN, "    if runtime.model_dir != str(model_dir):", "    if False:", MISMATCH),
    _m("extra_files_allowed", "an unexpected installed file is a mismatch",
       EN, "    for path in sorted(set(want) | set(files)):", "    for path in sorted(want):",
       MISMATCH),
    _m("shard_pins_unchecked", "the shards are the revision's pins",
       EN, "    if shards != list(serving.weight_shard_digests):", "    if False:", MISMATCH),
    _m("profile_flags_drift", "the profile is serve.sh's measured launch",
       H, '"--max-num-seqs", "8",', '"--max-num-seqs", "16",', PROFILE),
    _m("engine_exit_unnoticed", "a candidate that died while loading fails at once",
       CT, "            if await self.launcher.inspect(allocation) is None:\n"
       "                return await self._fail(op, hold, \"engine_exited\",",
       "            if False:\n                return await self._fail(op, hold, \"engine_exited\",",
       TERMINAL),
    _m("launch_unbounded", "a candidate that never answers fails after its window",
       CT, "                if waited > timedelta(seconds=self.launch_timeout_s):",
       "                if False:", TERMINAL),
    _m("no_deadline", "a create or smoke ends within its deadline",
       CT, '            if op.kind != "deployment.retire" and await self._late(op):',
       "            if False:", TERMINAL),
    # --- 05d: the smoke and promotion ------------------------------------------------------
    _m("smoke_failure_promotes", "a failed smoke never promotes",
       CT, "        if not smoke.passed:", "        if False:", SMOKE_FAILS),
    _m("smoke_skips_identity", "a smoke re-checks the identity first",
       CT, "        if not identity.passed:\n            return await self._fail(op, hold, "
       "\"identity_mismatch\",\n                                    \"the engine does not serve "
       "the requested revision\", keep=True)\n", "", SWAPPED),
    _m("smoke_twice", "a resumed smoke re-uses its recorded receipt",
       CT, '        smoke = await self._mine(op, "smoke")\n        if d.state is S.ready_private',
       "        smoke = None\n        if d.state is S.ready_private", SMOKE_ONCE),
    _m("stale_receipt_promotes", "only this operation's current receipts promote",
       CT, "        if gaps(allocation, current, await self.store.db_now(), (\"identity\", "
       "\"smoke\")) or any(", "        if False and any(", SUPERSEDED),
    _m("smoke_status_ignored", "a refused smoke request fails",
       EN, "        if answer.status_code != 200:\n            return observed, [_smoke(\"status\"",
       "        if False:\n            return observed, [_smoke(\"status\"", SMOKE_FAILS),
    _m("smoke_text_only_passes", "a smoke answer carries video tokens",
       EN, '                  ("video", _int(usage.get("prompt_tokens")) >= '
       "MIN_VIDEO_PROMPT_TOKENS,", '                  ("video", True,', SMOKE_FAILS),
    _m("smoke_empty_passes", "a smoke answer is not empty",
       EN, '                  ("content", bool(text.strip()), "an empty answer"),',
       '                  ("content", True, "an empty answer"),', SMOKE_FAILS),
    _m("smoke_unmetered_passes", "a smoke answer reports completion usage",
       EN, '                  ("usage", _int(usage.get("completion_tokens")) >= 1,',
       '                  ("usage", True,', SMOKE_FAILS),
    _m("smoke_abnormal_end_passes", "a smoke answer ends normally",
       EN, '                  ("finish_reason", choice.get("finish_reason") in ("stop", "length"),',
       '                  ("finish_reason", True,', SMOKE_FAILS),
    _m("smoke_unreachable_passes", "an unreachable engine fails the smoke",
       EN, '[_smoke("unreachable", "no engine")]', "[]", SMOKE_FAILS),
    _m("smoke_other_model_passes", "the smoke is answered by the served model",
       EN, '        checks = (("served_model", observed["served_model"] == model,',
       '        checks = (("served_model", True,', SMOKE_FAILS),
    # --- 05e: health, expiry, retirement, drain --------------------------------------------
    _m("retire_skips_cancel", "a retire cancels the deployment's running work first",
       CT, "        for other in others:\n            await self.ops.cancel(",
       "        for other in ():\n            await self.ops.cancel(", RETIRE),
    _m("retire_does_not_wait", "a retire waits for that work to stop",
       CT, "        if others:\n            await self.ops.advance(op.operation_id, hold.fence, "
       "\"waiting\"", "        if False:\n            await self.ops.advance(op.operation_id, "
       "hold.fence, \"waiting\"", RETIRE),
    _m("drain_skipped", "a retire lets in-flight requests finish",
       CT, "                and await self.engine.in_flight(allocation)\n",
       "                and False\n", RETIRE),
    _m("drain_unbounded", "a drain is bounded",
       CT, "                and await self.store.db_now() < _at(op.created_at)\n"
       "                + timedelta(seconds=DRAIN_S)):", "                and True):", BOUNDED),
    _m("in_flight_unread", "drain reads vLLM's running-requests gauge",
       EN, '            if name.startswith("vllm:num_requests_running"):', "            if False:",
       RETIRE),
    _m("no_expiry", "an expired deployment is retired",
       CT, "            if (d.state in (S.draft, S.retired) or now >= hosting.expires_at\n",
       "            if (d.state in (S.draft, S.retired)\n", EXPIRY),
    _m("no_validation_window", "a deployment never smoked is retired after its window",
       CT, "                    or (d.state is S.validating and now >= window)):",
       "                    ):", EXPIRY),
    _m("abandoned_draft_kept", "a draft whose create ended is retired",
       CT, "            if (d.state in (S.draft, S.retired) or now",
       "            if (d.state is S.retired or now", QUEUED),
    _m("no_health", "a launched engine's health is observed",
       CT, "                await self._health(allocation, await self._serving(deployment_id))",
       "                pass", HEALTH),
    _m("health_never_refreshed", "health is observed again before it expires",
       CT, "            if health is None or now >= health.checked_at + "
       "timedelta(seconds=HEALTH_EVERY_S):", "            if health is None:", HEALTH),
    _m("dead_engine_healthy", "no engine under the tag is a mismatch",
       EN, '        bad("runtime", "no engine runs under this deployment\'s tag")\n',
       "", HEALTH),
    # --- the box: its launcher, the role's slot and the window step ------------------------
    _m("box_adopts_any_unit", "the box launcher acts only on its own tag's unit",
       EN, "    async def inspect(self, allocation: Allocation) -> Runtime | None:\n"
       "        if not self._mine(allocation):\n            return None",
       "    async def inspect(self, allocation: Allocation) -> Runtime | None:\n"
       "        if False:\n            return None", BOX),
    _m("box_stops_any_unit", "the box launcher stops only its own tag's unit",
       EN, "    async def stop(self, allocation: Allocation) -> bool:\n"
       "        if not self._mine(allocation):\n            return False",
       "    async def stop(self, allocation: Allocation) -> bool:\n"
       "        if False:\n            return False", BOX),
    _m("box_serving_port", "a candidate never takes the serving engine's port",
       EN, "        if allocation.port == SERVING_PORT:", "        if False:", BOX),
    _m("box_stopped_container_running", "a stopped container is no engine",
       EN, '        if found.returncode != 0 or not doc.get("State", {}).get("Running"):',
       "        if found.returncode != 0:", BOX),
    _m("role_any_port", "the role's slot is a candidate port",
       CT, "    if not (port.isdigit() and 8100 <= int(port) <= 8199):",
       "    if not port.isdigit():", ROLE),
    _m("window_ignores_gateway", "the window opens only in maintenance",
       WINDOW, "    if systemctl is-active --quiet marlin2b-gateway.service; then",
       "    if false; then", WINDOW_CASE),
    _m("window_any_port", "the window's candidate port is never 8000",
       WINDOW, "    [[ $port =~ ^81[0-9][0-9]$ ]]", "    [[ $port =~ ^[0-9]+$ ]]",
       WINDOW_CASE),
    _m("window_installs_before_stop", "the serving engine stops before a candidate exists",
       WINDOW, "    systemctl stop marlin2b-vllm.service\n", "", WINDOW_CASE),
    _m("window_close_stops_serving", "close never touches port 8000",
       WINDOW, '      [ "$port" = 8000 ] && continue\n', "", WINDOW_CASE),
    _m("window_reopens_unhealthy", "close refuses to report a dead serving engine as back",
       WINDOW, "      || die 4 \"marlin2b-vllm did not answer /health: do not reopen the edge\"",
       "      || true", WINDOW_CASE),
)

#: decisions only PostgreSQL or a real engine process exercises (ap5)
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pg_unfenced_writes", "a PostgreSQL write holds the live lease (hosting_fence)",
       ST, '                    await conn.execute("select infrx.hosting_fence(%s)", '
       "(Jsonb({", '                    await conn.execute("select 1 where %s is not null", '
       "(Jsonb({", KILLED),
    _m("pg_receipts_oldest_first", "the newest receipt is read first",
       ST, '" where deployment_revision_id = %s order by recorded desc",',
       '" where deployment_revision_id = %s order by recorded",', KILLED),
    _m("local_adopts_any_pid", "a state file's pid counts only with the tag in its argv",
       EN, "        if stat.rpartition(\")\")[2].split()[0] == \"Z\" or not any(\n"
       "                allocation.resource_tag in a for a in argv):",
       "        if stat.rpartition(\")\")[2].split()[0] == \"Z\":", FOREIGN),
    _m("local_port_unchecked", "a local launch refuses a held port",
       EN, "    async def start(self, allocation: Allocation, model_dir: Path) -> None:\n"
       "        free(allocation.port)\n", "    async def start(self, allocation: Allocation, "
       "model_dir: Path) -> None:\n", PORT_HELD),
    # (never a mutant that leaves an engine running: an orphan on 57557 poisons later runs)
    _m("real_in_flight_ignored", "drain reads the real engine's in-flight gauge",
       EN, "        return int(total)", "        return 0", REAL_DRAIN),
)
FAKE_ONLY = (PROFILE, BOX, WINDOW_CASE, ROLE, NO_TARGET, SUPERSEDED, SMOKE_FAILS, SWAPPED,
             SMOKE_ONCE, EXPIRY, TERMINAL, RETIRE, BOUNDED)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The default copy one level down as `apps/infrx-api`, beside copies of what the suite
    reads from the repository: the migrations, the box step and its helpers, the measured
    serving version (the profile's fixture of truth) and the candidate engine."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, _PLAIN)
    repo = API_DIR.parents[1]
    for part in ("apps/app/supabase/migrations", "infra/lab/hosting",
                 "tests/integration/lab_hosting"):
        shutil.copytree(repo / part, root / part)
    for part in ("infra/rollout/box-lib.sh", "models/marlin2b/serving-version.json",
                 "tests/integration/fake_vllm.py"):
        (root / part).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / part, root / part)
    return api


_PLAIN = Runner(name="ap05-plain")
RUNNER = Runner(name="ap05", targets=(SUITE_FILE,), extra_args=("-m", "not pg"), layout=_layout)
PG_RUNNER = Runner(name="ap05-pg", targets=(SUITE_FILE,), extra_args=("-m", "pg"),
                   env=("INFRX_D_TASK",), layout=_layout)


def _python_only():
    """The shared runner loaded once more by path, its `compile` gated to Python files: the
    box step is bash, and the shared rule "a mutant that does not compile is broken_runner"
    is a Python rule (track I's same answer, `tests/i/mutants.py`)."""
    spec = importlib.util.spec_from_file_location(
        "ap05_shared_mutants", API_DIR / "tests" / "contracts" / "mutants.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.compile = lambda source, filename, mode, *a, **k: (  # type: ignore[attr-defined]
        compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)
    return module


_BASH = _python_only()
BOX_RUNNER = _BASH.Runner(name="ap05-box", targets=(SUITE_FILE,), extra_args=("-m", "not pg"),
                          layout=_layout)


def run_mutant(mutant) -> Result:
    """The PostgreSQL list's cases run unmutated first, once per process (R83 (b)); the box
    step's mutants through the Python-only-compile runner."""
    if mutant.file == WINDOW:
        return _BASH.run_mutant(mutant, BOX_RUNNER)
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-05's mutation list"))
