#!/usr/bin/env python3
"""I2B.c: the rollout runbook's scripts (`infra/rollout/`), checked without running them
against anything: they are strict bash, carry no secret, travel through SSM byte for
byte, and order the revert so the restored runtime starts on its own code.

`ssm.sh` runs against a fake `aws`; the box steps are only parsed. The coordinator runs
them for real (research/plan/evidence/i/I2B-*.md, handback).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib

from . import support

ROLLOUT = support.REPO / "infra" / "rollout"
STEPS = sorted((ROLLOUT / "steps").glob("*.sh"))
SECRET_SHAPES = (r"postgres(?:ql)?://[^:@\s/]+:[^@\s]+@", r"eyJ[A-Za-z0-9_-]{16,}",
                 r"AKIA[0-9A-Z]{16}", r"--value\s+(?!file://)\S",
                 r"-e\s+[A-Z_]+=\S",         # docker -e passes names; a value there is inline
                 # a credential-named variable assigned a literal: the runbook reads those
                 # with `read -rs` or `$(aws ssm ...)`, never writes the value
                 r"""\b[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|DSN)=[^$\s"'<`…]""")

FAKE_AWS = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "aws.log").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if "send-command" in args:
    print("cmd-1")
elif "Status" in args:
    print((here / "status").read_text())
else:
    print("step output")
