#!/usr/bin/env python3
"""R32/R83 for I2L: one single-edit defect per decision `tests/i/lab` claims.

The shared runner (`tests/contracts/mutants.py`), loaded as a private copy like track I's, so
its Python-only compile check can be relaxed for the unit, Caddy, JSON and Markdown files
here without changing it for any other list in the same process. The copy carries
`apps/infrx-api/{infrx,tests,deploy}`, `infra/lab/app` and `apps/lab/.env.example` at their
repository paths.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab/test_mutants.py
    uv run --frozen python tests/i/lab/mutants.py --list
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
REPO = API_DIR.parents[1]
SUITE_FILE = "tests/i/lab/test_lab_packaging.py"


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("i2l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)


def _compile_python_only(source, filename, mode, *args, **kwargs):
    """Track I's rule (tests/i/mutants.py): only a `.py` target must compile."""
    return compile(source, filename, mode, *args, **kwargs) if str(filename).endswith(".py") else None


shared.compile = _compile_python_only

U = "deploy/lab/app/infrx-lab-control.service"
S = "deploy/lab/app/lab-control.caddy"
J = "../../infra/lab/app/lab.json"
R = "../../infra/lab/app/README.md"
GATEWAY = "deploy/marlin2b-gateway.service"

ENABLE = "test_i2l__the_control_service_starts_only_once_the_operator_enables_the_lab"
UNLINKED = "test_i2l__no_app_runtime_unit_depends_on_a_lab_unit_or_the_reverse"
PROTECTED = "test_i2l__the_control_service_is_loopback_least_privilege_bounded_and_its_own"
DRAIN = "test_i2l__the_control_service_drains_accepted_operations_before_it_is_killed"
NAMES = "test_i2l__secret_names_only_and_every_name_the_files_read_is_declared"
ORIGINS = "test_i2l__the_lab_has_its_own_origin_and_its_callbacks_are_the_only_allowlist_change"
SITE = "test_i2l__the_lab_site_is_the_control_origin_and_nothing_else"
VALIDATE = "test_i2l__a_broken_or_hijacking_lab_site_never_validates_so_the_app_edge_is_unchanged"
LIVE = "test_i2l__the_live_lab_edge_refuses_consumer_keys_and_is_unavailable_not_open_when_down"
APP_RELEASE = ("test_i2l__an_app_release_that_conflicts_with_the_installed_lab_site_never_"
               "replaces_the_edge")
LIB = "deploy/lib.sh"
ROLLBACK = "test_i2l__lab_rollback_restarts_only_the_lab_on_its_previous_release"
CONTROL_APP = "infrx/lab/control/app.py"
NO_LOOP = "test_i2l__a_lab_worker_that_refuses_to_start_is_not_restarted_in_a_loop"
ONE_COMPOSITION = "test_i2l__the_control_factory_is_the_gateways_one_lab_operations_composition"

