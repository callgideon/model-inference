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
                                       "50-install.sh", "55-runtime-login.sh",
                                       "60-verify-local.sh", "71-pool-budget.sh",
                                       "72-observe-install.sh", "73-observe-status.sh",
                                       "74-alert-test.sh", "78-e4b-report.sh", "79-evidence-export.sh",
                                       "80-mirror-artifacts.sh",
                                       "81-restore-artifacts.sh", "85-known-good-box.sh",
                                       "86-cleanup.sh",
                                       "90-revert.sh", "91-abort.sh",
                                       "93-restore-edge.sh", "95-maintenance.sh"]
    for path in [*STEPS, ROLLOUT / "ssm.sh", ROLLOUT / "verify-external.sh",
                 ROLLOUT / "verify-journey.sh", ROLLOUT / "e4c-certify.sh"]:
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
