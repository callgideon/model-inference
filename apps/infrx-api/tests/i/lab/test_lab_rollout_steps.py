"""LAB-DEPLOY-PREP: the Lab's box rollout steps (`infra/lab/rollout/steps/*.sh`) and the R151
gate before the hosted apply (`infra/lab/rollout/lab-migrate.sh`), run against a box stand-in:
a sandbox root (`INFRX_ROOT`, lib.sh's seam) and recording stubs on PATH for aws, systemctl,
docker, curl and python3 (the test_ops_steps pattern). Nothing touches the box, AWS, SSM,
systemd, Caddy or hosted Supabase.

Every case asserts on what the script DID (the stubs' argv, files written, exit code) and that
no secret value reached an argument or the output. The roles' env and commands the steps
enable are the ones `make lab-local` composed (tests/integration/lab_local).
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
ROLLOUT = REPO / "infra" / "lab" / "rollout"
STEPS = ROLLOUT / "steps"
SECRET = "canary-lab-secret-0f3a"          # what every stubbed SSM read answers
IMAGE = "sha256:" + "c" * 64

STUB = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with (here / "calls.log").open("a") as log:
    log.write(json.dumps({{"tool": pathlib.Path(sys.argv[0]).name, "argv": sys.argv[1:]}}) + "\\n")
answer = here / (pathlib.Path(sys.argv[0]).name + ".out")
if answer.exists():
    sys.stdout.write(answer.read_text())
once = here / (pathlib.Path(sys.argv[0]).name + ".fail-once")
if once.exists():
    once.unlink(); sys.exit(1)
sys.exit(int(os.environ.get("STUB_EXIT_" + pathlib.Path(sys.argv[0]).name, "0")))
'''


def head() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def box(tmp_path, outputs=None):
    """The stand-in: stubs on PATH and a sandbox root with the Lab's image id recorded."""
    stub = tmp_path / "bin"
    stub.mkdir()
    for tool in ("aws", "systemctl", "docker", "curl", "python3"):
        (stub / tool).write_text(STUB.format(python=sys.executable))
        (stub / tool).chmod(0o755)
    outputs = {"aws": SECRET, **(outputs or {})}
    for tool, text in outputs.items():
        (stub / f"{tool}.out").write_text(text)
    root = tmp_path / "root"
    (root / "etc" / "infrx-lab").mkdir(parents=True)
    (root / "etc" / "infrx-lab" / "image").write_text(IMAGE + "\n")
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    return stub, root


def run(step: str, stub: Path, root: Path, **env):
    script = STEPS / step if not step.startswith("/") else Path(step)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env={
        "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "HOME": str(root),
        "REPO": str(REPO), "INFRX_ROOT": str(root), "READY_S": "2", "READY_SLEEP_S": "0",
        **env})


def calls(stub: Path, tool: str | None = None) -> list[list[str]]:
    log = stub / "calls.log"
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [row["argv"] for row in rows if tool in (None, row["tool"])]


def install_units(root: Path) -> None:
    for unit in (API / "deploy" / "lab").glob("*/infrx-lab-*.service"):
        (root / "etc" / "systemd" / "system" / unit.name).write_text(unit.read_text())


def clean(done, *extra: str) -> None:
    """No secret value in any argument the step passed, nor in its output."""
    text = done.stdout + done.stderr
    assert SECRET not in text and not any(SECRET in e for e in extra), text[-400:]


def allowed_names(role: str) -> set[str]:
    """The names 50-lab-role.sh lets ROLE's env file carry (its `case` block)."""
    text = (STEPS / "50-lab-role.sh").read_text()
    common = re.search(r'^common="([^"]+)"', text, re.M).group(1).split()
    line = re.search(rf'^  {role}\) names="([^"]+)"', text, re.M).group(1)
    return set(line.replace("$common", " ".join(common)).split())


# --- every step ------------------------------------------------------------------------------
def test_ldp__every_step_is_strict_bash_on_the_releases_own_helpers():
    """Each step stops on the first failure (`set -euo pipefail`), is valid bash as ssm.sh sends
    it (one file), and reads its helpers from the checked-out release, never from the host."""
    for script in sorted(STEPS.glob("*.sh")) + [ROLLOUT / "lab-migrate.sh"]:
        text = script.read_text()
        assert "\nset -euo pipefail\n" in text, script.name
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script.name
        if script.parent == STEPS:
            assert '. "$repo/infra/lab/rollout/lib.sh"' in text, script.name