MUTANTS: tuple[Mutant, ...] = (
    _m("starts_without_the_marker", "the Lab is off until the operator enables it",
       U, "ConditionPathExists=/etc/infrx-lab/enabled\n", "", ENABLE),
    _m("lab_unit_part_of_the_gateway", "a Lab unit has no lifecycle link to an App unit",
       U, "Wants=network-online.target\n",
       "Wants=network-online.target\nPartOf=marlin2b-gateway.service\n", UNLINKED),
    _m("gateway_wants_the_lab", "no App unit names a Lab unit",
       GATEWAY, "Wants=network-online.target\n",
       "Wants=network-online.target infrx-lab-control.service\n", UNLINKED),
    _m("control_public_bind", "the control service listens on loopback only",
       U, "--host 127.0.0.1 --port 8003", "--host 0.0.0.0 --port 8003", PROTECTED),
    _m("control_in_the_runtime_group", "the control service is outside the runtime's group",
       U, "--user 10003:10003", "--user 10003:10000", PROTECTED),
    _m("control_reads_the_app_env", "the Lab has its own env file, never the App's",
       U, "EnvironmentFile=/etc/infrx-lab-control.env", "EnvironmentFile=/etc/marlin2b-gateway.env",
       PROTECTED),
    _m("control_unbounded", "the control service is bounded so it cannot starve the App",
       U, "  --memory 1g --cpus 1 --pids-limit 128 \\\n", "", PROTECTED),
    _m("grace_outlives_the_stop", "accepted control operations drain before the kill",
       U, "--timeout-graceful-shutdown 25", "--timeout-graceful-shutdown 40", DRAIN),
    _m("a_secret_value_in_the_manifest", "names only: no value in the repository",
       J, '{"name": "INFRX_LAB_DATABASE_URL", "exposure"',
       '{"name": "INFRX_LAB_DATABASE_URL", "value": "postgres://lab:pw@db/lab", "exposure"', NAMES),
    _m("an_undeclared_setting", "every name a Lab file reads is declared",
       J, '      {"name": "INFRX_LAB_CONTROL_SITE", "exposure": "server", "purpose": "the control '
          'origin\'s address in the App edge (default the placeholder above)"},\n', "", NAMES),
    _m("lab_web_holds_the_service_role_key", "the Lab holds no service-role key",
       J, '{"name": "NEXT_PUBLIC_SUPABASE_ANON_KEY", "exposure": "public"',
       '{"name": "SUPABASE_SERVICE_ROLE_KEY", "exposure": "public"', NAMES),
    _m("site_url_moved_to_the_lab", "the App's Site URL is unchanged",
       J, '"site_url": "unchanged"', '"site_url": "https://lab.callbill.ai"', ORIGINS),
    _m("lab_callback_on_the_app_origin", "Lab callbacks sit under the Lab's own origins",
       J, '"production": ["https://lab.callbill.ai/auth/callback**"]',
       '"production": ["https://app.callbill.ai/lab/auth/callback**"]', ORIGINS),
    _m("site_serves_the_lab_web_origin", "the site is the control origin",
       S, "{$INFRX_LAB_CONTROL_SITE:lab-control.callbill.ai}", "{$INFRX_LAB_CONTROL_SITE:lab.callbill.ai}",
       SITE),
    _m("site_address_not_the_operators", "the Lab site's address is its own setting, checked "
       "against the App's before install", S, "{$INFRX_LAB_CONTROL_SITE:lab-control.callbill.ai} {",
       "lab-control.callbill.ai {", VALIDATE),
    _m("consumer_keys_reach_controls", "a consumer /v1 key never reaches a control operation",
       S, r"(?i)^Bearer\s+sk-", r"(?i)^Bearer\s+sk_", LIVE),
    _m("control_down_is_a_bare_502", "the control service down is 'Lab unavailable'",
       S, "handle_errors 502 503 504 {", "handle_errors 404 {", LIVE),
    _m("app_release_installed_unvalidated", "an App release that does not compose with the "
       "installed Lab site never replaces the edge", LIB,
       '|| die "$f does not validate with the pinned Caddy', '|| echo "$f does not validate with the pinned Caddy',
       APP_RELEASE),
    _m("rollback_restarts_the_worker", "a Lab rollback restarts only the Lab",
       R, "sudo systemctl restart infrx-lab-control\n",
       "sudo systemctl restart infrx-lab-control infrx-worker\n", ROLLBACK),
    # 0-F5: a refusing Lab worker (exit 2) is never restarted in a loop
    _m("refusing_annotation_restart_loops", "a refusal (exit 2) is not restarted",
       "deploy/lab/pipelines/infrx-lab-annotation.service", "RestartPreventExitStatus=2\n", "",
       NO_LOOP),
    _m("refusing_judge_restart_loops", "a refusal (exit 2) is not restarted (every unit)",
       "deploy/lab/observe/infrx-lab-judge.service", "RestartPreventExitStatus=2\n",
       "RestartPreventExitStatus=1\n", NO_LOOP),
    _m("dying_eval_never_restarts", "a pass that died (exit 1) still restarts",
       "deploy/lab/eval/infrx-lab-eval.service", "Restart=on-failure\n", "Restart=no\n",
       NO_LOOP),
    # WR-LAB-API-2c: the control factory is the gateway's one L3 composition, on its login
    _m("control_operations_off_the_login", "L3's operations run on the Lab's own login",
       CONTROL_APP, "    operations = lab_operations(connect, access)",
       "    operations = lab_operations(connector(\"\"), access)",
       ONE_COMPOSITION),
    _m("control_operations_other_access", "L3 checks the same L2 access the routes use",
       CONTROL_APP, "    operations = lab_operations(connect, access)",
       "    operations = lab_operations(connect, LabAccess(PgAccessStore(connect)))", ONE_COMPOSITION),
)


