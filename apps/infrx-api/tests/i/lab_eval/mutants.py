#!/usr/bin/env python3
"""R32/R40/R83 for I5: one single-edit defect per decision `test_units.py` and
`test_pool_budget.py` claim. The mutated files are outside `infrx` (the units, the Lab
budget script, and the consumer's installer and unit the isolation cases read), so the copy
has a layout of its own (track I's pattern). The PostgreSQL/MinIO drills
(`test_drills_pg.py`) are outside the runner.

    uv run --frozen pytest -q tests/i/lab_eval/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_eval/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
REPO = API_DIR.parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))



def _shared():
    """A private copy of the shared runner (track I's pattern, tests/i/mutants.py): the
    compile rule is relaxed for this copy only, never for another list in the process."""
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("i5_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compile_python_only(source, filename, mode, *args, **kwargs):
    """Unit files and bash are not Python: only a `.py` target must compile."""
    if str(filename).endswith(".py"):
        return compile(source, filename, mode, *args, **kwargs)
    return None


shared = _shared()
shared.compile = _compile_python_only
Mutant, Result, Runner = shared.Mutant, shared.Result, shared.Runner

SUITES = ("tests/i/lab_eval/test_units.py", "tests/i/lab_eval/test_pool_budget.py")
EVAL, DATASETS = "deploy/lab/eval/infrx-lab-eval.service", "deploy/lab/eval/infrx-lab-datasets.service"
CKPT = "deploy/lab/eval/infrx-lab-checkpoints.service"
PB = "../../infra/lab/workers/eval/pool_budget.py"

BOUNDED = "test_i5_each_role_is_its_own_bounded_unit_calling_the_documented_entry_point"
OFF = "test_i5_every_role_is_off_until_its_env_file_exists_and_nothing_installs_it"
UNCOUPLED = "test_i5_no_consumer_unit_depends_on_a_lab_unit_or_the_reverse"
ROWS = "test_i5_the_consumer_budget_is_i8s_and_each_enabled_role_adds_its_concurrency_plus_one"
SHARE = "test_i5_the_lab_cannot_take_the_consumers_share"
CLI = "test_i5_the_cli_reads_only_the_enabled_roles_env_files_and_never_prints_a_dsn"


def m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- the units: bounds and entry point
    m("i5_eval_memory_unbounded", "a Lab worker's memory is bounded", EVAL,
      "--memory 1g --memory-swap 1g ", "", BOUNDED, dies_by=("AttributeError",)),
    m("i5_eval_swap_unbounded", "swap never extends the memory bound", EVAL,
      "--memory-swap 1g", "--memory-swap -1", BOUNDED),
    m("i5_eval_outweighs_inference", "a Lab worker yields the CPU to inference", EVAL,
      "--cpu-shares 256 ", "", BOUNDED),
    m("i5_eval_pids_unbounded", "a Lab worker's processes are bounded", EVAL,
      "--pids-limit 128 ", "", BOUNDED),
    m("i5_eval_root_writable", "the root filesystem is read-only", EVAL,
      "--read-only ", "", BOUNDED),
    m("i5_eval_scratch_unbounded", "the only scratch space is a bounded tmpfs", EVAL,
      "--tmpfs /tmp:rw,size=256m,mode=1777", "--tmpfs /tmp:rw,mode=1777", BOUNDED),
    m("i5_eval_capabilities_kept", "capabilities are dropped", EVAL,
      "--cap-drop ALL ", "", BOUNDED),
    m("i5_eval_consumer_uid", "not the consumer worker's uid", EVAL,
      "--user 10003:10000", "--user 10002:10000", BOUNDED),
    m("i5_eval_restart_always", "a clean exit is not restarted", EVAL,
      "Restart=on-failure", "Restart=always", BOUNDED),
    m("i5_eval_crash_loop_unbounded", "a crash loop stops", EVAL,
      "StartLimitBurst=5\n", "", BOUNDED),
    m("i5_eval_other_entry_point", "the unit runs the documented entry point", EVAL,
      "python -m infrx.lab.workers eval", "python -m infrx.worker", BOUNDED),
    m("i5_checkpoints_shares_a_port", "each role its own health port", CKPT,
      "LAB_WORKER_HEALTH_PORT=8013", "LAB_WORKER_HEALTH_PORT=8011", BOUNDED),
    m("i5_eval_no_drain_bound", "a stop waits a bounded drain", EVAL,
      "TimeoutStopSec=90\n", "", BOUNDED),
    # --- OFF by default
    m("i5_eval_on_by_default", "a role starts only with its env file", EVAL,
      "ConditionPathExists=/etc/infrx-lab/eval.env\n", "", OFF),
    m("i5_datasets_reads_the_gateway_env", "never the consumer's env file", DATASETS,
      "EnvironmentFile=/etc/infrx-lab/datasets.env", "EnvironmentFile=/etc/marlin2b-gateway.env",
      OFF),
    m("i5_installer_names_a_lab_unit", "the installer never installs a Lab unit",
      "deploy/install.sh", "#!/usr/bin/env bash\n",
      "#!/usr/bin/env bash\n# also installs infrx-lab-eval.service\n", OFF),
    # --- uncoupled from the consumer
    m("i5_eval_part_of_inference", "a consumer restart never takes a Lab unit along", EVAL,
      "Requires=docker.service\n", "Requires=docker.service\nPartOf=marlin2b-vllm.service\n",
      UNCOUPLED),
    m("i5_eval_needs_the_worker", "a Lab unit requires only docker", EVAL,
      "Requires=docker.service\n", "Requires=docker.service infrx-worker.service\n", UNCOUPLED),
    m("i5_eval_ordered_after_inference", "a Lab unit is not ordered after a consumer one", EVAL,
      "After=docker.service network-online.target\n",
      "After=docker.service network-online.target marlin2b-vllm.service\n", UNCOUPLED),
    m("i5_consumer_wants_a_lab_unit", "no consumer unit names a Lab unit",
      "deploy/infrx-worker.service", "Wants=network-online.target\n",
      "Wants=network-online.target infrx-lab-eval.service\n", UNCOUPLED),
    # --- the pool budget
    m("i5_budget_consumer_differs", "the consumer rows are I8's own", PB,
      "headroom=headroom,\n                            txn_limit=txn_limit)",
      "headroom=headroom,\n                            txn_limit=txn_limit + 1)", ROWS),
    m("i5_budget_no_relay_connection", "a role holds its concurrency plus the relay", PB,
      '"peak": n + 1,', '"peak": n,', ROWS, SHARE, CLI),
    m("i5_budget_lab_left_out_of_the_pooler", "the Lab counts toward the transaction pooler", PB,
      'txn_peak = consumer["verdicts"]["transaction"]["peak"] + lab_peak',
      'txn_peak = consumer["verdicts"]["transaction"]["peak"]', ROWS),
    m("i5_budget_warning_dropped", "the shared server connections are called out", PB,
      "queue behind them (staging measurement owed, RUNBOOK.md)\"] if lab_peak else []",
      "queue behind them (staging measurement owed, RUNBOOK.md)\"] if False else []", ROWS),
    m("i5_budget_lab_unlimited", "the Lab has its own allotment", PB,
      '"ok": lab_peak <= lab_limit}', '"ok": True}', SHARE, CLI),
    m("i5_budget_lab_off_by_one", "the allotment itself is allowed", PB,
      '"ok": lab_peak <= lab_limit}', '"ok": lab_peak < lab_limit}', SHARE),
    m("i5_budget_session_allowed", "no Lab role on the session pooler", PB,
      '"ok": not on_session}', '"ok": True}', SHARE),
    m("i5_budget_headroom_ignored", "the reserved headroom is kept", PB,
      '"ok": txn_peak + headroom <= txn_limit}', '"ok": txn_peak <= txn_limit}', SHARE),
    m("i5_budget_zero_concurrency", "a role runs at least one at a time", PB,
      "        if n < 1:\n", "        if n < 0:\n", SHARE),
    m("i5_budget_unknown_role", "only the three roles", PB,
      "        if role not in ROLES:\n", "        if False:\n", SHARE, dies_by=("KeyError",)),
    m("i5_cli_exit_ignores_verdicts", "a FAIL exits 1", PB,
      'return 0 if result["ok"] else 1', "return 0", CLI),
    m("i5_cli_counts_absent_roles", "a role without its env file is OFF", PB,
      "            if path.is_file():\n", "            if True:\n", CLI,
      dies_by=("FileNotFoundError",)),
)


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", ".venv")
    for name in ("infrx", "tests", "deploy"):
        shutil.copytree(API_DIR / name, api / name, ignore=ignore)
    for part in (("infra", "runbooks"), ("infra", "lab"), ("infra", "rollout")):
        shutil.copytree(REPO.joinpath(*part), root.joinpath(*part), ignore=ignore)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(API_DIR / name, api / name)
    return api


def case_names() -> set[str]:
    names = set()
    for suite in SUITES:
        tree = ast.parse((API_DIR / suite).read_text())
        names |= {node.name for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    return names


# `package=""`: a mutant's `file` is relative to `apps/infrx-api`.
RUNNER = Runner(name="i5", targets=SUITES, package="", layout=_layout, require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the I5 Lab worker mutation list"))