'''


def test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret():
    """Each script parses, stops at the first failure, refuses to run without the release
    it acts on - the cutover also without the migration digest step 6 applied - and
    contains nothing shaped like a credential or an inline parameter value: secrets are
    read on the box by preflight.py, from SSM, and never travel."""
    assert [p.name for p in STEPS] == ["10-inventory.sh", "20-prepull.sh", "25-save-edge.sh",
                                       "30-pause.sh", "40-checkout.sh", "45-s3-check.sh",
                                       "50-install.sh", "55-runtime-login.sh", "56-resume.sh",
                                       "60-verify-local.sh", "71-pool-budget.sh",
                                       "72-observe-install.sh", "73-observe-status.sh",
                                       "74-alert-test.sh", "78-e4b-report.sh", "79-evidence-export.sh",
                                       "80-mirror-artifacts.sh",
                                       "81-restore-artifacts.sh", "85-known-good-box.sh",
                                       "86-cleanup.sh",
                                       "90-revert.sh", "91-abort.sh",
                                       "93-restore-edge.sh", "95-maintenance.sh"]
    for path in [*STEPS, ROLLOUT / "ssm.sh", ROLLOUT / "verify-external.sh",
                 ROLLOUT / "verify-journey.sh", ROLLOUT / "e4c-certify.sh",
                 ROLLOUT / "e1b-window.sh"]:
        text = path.read_text()
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, (path.name, done.stderr)
        if path.parent.name == "steps":
            assert "set -euo pipefail" in text, path.name
            if "$RELEASE" in text:
                assert ': "${RELEASE:?' in text, f"{path.name} runs without a release"
        for shape in SECRET_SHAPES:
            assert not re.search(shape, text), (path.name, shape)
    assert ': "${MIGRATION_DIGEST:?' in (ROLLOUT / "steps" / "50-install.sh").read_text()
    readme = (ROLLOUT / "README.md").read_text()
    for shape in SECRET_SHAPES:
        assert not re.search(shape, readme), shape


def _ssm(tmp_path, *args, status="Success"):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "aws").write_text(FAKE_AWS.format(python=sys.executable))
    (stub / "aws").chmod(0o755)
    (stub / "status").write_text(status)
    (stub / "aws.log").unlink(missing_ok=True)
    done = subprocess.run(["bash", str(ROLLOUT / "ssm.sh"), *args], capture_output=True,
                          text=True, env={**os.environ, "POLL_S": "0",
                                          "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
    log = stub / "aws.log"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return done, calls


def test_backend_deploy__ssm_carries_a_step_byte_for_byte(tmp_path):
    """What runs on the box is exactly the step, preceded by one `export` per argument:
    the base64 in the send-command decodes to those bytes; the document is
    AWS-RunShellScript on the pilot instance; the exit status is the invocation's."""
    step = ROLLOUT / "steps" / "50-install.sh"
    done, calls = _ssm(tmp_path, str(step), "RELEASE=abc123")
    assert done.returncode == 0, done.stderr
    send = next(call for call in calls if "send-command" in call)
    assert send[send.index("--instance-ids") + 1] == "i-0e8449a4ffca29bab"
    assert send[send.index("--document-name") + 1] == "AWS-RunShellScript"
    command = json.loads(send[send.index("--parameters") + 1])["commands"][0]
    wrapped = re.fullmatch(r"echo (\S+) \| base64 -d > /root/infrx-step.sh && .*", command)
    assert wrapped, command
    assert base64.b64decode(wrapped.group(1)) == b"export RELEASE=abc123\n" + step.read_bytes()

    done, _ = _ssm(tmp_path, str(step), "RELEASE=abc123", status="Failed")
    assert done.returncode != 0
    done, calls = _ssm(tmp_path, str(step), "release abc123")
    assert done.returncode == 2 and calls == []


def test_backend_deploy__ssm_refuses_what_is_not_a_step_before_any_aws_call(tmp_path):
    """ROLLOUT-FIXES: `ssm.sh --help` used to hand `--help` to `cat`, base64-wrap cat's help
    text and send it to the pilot instance. Oracle: --help/-h print the usage and exit 0, a
    step that is not an existing file, or a bad pair after a real one, exit 2 - all with no
    aws call at all; a good invocation still reaches aws exactly once with the payload."""
    step = ROLLOUT / "steps" / "10-inventory.sh"
    for flag in ("--help", "-h"):
        done, calls = _ssm(tmp_path, flag)
        assert done.returncode == 0 and "usage" in done.stdout and calls == [], (flag, done)
    for args in (("--version",), (str(tmp_path / "missing.sh"),), (str(ROLLOUT / "steps"),),
                 (str(step), "release abc123")):
        done, calls = _ssm(tmp_path, *args)
        assert done.returncode == 2 and calls == [], (args, done.stderr, calls)
    done, calls = _ssm(tmp_path, str(step))
    assert done.returncode == 0, done.stderr
    sends = [call for call in calls if "send-command" in call]
    assert len(sends) == 1
    command = json.loads(sends[0][sends[0].index("--parameters") + 1])["commands"][0]
    assert base64.b64decode(command.split()[1]) == step.read_bytes()


def test_ops_recover__the_revert_restores_the_tree_before_the_runtime():
    """The monolith and the engine unit run from the working tree, so the revert checks
    out the previous HEAD before rollback.sh restarts the restored engine and gateway, and
    reopens the edge after; the pause saves that HEAD before it replaces the edge."""
    revert = (ROLLOUT / "steps" / "90-revert.sh").read_text()
    order = [revert.find(s) for s in ("checkout --quiet --detach \"$previous\"",
                                      'ENGINE=restart "$d/rollback.sh" "$BACKUP"',
                                      '"$d/drain.sh" resume')]
    assert -1 not in order and order == sorted(order), order
    pause = (ROLLOUT / "steps" / "30-pause.sh").read_text()
    order = [pause.find(s) for s in ("pre-$RELEASE.head", 'edge_install "$d"',
                                     '"$d/drain.sh" pause')]
    assert -1 not in order and order == sorted(order), order


def test_backend_deploy__the_cutover_keeps_the_engines_concurrency(tmp_path):
    """The release's serve.sh reads `ENGINE_MAX_NUM_SEQS` from the validated file; the
    cutover step hands install.sh the value the operator names (rollout.md §1 pins 8) as a
    schema setting, and has no default of its own (ROLLOUT-FIXES: its old default of 32,
    the pre-release box unit's value, silently overrode the §1 pin when the argument was
    left out). Runs the step's own bytes against a stand-in checkout; without the value,
    or without step 6's digest (64 hex, or exactly `nothing-pending`), it does not reach
    install.sh at all."""
    box = tmp_path / "box"
    deploy = box / "apps" / "infrx-api" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "install.sh").write_text('#!/usr/bin/env bash\n'
                                       'echo "install INFRX_SET=[$INFRX_SET] $INFRX_MODE $ENGINE"\n')
    (deploy / "install.sh").chmod(0o755)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "git").write_text("#!/usr/bin/env bash\necho abc123\n")
    (stub / "git").chmod(0o755)
    step = (ROLLOUT / "steps" / "50-install.sh").read_text().replace(
        "/home/ubuntu/model-inference", str(box))

    def cutover(**env):
        return subprocess.run(["bash", "-c", step], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                                   "RELEASE": "abc123", **env})
    done = cutover(MIGRATION_DIGEST="nothing-pending")
    assert done.returncode != 0 and "install" not in done.stdout, done.stdout
    assert "ENGINE_MAX_NUM_SEQS" in done.stderr
    done = cutover(MIGRATION_DIGEST="nothing-pending", ENGINE_MAX_NUM_SEQS="8")
    assert done.returncode == 0, done.stderr
    assert "install INFRX_SET=[ENGINE_MAX_NUM_SEQS=8 ] pilot restart" in done.stdout
    done = cutover(MIGRATION_DIGEST="nothing-pending", ENGINE_MAX_NUM_SEQS="16")
    assert "INFRX_SET=[ENGINE_MAX_NUM_SEQS=16 ]" in done.stdout
    done = cutover(MIGRATION_DIGEST="a" * 64, ENGINE_MAX_NUM_SEQS="8")
    assert done.returncode == 0 and "install INFRX_SET=" in done.stdout
    for statement in (None, "nothing-pendng", "x", "A" * 64, "a" * 63, "a" * 65,
                      "nothing-pending-x"):
        done = cutover(ENGINE_MAX_NUM_SEQS="8",
                       **({} if statement is None else {"MIGRATION_DIGEST": statement}))
        assert done.returncode != 0 and "install" not in done.stdout, statement


FAKE_CURL = """#!{python}
import pathlib, stat, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "curl.log").open("a") as log:
    log.write(repr(args) + "\\n")
    for i, arg in enumerate(args[:-1]):
        if arg == "-H" and args[i + 1].startswith("@"):
            path = pathlib.Path(args[i + 1][1:])
            log.write("HEADER MODE " + oct(stat.S_IMODE(path.stat().st_mode)) + " " + str(path)
                      + "\\n")
            log.write("HEADER FILE " + path.read_text())
    if "-o" in args:
        log.write("BODY " + args[args.index("-o") + 1] + "\\n")
if "-o" in args and args[args.index("-o") + 1] != "/dev/null":
    pathlib.Path(args[args.index("-o") + 1]).write_text('{{"ok":true}}')
if "-D" in args:
    print("server-timing: total;dur=1")
if "-w" in args:
    print("200", end="")
"""


@support.LINUX_USERLAND
def test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line(tmp_path):
    """Every key the external check uses reaches curl in a header file, so none is ever an
    argument (visible in `ps` and /proc on the coordinator host) or printed; the key is
    still what curl sends. That file is 0600 while it exists, and it - like the response
    body file, which is never a fixed /tmp path - is a fresh temporary file, gone when the
    script exits."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(FAKE_CURL.format(python=sys.executable))
    (stub / "curl").chmod(0o755)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    keys = {name: f"{support.MARKER}-{name.lower()}"
            for name in ("INFRX_TEST_KEY", "INFRX_REVOKED_KEY", "LEGACY_KEY")}
    done = subprocess.run(["bash", str(ROLLOUT / "verify-external.sh")], capture_output=True,
                          text=True, env={**os.environ, **keys, "TMPDIR": str(scratch),
                                          "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
    log = (stub / "curl.log").read_text()
    argv = [line for line in log.splitlines() if not line.startswith(("HEADER ", "BODY "))]
    assert argv and not [line for line in argv if support.MARKER in line]
    assert support.MARKER not in done.stdout + done.stderr
    for key in keys.values():
        assert f"HEADER FILE Authorization: Bearer {key}\n" in log, key
    modes = [line.split(" ", 3)[2:] for line in log.splitlines() if line.startswith("HEADER MODE")]
    assert modes and all(mode == "0o600" and path.startswith(str(scratch)) for mode, path in modes)
    bodies = [line[5:] for line in log.splitlines()
              if line.startswith("BODY ") and line != "BODY /dev/null"]
    assert bodies and all(body.startswith(f"{scratch}/") for body in bodies), bodies
    assert list(scratch.iterdir()) == [], "a temporary file outlived the script"


BOX_BINARIES = ("hostname", "uptime", "df", "docker", "git", "systemctl", "ss", "curl")


def test_backend_deploy__the_read_only_steps_print_names_never_values(tmp_path):
    """10-inventory and 60-verify-local are the steps that read the env file, and their
    output lands in SSM and the coordinator's record: run with a canary env file (every
    box binary a silent success), each prints the file's names and never a value."""
    canary = f"{support.MARKER}-canary"
    env_file = tmp_path / "marlin2b-gateway.env"
    env_file.write_text(f"INFRX_MODE=pilot\nDATABASE_URL=postgresql://infrx:{canary}@db/x\n"
                        f"SUPABASE_SERVICE_ROLE_KEY={canary}\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    for name in BOX_BINARIES:
        (stub / name).write_text("#!/usr/bin/env bash\nexit 0\n")
        (stub / name).chmod(0o755)
    for step in ("10-inventory.sh", "60-verify-local.sh"):
        text = (ROLLOUT / "steps" / step).read_text().replace("/etc/marlin2b-gateway.env",
                                                              str(env_file))
        done = subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
        assert done.returncode == 0, (step, done.stderr)
        assert "DATABASE_URL" in done.stdout and "SUPABASE_SERVICE_ROLE_KEY" in done.stdout, step
        assert support.MARKER not in done.stdout + done.stderr, step


FAKE_DOCKER = """#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
with (here / "docker.log").open("a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "env": {{k: os.environ.get(k) for k in (
        "INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET")}}}}) + "\\n")
sys.exit(1 if sys.argv[1] == os.environ.get("DOCKER_FAIL") else 0)
"""


def _box_stubs(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(FAKE_DOCKER.format(python=sys.executable))
    (stub / "git").write_text('#!/usr/bin/env bash\necho "$HEAD_SHA"\n')
    for name in ("docker", "git"):
        (stub / name).chmod(0o755)
    return stub


def _docker_calls(stub):
    log = stub / "docker.log"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    log.unlink(missing_ok=True)
    return calls


def test_ops_recover__the_saved_edge_comes_back_byte_for_byte(tmp_path):
    """25-save-edge keeps the live Caddyfile and prints its sha256; after the window put
    the maintenance site there, 93-restore-edge writes it back in place (the same inode:
    Caddy's single-file bind mount keeps seeing it) and reloads through the admin socket.
    A saved file that does not match the recorded sha256 restores nothing."""
    stub = _box_stubs(tmp_path)
    live = tmp_path / "Caddyfile"
    live.write_bytes(b"{\n\tadmin localhost:2019\n}\n:443 { reverse_proxy 127.0.0.1:8001 }\n")
    original = live.read_bytes()
    mounted = tmp_path / "mounted"          # what Caddy's single-file bind mount holds: the inode
    mounted.hardlink_to(live)
    logs = tmp_path / "w4-logs"

    def step(name, **env):
        text = (ROLLOUT / "steps" / name).read_text()
        text = (text.replace("--config /etc/caddy/Caddyfile", "--config @IN-CONTAINER@")
                .replace("/etc/caddy/Caddyfile", str(live))
                .replace("@IN-CONTAINER@", "/etc/caddy/Caddyfile")
                .replace("/opt/dlami/nvme/w4-logs", str(logs)))
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", **env})

    saved = step("25-save-edge.sh")
    assert saved.returncode == 0, saved.stderr
    printed = re.search(r"^saved=(\S+)$", saved.stdout, re.M)
    assert printed, saved.stdout
    path = printed.group(1)
    assert re.fullmatch(rf"{logs}/Caddyfile\.live-\d{{8}}T\d{{6}}Z", path), path
    sha = hashlib.sha256(original).hexdigest()
    assert f"{sha}  {path}\n" in saved.stdout, "the saved edge's sha256 was not printed"
    assert pathlib.Path(path).read_bytes() == original
    assert [c["argv"][0] for c in _docker_calls(stub)] == ["inspect"]   # read-only

    live.write_bytes(b"# maintenance\n:443 { respond 503 }\n")          # the window's edge
    wrong = step("93-restore-edge.sh", SAVED=path, SAVED_SHA256="0" * 64)
    assert wrong.returncode != 0 and live.read_bytes().startswith(b"# maintenance")
    assert _docker_calls(stub) == [], "reloaded an edge it did not restore"
    for missing in ({"SAVED": path}, {"SAVED_SHA256": sha}):
        assert step("93-restore-edge.sh", **missing).returncode != 0
    assert live.read_bytes().startswith(b"# maintenance")

    restored = step("93-restore-edge.sh", SAVED=path, SAVED_SHA256=sha)
    assert restored.returncode == 0, restored.stderr
    assert live.read_bytes() == original and mounted.read_bytes() == original
    assert [c["argv"] for c in _docker_calls(stub)] == [
        ["exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile",
         "--adapter", "caddyfile", "--address", "unix//config/admin.sock"]]


def test_backend_deploy__the_real_bucket_check_runs_the_release_image_before_the_install(
        tmp_path):
    """45-s3-check runs M1-L2's conformance in the image built from the checked-out
    release, against AWS S3 and the media bucket, with the instance role: only the two
    names travel into the container (never INFRX_M_S3_LOCAL_CREDS). It refuses a checkout
    that is not the release, and a red run is a failed step."""
    stub = _box_stubs(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    release = "c" * 40
    text = (ROLLOUT / "steps" / "45-s3-check.sh").read_text().replace(
        "/home/ubuntu/model-inference", str(box))

    def check(**env):
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                                   "HEAD_SHA": release, "INFRX_M_S3_LOCAL_CREDS": "1", **env})

    assert check().returncode != 0 and _docker_calls(stub) == []
    stale = check(RELEASE=release, HEAD_SHA="d" * 40)
    assert stale.returncode == 2 and "40-checkout" in stale.stderr
    assert _docker_calls(stub) == []

    done = check(RELEASE=release)
    assert done.returncode == 0, done.stderr
    assert f"real-bucket conformance passed for {release}" in done.stdout
    build, run = _docker_calls(stub)
    assert build["argv"][0] == "build" and build["argv"][-3:] == [
        "-t", f"infrx-runtime:{release}", "apps/infrx-api"]
    argv = run["argv"]
    assert argv[0] == "run" and f"infrx-runtime:{release}" in argv
    assert f"{box}:/repo:ro" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "-e"] == [
        "INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET"]
    assert run["env"]["INFRX_M_S3_ENDPOINT"] == "https://s3.us-east-1.amazonaws.com"
    assert run["env"]["INFRX_M_S3_BUCKET"] == "llm-bootcamp-641134885443"
    # the container's exit status is pytest's: the script ends with it, nothing after
    assert "--require-hashes" in argv[-1] and argv[-1].rstrip().endswith(" tests/m/test_s3.py")

    check(RELEASE=release, S3_MEDIA_BUCKET="another-bucket")
    assert _docker_calls(stub)[-1]["env"]["INFRX_M_S3_BUCKET"] == "another-bucket"
    red = check(RELEASE=release, DOCKER_FAIL="run")
    assert red.returncode != 0 and "passed" not in red.stdout


def test_backend_deploy__the_bucket_check_installs_exactly_uv_locks_pytest_wheels():
    """45-s3-check's header says its pytest and dependencies are uv.lock's wheels. Oracle
    (ROLLOUT-FIXES): the heredoc pinned pytest 8.4.2 while uv.lock carries 9.1.1
    (GHSA-6w46-j5rx-g56g / CVE-2025-71176). Every heredoc pin is uv.lock's version and wheel
    hash, and the pinned set is exactly pytest plus its non-Windows dependencies."""
    text = (ROLLOUT / "steps" / "45-s3-check.sh").read_text()
    body = re.search(r"<<REQ\n(.*?)\nREQ\n", text, re.S)
    assert body, "no requirements heredoc"
    pins = {}
    for line in body.group(1).splitlines():
        m = re.fullmatch(r"([a-z0-9-]+)==(\S+) --hash=(sha256:[0-9a-f]{64})", line)
        assert m, line
        pins[m.group(1)] = (m.group(2), m.group(3))
    lock = {p["name"]: p for p in tomllib.loads(
        (support.REPO / "apps" / "infrx-api" / "uv.lock").read_text())["package"]}
    wanted = {"pytest"} | {d["name"] for d in lock["pytest"]["dependencies"]
                           if "win32" not in d.get("marker", "")}
    assert set(pins) == wanted, (sorted(pins), sorted(wanted))
    for name, (version, digest) in pins.items():
        assert lock[name]["version"] == version, (name, version, lock[name]["version"])
        assert digest in {w["hash"] for w in lock[name]["wheels"]}, name


def test_backend_deploy__the_runbooks_install_args_fit_the_session_pooler():
    """rollout.md §1's INSTALL_ARGS is what the operator pastes into W10. Oracle
    (ROLLOUT-FIXES): DATABASE_POOL_MAX_SIZE=6 sat in a comment beside it, so the pasted
    install ran the defaults - pool_budget.py FAIL, peak 21 + headroom 2 > 15 session slots
    (the 2026-09-24 EMAXCONNSESSION). The block's settings pass the budget at peak 13, and
    name ENGINE_MAX_NUM_SEQS=8, which 50-install now requires."""
    text = (support.REPO / "infra" / "runbooks" / "rollout.md").read_text()
    block = re.search(r"^INSTALL_ARGS=\((.*?)\)$", text, re.S | re.M)
    assert block, "no INSTALL_ARGS block"
    args = re.sub(r"#.*", "", block.group(1))
    assert re.search(r"(?:^|\s)ENGINE_MAX_NUM_SEQS=8(?:\s|$)", args), args
    infrx_set = re.search(r'INFRX_SET="([^"]*)"', args)
    assert infrx_set, args
    pairs = [*infrx_set.group(1).split(), "ENGINE_MAX_NUM_SEQS=8"]
    done = subprocess.run([sys.executable, str(support.REPO / "infra" / "runbooks" / "pool_budget.py"),
                           "--runtime-port", "5432", *[a for p in pairs for a in ("--set", p)]],
                          capture_output=True, text=True)
    assert done.returncode == 0 and "PASS session: peak 13 " in done.stdout, done.stdout


# --- E4C-RUNBOOK-2 item 4: the certify launcher carries certify's E4C flag set ---------------

CERTIFY = support.REPO / "tests" / "integration" / "backend" / "certify.py"
LAUNCHER = ROLLOUT / "e4c-certify.sh"
#: certify.py flags a box run never passes: the local E2 stack's scale/keep, and --hashes
#: (print and exit). Every other flag certify defines is part of the E4C invocation.
LOCAL_ONLY = {"--scale", "--keep", "--hashes"}

DOCKER_CERTIFY = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
record = {{"argv": args, "env_files": {{}}}}
for i, a in enumerate(args):
    if a == "--env-file":
        record["env_files"][args[i + 1]] = pathlib.Path(args[i + 1]).read_text()
with (here / "docker.log").open("a") as log:
    log.write(json.dumps(record) + "\\n")
if args[:1] == ["inspect"] or args[:2] == ["image", "inspect"]:
    print("sha256:" + "e" * 64)
'''