# --- LAB-DEPLOY-PREP: the Lab's box rollout steps and the R151 gate (test_lab_rollout_steps.py)
STEPS_FILE = "tests/i/lab/test_lab_rollout_steps.py"
LR = "../../infra/lab/rollout/"
LIB, GATE = LR + "lib.sh", LR + "lab-migrate.sh"
BOX = "../../infra/rollout/box-lib.sh"     # W6: the generic half of lib.sh (at_release, stage_env, place, ...)
ST = LR + "steps/"
STRICT = "test_ldp__every_step_is_strict_bash_on_the_releases_own_helpers"
PREFLIGHT = "test_ldp__preflight_refuses_another_checkout_and_reports_names_never_values"
IMAGE = "test_ldp__the_image_is_built_once_from_the_release_and_its_id_recorded"
UNITS = "test_ldp__units_are_installed_and_none_is_enabled"
CONTROL_ON = "test_ldp__control_on_writes_its_env_by_ssm_name_then_the_switch_then_readiness"
CONTROL_OFF = "test_ldp__control_off_removes_the_switch_and_not_ready_is_exit_4"
ROLE = "test_ldp__a_role_env_file_is_its_switch_and_carries_only_its_names"
STAGED = "test_ldp__a_budget_or_preflight_refusal_replaces_nothing"
REFUSES = "test_ldp__a_role_that_refuses_by_name_is_exit_5_and_other_unreadiness_exit_4"
SMOKE = "test_ldp__the_smoke_checks_every_switch_that_is_on_and_the_app"
REVERT = "test_ldp__revert_turns_every_switch_off_then_the_site_then_checks_the_app"
PRE_W6 = "test_ldp__revert_and_site_off_reload_once_on_the_boxs_pre_w6_lib"
SITE_CASE = "test_ldp__the_site_reaches_the_edge_only_after_it_validates_with_the_apps"
AGREE = "test_ldp__each_role_the_step_enables_can_start_on_the_names_it_allows"
R151 = "test_ldp__the_hosted_lab_apply_needs_all_three_r151_conditions"
TODAY = "test_ldp__todays_hosted_migrate_carries_the_reviewed_patch"
SECRET_CASE = "test_ldp__every_secret_is_refused_as_a_literal"
NEEDS_CASE = "test_ldp__a_spec_without_a_name_the_role_needs_is_refused_before_any_change"

SECRETS = (" LAB_DATABASE_URL LAB_EVAL_ENDPOINT_KEY LAB_ANNOTATION_TEACHER_TOKEN "
           "LAB_TRAINING_CONNECTOR_TOKEN CLICKHOUSE_URL")     # 50-lab-role.sh's `secrets=`

