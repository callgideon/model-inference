#!/usr/bin/env python3
"""r1 R32/R40 for track I: one single-edit defect per invariant this suite claims.

Same vocabulary as `tests/contracts/mutants.py` (`Mutant`, `Outcome`, `Result`), a
runner of I's own because the mutated file is not in the `infrx` package: it is
`deploy/preflight.py`, plus - for the two cases that are claims about the *runtime* -
`infrx/media/fetch.py` and `infrx/gateway/app.py`. The copied tree therefore carries
`infrx/`, `tests/` and `deploy/`, and `models/marlin2b/serve.sh`, which one case reads
as it stands.

A kill needs pytest to exit 1 with only the mutant's own named cases failing, so a
syntax error, an import error or a defect with wider reach than declared is
`broken_runner` and fails the run exactly as a survivor does.

    uv run --frozen pytest -q tests/i/test_mutants.py     # the whole list
    uv run --frozen python tests/i/mutants.py --list
    uv run --frozen python tests/i/mutants.py denied_read_tolerated
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

API_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO = API_DIR.parents[1]
SUITE = "tests/i"
NESTED_TIMEOUT_S = 300


def _shared():
    """`Mutant`/`Outcome`/`Result`/`_failing_ids` from the contracts list, loaded by
    path: under `--import-mode=importlib` the `tests` package is synthesised by pytest
    and a cross-directory import is not reliably available."""
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("i_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SHARED = _shared()
Mutant, Outcome, Result = _SHARED.Mutant, _SHARED.Outcome, _SHARED.Result
PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED = 0, 1

P = "deploy/preflight.py"               # relative to apps/infrx-api, not to `infrx`


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


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
    _m("one_unit_restarted", "every unit that reads the env file is restarted",
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
       P, '"--mode", cfg.mode, "--env-file", str(staged)],',
       '"--mode", cfg.mode, "--env-file", str(staged),\n'
       '                           *sorted(read_env(staged).values())],',
       "test_deploy_failclosed__no_secret_value_reaches_stdout_stderr_or_an_argument"),
    _m("value_printed_on_success", "a secret value never reaches stdout",
       P, '    print(f"mode={cfg.mode} keys=" + ",".join(sorted(values)))',
       '    print(f"mode={cfg.mode} " + str(values))',
       "test_deploy_failclosed__no_secret_value_reaches_stdout_stderr_or_an_argument"),
    _m("value_printed_on_refusal", "a refusal names settings, never values",
       P, 'return (f"{key.env}: the value contains a newline or NUL and would write a "',
       'return (f"{key.env}: {value} contains a newline or NUL and would write a "',
       "test_deploy_failclosed__a_value_cannot_write_a_second_variable"),
    # --- prerequisites (brief item 4) --------------------------------------------------
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
       "test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer"),
)


def _pytest(root: pathlib.Path, files: list[str], selection: str):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         "-rf", "--tb=no", *files, "-k", selection],
        cwd=root, capture_output=True, text=True, timeout=NESTED_TIMEOUT_S,
        env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin",
             "HOME": str(root), "TMPDIR": str(root / "tmp")})


def run_mutant(mutant) -> Result:
    """Apply one mutant to a throwaway copy of the tree and run the cases it names.

    A kill needs all three: pytest exited 1, at least one test failed, and every
    failing id names one of the mutant's own cases. The worktree is never written to.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"i0-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__", ".venv")
        for name in ("infrx", "tests", "deploy"):
            shutil.copytree(API_DIR / name, root / name, ignore=ignore)
        # One case reads `models/marlin2b/serve.sh` as it stands, so the copy needs it.
        engine = root / "models" / "marlin2b"
        engine.mkdir(parents=True)
        shutil.copy2(REPO / "models" / "marlin2b" / "serve.sh", engine / "serve.sh")
        (root / "tmp").mkdir()
        shutil.copy2(API_DIR / "pyproject.toml", root / "pyproject.toml")
        target = root / mutant.file
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        files = sorted(files_for(mutant.cases))
        if not files:
            return Result(Outcome.misdeclared, f"no file defines any of {list(mutant.cases)}")
        try:
            done = _pytest(root, files, " or ".join(mutant.cases))
        except subprocess.TimeoutExpired:
            return Result(Outcome.broken_runner,
                          f"the named cases did not finish within {NESTED_TIMEOUT_S}s")
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not re.search(r"(\d+) (?:passed|failed|skipped)", summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched: {summary}")
        failed, errored = _SHARED._failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(case in test_id for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        return Result(Outcome.killed, summary)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="run track I's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:38s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    bad = {}
    for mutant in (m for m in MUTANTS if not args.names or m.name in args.names):
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad[mutant.name] = result.detail
    print(f"\n{len(bad)} not killed" if bad else "\nall killed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