def _flags(command: str) -> list[str]:
    return re.findall(r"(?:^|\s)(--[a-z][\w-]*)", command.split("certify.py", 1)[1])


def test_e4c_certify__the_launcher_passes_exactly_certify_s_box_flags_and_no_secret(tmp_path):
    """E4C-readiness §2h: session-03's e4b-certify3.sh ran certify without --run-profile,
    --key-inventory and --overload-profile, so every bench cell and the P4 overload cell are
    BLOCKED (certify `profile_blocked`, R133), and it put the DSN on docker's command line
    (`-e DATABASE_URL=...`, visible in `ps`). Oracle: the launcher's certify flags equal
    certify.py's parser flags minus the local-only ones - a flag certify gains or the launcher
    drops fails here - and equal the E4C runbook's §4 command; the E4C paths are the ones the
    runbook fills; the ledger half gets the owner login on :6543 (OPERATIONS_DATABASE_URL:
    after W10b DATABASE_URL is infrx_runtime, which the operator tool refuses), in a 0600
    env file only; a missing profile is refused before docker runs."""
    text = LAUNCHER.read_text()
    assert subprocess.run(["bash", "-n", str(LAUNCHER)]).returncode == 0
    assert "set -euo pipefail" in text and ': "${RELEASE:?' in text
    for shape in SECRET_SHAPES:
        assert not re.search(shape, text), shape
    defined = set(re.findall(r'parser\.add_argument\("(--[\w-]+)"', CERTIFY.read_text()))
    assert LOCAL_ONLY < defined and {"--run-profile", "--key-inventory", "--overload-profile"} <= defined
    rb = (support.REPO / "models" / "marlin2b" / "results" / "E4C-runbook.md").read_text()
    runbook = re.search(r"python tests/integration/backend/certify\.py --no-stack --box.*?\n```",
                        rb, re.S).group(0)
    assert set(_flags(runbook)) == defined - LOCAL_ONLY, set(_flags(runbook)) ^ (defined - LOCAL_ONLY)

    secret = f"{support.MARKER}-owner"
    nvme, etc = tmp_path / "nvme", tmp_path / "etc"
    e4c = nvme / "e4b" / "e4c"
    for d in (nvme / "w3-corpus", e4c, etc):
        d.mkdir(parents=True, exist_ok=True)
    for f in ("key.env", "inventory.txt", "parity-e0.jsonl"):
        (nvme / "e4b" / f).write_text("INFRX_API_KEY=k\n" if f == "key.env" else "x\n")
    for f in ("E4C-box.json", "E4C-edge.json", "keys-certify.json"):
        (e4c / f).write_text("{}\n")
    (etc / "marlin2b-gateway.env").write_text(
        f"DATABASE_URL=postgresql://infrx_runtime.ref:{secret}@pooler:6543/postgres\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    for tool, body in (("docker", DOCKER_CERTIFY.format(python=sys.executable)),
                       ("aws", f"#!/bin/sh\necho 'postgresql://postgres.ref:{secret}@pooler:5432/postgres?sslmode=require'\n"),
                       # the launcher's `sleep 30`: until the backgrounded docker has started
                       ("sleep", '#!/bin/sh\nfor i in $(seq 200); do grep -q \'"run"\' '
                                 '"$(dirname "$0")/docker.log" 2>/dev/null && exit 0; /bin/sleep 0.05; done\n'),
                       ("nohup", '#!/bin/sh\nexec "$@"\n')):
        (stub / tool).write_text(body)
        (stub / tool).chmod(0o755)
    script = text.replace("/opt/dlami/nvme", str(nvme)).replace("/etc/marlin2b-gateway.env",
                                                                 str(etc / "marlin2b-gateway.env"))
    env = {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "RELEASE": "c" * 40,
           "TMPDIR": str(tmp_path)}
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert secret not in done.stdout + done.stderr
    runs = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
    (run,) = [r for r in runs if r["argv"][:1] == ["run"]]
    argv = run["argv"]
    assert not any(secret in a for a in argv), "a DSN on docker's command line"
    command = " ".join(argv)
    assert set(_flags(command)) == defined - LOCAL_ONLY
    for flag, path in (("--run-profile", "/e4b/e4c/E4C-box.json"),
                       ("--key-inventory", "/e4b/e4c/keys-certify.json"),
                       ("--overload-profile", "/e4b/e4c/E4C-edge.json")):
        assert argv[argv.index(flag) + 1] == path, flag
    assert f"{nvme / 'e4b'}:/e4b:ro" in argv
    (dsns,) = [body for path, body in run["env_files"].items()
               if path not in (str(etc / "marlin2b-gateway.env"), str(nvme / "e4b" / "key.env"))]
    assert dsns == (f"DATABASE_URL=postgresql://infrx_runtime.ref:{secret}@pooler:6543/postgres\n"
                    f"OPERATIONS_DATABASE_URL=postgresql://postgres.ref:{secret}@pooler:6543/postgres?sslmode=require\n"
                    "CORPUS_CACHE=/corpus\nE4B_WINDOW_OK=1\n"
                    f"INFRX_CERTIFY_GATEWAY_IMAGE=sha256:{'e' * 64}\nINFRX_CERTIFY_RELEASE_IMAGE=sha256:{'e' * 64}\n")
    assert not list(tmp_path.glob("tmp.*")), "the DSN file outlived the launch"
    (e4c / "E4C-edge.json").unlink()                            # no overload profile: refused
    (stub / "docker.log").unlink()
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert done.returncode == 2 and "E4C-edge.json" in done.stderr
    assert not (stub / "docker.log").exists()
    (e4c / "E4C-edge.json").write_text("{}\n")                  # F2: the owner DSN unreadable
    (stub / "aws").write_text("#!/bin/sh\necho 'ParameterNotFound' >&2; exit 254\n")
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={**env, "OPS_DSN_PARAM": "/model-inference/ops-dsn"})
    assert done.returncode == 2 and "/model-inference/ops-dsn" in done.stderr, done.stderr
    assert not [line for line in (stub / "docker.log").read_text().splitlines()
                if json.loads(line)["argv"][:1] in (["run"], ["ps"])], "a container after a failed read"