STEP_MUTANTS: tuple[Mutant, ...] = (
    _m("step_not_strict", "every step stops on its first failure", ST + "60-lab-smoke.sh",
       "\nset -euo pipefail\n", "\nset -uo pipefail\n", STRICT),
    _m("preflight_any_checkout", "a step runs only on the RELEASE checkout", BOX,
       '    || die 2 "the checkout $repo is not $RELEASE"', '    || true', PREFLIGHT),
    _m("preflight_prints_values", "names only, never a value", ST + "10-lab-preflight.sh",
       '"env $file: $(cut -d= -f1 "$file" | tr \'\\n\' \' \')"', '"env $file: $(cat "$file")"',
       PREFLIGHT),
    _m("image_rebuilt_every_time", "an existing Lab image is reused", ST + "20-lab-image.sh",
       '2>/dev/null) || id=   # absent: build it', '2>/dev/null) && id=   # absent: build it',
       IMAGE),
    _m("image_not_recorded", "the image id is what the env files take", ST + "20-lab-image.sh",
       'mv -f "$tmp" "$IMAGE_FILE"', 'rm -f "${tmp:?}"', IMAGE),
    _m("units_enabled_on_install", "installing a unit enables nothing", ST + "30-lab-units.sh",
       "systemctl daemon-reload\n", "systemctl daemon-reload\nsystemctl enable infrx-lab-eval.service\n",
       UNITS),
    _m("control_accepts_http", "the control origins are https", ST + "40-lab-control.sh",
       "https='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'", "https='^https?://[A-Za-z0-9./-]+$'",
       CONTROL_ON),
    _m("control_secret_as_argument", "a secret is read by NAME on the box, into the file",
       ST + "40-lab-control.sh", '"INFRX_LAB_DATABASE_URL=$CONTROL_DSN_PARAM"',
       '"INFRX_LAB_DATABASE_URL:=$CONTROL_DSN_PARAM"', CONTROL_ON),
    _m("control_without_the_switch", "the control service runs only with its marker",
       ST + "40-lab-control.sh", 'touch "$MARKER"\n', "", CONTROL_ON),
    _m("env_file_world_readable", "an env file is 0600", BOX, 'chmod 0600 "$tmp"; echo "$tmp"',
       'chmod 0644 "$tmp"; echo "$tmp"', CONTROL_ON, ROLE),
    _m("control_off_keeps_the_marker", "off removes the switch", ST + "40-lab-control.sh",
       'rm -f "${MARKER:?}"; say', 'say', CONTROL_OFF),
    _m("control_unready_passes", "an unready control service is exit 4",
       ST + "40-lab-control.sh", 'ready "$CONTROL_PORT" || die 4', 'ready "$CONTROL_PORT" || true', CONTROL_OFF),
    _m("role_any_name", "a role's env file carries only the names it reads", ST + "50-lab-role.sh",
       '  [[ " $names " == *" $name "* ]] || die 2', '  true || die 2', ROLE),
    _m("role_secret_literal", "a secret is never a literal", ST + "50-lab-role.sh",
       '  [ -z "$literal" ] || [[ $secrets != *" $name "* ]] || die 2',
       '  true || die 2', ROLE),
    _m("role_live_judging", "live judging is not switched on here", ST + "50-lab-role.sh",
       '[ "${spec#*:=}" = dry_run ] || die 2', 'true || die 2', ROLE),
    _m("role_without_a_login", "a role needs its own database login", ST + "50-lab-role.sh",
       '[[ $seen == *" LAB_DATABASE_URL "* ]] || die 2', 'true || die 2', ROLE),
    _m("role_off_keeps_the_switch", "off removes the role's env file", ST + "50-lab-role.sh",
       'rm -f "${LAB_ETC:?}/${role:?}.env"; say "$role OFF', 'say "$role OFF', ROLE),
    _m("role_image_not_the_labs", "INFRX_IMAGE is the Lab image", ST + "50-lab-role.sh",
       '"INFRX_IMAGE:=$image"', '"INFRX_IMAGE:=infrx-runtime"', ROLE),
    _m("budget_skipped", "the pooler budget judges every role before its switch",
       ST + "50-lab-role.sh", '  --lab-env-dir "$budget" || die 3', '  --lab-env-dir "$budget" || true',
       STAGED),
    _m("preflight_skipped", "the paid-adapter roles' preflight judges the staged file",
       ST + "50-lab-role.sh", '"$staged" \\\n    || die 3', '"$staged" \\\n    || true', STAGED),
    _m("refusal_is_a_generic_failure", "a refusal by name is exit 5, apart from exit 4",
       ST + "50-lab-role.sh", '    || die 5 "$role refused by name', '    || die 4 "$role refused by name',
       REFUSES),
    _m("served_refusal_is_a_pending_lane", "only a pending role's exit 2 is exit 5 (LDP-R3)",
       ST + "50-lab-role.sh", 'pending=" checkpoints training rollout "',
       'pending=" checkpoints training rollout eval "', REFUSES),
    _m("role_needs_unchecked", "a SPEC without a name the role needs is refused (LDP-R3)",
       ST + "50-lab-role.sh", '  [[ $seen == *" $need "* ]] || die 2', '  true || die 2', NEEDS_CASE),
    _m("role_needs_drift", "the step's needs are infrx.lab.workers NEEDS (LDP-R3)",
       ST + "50-lab-role.sh", 'needs="JUDGE_PROVIDER_URL CLICKHOUSE_URL S3_TRACE_BUCKET"',
       'needs="JUDGE_PROVIDER_URL S3_TRACE_BUCKET"', AGREE, NEEDS_CASE),
    *(_m(f"secret_literal_{name.lower()}", f"{name} is never a literal (LDP-R2)",
         ST + "50-lab-role.sh", f'secrets="{SECRETS} "', f'secrets="{SECRETS} "'.replace(
             f" {name} ", " "), SECRET_CASE) for name in SECRETS.split()),
    _m("smoke_skips_the_app", "the smoke always checks the App", ST + "60-lab-smoke.sh",
       'check "App gateway" 8001\n', "", SMOKE),
    _m("smoke_passes_unready", "an unready switch fails the smoke", ST + "60-lab-smoke.sh",
       'say "FAIL $1 127.0.0.1:$2/readyz"; bad=1', 'say "FAIL $1 127.0.0.1:$2/readyz"', SMOKE),
    _m("health_port_guessed", "a role is probed on its unit's port", LIB,
       "sed -n 's/.*-e LAB_WORKER_HEALTH_PORT=\\([0-9]*\\).*/\\1/p'", "echo 8010 #", SMOKE, ROLE),
    _m("revert_keeps_a_role_on", "revert turns every role off", ST + "90-lab-revert.sh",
       '  rm -f "${LAB_ETC:?}/${role:?}.env"\n', "", REVERT),
    _m("revert_reloads_before_the_switches", "switches off before the edge", ST + "90-lab-revert.sh",
       'for role in "${ROLES[@]}"; do\n  systemctl disable', 'docker exec caddy caddy reload\n'
       'for role in "${ROLES[@]}"; do\n  systemctl disable', REVERT),
    *(_m(f"pre_w6_lib_no_reload_{n}", "a pre-W6 box lib still reloads the edge once", ST + step,
         "declare -F caddy_reload >/dev/null || caddy_reload()", "declare -F caddy_reload >/dev/null || _unused()",
         PRE_W6) for n, step in (("revert", "90-lab-revert.sh"), ("site", "45-lab-site.sh"))),
    _m("site_installed_unvalidated", "the site reaches the edge only after it validates",
       ST + "45-lab-site.sh", '  || die 4 "the App\'s Caddyfile with the Lab site',
       '  || echo 4 "the App\'s Caddyfile with the Lab site', SITE_CASE),
    _m("site_without_the_import", "the site needs WR-I2L-1's import line", ST + "45-lab-site.sh",
       "  || die 2 \"the live App Caddyfile has no", "  || true \"the live App Caddyfile has no",
       SITE_CASE),
    _m("role_needs_unallowed", "every name a role requires is one the step allows",
       ST + "50-lab-role.sh", "LAB_EVAL_ENDPOINT_URL LAB_EVAL_ENDPOINT_KEY LAB_EVAL_CONCURRENCY",
       "LAB_EVAL_ENDPOINT_URL LAB_EVAL_CONCURRENCY", AGREE, ROLE),
    _m("r151_no_window", "condition 3: an operator window", GATE,
       'stop "condition 3:', 'true "condition 3:', R151),
    _m("r151_any_pending", "condition 2: the reviewed EXPECTED_PENDING", GATE,
       '[ "$current" = "$pending" ] || stop', 'true || stop', R151),
    _m("r151_no_post_check", "condition 2: the W7 post-check names the newest (LDP-R1)", GATE,
       'grep -qF "$post" "$HOSTED_MIGRATE" || stop', 'true || stop', R151),
    _m("r151_post_check_file_name", "the post-check is migrate.py plan's `NNNN name` (LDP-R1)",
       GATE, 'post="*\\"${stem:0:4} ${stem:5}\\"', 'post="*\\"${stem}\\"', TODAY),
    _m("r151_no_known_good", "condition 1: a KNOWN-GOOD target at the newest migration", GATE,
       '  || stop "condition 1:', '  || true "condition 1:', R151),
)