def test_ldp__preflight_refuses_another_checkout_and_reports_names_never_values(tmp_path):
    """10: a checkout that is not RELEASE is refused (exit 2); otherwise the switch, the env
    files' NAMES and the units' state are printed - never a value from those files."""
    stub, root = box(tmp_path)
    (root / "etc" / "infrx-lab" / "eval.env").write_text(f"LAB_DATABASE_URL=postgresql://u:{SECRET}@h/d\n")
    assert run("10-lab-preflight.sh", stub, root, RELEASE="0" * 40).returncode == 2
    done = run("10-lab-preflight.sh", stub, root, RELEASE=head())
    assert done.returncode == 0, done.stderr
    assert "LAB_DATABASE_URL" in done.stdout and "control switch (marker): off" in done.stdout
    clean(done)


def test_ldp__the_image_is_built_once_from_the_release_and_its_id_recorded(tmp_path):
    """20: no infrx-lab:<RELEASE> yet -> one build from the release's Dockerfile, its id in
    /etc/infrx-lab/image; a rerun reuses the image (no second build)."""
    stub, root = box(tmp_path, {"docker": "sha256:" + "d" * 64 + "\n"})
    (root / "etc" / "infrx-lab" / "image").unlink()
    (stub / "docker.fail-once").touch()                     # inspect: not built yet
    release = head()
    done = run("20-lab-image.sh", stub, root, RELEASE=release)
    assert done.returncode == 0, done.stderr
    docker = calls(stub, "docker")
    assert [c[0] for c in docker] == ["image", "build"], docker
    build = docker[1]
    assert build[:2] == ["build", "--provenance=false"] and f"infrx-lab:{release}" in build
    assert str(API / "deploy" / "Dockerfile") in build
    image = root / "etc/infrx-lab/image"
    assert image.exists() and image.read_text().strip() == "sha256:" + "d" * 64
    assert run("20-lab-image.sh", stub, root, RELEASE=release).returncode == 0
    assert [c[0] for c in calls(stub, "docker")] == ["image", "build", "image"]


def test_ldp__units_are_installed_and_none_is_enabled(tmp_path):
    """30: every Lab unit of the release lands in systemd; nothing is enabled or started
    (each waits for its switch)."""
    stub, root = box(tmp_path)
    done = run("30-lab-units.sh", stub, root, RELEASE=head())
    assert done.returncode == 0, done.stderr
    installed = {p.name for p in (root / "etc/systemd/system").iterdir()}
    assert {"infrx-lab-control.service", "infrx-lab-eval.service", "infrx-lab-judge.service",
            "infrx-lab-datasets.service"} <= installed
    assert calls(stub, "systemctl") == [["daemon-reload"]]


def test_ldp__control_on_writes_its_env_by_ssm_name_then_the_switch_then_readiness(tmp_path):
    """40 on: the DSN and anon key are read on the box by parameter NAME (the value only in
    the 0600 file), INFRX_LAB_IMAGE is the Lab image, then the marker, enable + restart and
    127.0.0.1:8003/readyz. http origins and a missing image are refused with nothing written."""
    stub, root = box(tmp_path)
    install_units(root)
    args = {"STATE": "on", "RELEASE": head(), "CONTROL_DSN_PARAM": "/model-inference/lab/control_dsn",
            "ANON_KEY_PARAM": "/model-inference/lab/anon", "SUPABASE_URL": "https://p.supabase.co",
            "LAB_ORIGIN": "https://lab.callbill.ai"}
    env_file = root / "etc" / "infrx-lab-control.env"
    for bad in ({"SUPABASE_URL": "http://p.supabase.co"}, {"LAB_ORIGIN": "https://lab.x/path"}):
        assert run("40-lab-control.sh", stub, root, **{**args, **bad}).returncode == 2
    assert not env_file.exists() and calls(stub, "aws") == []
    done = run("40-lab-control.sh", stub, root, **args)
    assert done.returncode == 0, done.stderr
    names = [line.split("=", 1)[0] for line in env_file.read_text().splitlines()]
    assert names == ["INFRX_LAB_IMAGE", "INFRX_LAB_DATABASE_URL", "INFRX_LAB_SUPABASE_URL",
                     "INFRX_LAB_SUPABASE_ANON_KEY", "INFRX_LAB_ORIGIN"]
    assert f"INFRX_LAB_IMAGE={IMAGE}" in env_file.read_text() and SECRET in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert [c[c.index("--name") + 1] for c in calls(stub, "aws")] == \
        ["/model-inference/lab/control_dsn", "/model-inference/lab/anon"]
    assert (root / "etc/infrx-lab/enabled").exists()
    assert calls(stub, "systemctl") == [["enable", "infrx-lab-control.service"],
                                        ["restart", "infrx-lab-control.service"]]
    assert calls(stub, "curl")[-1][-1] == "http://127.0.0.1:8003/readyz"
    clean(done, *[a for c in calls(stub) for a in c])
    (root / "etc/infrx-lab/image").unlink()
    assert run("40-lab-control.sh", stub, root, **args).returncode == 2