# --- E1B-WIRE WR-2: the window launcher runs §7.2's cells, one container each, never overlapping --

E1B_WINDOW = ROLLOUT / "e1b-window.sh"
MARLIN = support.REPO / "models" / "marlin2b"
DOCKER_E1B = '''#!{python}
import json, os, pathlib, signal, sqlite3, sys, time
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "docker.log").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "ps":
    print((here / "ps.txt").read_text(), end="")
elif args[0] == "inspect":                   # only the engine exists; a cell's container is gone
    if args[-1] != "marlin2b-8000":
        sys.exit(1)
    print((here / "engine-args.txt").read_text())
elif args[0] == "kill":                      # only a live WC-8 first half can be signalled
    if not (here / "sop.pid").exists():
        sys.exit(1)
    os.kill(int((here / "sop.pid").read_text()), signal.SIGINT if "--signal" in args else signal.SIGKILL)
elif args[0] == "run" and "--state" in args and (here / "sop-mode").exists() \\
        and not (here / "sop.pid").exists():    # WC-8's first half: 3 done, then held
    stuck = (here / "sop-mode").read_text() == "stuck"
    signal.signal(signal.SIGINT, signal.SIG_IGN if stuck else lambda *_: sys.exit(130))
    (here / "sop.pid").write_text(str(os.getpid()))
    out = next(v.rsplit(":", 1)[0] for v in args if v.endswith(":/out"))
    db = sqlite3.connect(out + "/sop.sqlite")
    db.execute("create table items (state text)")
    db.executemany("insert into items values (?)", [("done",)] * 3)
    db.commit()
    print("first half: 3 done", flush=True)
    time.sleep(20)
'''