# W6 infra-libs: DT-04 (the env files' owner), DT-15 + INFRA-04(3) + INFRA-11 (70's scrub, ROLE,
# 60's wait), INFRA-04(2) (lab-checkout.sh)
CHECKOUT_SH = LR + "lab-checkout.sh"
SMOKE_WAIT = "test_ldp__the_smoke_waits_thirty_tries_by_default"
STATUS = "test_ldp__status_prints_unit_state_and_drops_value_bearing_lines"
LAB_LOG_CASE = "test_ldp__a_steps_lines_reach_the_lab_log"
CHECKOUT = "test_ldp__the_lab_checkout_fetches_guards_the_engine_pin_and_delegates_once"
STEP_MUTANTS += (
    _m("control_env_root_owned", "the control env file is the unit user's (User=ubuntu reads --env-file)",
       ST + "40-lab-control.sh", 'place "$staged" "$CONTROL_ENV" ubuntu:ubuntu', 'place "$staged" "$CONTROL_ENV" root:root',
       CONTROL_ON),
    _m("role_env_root_owned", "a role env file is the unit user's", ST + "50-lab-role.sh",
       'place "$staged" "$env_file" ubuntu:ubuntu', 'place "$staged" "$env_file" root:root', ROLE),
    _m("env_owner_never_set", "place gives the file the owner it was asked for", BOX,
       'chown "$3" "$1" 2>/dev/null || true', "true", CONTROL_ON, ROLE),
    _m("smoke_ready_s_short", "the smoke waits 30 tries by default (INFRA-11)", ST + "60-lab-smoke.sh",
       "READY_S=${READY_S:-30}", "READY_S=${READY_S:-10}", SMOKE_WAIT),
    _m("status_journal_unscrubbed", "a journal line that could carry a value never reaches the output",
       ST + "70-lab-status.sh",
       " 2>/dev/null | grep -viE 'password|secret|anon_key|bearer|DATABASE_URL=|postgres(ql)?://' || true",
       " 2>/dev/null || true", STATUS),
    _m("status_readiness_unscrubbed", "a readiness line that could carry a value never reaches the output",
       ST + "70-lab-status.sh", "2>&1 | grep -viE 'password|secret|anon_key|bearer|postgres(ql)?://' | head -c 2000",
       "2>&1 | head -c 2000", STATUS),
    _m("status_unit_fixed", "ROLE picks the unit (INFRA-11)", ST + "70-lab-status.sh",
       'unit=${UNIT:-$(basename "$(unit_file "$role")" .service)}', "unit=${UNIT:-infrx-lab-control}", STATUS),
    _m("status_port_fixed", "ROLE picks the unit's health port (INFRA-11)", ST + "70-lab-status.sh",
       'port=${PORT:-$(health_port "$role" 2>/dev/null || true)}', "port=${PORT:-8003}", STATUS),
    # merge #72 lens minors: IL-3 (70 refuses an unknown ROLE), F3 (lib.sh wires the Lab log)
    _m("status_any_role", "70 refuses a ROLE lib.sh does not know (exit 2)", ST + "70-lab-status.sh",
       '*) die 2 "unknown ROLE $role', '*) : "unknown ROLE $role', STATUS),
    _m("lab_log_unwired", "every Lab step's say lines reach the Lab rollout log", LIB,
       "BOX_LOG=$LAB_LOG STEP=${STEP:-lab}", "STEP=${STEP:-lab}", LAB_LOG_CASE),
    _m("checkout_not_strict", "lab-checkout.sh stops on its first failure", CHECKOUT_SH,
       "\nset -euo pipefail\n", "\nset -uo pipefail\n", STRICT),
    _m("checkout_no_fetch", "the integration branch is fetched first", CHECKOUT_SH,
       'g fetch --quiet origin "${BRANCH:-claude/consumer-v1}"\n', "true\n", CHECKOUT),
    _m("checkout_unknown_release", "a RELEASE that is no commit on the branch is refused by name", CHECKOUT_SH,
       'g cat-file -e "$RELEASE^{commit}" || {', "true || {", CHECKOUT),
    _m("checkout_reapplies_the_release", "a checkout already at RELEASE is left alone", CHECKOUT_SH,
       'if [ "$(g rev-parse HEAD)" = "$RELEASE" ]; then', "if false; then", CHECKOUT),
    _m("checkout_engine_unchecked", "a RELEASE whose serve.sh differs is a consumer window", CHECKOUT_SH,
       'g diff --quiet HEAD "$RELEASE" -- models/marlin2b/serve.sh models/marlin2b/serving-version.json',
       'g diff --quiet HEAD "$RELEASE" -- models/marlin2b/serving-version.json', CHECKOUT),
    _m("checkout_as_root", "git runs as the checkout's owner", CHECKOUT_SH,
       'g() { sudo -u ubuntu git -C "$repo" "$@"; }', 'g() { git -C "$repo" "$@"; }', CHECKOUT),
    _m("checkout_not_delegated", "40-checkout.sh does the checkout, once", CHECKOUT_SH,
       'RELEASE="$RELEASE" bash "$repo/infra/rollout/steps/40-checkout.sh"', 'echo "checked out"', CHECKOUT),
)
MUTANTS = MUTANTS + STEP_MUTANTS