def test_ldp__control_off_removes_the_switch_and_not_ready_is_exit_4(tmp_path):
    stub, root = box(tmp_path)
    install_units(root)
    marker = root / "etc/infrx-lab/enabled"
    marker.touch()
    assert run("40-lab-control.sh", stub, root, STATE="off").returncode == 0
    assert not marker.exists()
    assert calls(stub, "systemctl") == [["disable", "--now", "infrx-lab-control.service"]]
    done = run("40-lab-control.sh", stub, root, STATE="on", RELEASE=head(),
               CONTROL_DSN_PARAM="/a", ANON_KEY_PARAM="/b", SUPABASE_URL="https://p.supabase.co",
               LAB_ORIGIN="https://lab.callbill.ai", STUB_EXIT_curl="7")
    assert done.returncode == 4 and "journalctl -u infrx-lab-control" in done.stderr


def test_ldp__a_role_env_file_is_its_switch_and_carries_only_its_names(tmp_path):
    """50 on: INFRX_IMAGE (the Lab image) plus exactly the SPEC's names, secrets read by SSM
    name; the pooler budget judged on the staged file; enable + restart; its unit's readyz
    port. Refused with nothing written: another role's or a consumer name, a secret as a
    literal, live judging, no database login, an unknown role."""
    stub, root = box(tmp_path)
    install_units(root)
    spec = ("LAB_DATABASE_URL=/model-inference/lab/eval_dsn LAB_S3_BUCKET:=lab-bucket "
            "LAB_EVAL_ENDPOINT_URL:=https://marlin2b.callbill.ai/v1 "
            "LAB_EVAL_ENDPOINT_KEY=/model-inference/lab/eval_key")
    base = {"STATE": "on", "ROLE": "eval", "RELEASE": head()}
    env_file = root / "etc/infrx-lab/eval.env"
    for role, bad in (("eval", spec + " SUPABASE_SERVICE_ROLE_KEY=/x"),
                      ("eval", spec.replace("LAB_EVAL_ENDPOINT_KEY=/", "LAB_EVAL_ENDPOINT_KEY:=/")),
                      ("judge", "LAB_DATABASE_URL=/a JUDGE_MODE:=live"),
                      ("eval", "LAB_S3_BUCKET:=lab-bucket"),
                      ("trainer", spec)):
        done = run("50-lab-role.sh", stub, root, **{**base, "ROLE": role, "SPEC": bad})
        assert done.returncode == 2, (role, bad, done.stderr)
    assert not list((root / "etc/infrx-lab").glob("*.env")) and calls(stub, "aws") == []
    done = run("50-lab-role.sh", stub, root, **base, SPEC=spec)
    assert done.returncode == 0, done.stderr
    names = [line.split("=", 1)[0] for line in env_file.read_text().splitlines()]
    assert names == ["INFRX_IMAGE", "LAB_DATABASE_URL", "LAB_S3_BUCKET", "LAB_EVAL_ENDPOINT_URL",
                     "LAB_EVAL_ENDPOINT_KEY"]
    assert f"INFRX_IMAGE={IMAGE}" in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    budget = calls(stub, "python3")[-1]
    assert budget[0].endswith("infra/lab/workers/eval/pool_budget.py") and "--lab-env-dir" in budget
    assert calls(stub, "systemctl")[:2] == [["enable", "infrx-lab-eval.service"],
                                            ["restart", "infrx-lab-eval.service"]]
    assert calls(stub, "curl")[-1][-1] == "http://127.0.0.1:8012/readyz"   # the unit's port
    clean(done, *[a for c in calls(stub) for a in c])
    assert run("50-lab-role.sh", stub, root, STATE="off", ROLE="eval").returncode == 0
    assert not env_file.exists()


