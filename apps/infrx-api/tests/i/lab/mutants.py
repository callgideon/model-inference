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
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


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


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run I2L's mutation list"))