def _e1b_box(tmp_path, *, ps="", seqs="8"):
    nvme = tmp_path / "nvme"
    e4c = nvme / "e4b" / "e4c"
    e4c.mkdir(parents=True)
    (nvme / "w3-checkout").symlink_to(support.REPO)
    for name, base in (("E1B-direct.json", "E1B-direct.base.json"), ("E1B-box.json", "E1B-box.base.json"),
                       ("E1B-box-forms.json", "E1B-box.forms.base.json"), ("E1B-sop.json", "E1B-sop.base.json"),
                       ("keys-certify.json", None)):
        (e4c / name).write_text((MARLIN / "profiles" / base).read_text() if base else
                                '{"active_key_id_prefixes": ["142c7d81"]}')
    (nvme / "e4b" / "key.env").write_text(f"INFRX_API_KEY={support.MARKER}\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(DOCKER_E1B.format(python=sys.executable))
    (stub / "docker").chmod(0o755)
    (stub / "ps.txt").write_text(ps)
    (stub / "engine-args.txt").write_text(json.dumps(["--model", "/w", "--max-num-seqs", seqs],
                                                          separators=(",", ":")))   # docker's {{json}}
    script = E1B_WINDOW.read_text().replace("/opt/dlami/nvme", str(nvme))
    return nvme, stub, script


def _e1b_run(stub, script, **env):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                               "RELEASE": "c" * 40, **env})


