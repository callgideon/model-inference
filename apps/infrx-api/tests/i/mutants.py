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


REGIME = "test_deploy_failclosed__pilot_refuses_a_regime_or_card_it_cannot_serve"
RELEASE_CASE = "test_deploy_failclosed__a_pilot_env_carries_the_release_install_sh_deploys"


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
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke",
       # the refusal-message case reads `"…" in shape_problem(...)`: with the guard gone
       # the refusal is `None` and the membership test raises TypeError - the missing
       # refusal, observed as a crash (IR: make it `(shape_problem(...) or "")`)
       dies_by=("TypeError",)),
    _m("padding_accepted", "a padded value is refused, never silently trimmed",
       P, "    if value != value.strip():", "    if False:",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke",
       # the refusal-message case reads `"…" in shape_problem(...)`: with the guard gone
       # the refusal is `None` and the membership test raises TypeError - the missing
       # refusal, observed as a crash (IR: make it `(shape_problem(...) or "")`)
       dies_by=("TypeError",)),
    _m("shape_unchecked", "every value matches its declared shape",
       P, "    if not SHAPES[key.shape](value):", "    if False:",
       "test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing",
       "test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke",
       # the refusal-message case reads `"…" in shape_problem(...)`: with the guard gone
       # the refusal is `None` and the membership test raises TypeError - the missing
       # refusal, observed as a crash (IR: make it `(shape_problem(...) or "")`)
       dies_by=("TypeError",)),
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
       "test_deploy_failclosed__pilot_passes_the_composition_gate_once_the_ingress_is_composed"),
    _m("probe_verdict_ignored", "a runtime refusal stops the install",
       P, '        if not verdict["ok"]:\n            report(verdict["problems"])\n'
          "            return REFUSED",
       '        if not verdict["ok"]:\n            report(verdict["problems"])',
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
    # E4B's served-build check (CUTOVER item 7): the release install.sh deploys
    _m("release_not_required_in_pilot", "a pilot env carries the deployed commit",
       P, '    Key("INFRX_RELEASE_SHA", "deployed commit (install.sh RELEASE)", "git_sha",\n'
          '        required_in=("pilot",)),',
       '    Key("INFRX_RELEASE_SHA", "deployed commit (install.sh RELEASE)", "git_sha",\n'
       '        required_in=()),', RELEASE_CASE),
    _m("release_shape_unchecked", "the deployed commit is a 40-hex id",
       P, '    "git_sha": _matches(r"[0-9a-f]{40}"),', '    "git_sha": _matches(r"\\S+"),',
       RELEASE_CASE),
    _m("release_shape_accepts_a_short_id", "the deployed commit is whole, never a prefix",
       P, '    "git_sha": _matches(r"[0-9a-f]{40}"),', '    "git_sha": _matches(r"[0-9a-f]{7,40}"),',
       RELEASE_CASE),
    _m("release_not_supplied", "the installer supplies the commit it deploys",
       P, '"INFRX_RELEASE_SHA": cfg.release,', '"INFRX_RELEASE_SHA": "",', RELEASE_CASE),
    _m("install_passes_no_release", "install.sh hands preflight its HEAD",
       "deploy/install.sh", ' --release "$sha"', "", RELEASE_CASE),
    # the cutover (CUTOVER item 4): the regime and the card, the runtime's own check
    _m("credit_without_card_installs", "a CREDIT pilot needs an approved card",
       "infrx/config.py", "    if deployment.accounting_regime == CREDIT_REGIME \\\n",
       "    if False \\\n", REGIME),
    _m("unknown_regime_installs", "an ACCOUNTING_REGIME the runtime does not know is refused",
       "infrx/config.py", "    if deployment.accounting_regime not in ACCOUNTING_REGIMES:",
       "    if False:", REGIME),
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
       "test_deploy_failclosed__the_engine_image_must_be_pinned_by_digest_in_pilot"),
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
    _m("unset_mode_starts_legacy", "an unset INFRX_MODE refuses to start (F2.2 item 14)",
       "infrx/config.py", '        raise RuntimeMisconfigured(mode, ("INFRX_MODE",))',
       '        return "legacy"',
       "test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer"),
)