def test_ldp__a_budget_or_preflight_refusal_replaces_nothing(tmp_path):
    """50: the annotation/training/rollout preflight and the Lab pooler budget judge the
    STAGED file; a refusal (exit 3) leaves the current env file as it was, no staged copy."""
    stub, root = box(tmp_path)
    install_units(root)
    env_file = root / "etc/infrx-lab/rollout.env"
    env_file.write_text("previous\n")
    done = run("50-lab-role.sh", stub, root, STATE="on", ROLE="rollout", RELEASE=head(),
               SPEC="LAB_DATABASE_URL=/a LAB_S3_BUCKET:=b", STUB_EXIT_python3="1")
    assert done.returncode == 3 and "preflight" in done.stderr
    assert env_file.read_text() == "previous\n"
    assert [p.name for p in (root / "etc/infrx-lab").iterdir() if "staged" in p.name] == []
    (stub / "python3.fail-once").touch()                   # eval: no preflight; the budget fails
    done = run("50-lab-role.sh", stub, root, STATE="on", ROLE="eval", RELEASE=head(),
               SPEC="LAB_DATABASE_URL=/a LAB_S3_BUCKET:=b")
    assert done.returncode == 3 and "budget" in done.stderr
    assert not (root / "etc/infrx-lab/eval.env").exists()
    assert calls(stub, "systemctl") == []


def test_ldp__a_role_that_refuses_by_name_is_exit_5_and_other_unreadiness_exit_4(tmp_path):
    """50: the unit's main process exited 2 (R198: no work source on this release; R211: never
    restarted) is exit 5 naming it; any other unreadiness is exit 4."""
    stub, root = box(tmp_path, {"systemctl": "2"})
    install_units(root)
    args = {"STATE": "on", "ROLE": "checkpoints", "RELEASE": head(),
            "SPEC": "LAB_DATABASE_URL=/a", "STUB_EXIT_curl": "7"}
    done = run("50-lab-role.sh", stub, root, **args)
    assert done.returncode == 5 and "R198" in done.stderr
    (stub / "systemctl.out").write_text("1")
    assert run("50-lab-role.sh", stub, root, **args).returncode == 4


def test_ldp__the_smoke_checks_every_switch_that_is_on_and_the_app(tmp_path):
    """60: the App's :8001/:8002 always; the control service only with the marker; a role only
    with its env file, on its unit's port; any unready answer is exit 1."""
    stub, root = box(tmp_path)
    install_units(root)
    (root / "etc/infrx-lab/enabled").touch()
    (root / "etc/infrx-lab/datasets.env").write_text("x=y\n")
    done = run("60-lab-smoke.sh", stub, root)
    assert done.returncode == 0, done.stderr
    assert [c[-1] for c in calls(stub, "curl")] == [f"http://127.0.0.1:{p}/readyz"
                                                    for p in (8001, 8002, 8003, 8011)]
    assert run("60-lab-smoke.sh", stub, root, STUB_EXIT_curl="7").returncode == 1


def test_ldp__revert_turns_every_switch_off_then_the_site_then_checks_the_app(tmp_path):
    """90: every role and the control service off (switches removed) BEFORE the edge reload,
    and the App's readiness last; the units stay installed (inert)."""
    stub, root = box(tmp_path)
    install_units(root)
    lab = root / "etc/infrx-lab"
    for name in ("enabled", "eval.env", "judge.env"):
        (lab / name).write_text("x\n")
    (root / "etc/infrx-lab-control.env").write_text("x\n")
    site = root / "etc/caddy/lab/lab-control.caddy"
    site.parent.mkdir(parents=True)
    site.write_text("x\n")
    done = run("90-lab-revert.sh", stub, root)
    assert done.returncode == 0, done.stderr
    assert not list(lab.glob("*.env")) and not (lab / "enabled").exists() and not site.exists()
    assert not (root / "etc/infrx-lab-control.env").exists()
    order = [(row["tool"], row["argv"]) for row in
             map(json.loads, (stub / "calls.log").read_text().splitlines())]
    disables = [i for i, (tool, argv) in enumerate(order) if argv[:1] == ["disable"]]
    reload_at = next(i for i, (tool, argv) in enumerate(order) if tool == "docker")
    assert len(disables) == 8 and max(disables) < reload_at
    assert [argv[-1] for tool, argv in order[reload_at + 1:]] == \
        ["http://127.0.0.1:8001/readyz", "http://127.0.0.1:8002/readyz"]
    assert (root / "etc/systemd/system/infrx-lab-eval.service").exists()