def _parser_flags(path: pathlib.Path) -> set[str]:
    return {f for pair in re.findall(r'add_argument\("(-[\w-]+)"(?:, "(--[\w-]+)")?', path.read_text())
            for f in pair if f}


def test_e1b_window__cells_run_in_order_one_container_each_with_only_parser_flags(tmp_path):
    """E1B-PREP WR-2 (E1B-protocol §7.1 rules 1-2, §7.2 order). Oracle: a cell out of §7.2's
    order (WC-1, WC-2, WC-3, WC-4, WC-5, WC-8) or WC-7 run with any other cell (its cold start
    is WC-6a's restore); a bench or dataset flag the client's parser does not define (a
    renamed flag would be refused on the box); a cell container that is not infrx-e1b-<cell>,
    not --rm, or whose profile is not its own stamped copy; the tenant key handed to the
    engine (direct cells) or put on a command line; DRY_RUN touching docker."""
    nvme, stub, script = _e1b_box(tmp_path)
    done = _e1b_run(stub, script, DRY_RUN="1")
    assert done.returncode == 0, done.stderr
    assert not (stub / "docker.log").exists(), "DRY_RUN called docker"
    done = _e1b_run(stub, script)
    assert done.returncode == 0, done.stderr
    assert support.MARKER not in done.stdout + done.stderr
    calls = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
    assert not any(support.MARKER in a for call in calls for a in call)
    runs = [c for c in calls if c[0] == "run"]
    names = [r[r.index("--name") + 1] for r in runs]
    assert names == [f"infrx-e1b-{n}" for n in ("L1-c1", "L1-c2", "L1-c4", "L1-c8", "pair-r0.5",
                                                "pair-r2.0", "L3", "L5", "forms", "sop", "sop", "sop")]
    bench_flags, dataset_flags = _parser_flags(MARLIN / "bench.py"), _parser_flags(MARLIN / "dataset.py")
    for r in runs:
        name = r[r.index("--name") + 1].removeprefix("infrx-e1b-")
        image = r.index(f"infrx-certify:{'c' * 40}")
        docker, client = r[:image], r[image + 1:]
        assert "--rm" in docker and "--network" in docker and "-e" in docker
        assert docker[docker.index("-e") + 1] == "CORPUS_CACHE"                # a name, never a value
        assert all(v.endswith(":ro") for i, v in enumerate(docker) if docker[i - 1] == "-v"
                   and not v.endswith(":/out")), docker
        direct = "direct" in client
        assert ("--env-file" in docker) is (not direct and "export" not in client), (name, docker)
        flags = {a for a in client if a.startswith("-")}
        if client[1].endswith("bench.py"):
            assert flags <= bench_flags, flags - bench_flags
            assert client[client.index("--profile") + 1] == f"/out/profiles/{name}.json"
            assert client[client.index("--key-inventory") + 1] == "/e4c/keys-certify.json"
        else:
            assert flags <= dataset_flags, flags - dataset_flags
    out = pathlib.Path(re.search(r"^out=(\S+)$", done.stdout, re.M).group(1))
    assert [line.split()[0] for line in (out / "cells.tsv").read_text().splitlines()] == \
        ["WC-1"] * 4 + ["WC-2"] * 2 + ["WC-3", "WC-4", "WC-5", "WC-8"]
    stamped = json.loads((out / "profiles" / "pair-r2.0.json").read_text())
    assert stamped["measurement"]["rate_per_s"] == 2.0 and stamped["measurement"]["arrival"] == "open-loop"
    assert stamped["workload"]["dataset_version"] == "e1b-w1-pair-r2.0"
    assert stamped["identity"]["run_id"] == "e1b-w1-direct-pair-r2.0"
    stamped = json.loads((out / "profiles" / "L1-c4.json").read_text())
    assert stamped["measurement"]["concurrency"] == 4 and stamped["measurement"]["rate_per_s"] is None
    (stub / "docker.log").unlink()
    done = _e1b_run(stub, script, CELLS="WC-7")                  # alone: its own invocation
    assert done.returncode == 0, done.stderr
    assert [c[c.index("--name") + 1] for c in map(json.loads, (stub / "docker.log").read_text()
            .splitlines()) if c[0] == "run"] == ["infrx-e1b-cold"]
    for cells in ("WC-7 WC-3", "WC-6a", "WC-9"):
        done = _e1b_run(stub, script, CELLS=cells, DRY_RUN="1")
        assert done.returncode == 2 and "plan " not in done.stdout, cells