# --- I2B.a: packaging (image, units, edge, schema, pilot prerequisites) ------------------
U = "deploy/"
CADDY, MAINT = "deploy/Caddyfile", "deploy/Caddyfile.maintenance"
PACK = "test_backend_deploy__"
FACTORY = "the_gateway_runs_the_factory_from_what_the_image_copies"
EDGE = "the_edge_proxies_jobs_and_uploads_untouched_and_unbuffered"
MUTANTS += (
    # the runtime image and its probe
    # the cutover (CUTOVER item 2): the factory, and nothing of the retired shim
    _m("unit_runs_the_retired_shim", "the gateway unit runs the create_app factory",
       U + "marlin2b-gateway.service",
       "${INFRX_IMAGE} uvicorn --factory infrx.gateway.app:create_app --host",
       "${INFRX_IMAGE} uvicorn gateway:app --host", PACK + FACTORY),
    # the cutover review (COMP-N1): one process holds the upload records; the drain budget
    _m("unit_two_workers", "one uvicorn worker holds the upload records",
       U + "marlin2b-gateway.service", "  --workers 1 \\\n", "  --workers 2 \\\n",
       PACK + FACTORY),
    _m("unit_drain_shortened", "the gateway drains 110 s, inside docker's 120 s and systemd's 150 s",
       U + "marlin2b-gateway.service", "  --timeout-graceful-shutdown 110\n",
       "  --timeout-graceful-shutdown 30\n", PACK + FACTORY),
    _m("image_copies_the_retired_shim", "the image copies only what exists in its context",
       U + "Dockerfile", "COPY infrx ./infrx\n", "COPY gateway.py ./\nCOPY infrx ./infrx\n",
       PACK + FACTORY),
    _m("image_compiles_the_retired_shim", "the image compiles only what it copied",
       U + "Dockerfile", "RUN python -m compileall -q infrx deploy",
       "RUN python -m compileall -q infrx gateway.py deploy", PACK + FACTORY),
    _m("context_drops_the_package", "the build context lets in what the image copies",
       U + "Dockerfile.dockerignore", "!infrx/\n", "", PACK + FACTORY),
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
    _m("worker_reads_media_only", "the worker writes the media root it prepares into "
       "(PREP-WORKER)",
       U + "infrx-worker.service", "-v ${PROCESSING_CACHE_DIR}:${PROCESSING_CACHE_DIR} \\\n",
       "-v ${PROCESSING_CACHE_DIR}:${PROCESSING_CACHE_DIR}:ro \\\n",
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
    # the cutover's routes at the edge (CUTOVER item 3; G3 request (e), G4U)
    _m("edge_strips_a_contract_response_header", "Location etc. reach the client untouched",
       CADDY, "\t\t\tflush_interval -1\n", "\t\t\tflush_interval -1\n\t\t\theader_down -Location\n",
       PACK + EDGE),
    _m("edge_strips_last_event_id", "Last-Event-ID reaches the gateway untouched",
       CADDY, "\t\t\tflush_interval -1\n",
       "\t\t\tflush_interval -1\n\t\t\theader_up -Last-Event-ID\n", PACK + EDGE),
    # the cutover review (COMP-B2): a site-level directive reaches every proxied response
    _m("edge_site_encodes_streams", "nothing in the site compresses or buffers a stream",
       CADDY, "\t# Bounded bodies at the edge", "\tencode gzip zstd\n\t# Bounded bodies at the edge",
       PACK + EDGE),
    _m("edge_site_strips_server_timing", "Server-Timing reaches the client untouched",
       CADDY, "\t# Bounded bodies at the edge",
       "\theader -Server-Timing\n\t# Bounded bodies at the edge", PACK + EDGE),
    _m("edge_buffers_events", "a stream (jobs events, chat SSE) is never buffered",
       CADDY, "\t\t\tflush_interval -1\n", "\t\t\tflush_interval 1s\n", PACK + EDGE),
    _m("edge_hides_jobs", "the edge proxies the jobs routes",
       CADDY, "@private path /metrics /metrics/* /readyz",
       "@private path /v1/jobs/* /metrics /metrics/* /readyz", PACK + EDGE),
    _m("edge_hides_uploads", "the edge proxies the upload routes",
       CADDY, "@private path /metrics /metrics/* /readyz",
       "@private path /v1/uploads /v1/uploads/* /metrics /metrics/* /readyz", PACK + EDGE),
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
       "ExecStartPre=+/usr/bin/install -d -o 10001 -g 10000 -m 2770 ${PROCESSING_CACHE_DIR}\n", "",
       PACK + "the_engine_takes_its_settings_from_the_validated_file"),
    _m("media_root_not_group_writable", "the worker's group may write the media root it "
       "prepares into (PREP-WORKER)",
       U + "marlin2b-vllm.service", "-g 10000 -m 2770 ", "-g 10000 -m 2750 ",
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
       INSTALL, '--region "$REGION" --image "$image" --release "$sha" --serve-script "$SERVE_SCRIPT" '
                '"${sets[@]}"',
       '--region "$REGION" --image "$image" --release "$sha" --serve-script "$SERVE_SCRIPT" '
       '"${sets[@]}" || true',
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
    _m("rehearsal_minio_unpullable_pin", "rehearse.sh names the integration stack's pullable MinIO digest",
       U + "rehearse.sh", "MINIO_IMAGE=pgsty/minio@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372",
       "MINIO_IMAGE=quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e",
       "test_the_rehearsal_minio_is_the_integration_stacks_pullable_pin"),
    _m("engine_restart_ignored", "the cutover can restart the engine onto its new pin",
       INSTALL, 'if [ "${ENGINE:-start}" = restart ]; then systemctl restart marlin2b-vllm', "if false; then systemctl restart marlin2b-vllm",
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
    _m("backup_dir_reused", "an install never writes into an existing backup directory",
       INSTALL, 'mkdir "$backup" 2>/dev/null || die', 'mkdir -p "$backup" 2>/dev/null || die',
       "test_ops_recover__an_install_never_writes_into_an_existing_backup"),
    _m("refused_install_rm_all_backups", "a refused run removes only its own backup",
       INSTALL, '|| { code=$?; rm -rf "$backup"; exit "$code"; }',
       '|| { code=$?; rm -rf "$BACKUPS"/*; exit "$code"; }',
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
    _m("rollback_runtime_gate_soft", "a restored runtime that is not ready stops the revert",
       ROLLBACK, '  || die "the restored runtime is not ready', '  || echo "the restored runtime is not ready',
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("rollback_engine_wait_shortened", "R2 waits the engine's whole load budget",
       ROLLBACK, '8000/health "${ENGINE_READY_S:-900}"', '8000/health "${ENGINE_READY_S:-1}"',
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("install_engine_wait_shortened", "the install waits the engine's whole load budget",
       INSTALL, '8000/health "${ENGINE_READY_S:-900}"', '8000/health "${ENGINE_READY_S:-1}"',
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("rollback_engine_wait_dropped", "R2 waits for the restored engine's health",
       ROLLBACK, "&& wait_http http://127.0.0.1:8000/health", "&& true http://127.0.0.1:8000/health",
       "test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it"),
    _m("rollback_engine_always_restarted", "a plain rollback leaves the engine alone",
       ROLLBACK, 'if [ "${ENGINE:-}" = restart ]; then', "if true; then",
       "test_ops_recover__rollback_restores_every_replaced_file"),
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
       MIGRATE, '        if OUTSIDE_TRANSACTION.search(COMMENTS.sub("", body.decode(errors="replace"))):',
       "        if False:", MIG + "refuses_a_history_it_cannot_explain"),
    _m("scan_without_reindex", "REINDEX CONCURRENTLY is refused at plan too",
       MIGRATE, "system|(create|drop|reindex)", "system|(create|drop)",
       MIG + "refuses_a_history_it_cannot_explain"),
    _m("comments_scanned", "a keyword inside a comment is not a statement",
       MIGRATE, 'OUTSIDE_TRANSACTION.search(COMMENTS.sub("", body.decode(errors="replace")))',
       'OUTSIDE_TRANSACTION.search(body.decode(errors="replace"))',
       MIG + "refuses_a_history_it_cannot_explain"),
    _m("scan_spans_lines", "the keyword scan reads one statement head per line",
       MIGRATE, '(create|drop|reindex)\\b[^;\\n]*"', '(create|drop|reindex)\\b[^;]*"',
       MIG + "refuses_a_history_it_cannot_explain"),
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

# --- I2B-R4: the worker's readiness port, one value for the runtime and both probes -------
WORKER_PORT = "test_backend_deploy__the_worker_readiness_port_is_one_value_the_installer_never_writes"
MUTANTS += (
    _m("worker_port_settable", "the installer never writes the worker's readiness port",
       P, '    "WORKER_HEALTH_PORT": "the worker', '    "WORKER_HEALTH_PORT_": "the worker',
       WORKER_PORT),
    _m("worker_ready_port_drift", "wait_ready probes the port the worker binds",
       "deploy/lib.sh", "WORKER_READY=http://127.0.0.1:${WORKER_HEALTH_PORT:-8002}/readyz",
       "WORKER_READY=http://127.0.0.1:${WORKER_HEALTH_PORT:-8003}/readyz", WORKER_PORT),
    _m("worker_port_default_drift", "the runtime's default is the probes' 8002",
       "infrx/config.py", "    worker_health_port: int = 8002\n",
       "    worker_health_port: int = 8003\n", WORKER_PORT),
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
    _m("hdr_file_world_readable", "the key's header file is 0600",
       "../../infra/rollout/verify-external.sh", "hdr=$(mktemp)", 'hdr=$(mktemp); chmod 644 "$hdr"',
       "test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line"),
    _m("hdr_file_kept_after_exit", "the key's header file is gone when the script exits",
       "../../infra/rollout/verify-external.sh", """trap 'rm -f "$hdr" "$out"' EXIT""",
       """trap 'rm -f "$out"' EXIT""",
       "test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line"),
    _m("readme_inline_key_literal", "the runbook never writes a key's value",
       "../../infra/rollout/README.md", "export LEGACY_KEY=$(aws ssm",
       "export LEGACY_KEY=sk-literal-0123456789 #$(aws ssm",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret"),
    _m("step_inline_key_literal", "no step assigns a key a literal value",
       STEP + "60-verify-local.sh", "set -euo pipefail\n",
       "set -euo pipefail\nSUPABASE_SERVICE_ROLE_KEY=sbp_0123456789abcdef0123456789abcdef\n",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret"),
    _m("body_file_fixed_path", "response bodies go to a fresh temporary file",
       "../../infra/rollout/verify-external.sh", "out=$(mktemp)", "out=/tmp/infrx-verify.body",
       "test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line"),
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
    _m("migration_digest_any_string", "the cutover's digest is a digest or nothing-pending",
       STEP + "50-install.sh", "[[ $MIGRATION_DIGEST =~ ^([0-9a-f]{64}|nothing-pending)$ ]]",
       "true", "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("digest_length_loose", "a plan digest is exactly 64 hex",
       STEP + "50-install.sh", "^([0-9a-f]{64}|nothing-pending)$", "^([0-9a-f]{63,65}|nothing-pending)$",
       "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("digest_unanchored", "the whole statement is the digest or the sentinel",
       STEP + "50-install.sh", "^([0-9a-f]{64}|nothing-pending)$", "([0-9a-f]{64}|nothing-pending)",
       "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("cutover_drops_engine_concurrency", "the cutover keeps the box's 32 engine sequences",
       STEP + "50-install.sh", 'INFRX_SET="ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-32} ${INFRX_SET:-}"',
       'INFRX_SET="${INFRX_SET:-}"',
       "test_backend_deploy__the_cutover_keeps_the_engines_concurrency"),
    _m("step_without_release_guard", "a step never runs without its release",
       STEP + "50-install.sh", ': "${RELEASE:?the release commit}"\n', "",
       "test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret"),
)


# ROLLOUT-PREP: the release route to the box (deploy/release-bundle.sh).
RB, RB_OK, RB_BAD = ("deploy/release-bundle.sh",
                     "test_backend_deploy__a_release_bundle_reaches_the_box_checked_against_its_manifest",
                     "test_backend_deploy__a_release_bundle_refuses_what_is_not_a_commit")
MUTANTS += (
    _m("bundle_manifest_unchecked", "the box checks the bundle against its sha256 before git",
       RB, '( cd "\\$d" && sha256sum -c "$name.sha256" )\n', "", RB_OK),
    _m("bundle_ships_head", "the bundle carries the commit named, not HEAD",
       RB, 'update-ref "$REF" "$sha"', 'update-ref "$REF" "$(git -C "$repo" rev-parse HEAD)"',
       RB_OK),
    _m("bundle_uploads_with_stale_keys", "the upload never uses the shell's stale AWS keys",
       RB, "aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \\",
       "aws() { env \\", RB_OK),
    _m("bundle_short_commit_accepted", "only a full lower-case commit id is shipped",
       RB, "^[0-9a-f]{40}$", "^[0-9a-fA-F]{7,40}$", RB_BAD),
    _m("bundle_unknown_commit_accepted", "a commit this repository lacks is refused by name",
       RB, 'git -C "$repo" cat-file -e "$sha^{commit}" 2>/dev/null || { echo "no commit $sha in $repo" >&2; exit 2; }\n',
       "", RB_BAD),
)

# ROLLOUT-PREP: the saved edge and the real-bucket check (infra/rollout/steps).
EDGE = "test_ops_recover__the_saved_edge_comes_back_byte_for_byte"
S3C = "test_backend_deploy__the_real_bucket_check_runs_the_release_image_before_the_install"
MUTANTS += (
    _m("save_edge_without_sha", "the saved edge's sha256 is printed for the record",
       STEP + "25-save-edge.sh", 'sha256sum "$saved"\n', "", EDGE),
    _m("restore_edge_unchecked", "a saved edge that does not match its sha256 restores nothing",
       STEP + "93-restore-edge.sh", 'echo "$SAVED_SHA256  $SAVED" | sha256sum -c -\n', "", EDGE),
    _m("restore_edge_new_inode", "the edge is rewritten in place, keeping the bind mount's inode",
       STEP + "93-restore-edge.sh", 'cat "$SAVED" > /etc/caddy/Caddyfile',
       'rm /etc/caddy/Caddyfile; cat "$SAVED" > /etc/caddy/Caddyfile', EDGE),
    _m("restore_edge_reload_on_loopback", "the reload goes through the admin socket",
       STEP + "93-restore-edge.sh", " --address unix//config/admin.sock", "", EDGE),
    _m("s3_check_any_checkout", "the real-bucket check runs only on the checked-out release",
       STEP + "45-s3-check.sh",
       '[ "$(git -c safe.directory="$PWD" rev-parse HEAD)" = "$RELEASE" ] || ', "", S3C),
    _m("s3_check_local_creds", "the check uses the instance role, never the MinIO credentials",
       STEP + "45-s3-check.sh", "-e INFRX_M_S3_ENDPOINT -e INFRX_M_S3_BUCKET",
       "-e INFRX_M_S3_ENDPOINT -e INFRX_M_S3_BUCKET -e INFRX_M_S3_LOCAL_CREDS", S3C),
    _m("s3_check_red_ignored", "a red conformance run is a failed step",
       STEP + "45-s3-check.sh", "tests/m/test_s3.py'", "tests/m/test_s3.py' || true", S3C),
    _m("s3_check_red_ignored_inside", "the container exits with pytest's status",
       STEP + "45-s3-check.sh", "tests/m/test_s3.py'", "tests/m/test_s3.py || true'", S3C),
)

# --- I8 (consumer v1): pooler budget, transaction-pooler semantics, env schema, least
# privilege. The docker cases (tests/i/pooler.py) run their own PostgreSQL + PgBouncer.
BUDGET_PY, POOLER_PY = "../../infra/runbooks/pool_budget.py", "tests/i/pooler.py"
PROBE_PY = "../../infra/runbooks/privilege_probe.py"
ADMITS = "test_ops_continuous__the_computed_budget_is_what_the_session_pooler_admits"
COMPOSED = "test_ops_continuous__the_composed_runtime_pool_holds_on_the_transaction_pooler"
TXN_CASES = ("test_ops_continuous__session_state_is_lost_and_leaked_on_the_transaction_pooler",
             "test_ops_continuous__transaction_scoped_patterns_survive_the_transaction_pooler",
             COMPOSED)
ON_6543 = "test_ops_continuous__on_6543_the_runtime_sends_no_session_set"
POOL_SCRAPE = "test_ops_continuous__both_processes_export_their_db_pool_at_scrape"
ENVCHECK_REFUSES = "test_deploy_failclosed__envcheck_refuses_a_file_the_runtime_would_start_on"
UNIT_REFUSES = "test_deploy_failclosed__each_runtime_unit_refuses_to_start_on_a_refused_env_file"
LEAST = "test_ops_continuous__the_least_privilege_login_passes_and_privileged_ones_fail"
MUTANTS += (
    _m("budget_forgets_the_worker_pool", "every pool of the pooler's clients is counted",
       BUDGET_PY, '"worker pool (inference + preparation + reaper)", mode, 1, pool_min, pool_max,',
       '"worker pool (inference + preparation + reaper)", mode, 1, pool_min, 0,', ADMITS),
    _m("budget_session_limit_raised", "the session pooler admits 15 clients, measured",
       BUDGET_PY, "SESSION_LIMIT = 15 ", "SESSION_LIMIT = 25 ", ADMITS),
    _m("budget_one_gateway_process", "uvicorn --workers multiplies the gateway's pool",
       BUDGET_PY, "return int(found.group(1)) if found else 1", "return 1",
       "test_ops_continuous__the_budget_counts_every_gateway_process"),
    _m("stand_in_pooler_in_session_mode", "the stand-in hands server connections between "
       "clients at transaction boundaries, as 6543 does",
       POOLER_PY, "dbname={DATABASE} pool_mode=transaction pool_size=2",
       "dbname={DATABASE} pool_mode=session pool_size=2", *TXN_CASES,
       # a session pooler keeps each client on its server: the case's second client then
       # waits for a server that never frees (query_wait_timeout) - the defect's absence,
       # observed as the pooler's refusal
       dies_by=("ProtocolViolation", "OperationalError")),
    _m("stand_in_pooler_port_literal", "the stand-in's host port is the one reserved in "
       "infrx.contracts.tasklocal", POOLER_PY,
       "BOUNCER: BOUNCER_SERVICE.host_port}", "BOUNCER: 55496}",
       "test_ops_continuous__the_stand_in_pooler_port_is_reserved_in_tasklocal"),
    _m("stand_in_pooler_replays_prepares", "the stand-in, like 6543, supports no prepared "
       "statements", POOLER_PY, "max_prepared_statements = 0", "max_prepared_statements = 100",
       "test_ops_continuous__auto_prepared_statements_break_on_the_transaction_pooler"),
    _m("runtime_adds_a_session_statement", "no new session-only statement reaches the pool",
       "infrx/gateway/pilot.py", '        await conn.execute("set role service_role")\n',
       '        await conn.execute("set role service_role")\n'
       '        await conn.execute("set search_path = infrx, public")\n',
       "test_ops_continuous__the_runtime_sends_no_other_session_only_statement"),
    # WR-I8-1..4 (i8-wiring): the runtime on 6543, the pool and GPU scrape
    _m("pool_prepares_again", "no server-side prepares on the pool's connections (WR-I8-1)",
       "infrx/gateway/pilot.py", 'kwargs={"autocommit": True, "prepare_threshold": None},',
       'kwargs={"autocommit": True},', ON_6543, COMPOSED,
       dies_by=("InvalidSqlStatementName", "DuplicatePreparedStatement")),
    _m("pool_sets_session_state_on_6543", "the hook sends no session SET on 6543 (WR-I8-1)",
       "infrx/gateway/pilot.py", "        if not session_state:\n            return\n", "",
       ON_6543, COMPOSED, dies_by=("PoolTimeout",)),
    _m("connector_sets_role_on_6543", "the CLI's connect sends no session SET on 6543",
       "infrx/state/jobstore.py", "        if session_state_allowed(dsn):\n"
       '            await conn.execute("set role service_role")',
       '        await conn.execute("set role service_role")', ON_6543, COMPOSED,
       dies_by=("InsufficientPrivilege",)),
    _m("connector_prepares", "no server-side prepares on the CLI's connections (WR-I8-1)",
       "infrx/state/jobstore.py", "autocommit=True,\n"
       "                                                     prepare_threshold=None)",
       "autocommit=True)", ON_6543),
    _m("pool_wait_in_ms", "the pool's wait counter is in seconds (WR-I8-2)",
       "infrx/observe/metrics.py", 'stats.get("requests_wait_ms", 0) / 1000)',
       'stats.get("requests_wait_ms", 0))', POOL_SCRAPE),
    _m("gateway_scrape_skips_pool", "the gateway's scrape reads its pool (WR-I8-2)",
       "infrx/observe/route.py", "            record_pool(rt.metrics, pool.pop_stats())",
       "            pass", POOL_SCRAPE),
    _m("worker_scrape_skips_pool", "the worker's scrape reads its pool (WR-I8-2)",
       "infrx/worker/service.py", "                    record_pool(self.metrics, self.pool.pop_stats())",
       "                    pass", POOL_SCRAPE),
    _m("gateway_collects_gpu", "the gateway exports no GPU gauge (WR-I8-4)",
       "infrx/observe/route.py", "rt.metrics, disks, gpu=False)", "rt.metrics, disks)",
       "test_ops_continuous__the_gateway_exports_no_gpu_gauge"),
    _m("pool_budget_step_env_as_mount", "the env file reaches the budget over stdin only",
       STEP + "71-pool-budget.sh", '"${sets[@]}" < "$env_file"', '"${sets[@]}"',
       "test_ops_continuous__the_pool_budget_step_hands_the_env_file_over_stdin"),
    # the env schema
    _m("envcheck_accepts_unknown_names", "a name outside the schema refuses the start",
       P, "for name in sorted(set(seen) - schema_names())]", "for name in ()]",
       "test_ops_continuous__the_runtime_ignores_a_mistyped_name_which_envcheck_refuses",
       ENVCHECK_REFUSES, UNIT_REFUSES),
    _m("envcheck_accepts_doubled_names", "a name set twice refuses the start",
       P, '            problems.append(f"{name}: set twice (systemd would take the last)")\n',
       "            pass\n", ENVCHECK_REFUSES),
    _m("envcheck_ignores_the_schema_version", "a file written for another schema refuses",
       P, "    elif header[len(SCHEMA_HEADER):].strip() != schema_id():",
       "    elif False:", ENVCHECK_REFUSES),
    _m("envcheck_skips_the_runtime_probe", "the runtime's own validation runs at start",
       P, '    problems += verdict["problems"]\n    return {"ok": not problems, "schema"',
       '    return {"ok": not problems, "schema"', ENVCHECK_REFUSES),
    _m("schema_id_ignores_the_names", "the schema id moves with the names it covers",
       P, '"\\n".join(sorted(schema_names()))', '"\\n".join(sorted(MODES))',
       "test_deploy_failclosed__the_schema_id_moves_with_the_names_it_covers"),
    _m("envcheck_refusal_exits_zero", "a refused env file fails the unit's start",
       P, '        return 0 if verdict["ok"] else REFUSED\n    if args.command == "manifest":',
       '        return 0\n    if args.command == "manifest":', UNIT_REFUSES),
    _m("gateway_starts_unchecked", "the gateway unit checks its env file before it starts",
       "deploy/marlin2b-gateway.service", "ExecStartPre=/bin/sh -c 'exec docker run",
       "#ExecStartPre=/bin/sh -c 'exec docker run", UNIT_REFUSES),
    # least privilege
    _m("probe_passes_an_allowed_operation", "an operation that succeeds fails its check",
       PROBE_PY, '"pass": got.startswith("denied"),', '"pass": got != "error",', LEAST),
    _m("probe_ignores_role_membership", "membership in a privileged role fails the probe",
       PROBE_PY, '"select not exists (select 1 from pg_roles r where r.rolname = any(%(privileged)s) "',
       '"select true or exists (select 1 from pg_roles r where r.rolname = any(%(privileged)s) "',
       LEAST),
    _m("probe_passes_without_function_list", "no D10 function list is PENDING, not a pass",
       PROBE_PY, '"got": "PENDING: no --allow-functions list given", "pass": False,',
       '"got": "PENDING: no --allow-functions list given", "pass": True,', LEAST),
    _m("probe_runs_without_its_dsn", "the DSN comes from the environment or nothing runs",
       PROBE_PY, "    if not dsn:\n", "    if False:\n",
       "test_ops_continuous__the_probe_refuses_to_run_without_its_dsn_in_the_environment"),
)

# --- I8 slices 3-4: monitoring, delivery ------------------------------------------------
OBS = "../../infra/observe/"
OPS_RULES = "../../infra/alerts/operations.json"
DELIVERY = "test_ops_continuous__delivery_sends_changes_only_and_blocks_without_a_destination"
MUTANTS += (
    _m("durable_forgets_unknown_holds", "the durable exporter feeds the reconcile rules",
       OBS + "durable.py", '        out[("infrx_holds_unknown", ())] = holds.get("unknown", 0)\n', "",
       "test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones"),
    _m("producer_scan_blind", "the producer check reads what the code writes",
       "tests/i/test_observe.py",
       "return {name for name, metrics in rule_metrics(rules).items() if not metrics <= produced}",
       "return set()",
       "test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones",
       "test_ops_continuous__every_alert_rule_names_a_metric_something_produces"),
    _m("merge_accepts_duplicate_rules", "a rule name defined twice is refused",
       OBS + "rules.py", '        if rule["name"] in rules:\n', "        if False:\n",
       "test_ops_continuous__the_merged_rule_set_is_versioned_and_well_formed"),
    _m("rule_names_a_missing_runbook_section", "every rule links a runbook section that exists",
       OPS_RULES, '"runbook": "infra/runbooks/observe.md#stuck-holds"',
       '"runbook": "infra/runbooks/observe.md#stuck-hold"',
       "test_ops_continuous__the_merged_rule_set_is_versioned_and_well_formed"),
    _m("gpu_rule_reads_the_gateway", "the GPU rule reads the host's gauge, not the gateway's",
       OPS_RULES, '"GpuUnavailable": {\n      "match": {\n        "process": "host"',
       '"GpuUnavailable": {\n      "match": {\n        "process": "gateway"',
       "test_ops_continuous__the_gateways_blind_gpu_gauge_does_not_page_but_the_hosts_does"),
    _m("durable_backlog_counts_the_future", "the ready backlog is what is available now",
       OBS + "durable.py", '"and claimed_at is null and available_at <= now() group by kind"',
       '"and claimed_at is null and available_at > now() group by kind"',
       "test_ops_continuous__durable_truth_reads_holds_backlog_and_drift_through_the_pooler"),
    _m("durable_hides_drift", "drift from durable truth reaches the drift rule",
       OBS + "durable.py", '        out[("infrx_reconciliation_drift", ())] = drift\n',
       '        out[("infrx_reconciliation_drift", ())] = 0\n',
       "test_ops_continuous__durable_truth_reads_holds_backlog_and_drift_through_the_pooler"),
    _m("durable_on_the_session_port", "the monitor never takes a session slot",
       OBS + "durable.py", 'TRANSACTION_PORT = "6543"', 'TRANSACTION_PORT = "5432"',
       "test_ops_continuous__durable_truth_uses_the_transaction_port_by_default"),
    _m("host_probe_engine_always_up", "the engine's health is probed, not assumed",
       OBS + "host-probe.sh",
       'm infrx_engine_up "$(ok curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health)"',
       "m infrx_engine_up 1",
       "test_ops_continuous__the_host_probe_reports_gpu_engine_units_disks_and_the_edge"),
    _m("canary_header_file_readable", "the canary's key file is 0600",
       OBS + "canary.sh", "( umask 077; printf", "( printf",
       "test_ops_continuous__the_canary_sends_one_text_and_one_video_request_with_a_hidden_key"),
    _m("canary_failure_exits_zero", "a failed synthetic request fails the run",
       OBS + "canary.sh", 'publish\nexit "$failed"', "publish\nexit 0",
       "test_ops_continuous__the_canary_sends_one_text_and_one_video_request_with_a_hidden_key"),
    _m("delivery_blocked_reads_as_success", "no destination is BLOCKED, never a success",
       OBS + "deliver.py", "        return BLOCKED if status == 0 else SEND_FAILED\n    current",
       "        return 0\n    current", DELIVERY),
    _m("delivery_repeats_tickets", "only pages repeat while firing",
       OBS + "deliver.py", 'repeat = alert["severity"] == "page" and last is not None',
       "repeat = last is not None", DELIVERY),
    _m("test_alert_unmarked", "the delivery test cannot be mistaken for a real alert",
       OBS + "deliver.py", 'text = (f"[TEST {word}] infrx', 'text = (f"[{word}] infrx',
       "test_ops_continuous__the_test_alert_is_marked_and_names_its_owner_and_runbook"),
    _m("observe_timer_hourly", "the monitoring cycle runs every minute",
       OBS + "systemd/infrx-observe.timer", "OnUnitActiveSec=60s", "OnUnitActiveSec=1h",
       "test_ops_continuous__the_monitoring_units_are_valid_and_scheduled"),
    _m("observe_hands_the_whole_env_file", "only the DSN crosses into the exporter",
       OBS + "observe.sh", "  || grep -E '^DATABASE_URL=' \"$env_file\" > \"$dsn_env\"",
       "  || cat \"$env_file\" > \"$dsn_env\"",
       "test_ops_continuous__one_observe_cycle_probes_exports_evaluates_and_delivers"),
    _m("observe_install_env_world_readable", "the monitor's env files are root 0600",
       STEP + "72-observe-install.sh", 'chmod 0600 "$tmp"; mv', 'chmod 0644 "$tmp"; mv',
       "test_ops_continuous__installing_the_monitor_writes_env_files_from_ssm_by_name"),
    _m("observe_runs_from_the_checkout", "the monitor survives a runtime rollback",
       OBS + "systemd/infrx-observe.service", "Environment=REPO=/opt/infrx/observe\n", "",
       "test_ops_continuous__installing_the_monitor_writes_env_files_from_ssm_by_name"),
    _m("alert_test_without_destination_runs", "no P-25 destination is BLOCKED (exit 3)",
       STEP + "74-alert-test.sh",
       '[ -f "$conf" ] || { echo "BLOCKED: no $conf (P-25: destination, owner, escalation)" >&2; exit 3; }\n',
       "", "test_ops_continuous__the_delivery_proof_is_blocked_until_p25_and_never_prints_the_url"),
)

# --- I8 slice 5: the durable model mirror, its restore, the backup/PITR read -------------
ART, POLICY = "../../infra/runbooks/artifacts.py", "../../infra/runbooks/supabase_policy.py"
PINS = "test_ops_recover__the_manifest_pins_the_served_bytes_and_records_names_only"
ROUND = "test_ops_recover__mirror_then_restore_round_trips_and_detects_a_changed_object"
POLICY_CASE = "test_ops_recover__the_policy_read_reports_pitr_backups_and_the_pooler_without_secrets"
MUTANTS += (
    _m("manifest_accepts_other_shards", "a directory serving other bytes is never mirrored",
       ART, '    if shards != sorted(model["weight_shard_digests"]):', "    if False:", PINS),
    _m("manifest_records_env_values", "the manifest carries env NAMES only",
       ART, "                env_names.append(name)", "                env_names.append(line)", PINS),
    _m("manifest_checks_a_renamed_pin", "the pins checked are the fields W3 records",
       ART, '"config_digest": "config.json"', '"config_sha256": "config.json"',
       "test_ops_recover__the_real_serving_record_has_the_fields_the_manifest_checks",
       dies_by=("KeyError",)),
    _m("restore_verify_ignores_changes", "a restored byte that differs is DIFFERENT",
       ART, "    changed = sorted(p for p in set(want) & set(have) if want[p] != have[p])",
       "    changed = []", ROUND),
    _m("mirror_uploads_the_download_cache", "only served files are mirrored",
       STEP + "80-mirror-artifacts.sh", "--exclude '.cache/*' ", "", ROUND),
    _m("restore_skips_verification", "a restore is verified against the manifest",
       STEP + "81-restore-artifacts.sh",
       '  python3 "$repo/infra/runbooks/artifacts.py" verify --weights', "  true --weights", ROUND),
    _m("policy_prints_connection_strings", "the pooler read never prints a connection string",
       POLICY, '("database_type", "pool_mode", "db_port", "default_pool_size",',
       '("database_type", "pool_mode", "db_port", "default_pool_size", "connection_string",',
       POLICY_CASE),
    _m("policy_passes_without_token", "no token is BLOCKED, never a pass",
       POLICY, "        code = BLOCKED\n", "        code = 0\n", POLICY_CASE),
)

# --- I8 slice 6: the rollback drill --------------------------------------------------------
KG, JOURNEY = "../../infra/rollout/known-good.py", "../../infra/rollout/verify-journey.sh"
JUDGED = "test_ops_recover__a_known_good_target_is_judged_by_its_tree_and_its_record"
BOXCHECK = "test_ops_recover__the_box_check_shows_what_each_backup_really_holds"
SERVES = "test_ops_recover__the_journey_proves_a_real_video_job_its_result_and_settleable_usage"
MUTANTS += (
    _m("known_good_without_preparation", "a tree without the preparation loop is no target",
       KG, 'check("preparation", prep and "PreparationRunner(" in main,', 'check("preparation", True,',
       JUDGED),
    _m("known_good_ignores_migrations", "a tree whose migrations are not applied is no target",
       KG, 'check("migrations", newest == applied or (newest < applied and proven),',
       'check("migrations", True,', JUDGED),
    _m("known_good_without_evidence", "the record must carry evidence that exists",
       KG, 'entry.get("known_good") is True and not absent,', "entry is not None,", JUDGED),
    _m("known_good_bundle_unchecked", "the release bundle must be in the release prefix",
       KG, '{f"{sha}.bundle", f"{sha}.sha256"} <= have,', "True,", JUDGED),
    _m("box_check_prints_backup_env", "only the release id is read from a backup",
       STEP + "85-known-good-box.sh", "sed -n 's/^INFRX_RELEASE_SHA=//p' | tail -n1", "cat", BOXCHECK),
    _m("box_check_trusts_a_tampered_bundle", "a bundle that fails its sha256 is not ready",
       STEP + "85-known-good-box.sh", 'sha256sum -c --status "$TARGET.sha256"', "true", BOXCHECK),
    _m("journey_accepts_a_closed_edge", "a public 503 after readiness fails the drill",
       JOURNEY, 'if [ "$lag" -lt "$EDGE_LAG_MAX_S" ]; then', "if true; then", SERVES),
    _m("journey_ignores_usage_certainty", "unsettleable usage fails the drill",
       JOURNEY, '[ "$(field "$work/status" usage_certainty)" = authoritative ] && ok',
       "true && ok", SERVES),
    _m("journey_may_replay", "the drill's job is fresh work, never an idempotent replay",
       JOURNEY, "printf 'Idempotency-Key: verify-journey-%s\\n'", "printf 'X-Note: %s\\n'", SERVES),
    _m("journey_window_admits", "during the window a submission must be refused",
       JOURNEY, "if [ \"$code\" = 503 ] && grep -qi '^retry-after:' \"$work/hd\"; then",
       "if true; then", "test_ops_recover__during_the_window_nothing_new_is_admitted"),
    _m("install_readiness_as_cold_start", "readiness is never reported as a cold start",
       "deploy/install.sh", 'else kind="engine kept running: NOT a cold start"; fi',
       'else kind="cold start: engine restarted, weights loaded"; fi',
       "test_ops_recover__an_install_never_reports_readiness_as_a_cold_start"),
)

# --- I8 slice 7: evidence export, bounded cleanup -------------------------------------------
MUTANTS += (
    _m("evidence_exports_env_values", "the evidence export carries env NAMES only",
       STEP + "79-evidence-export.sh", '"env_names": sorted(env),',
       '"env_names": sorted(f"{k}={v}" for k, v in env.items()),',
       "test_ops_continuous__the_evidence_export_is_one_names_only_document"),
    _m("cleanup_removes_known_good_backups", "a backup holding a known-good release is kept",
       STEP + "86-cleanup.sh",
       """  case " $keep_releases " in *" ${held:-none} "*) echo "kept $dir (holds known-good $held)"; continue ;; esac\n""",
       "", "test_ops_continuous__cleanup_removes_only_allowlisted_paths_and_keeps_known_good"),
    _m("cleanup_deletes_by_default", "cleanup is a dry run unless DRY_RUN=0",
       STEP + "86-cleanup.sh", "DRY_RUN=${DRY_RUN:-1}", "DRY_RUN=${DRY_RUN:-0}",
       "test_ops_continuous__cleanup_removes_only_allowlisted_paths_and_keeps_known_good"),
)

# --- I8 fix round (review of 103d20a/d2f90ce): oracles the review found missing -------------
DRIFT_PY = "../../infra/runbooks/drift.py"
READ_ONLY = "test_ops_continuous__durable_truth_sends_only_reads_inside_a_read_only_transaction"
HEADROOM = "test_ops_continuous__the_budget_reserves_headroom_and_counts_the_startup_peak"
SETTLES = "test_ops_recover__the_settlement_check_passes_either_regime_and_fails_the_unsettled"
INSTALL_OBSERVE = "test_ops_continuous__installing_the_monitor_writes_env_files_from_ssm_by_name"
MUTANTS += (
    _m("durable_not_read_only", "the monitor's one transaction is read-only",
       OBS + "durable.py", '        conn.execute("set transaction read only")\n', "", READ_ONLY),
    _m("durable_writes", "the monitor sends reads only",
       OBS + "durable.py", '        conn.execute("set transaction read only")\n',
       '        conn.execute("create table if not exists infrx.i8_monitor_wrote (x int)")\n',
       READ_ONLY),
    _m("budget_headroom_ignored", "the verdict keeps the reserved headroom free",
       BUDGET_PY, '"ok": peak + headroom <= limit}', '"ok": peak <= limit}', HEADROOM),
    _m("budget_startup_ignored", "a concurrent startup above the steady peak is the peak",
       BUDGET_PY, "peak = max(startup, steady)", "peak = steady", HEADROOM),
    _m("art_pins_ignored", "the tokenizer, template and config pins refuse other bytes",
       ART, "        if by_name.get(name) != model[field]:", "        if False:", PINS),
    _m("art_verify_missing_ignored", "a restore that lost a file is DIFFERENT",
       ART, "    missing = sorted(set(want) - set(have))", "    missing = []", ROUND),
    _m("art_verify_extra_ignored", "a restore that gained a file is DIFFERENT",
       ART, "    extra = sorted(set(have) - set(want))", "    extra = []", ROUND),
    _m("restore_trusts_a_replaced_mirror", "restored bytes are checked against the release's pins",
       ART, "    if a.serving_version:", "    if False:", ROUND),
    _m("mirror_readback_mismatch_passes", "a manifest that reads back different fails the mirror",
       STEP + "80-mirror-artifacts.sh",
       '|| { echo "manifest read back DIFFERENT from the one uploaded" >&2; exit 1; }', "|| true",
       ROUND),
    _m("probe_attrs_blind", "a BYPASSRLS (or superuser...) login fails the probe",
       PROBE_PY, '"select not (rolsuper or rolbypassrls', '"select true or (rolsuper or rolbypassrls',
       LEAST),
    _m("probe_timeout_identity_blind", "a login with no statement_timeout fails the probe",
       PROBE_PY, "\"select current_setting('statement_timeout') not in ('0', '0ms')\"",
       '"select true"', LEAST),
    _m("probe_identity_reads_its_own_timeout", "the timeout check reads the login's, not the probe's",
       PROBE_PY, "attempt(conn, sql, params, bounded=False)", "attempt(conn, sql, params)", LEAST),
    _m("drift_credit_charge_unchecked", "a CREDIT job settles only with its ledger debit",
       DRIFT_PY, '"then debit = 0 and exists (select 1 from infrx.credit_ledger',
       '"then true or exists (select 1 from infrx.credit_ledger', SETTLES),
    _m("drift_usd_only", "a settled CREDIT job is SETTLED (its USD debit is 0 by design)",
       DRIFT_PY, ',\n           [("succeeded", "settled", "credit", True, "authoritative")])', ",)",
       SETTLES),
    _m("drift_ignores_wallet_drift", "wallet drift fails the settlement check",
       DRIFT_PY, '                           and seen["wallet drift rows"] == [(0,)]\n', "", SETTLES),
    _m("known_good_assumes_additive", "a schema ahead of the tree needs a recorded proof",
       KG, "newest == applied or (newest < applied and proven)", "newest <= applied", JUDGED),
    _m("canary_timer_without_p24", "the recurring canary waits for P-24's approval",
       STEP + "72-observe-install.sh", 'if [ -n "${P24_APPROVED:-}" ]; then', "if true; then",
       INSTALL_OBSERVE),
    _m("canary_key_defaulted", "the canary key has no default",
       STEP + "72-observe-install.sh",
       ': "${CANARY_KEY_PARAM:?the SSM name of the canary tenant key - no default, P-24 bounds its spend}"',
       "CANARY_KEY_PARAM=${CANARY_KEY_PARAM:-/model-inference/e4b_api_key}", INSTALL_OBSERVE),
)

# --- KNOWN-GOOD-PROOF: a schema proof reaches its `through` and no further ---------------
PROOF_PY = "../../infra/runbooks/schema_proof.py"
MUTANTS += (
    _m("known_good_proof_ignores_through", "a schema_proof proves the schema only through its `through`",
       KG, 'proven = (proof.get("through", "0000") >= applied and bool(proof.get("evidence"))',
       'proven = (bool(proof.get("evidence"))',
       "test_ops_recover__a_schema_proof_reaches_exactly_its_through"),
    _m("known_good_record_unproven", "both known-good targets carry their schema proof",
       "../../infra/rollout/known-good.json", '"schema_proof": {"through": "0023", "result": "bda1586',
       '"schema_proof_withdrawn": {"through": "0023", "result": "bda1586',
       "test_ops_recover__the_record_proves_both_targets_on_the_candidate_schema"),
    _m("schema_proof_trusts_moved_statements", "a migrated history that differs from the files is refused",
       PROOF_PY, "parts != [files[v]] and not (", "False and not (",
       "test_ops_recover__the_proof_driver_refuses_a_bad_target_and_a_moved_history"),
)

# --- M6 wiring 4: retention/cache panels and rules, the bucket lifecycle rule -------------
DASH = "../../infra/alerts/dashboard.json"
M6_PANELS = "test_ops_retention__every_m6_family_has_a_pending_panel_and_a_closed_vocabulary"
M6_RULES = "test_ops_retention__each_rule_fires_on_its_fault_and_nothing_fires_when_healthy"
BUCKET = "test_ops_retention__the_bucket_rule_aborts_stale_multipart_uploads_only"
MUTANTS += (
    _m("m6_panel_missing", "every retention/cache family has a panel", DASH,
       '"unit": "1/s"},\n          {"title": "Oldest pending delete", "metric": '
       '"infrx_retention_pending_delete_seconds", "unit": "s"}', '"unit": "1/s"}', M6_PANELS),
    _m("m6_abort_rule_blunted", "three consecutive aborted passes page", OPS_RULES,
       '"op": ">=",\n      "threshold": 3,', '"op": ">=",\n      "threshold": 30,', M6_RULES),
    _m("bucket_rule_whole_bucket", "the bucket rule stays inside the media prefix",
       "deploy/s3-lifecycle.json", '"Filter": {"Prefix": "infrx/"}', '"Filter": {"Prefix": ""}',
       BUCKET),
)

# --- intake panels: WR-I8-3's large-body gate and refusal drain (OB-10 follow-up) ---------
INTAKE_ROW = "test_ops_intake__the_intake_row_shows_every_intake_family_on_closed_labels"
INTAKE_RULE = "test_ops_intake__the_slots_rule_fires_at_the_limit_only"
MUTANTS += (
    _m("intake_panel_missing", "every intake family has a panel in the intake row", DASH,
       '        {"title": "Large bodies refused (every slot held)", "metric": '
       '"infrx_large_body_refused_total", "rate": true, "unit": "1/s"},\n', "", INTAKE_ROW),
    _m("intake_vocabulary_widened", "intake panels split on declared closed labels only", DASH,
       '"metric": "infrx_intake_drained_total", "by": ["code"]',
       '"metric": "infrx_intake_drained_total", "by": ["code", "tenant"]', INTAKE_ROW),
    _m("intake_rule_stale_family", "the slots rule reads the families the gateway records",
       OPS_RULES, '"metric": "infrx_large_body_slots_in_use"', '"metric": "infrx_large_body_in_use"',
       INTAKE_RULE),
)

# --- ALERT-SNS (P-25's SNS destination form) --------------------------------------------
SNS = "test_ops_alert_sns__"
MUTANTS += (
    _m("sns_failure_swallowed", "a failed SNS publish is kept in UNDELIVERED and retried",
       OBS + "deliver.py", 'return -1, f"sns={type(failed).__name__}',
       'return 200, f"sns={type(failed).__name__}', SNS + "a_failed_publish_is_kept_and_retried"),
    _m("both_destinations_accepted", "exactly one destination is configured, else BLOCKED",
       OBS + "deliver.py", "    if bool(hook) == bool(topic):\n", "    if not hook and not topic:\n",
       SNS + "exactly_one_destination_or_blocked"),
    _m("sns_subject_unbounded", "the SNS subject fits SNS's 100-character limit",
       OBS + "deliver.py", '.decode()[:100]', ".decode()",
       SNS + "publishes_one_subject_and_message_with_the_instance_role"),
    _m("sns_without_boto3_passes", "the SNS path without boto3 is BLOCKED, never a success",
       OBS + "deliver.py", 'return 0, "BLOCKED: ALERT_SNS_TOPIC_ARN is set but boto3',
       'return 200, "BLOCKED: ALERT_SNS_TOPIC_ARN is set but boto3',
       SNS + "without_boto3_the_sns_path_is_blocked"),
    _m("sns_branch_dropped", "a configured topic is where the alert goes",
       OBS + "deliver.py", "    if topic:\n        return publish(topic, text)\n", "",
       SNS + "publishes_one_subject_and_message_with_the_instance_role",
       SNS + "the_test_alert_and_its_recovery_go_to_the_topic"),
    _m("observe_install_drops_the_topic", "72 writes the SNS topic into the alert env file",
       STEP + "72-observe-install.sh",
       '[ -n "${ALERT_SNS_TOPIC_ARN:-}" ] && alert+=("ALERT_SNS_TOPIC_ARN:=$ALERT_SNS_TOPIC_ARN")\n',
       "", "test_ops_continuous__the_monitor_takes_an_sns_topic_as_the_other_destination"),
    _m("alert_test_drops_the_topic", "74 hands the SNS topic to deliver.py",
       STEP + "74-alert-test.sh", "ALERT_WEBHOOK_URL|ALERT_SNS_TOPIC_ARN|", "ALERT_WEBHOOK_URL|",
       "test_ops_continuous__the_delivery_proof_publishes_to_the_sns_topic"),
    # fix round (verifier F1/F3/F4)
    _m("webhook_value_error_escapes", "a malformed webhook URL is a kept failed send, never a traceback",
       OBS + "deliver.py", "    except Exception:                  # URLError, OSError, InvalidURL",
       "    except (urllib.error.URLError, OSError):  # URLError, OSError, InvalidURL",
       SNS + "a_malformed_webhook_url_is_kept_and_never_printed"),
    _m("sns_missing_metadata_is_success", "an SNS answer without an explicit 2xx is a failed send",
       OBS + "deliver.py", '.get("HTTPStatusCode", -1)', '.get("HTTPStatusCode", 200)',
       SNS + "a_failed_publish_is_kept_and_retried"),
    _m("observe_install_owner_multiline", "the owner/escalation literals stay one line each",
       STEP + "72-observe-install.sh", "  [[ ! $literal =~ [[:cntrl:]] ]] \\\n", "  true \\\n",
       "test_ops_continuous__the_monitor_takes_an_sns_topic_as_the_other_destination"),
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
    for name in ("infrx", "tests", "deploy", "openrouter"):   # openrouter: the image copies it
        shutil.copytree(API_DIR / name, api / name, ignore=ignore)
    engine = root / "models" / "marlin2b"
    engine.mkdir(parents=True)
    shutil.copy2(REPO / "models" / "marlin2b" / "serve.sh", engine / "serve.sh")
    # W3: the pin record beside serve.sh, which the real-script case now requires (pilot gate)
    shutil.copy2(REPO / "models" / "marlin2b" / "serving-version.json", engine / "serving-version.json")
    # I2B.c: the rollout scripts one suite file reads, at their repository path
    shutil.copytree(REPO / "infra" / "rollout", root / "infra" / "rollout", ignore=ignore)
    # I8: its scripts, rules and units, and the migrations its PostgreSQL stand-in applies
    for part in (("infra", "runbooks"), ("infra", "observe"), ("infra", "alerts"),
                 ("apps", "app", "supabase", "migrations")):
        if REPO.joinpath(*part).exists():
            shutil.copytree(REPO.joinpath(*part), root.joinpath(*part), ignore=ignore)
    # E2C wiring: the rehearsal pin case compares rehearse.sh with the integration compose file
    compose = REPO / "tests" / "integration" / "compose.yaml"
    if compose.exists():
        (root / "tests" / "integration").mkdir(parents=True, exist_ok=True)
        shutil.copy2(compose, root / "tests" / "integration" / "compose.yaml")
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(API_DIR / name, api / name)
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

