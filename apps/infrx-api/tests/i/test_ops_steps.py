"""I8: the box steps and coordinator-host scripts of the continuous-operations lane, run
against stubs (PATH is the only seam: docker, curl, aws, systemctl, nvidia-smi, git). The
coordinator runs them for real; nothing here touches the box, AWS or hosted Supabase.

Every case asserts on what the script *did* (the stub's recorded argv/stdin, files written,
exit code) and that no secret value reached an argument or the output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import support

ROLLOUT = support.REPO / "infra" / "rollout"
STEPS = ROLLOUT / "steps"

RECORDING_STUB = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with (here / "calls.log").open("a") as log:
    log.write(json.dumps({{"tool": pathlib.Path(sys.argv[0]).name, "argv": sys.argv[1:],
                          "stdin": stdin}}) + "\\n")
answer = here / (pathlib.Path(sys.argv[0]).name + ".out")
if answer.exists():
    sys.stdout.write(answer.read_text())
sys.exit(int(os.environ.get("STUB_EXIT_" + pathlib.Path(sys.argv[0]).name.replace("-", "_"), "0")))
'''


def stubs(tmp_path, *tools, outputs=None):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for tool in tools:
        (stub / tool).write_text(RECORDING_STUB.format(python=sys.executable))
        (stub / tool).chmod(0o755)
    for tool, text in (outputs or {}).items():
        (stub / f"{tool}.out").write_text(text)
    return stub


def calls(stub):
    log = stub / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_step(text: str, stub, env=None, stdin=None):
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, input=stdin,
                          env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                               **(env or {})})


def test_ops_continuous__the_pool_budget_step_hands_the_env_file_over_stdin(tmp_path):
    """71-pool-budget.sh: the installed env file reaches the budget script on stdin, inside
    the installed runtime image, with no network; no value is an argument; a proposed change
    travels as --set pairs."""
    canary = f"{support.MARKER}-dsn"
    env_file = tmp_path / "marlin2b-gateway.env"
    image = "sha256:" + "a" * 64
    env_file.write_text(f"INFRX_MODE=pilot\nINFRX_IMAGE={image}\n"
                        f"DATABASE_URL=postgresql://u:{canary}@h:5432/d\n")
    stub = stubs(tmp_path, "docker")
    text = (STEPS / "71-pool-budget.sh").read_text().replace(
        "/etc/marlin2b-gateway.env", str(env_file)).replace(
        "/home/ubuntu/model-inference", str(support.REPO))    # an I8+ checkout (the guard)
    done = run_step(text, stub, env={"SET": "DATABASE_POOL_MAX_SIZE=6"})
    assert done.returncode == 0, done.stderr
    (call,) = calls(stub)
    argv = call["argv"]
    assert argv[:5] == ["run", "--rm", "-i", "--network", "none"] and image in argv
    assert argv[-4:] == ["--units", "/repo/apps/infrx-api/deploy", "--set",
                         "DATABASE_POOL_MAX_SIZE=6"]
    assert canary in call["stdin"] and not any(canary in a for a in argv)
    assert support.MARKER not in done.stdout + done.stderr
    env_file.write_text("INFRX_MODE=pilot\n")                        # no image: refused
    assert run_step(text, stub).returncode == 2


DOCKER_OBSERVE = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
record = {{"argv": args}}
if "--env-file" in args:
    record["env_file"] = args[args.index("--env-file") + 1]
    record["env_names"] = [l.split("=", 1)[0] for l in
                           pathlib.Path(record["env_file"]).read_text().splitlines()]
with (here / "docker.log").open("a") as log:
    log.write(json.dumps(record) + "\\n")
if "infrx.observe.alerts" in args:
    print(json.dumps({{"alert": "StuckHolds", "severity": "page", "value": 1, "labels": {{}},
                      "summary": "s", "runbook": "r"}}))
'''


def test_ops_continuous__one_observe_cycle_probes_exports_evaluates_and_delivers(tmp_path):
    """observe.sh: the host probe, then the durable exporter in the installed image with the
    DSN in a --env-file only (gone afterwards), then the evaluator over the merged rule set,
    then delivery - which, with no P-25 destination, is BLOCKED (exit 3), the alert kept."""
    canary = f"{support.MARKER}-dsn"
    env_file = tmp_path / "marlin2b-gateway.env"
    image = "sha256:" + "b" * 64
    env_file.write_text(f"INFRX_IMAGE={image}\nDATABASE_URL=postgresql://u:{canary}@h:5432/d\n"
                        f"SUPABASE_SERVICE_ROLE_KEY={canary}\n")
    stub = stubs(tmp_path, "nvidia-smi", "curl", "systemctl", "du", "install")
    (stub / "docker").write_text(DOCKER_OBSERVE.format(python=sys.executable))
    (stub / "docker").chmod(0o755)
    metrics, run = tmp_path / "metrics", tmp_path / "run"
    metrics.mkdir(), run.mkdir()
    done = run_step((support.REPO / "infra" / "observe" / "observe.sh").read_text(), stub, env={
        "REPO": str(support.REPO), "METRICS_DIR": str(metrics), "ENV_FILE": str(env_file),
        "RUNTIME_DIRECTORY": str(run), "HOME": str(tmp_path)})
    assert done.returncode == 3, (done.stdout, done.stderr)             # BLOCKED on P-25
    assert "BLOCKED" in done.stdout and canary not in done.stdout + done.stderr
    docker = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
    durable, evaluate = docker
    assert durable["env_names"] == ["DATABASE_URL"]                     # only the DSN crosses
    assert not any(canary in a for a in durable["argv"]) and image in durable["argv"]
    assert "--read-only" in durable["argv"] and "/observe/durable.py" in durable["argv"]
    assert "infrx.observe.alerts" in evaluate["argv"] and "/rules.json" in evaluate["argv"]
    assert list(run.iterdir()) == []                                    # DSN file removed
    assert "StuckHolds" in (metrics / "undelivered.jsonl").read_text()
    assert (metrics / "host.prom").exists()


def test_ops_continuous__installing_the_monitor_writes_env_files_from_ssm_by_name(tmp_path):
    """72-observe-install.sh: the four units from the release's deploy dir, the canary key
    and (when P-25 names one) the webhook read from SSM on the box into 0600 files - by
    parameter NAME on the command line, the value only in the file - then the timers.
    Oracle (P-24): no canary key parameter is a refusal (no default key), and the recurring
    canary timer is enabled only with P24_APPROVED=<ref>; the observe timer always is."""
    secret = f"{support.MARKER}-ssm-value"
    stub = stubs(tmp_path, "aws", "systemctl", "git", outputs={"aws": secret + "\n"})
    (stub / "git.out").write_text("c" * 40 + "\n")
    (stub / "install").write_text("#!/usr/bin/env bash\n"
                                  'if [ "$1" = -d ]; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi\n')
    (stub / "install").chmod(0o755)
    root = tmp_path / "root"
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    env = {"RELEASE": "c" * 40, "CANARY_VIDEO": str(clip), "INFRX_ROOT": str(root),
           "REPO": str(support.REPO), "ALERT_WEBHOOK_PARAM": "/model-inference/alert_webhook",
           "ALERT_OWNER": "sofia", "CANARY_KEY_PARAM": "/model-inference/canary_key"}
    # the canary key has no default (never the certify key by accident): refused, nothing written
    no_key = {k: v for k, v in env.items() if k != "CANARY_KEY_PARAM"}
    done = run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=no_key)
    assert done.returncode != 0 and "CANARY_KEY_PARAM" in done.stderr
    assert not (root / "etc" / "infrx-canary.env").exists() and calls(stub) == []
    # without P-24's approval the recurring canary (288 requests a day) is not enabled
    done = run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=env)
    assert done.returncode == 0, done.stderr
    systemctl = [c["argv"] for c in calls(stub) if c["tool"] == "systemctl"]
    assert ["enable", "--now", "infrx-observe.timer"] in systemctl
    assert not any("infrx-canary.timer" in a or "infrx-canary.service" in a
                   for argv in systemctl for a in argv), systemctl
    assert "BLOCKED (P-24)" in done.stdout + done.stderr
    (stub / "calls.log").unlink()
    env["P24_APPROVED"] = "P-24/2026-09-25"
    done = run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=env)
    assert done.returncode == 0, done.stderr
    assert secret not in done.stdout + done.stderr
    canary = root / "etc" / "infrx-canary.env"
    alert = root / "etc" / "infrx-alert.env"
    assert canary.read_text() == f"INFRX_CANARY_KEY={secret}\nCANARY_VIDEO={clip}\n"
    assert f"ALERT_WEBHOOK_URL={secret}\n" in alert.read_text() and "ALERT_OWNER=sofia\n" in alert.read_text()
    assert oct(canary.stat().st_mode & 0o777) == "0o600" and oct(alert.stat().st_mode & 0o777) == "0o600"
    assert not (root / "etc" / "infrx-observe.env").exists()            # no D10 monitor DSN yet
    assert sorted(p.name for p in (root / "etc" / "systemd" / "system").iterdir()) == [
        "infrx-canary.service", "infrx-canary.timer", "infrx-observe.service", "infrx-observe.timer"]
    # the monitor runs from its own pinned copy, so a runtime rollback cannot blind it
    pinned = root / "opt" / "infrx" / "observe"
    assert (pinned / "infra" / "observe" / "observe.sh").is_file()
    assert (pinned / "infra" / "alerts" / "operations.json").is_file()
    assert (pinned / "RELEASE").read_text().strip() == "c" * 40
    unit = (root / "etc" / "systemd" / "system" / "infrx-observe.service").read_text()
    assert "Environment=REPO=/opt/infrx/observe" in unit and "/home/ubuntu" not in unit
    ssm = [c["argv"] for c in calls(stub) if c["tool"] == "aws"]
    assert [a[a.index("--name") + 1] for a in ssm] == ["/model-inference/canary_key",
                                                        "/model-inference/alert_webhook"]
    systemctl = [c["argv"] for c in calls(stub) if c["tool"] == "systemctl"]
    assert ["enable", "--now", "infrx-observe.timer"] in systemctl
    assert ["enable", "--now", "infrx-canary.timer"] in systemctl
    assert "P-24/2026-09-25" in done.stdout
    # another checkout than RELEASE: refused before anything is written
    (stub / "git.out").write_text("d" * 40 + "\n")
    canary.unlink()
    assert run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=env).returncode == 2
    assert not canary.exists()


def test_ops_continuous__the_delivery_proof_is_blocked_until_p25_and_never_prints_the_url(
        tmp_path):
    """74-alert-test.sh: without /etc/infrx-alert.env it is BLOCKED (exit 3); with it, the
    marked test goes to the configured URL (here an unreachable https one: exit 4, SEND
    FAILED), the URL never printed; a RESOLVE that is not a nonce is refused."""
    step = (STEPS / "74-alert-test.sh").read_text()
    conf = tmp_path / "infrx-alert.env"
    env = {"REPO": str(support.REPO), "ALERT_ENV": str(conf)}
    stub = stubs(tmp_path)
    done = run_step(step, stub, env=env)
    assert done.returncode == 3 and "BLOCKED" in done.stderr and "P-25" in done.stderr
    url = f"https://127.0.0.1:9/{support.MARKER}"
    conf.write_text(f"ALERT_WEBHOOK_URL={url}\nALERT_OWNER=sofia\nALERT_ESCALATION=pager\n")
    done = run_step(step, stub, env=env)
    assert done.returncode == 4 and "test firing nonce=" in done.stdout
    assert support.MARKER not in done.stdout + done.stderr
    assert run_step(step, stub, env={**env, "RESOLVE": "not-a-nonce"}).returncode == 2


SNS_TOPIC = "arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts"


def test_ops_continuous__the_monitor_takes_an_sns_topic_as_the_other_destination(tmp_path):
    """72-observe-install.sh with ALERT_SNS_TOPIC_ARN (P-25's SNS form): the ARN is a plain
    value written into the 0600 alert env file, never an SSM read. Oracle: both destinations,
    a malformed ARN, or a control character in the owner/escalation literals (a second line in
    the env file) are refused before anything is written."""
    stub = stubs(tmp_path, "aws", "systemctl", "git", outputs={"aws": "unused\n"})
    (stub / "git.out").write_text("c" * 40 + "\n")
    (stub / "install").write_text("#!/usr/bin/env bash\n"
                                  'if [ "$1" = -d ]; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi\n')
    (stub / "install").chmod(0o755)
    root = tmp_path / "root"
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    env = {"RELEASE": "c" * 40, "CANARY_VIDEO": str(clip), "INFRX_ROOT": str(root),
           "REPO": str(support.REPO), "ALERT_SNS_TOPIC_ARN": SNS_TOPIC,
           "ALERT_OWNER": "sofia", "CANARY_KEY_PARAM": "/model-inference/canary_key"}
    step = (STEPS / "72-observe-install.sh").read_text()
    alert = root / "etc" / "infrx-alert.env"
    for bad in ({"ALERT_WEBHOOK_PARAM": "/model-inference/alert_webhook"},
                {"ALERT_SNS_TOPIC_ARN": SNS_TOPIC + "\nALERT_WEBHOOK_URL=x"},
                {"ALERT_OWNER": "sofia\nALERT_WEBHOOK_URL=https://x"},       # F4: injected line
                {"ALERT_ESCALATION": "pager\rALERT_SNS_TOPIC_ARN=x"}):
        done = run_step(step, stub, env={**env, **bad})
        assert done.returncode == 2 and not alert.exists(), done.stderr
        assert [c for c in calls(stub) if c["tool"] != "git"] == []
    done = run_step(step, stub, env=env)
    assert done.returncode == 0, done.stderr
    assert f"ALERT_SNS_TOPIC_ARN={SNS_TOPIC}\n" in alert.read_text()
    assert "ALERT_WEBHOOK_URL" not in alert.read_text() and "ALERT_OWNER=sofia\n" in alert.read_text()
    assert oct(alert.stat().st_mode & 0o777) == "0o600"
    ssm = [c["argv"] for c in calls(stub) if c["tool"] == "aws"]
    assert [a[a.index("--name") + 1] for a in ssm] == ["/model-inference/canary_key"]


def test_ops_continuous__the_delivery_proof_publishes_to_the_sns_topic(tmp_path):
    """74-alert-test.sh with an SNS destination in the env file: the marked test goes to
    the topic (boto3 faked on PYTHONPATH - no network), the recovery too. Oracle: a step
    that does not pass ALERT_SNS_TOPIC_ARN through is BLOCKED (neither set)."""
    fake = tmp_path / "fake"
    (fake / "botocore").mkdir(parents=True)
    (fake / "botocore" / "__init__.py").write_text("")
    (fake / "botocore" / "exceptions.py").write_text(
        "class ClientError(Exception): pass\nclass BotoCoreError(Exception): pass\n")
    (fake / "boto3.py").write_text(
        "import json, os\n"
        "class _C:\n"
        "    def publish(self, **kw):\n"
        "        open(os.environ['SNS_LOG'], 'a').write(json.dumps(kw) + '\\n')\n"
        "        return {'ResponseMetadata': {'HTTPStatusCode': 200}}\n"
        "def client(service, region_name):\n"
        "    return _C()\n")
    log = tmp_path / "sns.log"
    conf = tmp_path / "infrx-alert.env"
    conf.write_text(f"ALERT_SNS_TOPIC_ARN={SNS_TOPIC}\nALERT_OWNER=sofia\nALERT_ESCALATION=pager\n")
    env = {"REPO": str(support.REPO), "ALERT_ENV": str(conf), "PYTHONPATH": str(fake),
           "SNS_LOG": str(log)}
    step = (STEPS / "74-alert-test.sh").read_text()
    done = run_step(step, stubs(tmp_path), env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "topic=infrx-pilot-alerts" in done.stdout
    nonce = done.stdout.split("nonce=")[1].split()[0]
    done = run_step(step, stubs(tmp_path), env={**env, "RESOLVE": nonce})
    assert done.returncode == 0, done.stdout + done.stderr
    fired, resolved = [json.loads(line) for line in log.read_text().splitlines()]
    assert fired["TopicArn"] == SNS_TOPIC and fired["Subject"].startswith("[TEST FIRING]")
    assert resolved["Subject"].startswith("[TEST RESOLVED]") and nonce in resolved["Message"]

def _backup(dirpath: Path, held: str | None, extra: str = "") -> None:
    import io
    import tarfile
    dirpath.mkdir(parents=True)
    env = ((f"INFRX_RELEASE_SHA={held}\n" if held else "") + extra).encode()
    with tarfile.open(dirpath / "files.tar", "w") as tar:
        info = tarfile.TarInfo("etc/marlin2b-gateway.env")
        info.size = len(env)
        tar.addfile(info, io.BytesIO(env))


def test_ops_continuous__the_evidence_export_is_one_names_only_document(tmp_path):
    """79-evidence-export.sh: release, image, env schema and NAMES, units, the metrics
    textfiles, undelivered alerts, what each backup holds, the bundles - never a value."""
    secret = f"{support.MARKER}-value"
    env_file = tmp_path / "gateway.env"
    env_file.write_text(f"# INFRX_ENV_SCHEMA 1:abc\nINFRX_MODE=pilot\nINFRX_RELEASE_SHA={'c' * 40}\n"
                        f"INFRX_IMAGE=sha256:{'e' * 64}\nDATABASE_URL=postgresql://u:{secret}@h/d\n")
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    (metrics / "host.prom").write_text('infrx_gpu_up{process="host"} 1\n')
    (metrics / "undelivered.jsonl").write_text('{"text":"a"}\n{"text":"b"}\n')
    _backup(tmp_path / "backups" / f"20260924T200647.1Z-{'b' * 40}", "a" * 40, f"KEY={secret}\n")
    releases = tmp_path / "releases"
    releases.mkdir()
    (releases / f"{'b' * 40}.bundle").write_bytes(b"x")
    stub = stubs(tmp_path, "systemctl", "df", outputs={"systemctl": "active\n"})
    done = run_step((STEPS / "79-evidence-export.sh").read_text(), stub, env={
        "ENV_FILE": str(env_file), "METRICS_DIR": str(metrics), "BACKUPS": str(tmp_path / "backups"),
        "RELEASES_DIR": str(releases)})
    assert done.returncode == 0, done.stderr
    doc = json.loads(done.stdout)
    assert secret not in done.stdout
    assert doc["release"] == "c" * 40 and doc["env_schema"] == "1:abc" and doc["mode"] == "pilot"
    assert "DATABASE_URL" in doc["env_names"] and doc["undelivered_alerts"] == 2
    assert doc["backups"] == {f"20260924T200647.1Z-{'b' * 40}": "a" * 40}
    assert doc["release_bundles"] == ["b" * 40]
    assert doc["metrics"]["host"] == ['infrx_gpu_up{process="host"} 1']


def test_ops_continuous__cleanup_removes_only_allowlisted_paths_and_keeps_known_good(tmp_path):
    """86-cleanup.sh: dry run by default; keeps the newest KEEP backups and any backup that
    holds a known-good release; removes a restore's leftovers only for the id given; touches
    nothing outside its two patterns."""
    import shutil
    repo = tmp_path / "repo"
    (repo / "infra" / "rollout").mkdir(parents=True)
    shutil.copy2(support.REPO / "infra" / "rollout" / "known-good.json",
                 repo / "infra" / "rollout" / "known-good.json")
    backups = tmp_path / "backups"
    good = "422631591845fbd66b590c73d5ff4150318d9d7a"
    names = [f"2026092{d}T000000.1Z-{c * 40}" for d, c in zip("12345", "abcde")]
    for name, held in zip(names, ("1" * 40, good, "2" * 40, "3" * 40, "4" * 40)):
        _backup(backups / name, held)
    (backups / "pre-something.head").write_text("x")                 # not a backup dir
    (backups / "unrelated-dir").mkdir()
    nvme = tmp_path / "nvme"
    for path in ("restore-20260924T230000Z", "marlin2b.pre-restore-20260924T230000Z",
                 "restore-20260101T000000Z", "marlin2b"):
        (nvme / path).mkdir(parents=True)
    step = (STEPS / "86-cleanup.sh").read_text()
    env = {"REPO": str(repo), "BACKUPS": str(backups), "NVME": str(nvme)}
    stub = stubs(tmp_path)
    done = run_step(step, stub, env=env)
    assert done.returncode == 0, done.stderr
    assert f"would remove {backups / names[0]}" in done.stdout
    assert f"kept {backups / names[1]} (holds known-good {good})" in done.stdout
    assert all((backups / n).exists() for n in names)                     # dry run
    done = run_step(step, stub, env={**env, "DRY_RUN": "0", "RESTORE_ID": "20260924T230000Z"})
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in backups.iterdir()) == sorted(
        [names[1], names[2], names[3], names[4], "pre-something.head", "unrelated-dir"])
    assert sorted(p.name for p in nvme.iterdir()) == ["marlin2b", "restore-20260101T000000Z"]
    assert run_step(step, stub, env={**env, "KEEP": "1"}).returncode == 2
    assert run_step(step, stub, env={**env, "RESTORE_ID": "*"}).returncode == 2


# ROLLOUT-FIXES item 5: each step and the checkout paths it reads that the known-good
# targets (bda1586, 4226315, pre-I8) do not have, with the arguments that get it past its
# own argument checks.
PRE_I8 = {
    "71-pool-budget.sh": (["infra/runbooks/pool_budget.py"], {}),
    "72-observe-install.sh": (["infra/observe/systemd", "infra/alerts/operations.json"],
                              {"RELEASE": "c" * 40, "CANARY_VIDEO": "/nonexistent.mp4",
                               "CANARY_KEY_PARAM": "/model-inference/canary_key"}),
    "74-alert-test.sh": (["infra/observe/deliver.py"], {}),
    "80-mirror-artifacts.sh": (["infra/runbooks/artifacts.py"],
                               {"RELEASE": "c" * 40, "MIRROR_URL": "s3://bucket/prefix/"}),
    "81-restore-artifacts.sh": (["infra/runbooks/artifacts.py"],
                                {"RELEASE": "c" * 40, "MIRROR_URL": "s3://bucket/prefix/"}),
    "86-cleanup.sh": (["infra/rollout/known-good.json"], {}),
}
BOX_TOOLS = ("docker", "aws", "git", "systemctl", "curl", "install", "python3", "sed", "tar",
             "find", "mktemp")


def test_ops_continuous__a_step_on_a_pre_i8_checkout_is_blocked_before_it_acts(tmp_path):
    """Steps 71/72/74/80/81/86 read infra/runbooks, infra/observe or known-good.json from
    the box's checkout, absent at every known-good target: after a revert to one, each
    says BLOCKED (exit 3) naming the missing path before any command runs (every box tool
    a recording stub: none is called, nothing is written). Oracle: without the guard they
    ran on - 71 into docker, 72 into git/SSM, 86 into python3 on a missing file. With the
    paths present the guard is silent. 81's MODE=undo reads nothing from the checkout and
    stays available as the recovery path."""
    for name, (needs, args) in PRE_I8.items():
        path = STEPS / name
        assert subprocess.run(["bash", "-n", str(path)]).returncode == 0, name
        text = path.read_text().replace("/home/ubuntu/model-inference", "@REPO@")
        for present in (False, True):
            case = tmp_path / f"{name}-{present}"
            repo, sandbox = case / "repo", case / "sandbox"
            repo.mkdir(parents=True), sandbox.mkdir()
            if present:
                for need in needs:
                    (repo / need).parent.mkdir(parents=True, exist_ok=True)
                    (repo / need).touch() if "." in need.rsplit("/", 1)[-1] else (repo / need).mkdir()
            stub = stubs(case, *BOX_TOOLS)
            env = {"REPO": str(repo), "INFRX_ROOT": str(sandbox), "NVME": str(sandbox),
                   "BACKUPS": str(sandbox), "ALERT_ENV": str(sandbox / "none.env"), **args}
            done = run_step(text.replace("@REPO@", str(repo)), stub, env=env)
            blocked = f"BLOCKED: this step needs an I8+ checkout (missing {needs[0]})"
            if present:
                assert "I8+ checkout" not in done.stderr, (name, done.stderr)
                continue
            assert done.returncode == 3 and blocked in done.stderr, (name, done.stderr)
            assert calls(stub) == [], (name, calls(stub))
            assert list(sandbox.iterdir()) == [], name
    undo = run_step((STEPS / "81-restore-artifacts.sh").read_text(), stubs(tmp_path),
                    env={"REPO": str(tmp_path / "empty"), "NVME": str(tmp_path),
                         "RELEASE": "c" * 40, "MIRROR_URL": "s3://bucket/prefix/",
                         "MODE": "undo", "RESTORE_ID": "20260925T000000Z"})
    assert undo.returncode == 2 and "nothing moved aside" in undo.stderr, undo.stderr


# --- E4C-RUNBOOK-2 item 5: the dedicated runtime logins (RV-09, D10 wiring 6, R127) ----------

AWS_BY_NAME = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "calls.log").open("a") as log:
    log.write(json.dumps({{"tool": "aws", "argv": args, "stdin": ""}}) + "\\n")
values = json.loads((here / "ssm.json").read_text())
name = args[args.index("--name") + 1]
if name not in values:
    sys.stderr.write("ParameterNotFound\\n"); sys.exit(254)
print(values[name])
'''

DOCKER_LOGIN = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
record = {{"tool": "docker", "argv": args, "stdin": stdin, "env_files": {{}}}}
for i, a in enumerate(args[:-1]):
    if a == "--env-file" and args[i + 1] != "/dev/stdin":
        record["env_files"][args[i + 1]] = pathlib.Path(args[i + 1]).read_text()
with (here / "calls.log").open("a") as log:
    log.write(json.dumps(record) + "\\n")
if "envcheck" in args:
    sys.exit(int(os.environ.get("STUB_ENVCHECK_EXIT", "0")))
if args[-2:] == ["python", "-"]:
    sys.stdout.write((here / "dsns.out").read_text())
'''

RUNTIME_PASSWORD_PARAM = "/model-inference/infrx_runtime_password"
MONITOR_PASSWORD_PARAM = "/model-inference/infrx_monitor_password"
OWNER_DSN_PARAM = "/model-inference/pg_journal_url"


def test_ops_login__the_runtime_moves_to_its_dedicated_logins_by_name_only(tmp_path):
    """55-runtime-login.sh (after W10, which writes the env file from SSM): the two login
    passwords and the owner DSN are read from SSM by NAME (preflight's /model-inference/<leaf>
    convention) into a 0600 file handed to the installed image as --env-file; the image sets
    each password (a SCRAM verifier, never the plain text, crosses the wire) and logs in as
    each on :6543; the env file gets DATABASE_URL (infrx_runtime) and MONITOR_DATABASE_URL
    (infrx_monitor), 0600 by one rename after envcheck passes on the staged bytes; gateway and
    worker restart and must answer /readyz. Oracles: a value on any argument or in the output;
    a missing parameter or no installed image refused before anything changes; envcheck red
    leaves the file and the units alone (exit 3); not ready puts the previous file back and
    restarts on it (exit 4); a rerun on an unchanged file restarts nothing (idempotent)."""
    owner, rt, mon = (f"{support.MARKER}-{w}" for w in ("owner", "rt", "mon"))
    image = "sha256:" + "a" * 64
    before = ("# INFRX_ENV_SCHEMA 1:abc\nINFRX_MODE=pilot\nINFRX_IMAGE=" + image + "\n"
              f"DATABASE_URL=postgresql://postgres.ref:{owner}@pooler:5432/postgres?sslmode=require\n"
              "S3_MEDIA_BUCKET=b\n")
    new = (f"DATABASE_URL=postgresql://infrx_runtime.ref:{rt}@pooler:6543/postgres?sslmode=require\n"
           f"MONITOR_DATABASE_URL=postgresql://infrx_monitor.ref:{mon}@pooler:6543/postgres?sslmode=require\n")
    env_file, backups = tmp_path / "marlin2b-gateway.env", tmp_path / "backups"
    env_file.write_text(before)
    env_file.chmod(0o600)
    stub = stubs(tmp_path, "systemctl", "curl", "sleep")
    (stub / "aws").write_text(AWS_BY_NAME.format(python=sys.executable))
    (stub / "docker").write_text(DOCKER_LOGIN.format(python=sys.executable))
    (stub / "aws").chmod(0o755), (stub / "docker").chmod(0o755)
    (stub / "ssm.json").write_text(json.dumps({OWNER_DSN_PARAM: before.split("DATABASE_URL=")[1].split("\n")[0],
                                               RUNTIME_PASSWORD_PARAM: rt, MONITOR_PASSWORD_PARAM: mon}))
    (stub / "dsns.out").write_text(new)
    step = (STEPS / "55-runtime-login.sh").read_text()
    env = {"ENV_FILE": str(env_file), "BACKUPS": str(backups), "TMPDIR": str(tmp_path)}

    def run(**extra):
        (stub / "calls.log").unlink(missing_ok=True)
        done = run_step(step, stub, env={**env, **extra})
        assert support.MARKER not in done.stdout + done.stderr, "a value in the output"
        for call in calls(stub):
            assert not any(support.MARKER in a for a in call["argv"]), call["argv"]
        return done, calls(stub)

    text = step
    assert subprocess.run(["bash", "-n", str(STEPS / "55-runtime-login.sh")]).returncode == 0
    assert "set -euo pipefail" in text and "ALTER ROLE" in text.upper()
    done, made = run()
    assert done.returncode == 0, done.stderr
    ssm = [c["argv"] for c in made if c["tool"] == "aws"]
    assert [a[a.index("--name") + 1] for a in ssm] == [OWNER_DSN_PARAM, RUNTIME_PASSWORD_PARAM,
                                                        MONITOR_PASSWORD_PARAM]
    (login,) = [c for c in made if c["tool"] == "docker" and c["argv"][-2:] == ["python", "-"]]
    assert login["argv"][:5] == ["run", "--rm", "-i", "--network", "host"] and image in login["argv"]
    (secrets,) = login["env_files"].values()
    assert [line.split("=", 1)[0] for line in secrets.splitlines()] == [
        "OWNER_DSN", "RUNTIME_PASSWORD", "MONITOR_PASSWORD"]
    program = login["stdin"]
    for needle in ("alter role {} with login password {}", "encrypt_password", "scram-sha-256",
                   ":6543/", "select current_user", "infrx_runtime", "infrx_monitor"):
        assert needle in program, needle
    assert "except" not in program                   # any SQL error stops it (ON_ERROR_STOP)
    (check,) = [c for c in made if c["tool"] == "docker" and "envcheck" in c["argv"]]
    assert check["argv"][:5] == ["run", "--rm", "-i", "--network", "none"]
    assert check["stdin"] == before.replace(before.split("\n")[3] + "\n", "") + new
    assert env_file.read_text() == check["stdin"]
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    (saved,) = backups.iterdir()
    assert saved.read_text() == before and oct(saved.stat().st_mode & 0o777) == "0o600"
    systemctl = [c["argv"] for c in made if c["tool"] == "systemctl"]
    assert systemctl == [["restart", "marlin2b-gateway", "infrx-worker"]]
    curls = [" ".join(c["argv"]) for c in made if c["tool"] == "curl"]
    assert any("127.0.0.1:8001/readyz" in c for c in curls) and any("127.0.0.1:8002/readyz" in c for c in curls)
    assert not list(tmp_path.glob("tmp.*")), "the secrets file outlived the step"

    done, made = run()                                            # rerun: nothing to restart
    assert done.returncode == 0 and "unchanged" in done.stdout, done.stderr
    assert not [c for c in made if c["tool"] == "systemctl"] and len(list(backups.iterdir())) == 1

    env_file.write_text(before)                                   # envcheck red: untouched
    done, made = run(STUB_ENVCHECK_EXIT="1")
    assert done.returncode == 3 and env_file.read_text() == before
    assert not [c for c in made if c["tool"] == "systemctl"]

    done, made = run(STUB_EXIT_curl="7", READY_S="2")            # not ready: previous file back
    assert done.returncode == 4 and env_file.read_text() == before, done.stderr
    assert [c["argv"] for c in made if c["tool"] == "systemctl"] == [
        ["restart", "marlin2b-gateway", "infrx-worker"]] * 2

    ssm_values = json.loads((stub / "ssm.json").read_text())      # a parameter missing: refused
    del ssm_values[MONITOR_PASSWORD_PARAM]
    (stub / "ssm.json").write_text(json.dumps(ssm_values))
    done, made = run()
    assert done.returncode != 0 and env_file.read_text() == before
    assert not [c for c in made if c["tool"] in ("docker", "systemctl")]

    env_file.write_text("# INFRX_ENV_SCHEMA 1:abc\nINFRX_MODE=pilot\n")   # no install yet
    done, made = run()
    assert done.returncode == 2 and "50-install" in done.stderr and made == []