def test_e1b_window__refuses_a_cell_that_would_overlap_or_run_off_the_pinned_engine(tmp_path):
    """E1B-protocol §7.1 rule 1: one engine, so a window cell never shares it. Oracle: a cell
    started while a certify container exists (any name), while a previous cell's
    infrx-e1b-* container is left, or on an engine whose --max-num-seqs is not the filled
    profile's pin (the ladder's top rung would not be the served concurrency) - each must be
    refused before any container runs; a certify container at the top of a long listing too
    (E1BW-R1: `grep -v | grep -q` under pipefail SIGPIPEs the first grep and the guard passed)."""
    long = "sharp_hopper infrx-certify:" + "c" * 40 + "\n" + "".join(f"pg_{n} postgres:16\n" for n in range(50000))
    for n, (ps, seqs, why) in enumerate((("sharp_hopper infrx-certify:" + "c" * 40 + "\n", "8", "a certify run is live"),
                          (long, "8", "a certify run is live"),     # E1BW-R1: no SIGPIPE'd pipeline
                          ("infrx-e1b-L1-c1 infrx-certify:" + "c" * 40 + "\n", "8", "a previous cell left"),
                          ("", "32", "not at the pinned max_num_seqs 8"))):
        _, stub, script = _e1b_box(tmp_path / str(n), ps=ps, seqs=seqs)
        done = _e1b_run(stub, script)
        assert done.returncode == 2 and why in done.stderr, (why, done.stderr)
        calls = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
        assert not [c for c in calls if c[0] == "run"], why