def case_names() -> set[str]:
    return {name for file in (SUITE_FILE, STEPS_FILE) for name in
            re.findall(r"^def (test_\w+)\(", (API_DIR / file).read_text(), re.M)}


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules")
    for name in ("infrx", "tests", "deploy"):
        shutil.copytree(API_DIR / name, api / name, ignore=junk)
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    shutil.copytree(REPO / "infra" / "lab" / "app", root / "infra" / "lab" / "app", ignore=junk)
    (root / "apps" / "lab").mkdir(parents=True)
    shutil.copy2(REPO / "apps" / "lab" / ".env.example", root / "apps" / "lab" / ".env.example")
    return api


RUNNER = Runner(name="i2l", targets=(SUITE_FILE,), package="", layout=_layout)


def _steps_layout(root: pathlib.Path) -> pathlib.Path:
    """I2L's copy plus the Lab rollout steps, the role preflight/budget scripts and a git
    checkout (the steps' RELEASE check reads HEAD): the copy is committed once."""
    import subprocess
    api = _layout(root)
    for part in ("infra/lab/rollout", "infra/lab/workers", "apps/app/supabase/migrations"):
        shutil.copytree(REPO / part, root / part, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "infra" / "rollout").mkdir()
    for name in ("hosted-migrate.sh", "box-lib.sh"):          # W6: lib.sh sources box-lib.sh
        shutil.copy2(REPO / "infra/rollout" / name, root / "infra/rollout" / name)
    git = ["git", "-C", str(root), "-c", "user.name=m", "-c", "user.email=m@x"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A", "infra", "apps/infrx-api/deploy", "apps/app"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "copy"], check=True)
    return api


STEPS_RUNNER = Runner(name="ldp", targets=(STEPS_FILE,), package="", layout=_steps_layout)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, STEPS_RUNNER if mutant in STEP_MUTANTS else RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run I2L's mutation list"))