def test_ldp__the_site_reaches_the_edge_only_after_it_validates_with_the_apps(tmp_path):
    """45 on: refused without WR-I2L-1's import line; the Lab site validated with the App's
    live Caddyfile by the pinned Caddy (no network) BEFORE it is renamed into place; a file that
    does not validate never reaches the edge (exit 4, no reload)."""
    stub, root = box(tmp_path)
    caddy = root / "etc/caddy"
    caddy.mkdir(parents=True)
    (caddy / "Caddyfile").write_text("app.example {\n}\n")
    assert run("45-lab-site.sh", stub, root, STATE="on", RELEASE=head()).returncode == 2
    (caddy / "Caddyfile").write_text("app.example {\n}\nimport /etc/caddy/lab/*.caddy\n")
    (stub / "docker.fail-once").touch()                    # validate refuses
    done = run("45-lab-site.sh", stub, root, STATE="on", RELEASE=head())
    assert done.returncode == 4 and not (caddy / "lab/lab-control.caddy").exists()
    assert len(calls(stub, "docker")) == 1
    done = run("45-lab-site.sh", stub, root, STATE="on", RELEASE=head())
    assert done.returncode == 0, done.stderr
    validate, reload_ = calls(stub, "docker")[-2:]
    assert validate[:4] == ["run", "--rm", "--network", "none"] and "validate" in validate
    assert reload_[:3] == ["exec", "caddy", "caddy"] and "reload" in reload_
    assert (caddy / "lab/lab-control.caddy").read_text() == \
        (API / "deploy/lab/app/lab-control.caddy").read_text()


def test_ldp__each_role_the_step_enables_can_start_on_the_names_it_allows():
    """The step and the composition agree: every name a role's entry point requires
    (`infrx.lab.workers` NEEDS, minus the unit's health port and the step's image) is one the
    step lets that role's env file carry - else the role could never be switched on."""
    sys.path.insert(0, str(API))
    from infrx.lab.workers import __main__ as workers
    for role in workers.ROLES:
        needs = {workers.DATABASE, *workers.NEEDS[role]}
        assert needs <= allowed_names(role), (role, needs - allowed_names(role))
        assert "SUPABASE_SERVICE_ROLE_KEY" not in allowed_names(role)


# --- the R151 gate ------------------------------------------------------------------------------
def gate(tmp_path, *, pending: str, known_good: int, window: str | None = "P-08:2026-10-01"):
    """lab-migrate.sh with hosted-migrate.sh, known-good.py and the migrations stubbed."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ("0026_fence.sql", "0027_lab_access.sql", "0028_lab_x.sql"):
        (migrations / name).write_text("select 1;\n")
    hosted = tmp_path / "hosted-migrate.sh"
    hosted.write_text(f'#!/usr/bin/env bash\nEXPECTED_PENDING="{pending}"\n# 0028_lab_x post-check\n'
                      f'echo "hosted-migrate $*" > {tmp_path}/ran\n')
    hosted.chmod(0o755)
    kg = tmp_path / "known-good.py"
    kg.write_text(f"import sys\nprint(sys.argv[1:])\nsys.exit({known_good})\n")
    args = ["bash", str(ROLLOUT / "lab-migrate.sh"), "--release", "a" * 40, "--hosted-at", "0026"]
    if window:
        args += ["--window", window]
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO, env={
        **os.environ, "HOSTED_MIGRATE": str(hosted), "KNOWN_GOOD": str(kg), "PY": sys.executable,
        "MIGRATIONS": str(migrations)})


def test_ldp__the_hosted_lab_apply_needs_all_three_r151_conditions(tmp_path):
    """R151/R201: without the operator window, the reviewed EXPECTED_PENDING or a KNOWN-GOOD
    rollback target at the newest migration, nothing is dialled (exit 2, hosted-migrate never
    runs); with all three, hosted-migrate.sh runs exactly as it always does."""
    for case, kw in (("window", {"pending": "0027, 0028", "known_good": 0, "window": None}),
                     ("pending", {"pending": "0019, 0020", "known_good": 0}),
                     ("known-good", {"pending": "0027, 0028", "known_good": 1})):
        sub = tmp_path / case
        sub.mkdir()
        done = gate(sub, **kw)
        assert done.returncode == 2 and "R151" in done.stderr, (case, done.stderr)
        assert not (sub / "ran").exists(), case
    done = gate(tmp_path, pending="0027, 0028", known_good=0)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "ran").read_text().split() == \
        ["hosted-migrate", "--release", "a" * 40, "--through", "w6b"]


def test_ldp__todays_hosted_migrate_is_refused_for_the_lab_apply():
    """The tree as it stands: hosted-migrate.sh's EXPECTED_PENDING is W7's 0019-0026, so the
    Lab apply (0027 on) is refused until the reviewed patch lands (R151 condition 2)."""
    done = subprocess.run(["bash", str(ROLLOUT / "lab-migrate.sh"), "--release", "a" * 40,
                           "--hosted-at", "0026", "--window", "P-08:dry"], capture_output=True,
                          text=True, cwd=REPO, env={**os.environ, "KNOWN_GOOD": "/bin/true",
                                                    "PY": "/usr/bin/env"})
    assert done.returncode == 2 and "condition 2" in done.stderr, done.stderr