def test_e1b_window__wc8_keeps_the_interrupted_half_and_bounds_its_exit(tmp_path):
    """E1B-protocol §7.2 WC-8 (MARLIN-SOP's interrupt half). Oracle: the interrupted run's
    output discarded (it ran detached into /dev/null, so the half the resume is judged
    against left no log; E1BW-R2); a first half that ignores SIGINT holding the window
    forever instead of being killed after the bound and recorded (E1BW-R3); the progress
    poll creating the state file itself (a root-owned empty sop.sqlite the container's
    dataset.py may not write)."""
    for mode in ("exits", "stuck"):
        _, stub, script = _e1b_box(tmp_path / mode)
        (stub / "sop-mode").write_text(mode)
        done = subprocess.run(["bash", "-c", script.replace("t < 120", "t < 2")],
                              capture_output=True, text=True, timeout=120,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                                   "RELEASE": "c" * 40, "CELLS": "WC-8"})
        assert done.returncode == 0, (mode, done.stderr)
        out = pathlib.Path(re.search(r"^out=(\S+)$", done.stdout, re.M).group(1))
        assert (out / "sop-interrupted.log").read_text().startswith("first half: 3 done\n"), mode
        assert "WC-8 SIGINT after 3 done" in done.stdout, (mode, done.stdout)
        calls = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
        kills = [c for c in calls if c[0] == "kill"]
        rows = (out / "cells.tsv").read_text().splitlines()
        if mode == "exits":
            assert kills == [["kill", "--signal", "INT", "infrx-e1b-sop"]], kills
            assert rows == ["WC-8 sop exit=0"], rows
        else:
            assert kills[-1] == ["kill", "infrx-e1b-sop"], kills
            assert rows == ["WC-8 interrupt did not exit in 120 s: killed", "WC-8 sop exit=0"], rows
        assert len([c for c in calls if c[0] == "run"]) == 3, mode      # half, resume, export
    _, stub, script = _e1b_box(tmp_path / "unstarted")          # no state yet: the poll opens it read-only
    done = _e1b_run(stub, script.replace("sleep 2\n", "sleep 0\n"), CELLS="WC-8")
    assert done.returncode == 0, done.stderr
    out = pathlib.Path(re.search(r"^out=(\S+)$", done.stdout, re.M).group(1))
    assert not (out / "sop.sqlite").exists()
