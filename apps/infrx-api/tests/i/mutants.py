#!/usr/bin/env python3
"""r1 R32/R40 for track I: one single-edit defect per invariant this suite claims.

The shared runner of `tests/contracts/mutants.py` (F2R item 9, IR-A10) with a layout of
I's own, because the mutated file is not in the `infrx` package: it is
`deploy/preflight.py`, plus - for the cases that are claims about the *runtime* - files
under `infrx/`. The copied tree therefore carries `infrx/`, `tests/` and `deploy/` at
`apps/infrx-api`, and `models/marlin2b/serve.sh`, which one case reads as it stands.
What a kill is - and that a crash is not one unless declared - is the shared rule.

    uv run --frozen pytest -q tests/i/test_mutants.py     # the whole list
    uv run --frozen python tests/i/mutants.py --list
    uv run --frozen python tests/i/mutants.py denied_read_tolerated
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO = API_DIR.parents[1]
SUITE = "tests/i"


def _shared():
    """The shared runner, loaded by path: under `--import-mode=importlib` the `tests`
    package is synthesised by pytest and a cross-directory import is not reliably
    available."""
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("i_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SHARED = _shared()
Mutant, Outcome, Result = _SHARED.Mutant, _SHARED.Outcome, _SHARED.Result


def _compile_python_only(source, filename, mode, *args, **kwargs):
    """I2B mutates unit files, Caddyfiles, a Dockerfile and bash scripts as well as
    Python. The shared runner's "a mutant that does not compile is `broken_runner`" rule
    is a Python rule; applied to a unit file it refuses every edit. This keeps the rule
    for `.py` targets and every other rule of the runner unchanged. Track-local until the
    shared runner gates `compile()` on the suffix itself (I2B integration request)."""
    if str(filename).endswith(".py"):
        return compile(source, filename, mode, *args, **kwargs)
    return None


_SHARED.compile = _compile_python_only

P = "deploy/preflight.py"               # relative to apps/infrx-api, not to `infrx`


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- the fail-open defect itself (row O-FAILOPEN) -----------------------------
    _m("denied_read_tolerated", "a denied or unexplained read is never an absent value",
       P, 'if kind == NOT_FOUND and not key.needed(cfg.mode):',
       'if not key.needed(cfg.mode):',
       "test_deploy_failclosed__an_optional_parameter_is_omitted_only_when_absent"),
    _m("unknown_error_is_not_found", "an unrecognised AWS failure is not `not_found`",
       P, "    return UNKNOWN", "    return NOT_FOUND",
       "test_deploy_failclosed__a_denied_or_unexplained_read_installs_nothing",
       "test_deploy_failclosed__a_failed_read_is_classified_before_it_is_tolerated"),
    _m("validation_skipped", "no value is written before it is validated",
       P, "        problem = shape_problem(key, value)", "        problem = None",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_value_cannot_write_a_second_variable"),
    _m("problems_ignored", "a problem stops the install before the file is touched",
       P, "    if problems:\n        report(problems)\n        return REFUSED",
       "    if problems:\n        report(problems)",
       "test_deploy_failclosed__a_required_parameter_that_is_missing_is_a_failure",
       "test_deploy_failclosed__the_old_installer_published_an_open_gateway"),
    # --- shape validation, value by value -----------------------------------------
    _m("empty_value_accepted", "an empty or whitespace-only value is missing",
       P, 'if value == "" or value.strip() == "":', "if False:",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke"),
    _m("newline_injection_accepted", "a value cannot write a second env variable",
       P, "    if any(bad in value for bad in FORBIDDEN_CHARS):", "    if False:",
       "test_deploy_failclosed__a_value_cannot_write_a_second_variable",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke"),
    _m("padding_accepted", "a padded value is refused, never silently trimmed",
       P, "    if value != value.strip():", "    if False:",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke"),
    _m("shape_unchecked", "every value matches its declared shape",
       P, "    if not SHAPES[key.shape](value):", "    if False:",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke"),
    _m("plaintext_identity_url_accepted", "the identity source is https",
       P, r'return bool(re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?/?", value))',
       r'return bool(re.fullmatch(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?/?", value))',
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing"),
    _m("withdrawn_price_key_allowed", "no env file carries a second price authority",
       P, 'for key in WITHDRAWN_KEYS if key in values]', "for key in () if key in values]",
       "test_deploy_failclosed__a_withdrawn_price_key_is_refused"),
    _m("undeclared_key_rendered", "only declared keys reach the env file",
       P, "for name in order if name in values)", "for name in sorted(values))",
       "test_deploy_failclosed__the_manifest_is_the_only_source_of_env_keys"),
    # --- the mode --------------------------------------------------------------------
    _m("unknown_mode_accepted", "there is no default mode and a typo is not a mode",
       P, "    if cfg.mode not in MODES:", "    if False:",
       "test_deploy_failclosed__an_unset_or_unknown_mode_installs_nothing"),
    _m("mode_checked_after_reading", "nothing is read for an unusable mode",
       P, "    if cfg.mode not in MODES:\n        report(",
       "    values, problems = collect(cfg)\n    if cfg.mode not in MODES:\n        report(",
       "test_deploy_failclosed__an_unset_or_unknown_mode_installs_nothing"),
    _m("pilot_key_not_forbidden", "pilot never carries the shared legacy key (R51)",
       P, 'forbidden_in=("pilot",)),\n)', "forbidden_in=()),\n)",
       "test_deploy_failclosed__pilot_never_writes_the_shared_legacy_key"),
    _m("staged_mode_unchecked", "the mode checked is the mode written",
       P, '    if staged_env.get("INFRX_MODE") != mode:', "    if False:",
       "test_deploy_failclosed__the_mode_checked_is_the_mode_written"),
    _m("runtime_not_validated", "the runtime validates the exact staged bytes",
       P, "        validated = validate_runtime(from_env(staged_env))",
       '        validated = "unchecked"',
       "test_deploy_failclosed__the_staged_bytes_are_what_the_runtime_validates",
       "test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime",
       "test_deploy_failclosed__pilot_never_writes_the_shared_legacy_key"),
    _m("composition_gate_removed", "pilot is refused while the pilot routers are absent",
       P, '    if mode == "pilot" and ingress not in composition.ROUTERS:',
       "    if False:",
       "test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed"),
    _m("probe_verdict_ignored", "a runtime refusal stops the install",
       P, '        if not verdict["ok"]:\n            report(verdict["problems"])\n'
          "            return REFUSED",
       '        if not verdict["ok"]:\n            report(verdict["problems"])',
       "test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed",
       "test_deploy_failclosed__the_probe_that_says_nothing_is_a_refusal"),
    _m("unparsable_verdict_passes", "a probe that answers nothing is not a pass",
       P, '        return {"ok": False, "python": None, "mode": cfg.mode, "warnings": [],',
       '        return {"ok": True, "python": None, "mode": cfg.mode, "warnings": [],',
       "test_deploy_failclosed__the_probe_that_says_nothing_is_a_refusal"),
    # --- atomicity, permissions and cleanup ------------------------------------------
    _m("rename_before_validate", "the installed file is replaced only after every check",
       P, "    values, problems = collect(cfg)",
       "    values, problems = collect(cfg)\n"
       "    if values:\n        commit(stage(values, cfg.env_file, cfg.owner), cfg.env_file)",
       "test_deploy_failclosed__a_required_parameter_that_is_missing_is_a_failure",
       "test_deploy_failclosed__the_old_installer_published_an_open_gateway"),
    _m("staged_file_is_world_readable", "the staged file is 0600 before it is renamed",
       P, "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600",
       "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644",
       "test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts",
       "test_deploy_failclosed__a_crash_before_the_rename_changes_nothing"),
    _m("staged_outside_the_target_directory",
       "the staged file shares the target's directory, so the rename is atomic",
       P, "    return target.parent / f\".{target.name}.{os.getpid()}.tmp\"",
       "    return pathlib.Path(\"/tmp\") / f\".{target.name}.{os.getpid()}.tmp\"",
       "test_deploy_failclosed__a_directory_it_cannot_write_installs_nothing"),
    _m("truncating_write_instead_of_staging", "the target is never opened for writing",
       P, "    staged = staged_name(target)", "    staged = target",
       "test_deploy_failclosed__a_write_that_fails_leaves_no_staged_file",
       "test_deploy_failclosed__a_crash_before_the_rename_changes_nothing"),
    _m("failed_write_keeps_the_staged_file", "a failed write leaves nothing behind",
       P, "        staged.unlink(missing_ok=True)\n        raise", "        raise",
       "test_deploy_failclosed__a_write_that_fails_leaves_no_staged_file"),
    _m("stale_staged_file_kept", "a crashed run's staged secret is removed",
       P, "    clear_stale(cfg.env_file)", "    pass",
       "test_deploy_failclosed__a_stale_staged_file_is_never_left_in_place",
       "test_deploy_failclosed__a_crash_before_the_rename_changes_nothing"),
    _m("owner_not_set", "the installed file has its final owner before the rename",
       P, "        if owner:\n            shutil.chown(staged, user=owner)", "        pass",
       "test_deploy_failclosed__the_final_owner_is_set_before_the_rename"),
    # --- restarts ---------------------------------------------------------------------
    _m("one_unit_restarted", "every unit passed to --restart is restarted, in one call",
       P, 'systemctl(cfg, "restart", *cfg.units)', 'systemctl(cfg, "restart", cfg.units[0])',
       "test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts"),
    _m("restart_after_failed_reload", "units are not restarted against an unreloaded unit",
       P, '            failed.append("daemon-reload")\n        elif systemctl(',
       '            failed.append("daemon-reload")\n        if systemctl(',
       "test_deploy_failclosed__a_failed_daemon_reload_never_reaches_the_restart"),
    _m("restart_failure_is_success", "a unit that will not start is not a successful deploy",
       P, "    if failed:", "    if False:",
       "test_deploy_failclosed__a_failed_restart_is_reported_and_not_rolled_back"),
    _m("restart_calls_unrecorded", "the systemctl calls a run made are the ones asserted",
       P, "    cfg.calls.append(\" \".join(args))\n    return subprocess.run([*cfg.systemctl, *args]).returncode",
       "    cfg.calls.append(\" \".join(args))\n    return 0",
       "test_deploy_failclosed__a_failed_restart_is_reported_and_not_rolled_back",
       "test_deploy_failclosed__a_failed_daemon_reload_never_reaches_the_restart"),
    # --- secrets ----------------------------------------------------------------------
    _m("value_passed_as_an_argument", "a secret value never becomes an argument",
       P, '"--mode", cfg.mode, "--env-file", str(staged)]',
       '"--mode", cfg.mode, "--env-file", str(staged), *sorted(read_env(staged).values())]',
       "test_deploy_failclosed__no_secret_value_reaches_stdout_stderr_or_an_argument"),
    _m("value_printed_on_success", "a secret value never reaches stdout",
       P, '    print(f"mode={cfg.mode} keys=" + ",".join(sorted(values)))',
       '    print(f"mode={cfg.mode} " + str(values))',
       "test_deploy_failclosed__no_secret_value_reaches_stdout_stderr_or_an_argument"),
    _m("value_printed_on_refusal", "a refusal names settings, never values",
       P, 'return (f"{key.env}: the value contains a newline, NUL, quote or backslash; "',
       'return (f"{key.env}: {value} contains a newline, NUL, quote or backslash; "',
       "test_deploy_failclosed__a_value_cannot_write_a_second_variable"),
    # --- prerequisites (brief item 4) --------------------------------------------------
    _m("python_pin_lowered", "the pin is 3.12.4 exactly, not whatever is installed",
       P, "REQUIRED_PYTHON = (3, 12, 4)", "REQUIRED_PYTHON = (3, 12, 0)",
       "test_deploy_failclosed__the_runtime_interpreter_must_be_new_enough"),
    _m("import_failure_is_not_fatal", "a runtime that does not import is not a pass",
       P, 'return {"ok": False, "python": version, "mode": mode, "problems": problems,',
       'return {"ok": True, "python": version, "mode": mode, "problems": problems,',
       "test_deploy_failclosed__a_runtime_that_does_not_import_is_not_a_pass"),
    _m("python_pin_dropped", "the runtime interpreter is at least 3.12.4",
       P, "    if sys.version_info[:3] < REQUIRED_PYTHON:", "    if False:",
       "test_deploy_failclosed__the_runtime_interpreter_must_be_new_enough"),
    _m("python_pin_is_only_a_warning", "the interpreter pin is a refusal in pilot",
       P, '(problems if mode == "pilot" else warnings).append(',
       "(warnings if True else problems).append(",
       "test_deploy_failclosed__the_runtime_interpreter_must_be_new_enough"),
    _m("transport_root_may_be_unset", "httpx/httpcore carry an explicit level",
       P, "        if level == logging.NOTSET or level < logging.WARNING:",
       "        if level < logging.WARNING and level != logging.NOTSET:",
       "test_deploy_failclosed__an_unset_transport_level_is_not_good_enough"),
    _m("transport_children_unchecked", "a child transport logger cannot reopen the leak",
       P, '        if not name.startswith(("httpx.", "httpcore.")):\n            continue',
       "        continue",
       "test_deploy_failclosed__a_child_transport_logger_cannot_reopen_the_leak"),
    _m("transport_check_dropped", "a below-WARNING transport logger refuses the install",
       P, "    problems += transport_logger_problems()", "    pass",
       "test_deploy_failclosed__a_transport_logger_below_warning_refuses_the_install"),
    _m("engine_flags_unchecked", "the engine passes no flag the adapter cannot read",
       P, "    problems = [f\"{script} passes {flag}, which the gateway's adapter does not support\"\n"
          "                for flag in FORBIDDEN_ENGINE_FLAGS if flag in text]",
       "    problems = []",
       "test_deploy_failclosed__an_unsupported_engine_flag_refuses_the_install"),
    _m("engine_digest_unchecked", "pilot runs a digest-pinned engine image",
       P, '        if "@sha256:" not in image:', "        if False:",
       "test_deploy_failclosed__the_engine_image_must_be_pinned_by_digest_in_pilot",
       "test_deploy_failclosed__the_repository_engine_script_is_checked_as_it_stands"),
    _m("missing_engine_script_passes", "an unreadable engine script is not an empty one",
       P, '        return [f"{script} does not exist: the engine flags cannot be checked"]',
       "        return []",
       "test_deploy_failclosed__a_missing_engine_script_is_not_a_pass"),
    _m("engine_problems_not_collected", "the engine checks reach the install decision",
       P, "    problems += engine_problems(cfg.serve_script, cfg.mode)", "    pass",
       "test_deploy_failclosed__an_engine_the_adapter_cannot_read_installs_nothing"),
    # review r1 B2: the mode-selective form of the same edit. It survived every case
    # until an apply-level pilot case existed, because the only pilot case that reached
    # `apply` used a pinned script.
    _m("engine_checks_skipped_in_pilot", "the engine checks reach the install decision "
       "in pilot too, where the digest is required",
       P, "    problems += engine_problems(cfg.serve_script, cfg.mode)",
       '    problems += engine_problems(cfg.serve_script, cfg.mode) if cfg.mode != "pilot" else []',
       "test_deploy_failclosed__a_pilot_install_stops_on_the_unpinned_engine_image"),
    # --- claims about the runtime, edited in the copied tree only --------------------
    _m("transport_logs_not_silenced", "the runtime itself silences the transport loggers",
       "infrx/media/fetch.py", "silence_transport_logs()\n", "\n",
       "test_deploy_failclosed__a_transport_logger_below_warning_refuses_the_install"),
    _m("manifest_modes_drift", "the installer and the runtime share one mode vocabulary",
       "infrx/contracts/limits.py", 'MODES = ("dev", "test", "pilot")',
       'MODES = ("dev", "test", "pilot", "prod")',
       "test_deploy_failclosed__the_manifest_is_the_only_source_of_env_keys"),
    _m("unset_mode_refuses", "an unset INFRX_MODE is still legacy behaviour (F2.2 item 14)",
       "infrx/config.py", '        return "legacy"',
       '        raise RuntimeMisconfigured(mode, detail="INFRX_MODE must be set")',
       "test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer",
       # the defect IS the raise: `validate_runtime(Settings())` refusing instead of
       # answering "legacy" (the shared rule makes that kill mode explicit)
       dies_by=("RuntimeMisconfigured",)),
)



# --- I2B.a: packaging (image, units, edge, schema, pilot prerequisites) ------------------
U = "deploy/"
CADDY, MAINT = "deploy/Caddyfile", "deploy/Caddyfile.maintenance"
PACK = "test_backend_deploy__"
MUTANTS += (
    # the runtime image and its probe
    _m("image_not_required_in_pilot", "a pilot runs only the pinned runtime image",
       P, 'Key("INFRX_IMAGE", "runtime image pin (built image id)", "image_id",\n'
          '        required_in=("pilot",)),',
       'Key("INFRX_IMAGE", "runtime image pin (built image id)", "image_id",\n'
       '        required_in=()),',
       "test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image"),
    _m("image_shape_accepts_tags", "an image pin is a content address, never a tag",
       P, '"image_id": _matches(r"sha256:[0-9a-f]{64}"),', r'"image_id": _matches(r"\S+"),',
       "test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image",
       PACK + "every_image_is_pinned_by_digest"),
    _m("probe_on_host_despite_image", "with an image, the probe runs inside it",
       P, "    if cfg.image:\n        return [*cfg.docker,", "    if False:\n        return [*cfg.docker,",
       "test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image"),
    _m("probe_online", "the in-image probe has no network",
       P, '"run", "--rm", "-i", "--network", "none", cfg.image,',
       '"run", "--rm", "-i", "--network", "host", cfg.image,',
       "test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image"),
    _m("image_probe_values_in_argv", "a secret value never becomes an argument of docker",
       P, '"--env-file", "/dev/stdin"]',
       '"--env-file", "/dev/stdin", *sorted(read_env(staged).values())]',
       "test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image"),
    _m("root_runtime_accepted", "a pilot image never serves as root",
       P, "        if os.geteuid() == 0:", "        if False:",
       "test_deploy_failclosed__a_pilot_image_must_be_unprivileged_and_carry_the_worker"),
    _m("worker_entries_unchecked", "a pilot image carries the worker's entry point",
       P, "            if not _importable(entry):", "            if False:",
       "test_deploy_failclosed__a_pilot_image_must_be_unprivileged_and_carry_the_worker"),
    # loopback: the engine and the index are never public
    _m("loopback_engine_url_widened", "the gateway dials the engine on loopback only",
       P, r'"loopback_url": _matches(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):\d{1,5}/?"),',
       r'"loopback_url": _matches(r"https?://[^/]+:\d{1,5}/?"),',
       PACK + "the_runtime_listens_only_on_loopback"),
    _m("loopback_index_widened", "the index is dialled on loopback only",
       P, r'(?:valkey|redis)://(?:127\.0\.0\.1|localhost):\d{1,5}',
       r'(?:valkey|redis)://[^/]+:\d{1,5}',
       PACK + "the_runtime_listens_only_on_loopback"),
    _m("public_engine_bind_allowed", "serve.sh must default to a loopback bind",
       P, "        if not publishes or not all(spec.startswith(LOOPBACK_PUBLISH) for spec in publishes):",
       "        if False:",
       "test_deploy_failclosed__a_public_engine_bind_is_refused"),
    # W3's recorded pin (the named placeholder)
    _m("serving_version_optional", "a pilot needs W3's serving-version record",
       P, "        if not pin.exists():\n            problems.append(",
       "        if not pin.exists():\n            (",
       "test_deploy_failclosed__pilot_needs_w3s_recorded_engine_pin",
       "test_deploy_failclosed__the_repository_engine_script_is_checked_as_it_stands"),
    _m("serving_version_digest_unchecked", "the recorded pin is the digest serve.sh runs",
       P, '        elif "@sha256:" in image and image.partition("@")[2] not in pin.read_text():',
       "        elif False:",
       "test_deploy_failclosed__pilot_needs_w3s_recorded_engine_pin"),
    # disk budget
    _m("disk_budget_skipped", "a pilot host below its disk budget installs nothing",
       P, "        problems += disk_problems(cfg.disk)", "        pass",
       "test_deploy_failclosed__a_host_below_its_disk_budget_installs_no_pilot"),
    _m("disk_budget_compares_wrong", "free space is compared with the budget",
       P, "        if free < need:", "        if free < 0:",
       "test_deploy_failclosed__a_host_below_its_disk_budget_installs_no_pilot"),
    _m("disk_budget_lowered", "the declared budget is the est. allocation of infra §2",
       P, '("/opt/dlami/nvme/processing", 60 * 2**30))',
       '("/opt/dlami/nvme/processing", 6 * 2**30))',
       "test_deploy_failclosed__a_host_below_its_disk_budget_installs_no_pilot"),
    # the config schema
    _m("unknown_setting_accepted", "a name outside the schema is refused",
       P, "        if name not in TUNABLE:", "        if False:",
       "test_deploy_failclosed__a_setting_outside_the_schema_installs_nothing"),
    _m("tunable_shape_unchecked", "a --set value passes the same trust boundary as a read one",
       P, '            problem = shape_problem(Key(name, "tunable", TUNABLE_SHAPES.get(name, "tunable")),\n'
          '                                    value)',
       "            problem = None",
       "test_deploy_failclosed__a_setting_outside_the_schema_installs_nothing"),
    _m("setting_given_twice_accepted", "a tunable is set at most once",
       P, "        elif name in values:", "        elif False:",
       "test_deploy_failclosed__a_setting_outside_the_schema_installs_nothing"),
    _m("tunable_dropped_from_schema", "the schema is every name the runtime reads",
       P, '"TRACE_CONTENT_MAX_DAYS", "TRACE_METADATA_MONTHS", "MAX_ACTIVE_JOBS",',
       '"TRACE_CONTENT_MAX_DAYS", "TRACE_METADATA_MONTHS",',
       PACK + "the_config_schema_is_every_name_the_runtime_reads",
       "test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime",
       "test_deploy_failclosed__a_setting_outside_the_schema_installs_nothing"),
    _m("engine_seqs_zero_accepted", "the engine's sequence count is a positive int",
       P, 'TUNABLE_SHAPES.get(name, "tunable")', '"tunable"',
       "test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime"),
    _m("positive_int_by_isdigit", "a positive int is serve.sh's [1-9][0-9]*, ASCII only",
       P, '    return bool(re.fullmatch(r"[1-9][0-9]*", value))',
       "    return value.isdigit() and int(value) > 0",
       "test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime"),
    _m("tunables_not_rendered", "a tunable reaches the env file",
       P, "    order = [key.env for key in MANIFEST] + list(TUNABLE)",
       "    order = [key.env for key in MANIFEST]",
       "test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime"),
    # the units
    _m("worker_killed_before_drain", "systemd outlasts docker's stop on the worker",
       U + "infrx-worker.service", "TimeoutStopSec=360", "TimeoutStopSec=300",
       "test_ops_recover__every_container_unit_stops_later_than_docker_does"),
    _m("worker_drain_shorter_than_generation", "the worker drain covers one generation",
       U + "infrx-worker.service", "ExecStop=/usr/bin/docker stop -t 330 infrx-worker",
       "ExecStop=/usr/bin/docker stop -t 30 infrx-worker",
       "test_ops_recover__the_worker_drain_outlasts_one_generation"),
    _m("engine_default_drain", "the engine is not stopped with docker's 10 s default",
       U + "marlin2b-vllm.service", "ExecStop=/usr/bin/docker stop -t 30 marlin2b-8000",
       "ExecStop=/usr/bin/docker stop marlin2b-8000",
       "test_ops_recover__every_container_unit_stops_later_than_docker_does"),
    _m("gateway_stops_other_container", "a unit stops its own container",
       U + "marlin2b-gateway.service", "ExecStop=/usr/bin/docker stop -t 120 infrx-gateway",
       "ExecStop=/usr/bin/docker stop -t 120 infrx-worker",
       "test_ops_recover__every_container_unit_stops_later_than_docker_does",
       "test_ops_recover__the_worker_drain_outlasts_one_generation"),
    _m("unit_syntax_broken", "every shipped unit parses as systemd",
       U + "infrx-valkey.service", "RestartSec=3", "RestartSecs=3",
       PACK + "the_units_are_valid_systemd"),
    _m("worker_not_part_of_engine", "an engine stop drains the worker first",
       U + "infrx-worker.service", "PartOf=marlin2b-vllm.service\n", "",
       "test_ops_recover__the_worker_drains_before_the_engine_stops"),
    _m("worker_signal_not_delivered", "the worker's drain signal reaches it (tini)",
       U + "infrx-worker.service", "docker run --rm --init --name infrx-worker",
       "docker run --rm --name infrx-worker",
       "test_ops_recover__the_worker_drains_before_the_engine_stops"),
    _m("gateway_writable_root", "the gateway's root filesystem is read-only",
       U + "marlin2b-gateway.service", "--user 10001:10000 --read-only", "--user 10001:10000",
       PACK + "runtime_containers_run_unprivileged_and_bounded"),
    _m("worker_shares_gateway_uid", "gateway and worker are different identities",
       U + "infrx-worker.service", "--user 10002:10000 --read-only --tmpfs /tmp:rw,size=256m",
       "--user 10001:10000 --read-only --tmpfs /tmp:rw,size=256m",
       PACK + "runtime_containers_run_unprivileged_and_bounded"),
    _m("worker_writes_media", "only the gateway writes the media root",
       U + "infrx-worker.service", "-v ${PROCESSING_CACHE_DIR}:${PROCESSING_CACHE_DIR}:ro",
       "-v ${PROCESSING_CACHE_DIR}:${PROCESSING_CACHE_DIR}",
       PACK + "runtime_containers_run_unprivileged_and_bounded"),
    _m("valkey_capabilities_kept", "the index runs with no capabilities",
       U + "infrx-valkey.service", "--read-only --cap-drop ALL", "--read-only",
       PACK + "runtime_containers_run_unprivileged_and_bounded"),
    _m("gateway_public_bind", "the gateway listens on loopback only",
       U + "marlin2b-gateway.service", "--host 127.0.0.1 --port 8001",
       "--host 0.0.0.0 --port 8001", PACK + "the_runtime_listens_only_on_loopback"),
    _m("valkey_public_bind", "the index listens on loopback only",
       U + "infrx-valkey.service", "--bind 127.0.0.1", "--bind 0.0.0.0",
       PACK + "the_runtime_listens_only_on_loopback"),
    _m("worker_reads_other_env", "every runtime unit reads the validated env file",
       U + "infrx-worker.service", "  --env-file /etc/marlin2b-gateway.env",
       "  --env-file /etc/infrx-worker.env",
       PACK + "one_env_file_configures_every_runtime_unit"),
    # pinned images
    _m("runtime_image_floating", "the units run the pinned image id",
       U + "marlin2b-gateway.service", "${INFRX_IMAGE} uvicorn", "infrx-runtime:latest uvicorn",
       PACK + "every_image_is_pinned_by_digest"),
    _m("dockerfile_base_by_tag", "the runtime base image is pinned by digest",
       U + "Dockerfile", "FROM python:3.12.14-slim-trixie@sha256:"
       "2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9",
       "FROM python:3.12.14-slim-trixie", PACK + "every_image_is_pinned_by_digest"),
    _m("image_runs_as_root", "the image's user is unprivileged",
       U + "Dockerfile", "USER infrx-gateway:infrx", "USER root",
       PACK + "the_image_runs_nothing_as_root"),
    _m("valkey_by_tag", "the index image is pinned by digest",
       U + "infrx-valkey.service", "valkey/valkey@sha256:"
       "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1",
       "valkey/valkey:8.1-alpine", PACK + "every_image_is_pinned_by_digest"),
    _m("install_caddy_unpinned", "the edge image is pinned by digest",
       U + "lib.sh", "caddy@sha256:"
       "14a9c00d4e833ebc2b65d36515b37bde3b73f0b323a2663aaafc88953d8c4e3f", "caddy:2",
       PACK + "every_image_is_pinned_by_digest"),
    # the edge
    _m("admin_api_widened", "the edge's admin API is not on the shared loopback",
       CADDY, "admin unix//config/admin.sock", "admin localhost:2019",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("maintenance_admin_widened", "the maintenance site keeps the admin API off loopback",
       MAINT, "admin unix//config/admin.sock", "admin localhost:2019",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("reload_on_default_admin", "a reload names the admin socket",
       U + "lib.sh", " --address unix//config/admin.sock\n", "\n",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health",
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    _m("metrics_public", "/metrics never leaves the host",
       CADDY, "@private path /metrics /metrics/* /readyz",
       "@private path /readyz", PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("edge_proxies_engine", "the edge never routes to the engine",
       CADDY, "reverse_proxy 127.0.0.1:8001 {\n\t\t\tflush_interval -1",
       "reverse_proxy 127.0.0.1:8000 {\n\t\t\tflush_interval -1",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("health_body_passed_through", "public health is up/down only",
       CADDY, 'respond `{"ok":true}` 200', "copy_response 200",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("edge_body_unbounded", "the edge bounds bodies at MAX_REQUEST_BYTES",
       CADDY, "max_size 96MiB", "max_size 960MiB",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("engine_second_setting_source", "the engine takes its settings from the file only",
       U + "marlin2b-vllm.service", "ExecStart=/home/ubuntu/model-inference/models/marlin2b/serve.sh",
       "ExecStart=/home/ubuntu/model-inference/models/marlin2b/serve.sh --max-num-seqs 32",
       PACK + "the_engine_takes_its_settings_from_the_validated_file"),
    _m("engine_reads_no_settings", "the engine reads the validated file",
       U + "marlin2b-vllm.service", "EnvironmentFile=/etc/marlin2b-gateway.env\n", "",
       PACK + "the_engine_takes_its_settings_from_the_validated_file"),
    _m("media_root_created_by_docker", "the wiped NVMe root is recreated with its owner",
       U + "marlin2b-gateway.service",
       "ExecStartPre=+/usr/bin/install -d -o 10001 -g 10000 -m 2750 ${PROCESSING_CACHE_DIR}\n", "",
       PACK + "the_engine_takes_its_settings_from_the_validated_file"),
    _m("declared_length_unchecked", "a declared oversize is refused before the gateway",
       CADDY, "int({http.request.header.Content-Length}) > 100663296`",
       "int({http.request.header.Content-Length}) > 1006632960`",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("health_down_is_empty", "a down gateway is a sanitized down, not an empty 502",
       CADDY, "handle_errors 502 503 504 {", "handle_errors 599 {",
       PACK + "the_edge_hides_operator_paths_and_sanitizes_health"),
    _m("maintenance_proxies", "maintenance serves nothing from the runtime",
       MAINT, "\thandle {\n\t\theader Content-Type",
       "\thandle {\n\t\treverse_proxy 127.0.0.1:8001\n\t\theader Content-Type",
       PACK + "maintenance_answers_every_request_with_the_retry_envelope"),
    _m("maintenance_retry_mismatch", "the envelope's retry equals Retry-After",
       MAINT, "header Retry-After 120", "header Retry-After 30",
       PACK + "maintenance_answers_every_request_with_the_retry_envelope"),
)

# --- I2B.b: the deploy, drain and rollback scripts ------------------------------------
INSTALL, DRAIN, ROLLBACK, LIB = (U + "install.sh", U + "drain.sh", U + "rollback.sh",
                                 U + "lib.sh")
MUTANTS += (
    _m("install_mode_guard_removed", "an unusable mode leaves no half deploy behind",
       INSTALL, """  *) die "INFRX_MODE must be dev, test or pilot (no default); got '${INFRX_MODE:-}'" 2 ;;""",
       "  *) ;;", "test_deploy_failclosed__only_a_committed_checkout_is_deployed"),
    _m("dirty_checkout_deployed", "the image is built from exactly a commit",
       INSTALL, '[ -z "$(g status --porcelain)" ] || die', ": || die",
       "test_deploy_failclosed__only_a_committed_checkout_is_deployed"),
    _m("git_refuses_root", "root can read ubuntu's checkout (safe.directory)",
       INSTALL, 'g() { git -c safe.directory="$repo" -C "$repo" "$@"; }',
       'g() { git -C "$repo" "$@"; }',
       "test_deploy_failclosed__only_a_committed_checkout_is_deployed"),
    _m("release_unchecked", "HEAD must be the RELEASE the runbook names",
       INSTALL, '[ -z "${RELEASE:-}" ] || [ "$sha" = "$RELEASE" ] || die', ": || die",
       "test_deploy_failclosed__only_a_committed_checkout_is_deployed"),
    _m("preflight_refusal_ignored", "a refused preflight stops the deploy",
       INSTALL, '--region "$REGION" --image "$image" --serve-script "$SERVE_SCRIPT" "${sets[@]}"',
       '--region "$REGION" --image "$image" --serve-script "$SERVE_SCRIPT" "${sets[@]}" || true',
       "test_deploy_failclosed__a_refused_install_changes_nothing_on_the_host"),
    _m("units_before_preflight", "no unit file changes before the env file is validated",
       INSTALL, "# 4. the env file (the only step that reads secrets)",
       'mkdir -p "$UNIT_DIR"; for f in $UNIT_FILES; do put "$here/$f" "$UNIT_DIR/$f"; done\n'
       "# 4. the env file (the only step that reads secrets)",
       "test_deploy_failclosed__a_refused_install_changes_nothing_on_the_host"),
    _m("image_not_pinned_in_env", "the image the probe ran in is the one the units run",
       INSTALL, '--region "$REGION" --image "$image"', '--region "$REGION"',
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed"),
    _m("engine_restarted_by_install", "an install never restarts the engine",
       INSTALL, "else systemctl start marlin2b-vllm; fi", "else systemctl restart marlin2b-vllm; fi",
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed",
       "test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness"),
    _m("runtime_before_engine_health", "the runtime restarts only on a healthy engine",
       INSTALL, 'wait_http http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}"',
       'true http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}"',
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed"),
    _m("edge_opened_unready", "the edge changes only after readiness",
       INSTALL, 'wait_ready "$mode" || die "the runtime did not become ready',
       'wait_ready "$mode" || true "the runtime did not become ready',
       "test_ops_recover__a_runtime_that_is_not_ready_leaves_the_edge_alone"),
    _m("edge_in_dev", "a dev host never serves the public site",
       INSTALL, "unmetered pilot)\nif [ \"$mode\" = pilot ]; then",
       "unmetered pilot)\nif true; then",
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed"),
    _m("edge_unvalidated", "the edge is validated with the pinned Caddy before it serves",
       LIB, '"$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null',
       '"$CADDY_IMAGE" version >/dev/null',
       "test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness"),
    _m("maintenance_site_unvalidated", "the maintenance site is validated before install too",
       LIB, "  for f in Caddyfile Caddyfile.maintenance; do", "  for f in Caddyfile; do",
       "test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness"),
    _m("validate_after_put", "no edge file is written before both sites validate",
       LIB, "  for f in Caddyfile Caddyfile.maintenance; do",
       '  mkdir -p "$CADDY_DIR/infrx"; put "$src/Caddyfile.maintenance" '
       '"$CADDY_DIR/infrx/Caddyfile.maintenance"\n  for f in Caddyfile Caddyfile.maintenance; do',
       "test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness"),
    _m("engine_restart_ignored", "the cutover can restart the engine onto its new pin",
       INSTALL, 'if [ "${ENGINE:-start}" = restart ]; then', "if false; then",
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed"),
    _m("worker_readiness_skipped", "the edge opens only once the worker is ready too",
       LIB, ' && wait_http "$WORKER_READY" "${READY_S:-120}"', "",
       "test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness",
       "test_ops_recover__a_runtime_that_is_not_ready_leaves_the_edge_alone",
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    _m("backup_skipped", "every replaced file is backed up first",
       INSTALL, 'tar -C "${ROOT:-/}" -cpf "$backup/files.tar" --files-from /dev/null "${present[@]}"',
       'tar -C "${ROOT:-/}" -cpf "$backup/files.tar" --files-from /dev/null',
       "test_backend_deploy__a_dev_install_pins_the_image_it_probed",
       "test_ops_recover__rollback_restores_every_replaced_file"),
    _m("refused_backup_kept", "a refused run leaves no copy of the previous secrets",
       INSTALL, '|| { code=$?; rm -rf "$backup"; exit "$code"; }', '|| { code=$?; exit "$code"; }',
       "test_deploy_failclosed__a_refused_install_changes_nothing_on_the_host"),
    _m("backup_dir_world_readable", "the backup directories are root-only",
       INSTALL, 'chmod 0700 "$BACKUPS" "$backup"', 'chmod 0755 "$BACKUPS" "$backup"',
       "test_ops_recover__rollback_restores_every_replaced_file"),
    _m("backup_archive_world_readable", "the backup archive is 0600",
       INSTALL, "( umask 077; tar -C", "( tar -C",
       "test_ops_recover__rollback_restores_every_replaced_file"),
    # drain
    _m("drain_leaves_edge_open", "pause closes the edge first",
       DRAIN, "    caddy_site Caddyfile.maintenance \\\n", "    true \\\n",
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    _m("pause_reload_failure_ignored", "a pause whose edge did not close stops nothing",
       DRAIN, '|| die "the edge did not reload into maintenance',
       '|| true "the edge did not reload into maintenance',
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    _m("maintenance_not_sticky", "maintenance is the active site, surviving a restart",
       LIB, '  put "$CADDY_DIR/infrx/$1" "$CADDY_DIR/Caddyfile"\n', "",
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    _m("resume_opens_edge_unready", "resume opens the edge only after readiness",
       DRAIN, 'wait_ready "$mode" || die "the runtime is not ready; the edge stays',
       'wait_ready "$mode" || true "the runtime is not ready; the edge stays',
       "test_ops_recover__drain_closes_the_edge_before_stopping_the_worker"),
    # rollback
    _m("rollback_to_unmetered_allowed", "a pilot host never rolls back to an unmetered runtime",
       ROLLBACK, 'if [ "$current" = pilot ] && [ "$restored" != pilot ] \\',
       'if false && [ "$restored" != pilot ] \\',
       "test_deploy_failclosed__rollback_never_returns_a_pilot_to_an_unmetered_runtime"),
    _m("attestation_any_nonempty", "only the exact statement unlocks an unmetered rollback",
       ROLLBACK, '[ "${ROLLBACK_TO_UNMETERED:-}" != "no-pilot-request-was-accepted" ]',
       '[ -z "${ROLLBACK_TO_UNMETERED:-}" ]',
       "test_deploy_failclosed__rollback_never_returns_a_pilot_to_an_unmetered_runtime"),
    _m("rollback_engine_restart_ignored", "ENGINE=restart restarts the restored engine first",
       ROLLBACK, 'if [ "${ENGINE:-}" = restart ]; then', "if false; then",
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("rollback_engine_failure_ignored", "a restored engine that is down stops the revert",
       ROLLBACK, '"${ENGINE_READY_S:-900}" \\\n    || die', '"${ENGINE_READY_S:-900}" \\\n    || true',
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("rollback_keeps_new_units", "units that did not exist before are removed",
       ROLLBACK, '  rm -f "${ROOT:-}/$path"', "  :",
       "test_ops_recover__rollback_restores_every_replaced_file"),
    _m("rollback_restores_nothing", "the backed-up files come back",
       ROLLBACK, 'tar -C "${ROOT:-/}" -xpf "$backup/files.tar"', ":",
       "test_ops_recover__rollback_restores_every_replaced_file",
       "test_deploy_failclosed__rollback_never_returns_a_pilot_to_an_unmetered_runtime"),
)

MIGRATE = U + "migrate.py"
MIG = "test_deploy_failclosed__migrate_"
MUTANTS += (
    _m("plan_not_read_only", "the dry run cannot write",
       MIGRATE, '        conn.execute("set transaction_read_only = on")\n', "",
       "test_backend_deploy__migrate_plans_read_only_and_names_every_pending_file"),
    _m("applied_names_hidden", "the plan shows each applied version's recorded name",
       MIGRATE, "f'{v} {applied[v]}'.strip() for v in sorted(applied)",
       "v for v in sorted(applied)",
       "test_backend_deploy__migrate_plans_read_only_and_names_every_pending_file"),
    _m("reviewed_digest_not_enforced", "apply runs only the reviewed plan",
       MIGRATE, "        if digest(plan) != expect:", "        if False:",
       MIG + "applies_only_the_reviewed_plan"),
    _m("digest_blind_to_content", "an edited file is a different plan",
       MIGRATE, 'lines = "".join(f"{version} {name} {hashlib.sha256(body).hexdigest()}\\n"',
       'lines = "".join(f"{version} {name}\\n"',
       MIG + "applies_only_the_reviewed_plan"),
    _m("lock_not_taken", "concurrent migrators are excluded",
       MIGRATE, '        conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))\n', "",
       MIG + "applies_only_the_reviewed_plan"),
    _m("commit_per_file", "the whole plan is one transaction",
       MIGRATE, "                         [values[c] for c in insert])",
       "                         [values[c] for c in insert]); conn.commit()",
       MIG + "applies_only_the_reviewed_plan"),
    _m("failure_not_rolled_back", "a failed file rolls every file back",
       MIGRATE, "any SQL error\n                conn.rollback()\n", "any SQL error\n",
       "test_deploy_failclosed__a_failed_migration_rolls_back_the_whole_plan"),
    _m("failure_reported_as_success", "a failed migration is not exit 0",
       MIGRATE, "                return FAILED", "                return 0",
       "test_deploy_failclosed__a_failed_migration_rolls_back_the_whole_plan"),
    _m("transaction_end_unchecked", "a file that ends the transaction stops the plan",
       MIGRATE, "            if conn.info.transaction_status != TransactionStatus.INTRANS:",
       "            if False:",
       "test_deploy_failclosed__a_migration_that_ends_the_transaction_stops_the_plan"),
    _m("no_history_assumed_empty", "a database without history is not assumed fresh",
       MIGRATE, '    if "version" not in columns:', "    if False:",
       MIG + "refuses_a_history_it_cannot_explain"),
    _m("unknown_version_ignored", "a version the repository lacks is a refusal",
       MIGRATE, "    if unknown:", "    if False:", MIG + "refuses_a_history_it_cannot_explain"),
    _m("gap_filled", "a gap in the applied versions is a refusal",
       MIGRATE, "    if versions[:len(applied)] != sorted(applied):", "    if False:",
       MIG + "refuses_a_history_it_cannot_explain"),
    _m("odd_file_skipped", "a file outside the grammar is a refusal, not a skip",
       MIGRATE, '            raise Refused(f"{path.name}: not a NNNN_name.sql migration")',
       "            continue", MIG + "refuses_a_history_it_cannot_explain"),
    _m("outside_transaction_accepted",
       "a file that cannot run in the plan's transaction is refused at plan",
       MIGRATE, '        if OUTSIDE_TRANSACTION.search(body.decode(errors="replace")):',
       "        if False:", MIG + "refuses_a_history_it_cannot_explain"),
    _m("duplicate_version_accepted", "two files cannot share a version",
       MIGRATE, "    if len(set(versions)) != len(versions):", "    if False:",
       MIG + "refuses_a_history_it_cannot_explain"),
    _m("connect_error_unwrapped", "a connection failure never echoes the DSN",
       MIGRATE, "    except Exception as failure:  # noqa: BLE001 - libpq quotes a malformed DSN",
       "    except ImportError as failure:  # noqa: BLE001 - libpq quotes a malformed DSN",
       "test_deploy_failclosed__a_connection_failure_never_echoes_the_dsn"),
    _m("missing_dsn_attempted", "no DSN is a refusal, never a default connection",
       MIGRATE, "    if not dsn.strip():", "    if False:",
       MIG + "refuses_a_history_it_cannot_explain"),
)

# --- I2B.c: the rollout scripts (paths relative to apps/infrx-api in the copy) ---------
SSM, STEP = "../../infra/rollout/ssm.sh", "../../infra/rollout/steps/"
MUTANTS += (
    _m("ssm_drops_arguments", "a step runs with the arguments it was given",
       SSM, """b64=$( { printf '%s' "$header"; cat "$step"; } | base64 -w0)""",
       """b64=$( { cat "$step"; } | base64 -w0)""",
       "test_backend_deploy__ssm_carries_a_step_byte_for_byte"),
    _m("ssm_ignores_status", "a failed invocation is a failed step",
       SSM, '[ "$status" = Success ]', "true",
       "test_backend_deploy__ssm_carries_a_step_byte_for_byte"),
    _m("ssm_accepts_any_argument", "only NAME=VALUE travels, as an export",
       SSM, """*) echo "not NAME=VALUE: $pair" >&2; exit 2 ;; esac""",
       """*) header+="$pair"$'\\n' ;; esac""",
       "test_backend_deploy__ssm_carries_a_step_byte_for_byte"),
    _m("key_in_curl_argv", "the external check never puts a key in curl's argv",
       "../../infra/rollout/verify-external.sh",
       """auth() { printf 'Authorization: Bearer %s\\n' "$1" > "$hdr"; echo "@$hdr"; }""",
       """auth() { printf 'Authorization: Bearer %s' "$1"; }""",
       "test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line"),
    _m("verify_local_prints_values", "the verify step prints env names, never values",
       STEP + "60-verify-local.sh", 'echo "== env names"; cut -d= -f1 /etc/marlin2b-gateway.env',
       'echo "== env names"; cat /etc/marlin2b-gateway.env',
       "test_backend_deploy__the_read_only_steps_print_names_never_values"),
    _m("inventory_prints_values", "the inventory prints env names, never values",
       STEP + "10-inventory.sh", "then cut -d= -f1 /etc/marlin2b-gateway.env;",
       "then cat /etc/marlin2b-gateway.env;",
       "test_backend_deploy__the_read_only_steps_print_names_never_values"),
    _m("readme_inline_env_value", "the runbook passes env names to docker, never values",
       "../../infra/rollout/README.md", "-e MIGRATE_DATABASE_URL -v",
       "-e MIGRATE_DATABASE_URL=pgpass-literal-0123456789 -v",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret"),
    _m("revert_runtime_before_tree", "the revert restores the tree before the runtime",
       STEP + "90-revert.sh",
       'sudo -u ubuntu git -C "$repo" checkout --quiet --detach "$previous"\nENGINE=restart "$d/rollback.sh"',
       'ENGINE=restart "$d/rollback.sh"',
       "test_ops_recover__the_revert_restores_the_tree_before_the_runtime"),
    _m("revert_engine_not_restarted", "R2 restarts the engine before the gateway that asks it",
       STEP + "90-revert.sh", 'ENGINE=restart "$d/rollback.sh" "$BACKUP"', '"$d/rollback.sh" "$BACKUP"',
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("revert_leaves_edge_in_maintenance", "R2 reopens the edge on the restored runtime",
       STEP + "90-revert.sh", '\n"$d/drain.sh" resume\n', "\n",
       "test_ops_recover__the_r2_revert_reopens_the_edge_on_the_restored_runtime"),
    _m("pause_forgets_previous_head", "the pause records the HEAD a revert returns to",
       STEP + "30-pause.sh",
       'sudo -u ubuntu git -C "$repo" rev-parse HEAD > "/var/backups/infrx/pre-$RELEASE.head"\n', "",
       "test_ops_recover__the_revert_restores_the_tree_before_the_runtime"),
    _m("cutover_without_migration_step", "the cutover needs the step-6 digest",
       STEP + "50-install.sh",
       ': "${MIGRATION_DIGEST:?the plan digest step 6 applied, or nothing-pending}"\n', "",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret",
       "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("cutover_drops_engine_concurrency", "the cutover keeps the box's 32 engine sequences",
       STEP + "50-install.sh", 'INFRX_SET="ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-32} ${INFRX_SET:-}"',
       'INFRX_SET="${INFRX_SET:-}"',
       "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("step_without_release_guard", "a step never runs without its release",
       STEP + "50-install.sh", ': "${RELEASE:?the release commit}"\n', "",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret"),
)


# The copy reproduces the repository's shape, not just the package's: `support.REPO` is
# `API_DIR.parents[1]`, so a flat copy made it `/` and
# `test_deploy_failclosed__the_repository_engine_script_is_checked_as_it_stands` failed in
# every copied tree whatever the edit - which reports `killed` for a mutant that changed
# nothing (review r1 B1). `SELF_TESTS` pins that a no-op mutant naming that case is
# `survived`.
COPY_ROOT = pathlib.Path("apps/infrx-api")


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / COPY_ROOT
    api.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", ".venv")
    for name in ("infrx", "tests", "deploy"):
        shutil.copytree(API_DIR / name, api / name, ignore=ignore)
    engine = root / "models" / "marlin2b"
    engine.mkdir(parents=True)
    shutil.copy2(REPO / "models" / "marlin2b" / "serve.sh", engine / "serve.sh")
    # I2B.c: the rollout scripts one suite file reads, at their repository path
    shutil.copytree(REPO / "infra" / "rollout", root / "infra" / "rollout", ignore=ignore)
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    return api


def _definitions() -> dict[str, str]:
    """case name -> the suite-relative file that defines it."""
    where = {}
    for path in sorted((API_DIR / SUITE).glob("test_*.py")):
        if path.name == "test_mutants.py":
            continue
        for name in re.findall(r"^def (test_\w+)", path.read_text(), re.M):
            where[name] = f"{SUITE}/{path.name}"
    return where


def files_for(cases) -> set[str]:
    where = _definitions()
    return {where[case] for case in cases if case in where}


def case_names() -> set[str]:
    """Every case this suite defines about the installer. `test_mutants.py` is excluded:
    its cases are claims about this list, not about the installer."""
    return set(_definitions())


# `package=""`: a mutant's `file` is relative to `apps/infrx-api`, not to `infrx`.
RUNNER = _SHARED.Runner(name="i0", package="", layout=_layout,
                        targets_for=lambda cases: sorted(files_for(cases)))


def run_mutant(mutant) -> Result:
    return _SHARED.run_mutant(mutant, RUNNER)


def main() -> int:
    return _SHARED.main(MUTANTS, RUNNER, "run track I's mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
