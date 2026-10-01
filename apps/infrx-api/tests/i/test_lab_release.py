"""W6 lab-release-tool (INFRA-01/03/08/09, DT-13/18): `infra/lab/rollout/lab-release.sh`, the
maintained Lab release tool (box | web | members | main), run layer 0 in a scratch git checkout
with every outside system stubbed: `infra/rollout/ssm.sh` and `verify-external.sh` record their
argv, `aws`, `vercel` and `curl` are PATH stubs, `psycopg` is a stub module on PYTHONPATH and the
`origin` remote is a local bare repository. Nothing here reaches AWS, the box, Vercel or hosted.

Failure oracles: the tool still refuses on the migration level (the spent THROUGH table) or
still offers `window`/`all`; box drops L0 or runs its steps in another order or without
SUPABASE_URL; web stores a pasted Vercel token before `vercel whoami` proved it, stages it at
a fixed HOME path or world-readable, or deploys outside the callgideon scope; members takes
the owner DSN from anywhere but SSM by name, puts it (or the SQL) on an argv, or splices an
email or the operator name into SQL text; main pushes anything but RELEASE:main or moves main
backwards; the launch-v1.sh shim stops forwarding (`vercel` -> `web`).
"""
from __future__ import annotations

import json
import os
import pathlib
import pty
import select
import signal
import subprocess
import sys

from . import support

ROLLOUT = support.REPO / "infra" / "lab" / "rollout"
TOOL = ROLLOUT / "lab-release.sh"
SHIM = ROLLOUT / "launch-v1.sh"
CANARY = "postgresql://owner:CANARY-pw@db.invalid:5432/postgres"
ANON = "anon-CANARY-key"

STUB = '''#!{python}
import json, os, pathlib, stat, sys
log = pathlib.Path({log!r})
params = pathlib.Path({params!r})
name = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
stdin = sys.stdin.read() if name == "vercel" and a[:2] == ["env", "add"] else None
with log.open("a") as f:
    f.write(json.dumps([name, *a, "token=" + os.environ.get("VERCEL_TOKEN", "-"), stdin]) + "\\n")
if name == "aws":
    a = a[a.index("ssm"):]
    p = json.loads(params.read_text())
    if a[1] == "put-parameter":
        staged = pathlib.Path(a[a.index("--value") + 1].removeprefix("file://"))
        with log.open("a") as f:
            f.write(json.dumps(["staged", str(staged), stat.S_IMODE(staged.stat().st_mode)]) + "\\n")
        p[a[a.index("--name") + 1]] = staged.read_text()
        params.write_text(json.dumps(p))
    elif a[1] == "get-parameter":
        print(p[a[a.index("--name") + 1]])
elif name == "vercel" and a[:1] == ["whoami"]:
    sys.exit(0 if os.environ.get("VERCEL_TOKEN") == "good-token" else 1)
'''

PSYCOPG = '''import json, os, pathlib, sys
LOG = pathlib.Path(os.environ["STUB_PSYCOPG_LOG"])
def _log(**e):
    with LOG.open("a") as f:
        f.write(json.dumps(e) + "\\n")
_log(argv=sys.argv)
class _Conn:
    def __enter__(self): return self
    def __exit__(self, *exc): _log(committed=exc[0] is None)
    def execute(self, sql, params=None):
        _log(sql=sql, params=params)
        return iter([("a@x.io", "developer", "2026-10-01 00:00:00+00")]) if sql.lstrip().startswith("select") else iter(())
def connect(conninfo, **kw):
    _log(connect=conninfo)
    return _Conn()
'''


class Checkout:
    """A scratch git checkout carrying the tool, stub ssm.sh/verify-external.sh, a stub bin dir
    and a bare `origin`; `run(...)` returns (exit code, stdout+stderr, calls)."""

    def __init__(self, root: pathlib.Path):
        self.root, self.repo, self.log = root, root / "repo", root / "calls.log"
        self.params = root / "params.json"
        self.params.write_text(json.dumps({"/model-inference/pg_journal_url": CANARY,
                                           "/model-inference/lab/supabase_anon_key": ANON,
                                           "/callgideon/prod/VERCEL_TOKEN": "stored-bad-token"}))
        lab = self.repo / "infra" / "lab" / "rollout"
        lab.mkdir(parents=True)
        for script in (TOOL, SHIM):
            (lab / script.name).write_bytes(script.read_bytes())
        rec = f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$(basename "$0") $*" >> {self.root / "ssm.log"}\n'
        for name in ("ssm.sh", "verify-external.sh"):
            path = self.repo / "infra" / "rollout" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rec)
            path.chmod(0o755)
        host_lib = support.REPO / "infra" / "rollout" / "host-lib.sh"   # WR-IL-1: the tool sources it
        (self.repo / "infra" / "rollout" / "host-lib.sh").write_bytes(host_lib.read_bytes())
        mig = self.repo / "apps" / "app" / "supabase" / "migrations"
        mig.mkdir(parents=True)
        (mig / "0060_beyond_any_reproof.sql").write_text("select 1;\n")   # no THROUGH level gates the tool
        venv = self.repo / "apps" / "infrx-api" / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(os.path.realpath(sys.executable))   # no pyvenv.cfg: the stub psycopg only
        self.bin, self.pypath, self.tmp, self.home = root / "bin", root / "py", root / "tmp", root / "home"
        for d in (self.bin, self.pypath, self.tmp, self.home):
            d.mkdir()
        for name in ("aws", "vercel", "curl"):
            (self.bin / name).write_text(STUB.format(python=sys.executable, log=str(self.log),
                                                     params=str(self.params)))
            (self.bin / name).chmod(0o755)
        (self.pypath / "psycopg.py").write_text(PSYCOPG)
        self.git("init", "-q", "-b", "trunk")
        self.git("config", "user.name", "Scratch Operator")
        self.git("config", "user.email", "op@invalid")
        self.base = self.commit("base")
        subprocess.run(["git", "init", "-q", "--bare", str(root / "origin.git")], check=True)
        self.git("remote", "add", "origin", str(root / "origin.git"))
        self.git("push", "-q", "origin", f"{self.base}:refs/heads/main")
        self.release = self.commit("release")
        self.git("branch", "claude/consumer-v1", self.release)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, msg: str) -> str:
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def env(self, **extra: str) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPABASE", "VERCEL", "TESTER",
                                                                        "OPERATOR", "OPS_", "WINDOW"))}
        env.update(PATH=f"{self.bin}:{env['PATH']}", HOME=str(self.home), TMPDIR=str(self.tmp),
                   PYTHONPATH=str(self.pypath), STUB_PSYCOPG_LOG=str(self.root / "psycopg.log"),
                   RELEASE=self.release)
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}   # extra NAME=None unsets NAME

    def run(self, *args: str, script: str = "lab-release.sh", **extra: str):
        done = subprocess.run(["bash", f"infra/lab/rollout/{script}", *args], cwd=self.repo,
                              capture_output=True, text=True, env=self.env(**extra),
                              stdin=subprocess.DEVNULL, timeout=60, start_new_session=True)
        return done.returncode, done.stdout + done.stderr, self.calls()

    def run_tty(self, *args: str, typed: bytes, **extra: str):
        """The tool on a pseudo-terminal (its token prompt reads /dev/tty); `typed` answers the prompt."""
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(self.repo)
                os.execvpe("bash", ["bash", "infra/lab/rollout/lab-release.sh", *args], self.env(**extra))
            finally:
                os._exit(127)
        out, sent = b"", False
        while select.select([fd], [], [], 60)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if not sent and b"token (hidden): " in out:
                os.write(fd, typed + b"\n")
                sent = True
        else:
            os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        return os.waitstatus_to_exitcode(status), out.decode(errors="replace"), self.calls()

    def calls(self) -> list:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def ssm(self) -> list[str]:
        path = self.root / "ssm.log"
        return path.read_text().splitlines() if path.exists() else []

    def psycopg(self) -> list[dict]:
        path = self.root / "psycopg.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_lab_release__strict_bash_with_no_window_left(tmp_path):
    """INFRA-03/09: both scripts parse and are strict; the spent window half is gone - `window`
    and `all` are refused as unknown actions and box runs on a checkout whose newest migration
    (0060) no THROUGH level names; the two spent patches are evidence, not rollout tooling."""
    for script in (TOOL, SHIM):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script
        assert "set -euo pipefail" in script.read_text(), script
    assert not list(ROLLOUT.glob("*.patch"))
    evidence = support.REPO / "research" / "plan" / "evidence" / "i"
    assert {p.name for p in evidence.glob("hosted-migrate-00*.patch")} == \
        {"hosted-migrate-0052-0056.patch", "hosted-migrate-0057-0059.patch"}
    c = Checkout(tmp_path)
    for action in ("window", "all", "vercel", ""):
        code, out, calls = c.run(action, SUPABASE_URL="https://ref.supabase.co")
        assert code == 2 and "lab-release.sh box" in out and not calls and not c.ssm(), (action, out)
    code, out, _ = c.run("box", SUPABASE_URL="https://ref.supabase.co")
    assert code == 0, out


def test_lab_release__box_runs_the_launched_order_through_ssm_by_name(tmp_path):
    """INFRA-09: box is the 2026-10-01 launch's sequence, unchanged - L0 lab-checkout, L1, L3, L4,
    L5 (the control DSN and anon key by SSM NAME), L5s, L6, L6s, then the App's external checks;
    without SUPABASE_URL nothing is called."""
    c = Checkout(tmp_path)
    code, out, calls = c.run("box")
    assert code == 2 and "set SUPABASE_URL" in out and not calls and not c.ssm(), out
    code, out, _ = c.run("box", SUPABASE_URL="https://ref.supabase.co")
    assert code == 0, out
    r = c.release
    assert c.ssm() == [
        f"ssm.sh infra/lab/rollout/lab-checkout.sh RELEASE={r}",
        f"ssm.sh infra/lab/rollout/steps/10-lab-preflight.sh RELEASE={r}",
        f"ssm.sh infra/lab/rollout/steps/20-lab-image.sh RELEASE={r}",
        f"ssm.sh infra/lab/rollout/steps/30-lab-units.sh RELEASE={r}",
        f"ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=on RELEASE={r} "
        "CONTROL_DSN_PARAM=/model-inference/lab/control_database_url "
        "ANON_KEY_PARAM=/model-inference/lab/supabase_anon_key SUPABASE_URL=https://ref.supabase.co "
        "LAB_ORIGIN=https://lab.callbill.ai",
        "ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh",
        f"ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=on RELEASE={r}",
        "ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh",
        "verify-external.sh ",
    ]
    code, out, _ = c.run("box", SUPABASE_URL="https://ref.supabase.co", RELEASE="main")
    assert code == 2 and "40-hex" in out, out


def test_lab_release__box_defaults_release_to_the_claude_consumer_v1_tip(tmp_path):
    """LRT-RV-2: with RELEASE unset, box releases the tip of claude/consumer-v1 (not HEAD): the L0
    line carries that sha."""
    c = Checkout(tmp_path)
    assert c.commit("past the release") != c.release   # HEAD moves on; claude/consumer-v1 stays
    code, out, _ = c.run("box", SUPABASE_URL="https://ref.supabase.co", RELEASE=None)
    assert code == 0, out
    assert c.ssm()[0] == f"ssm.sh infra/lab/rollout/lab-checkout.sh RELEASE={c.git('rev-parse', 'claude/consumer-v1')}"


def test_lab_release__web_validates_a_pasted_token_before_storing_it(tmp_path):
    """INFRA-08: a pasted token is staged under mktemp (TMPDIR, mode 0600, never a fixed HOME
    path), proven by `vercel whoami` BEFORE it overwrites the stored one, and removed after; an
    invalid paste stops the action with the stored token untouched."""
    c = Checkout(tmp_path)
    code, out, calls = c.run_tty("web", typed=b"bad-token", SUPABASE_URL="https://ref.supabase.co")
    assert code == 2 and "not valid" in out, out
    assert not [x for x in calls if x[0] == "aws" and "put-parameter" in x]
    assert json.loads(c.params.read_text())["/callgideon/prod/VERCEL_TOKEN"] == "stored-bad-token"
    assert not list(c.tmp.iterdir())   # LRT-RV-1: the EXIT trap removed the staged bad paste
    c.log.unlink()
    code, out, calls = c.run_tty("web", typed=b"good-token", SUPABASE_URL="https://ref.supabase.co")
    assert code == 0, out
    whoami = next(i for i, x in enumerate(calls) if x[:2] == ["vercel", "whoami"])
    put = next(i for i, x in enumerate(calls) if x[0] == "aws" and "put-parameter" in x)
    assert whoami < put and calls[whoami][2] == "token=good-token", calls
    staged = next(x for x in calls if x[0] == "staged")
    assert pathlib.Path(staged[1]).parent == c.tmp and staged[2] == 0o600, staged
    assert not pathlib.Path(staged[1]).exists() and not list(c.home.iterdir())
    assert json.loads(c.params.read_text())["/callgideon/prod/VERCEL_TOKEN"] == "good-token"
    assert "good-token" not in out


def test_lab_release__web_deploys_infrx_lab_in_the_callgideon_scope(tmp_path):
    """INFRA-09: Enter = the stored token (or the CLI login); the project infrx-lab is linked in
    the App's team (callgideon unless VERCEL_SCOPE says otherwise), deployed from the repo root,
    with the env list of the launch: LAB_API_URL, the one Lab API name (the six deprecated names
    were removed from the Vercel project on 2026-10-01 after the fallback landed, LAB-03)."""
    c = Checkout(tmp_path)
    c.params.write_text(json.dumps({**json.loads(c.params.read_text()),
                                    "/callgideon/prod/VERCEL_TOKEN": "good-token"}))
    code, out, calls = c.run("web")
    assert code == 2 and "set SUPABASE_URL" in out, out
    c.log.unlink()
    code, out, calls = c.run("web", SUPABASE_URL="https://ref.supabase.co")
    assert code == 0, out
    vercel = [x[1:-2] for x in calls if x[0] == "vercel"]
    assert ["link", "--yes", "--project", "infrx-lab", "--scope", "callgideon"] in vercel, vercel
    assert ["--prod", "--yes"] in vercel and ["domains", "add", "lab.callbill.ai"] in vercel
    added = {x[3]: x[-1] for x in calls if x[:3] == ["vercel", "env", "add"]}
    assert added == {"NEXT_PUBLIC_LAB_URL": "https://lab.callbill.ai",
                     "NEXT_PUBLIC_SUPABASE_URL": "https://ref.supabase.co",
                     "NEXT_PUBLIC_SUPABASE_ANON_KEY": ANON,
                     **{k: "https://lab-control.callbill.ai" for k in (
                         "LAB_API_URL")}}
    assert ANON not in out
    c.log.unlink()
    assert c.run("web", SUPABASE_URL="https://ref.supabase.co", VERCEL_SCOPE="other")[0] == 0
    assert ["link", "--yes", "--project", "infrx-lab", "--scope", "other"] in \
        [x[1:-2] for x in c.calls() if x[0] == "vercel"]


def test_lab_release__members_reads_the_dsn_by_name_and_binds_every_value(tmp_path):
    """INFRA-01/DT-18: the owner DSN comes from SSM by name (OPS_DSN_PARAM, default
    /model-inference/pg_journal_url) into the environment of a psycopg program read from stdin:
    never on an argv, never printed; the operator name and every email are bound parameters (an
    apostrophe is data); nothing is typed; the output is the org's memberships by email."""
    c = Checkout(tmp_path)
    code, out, calls = c.run("members")
    assert code == 2 and "set TESTER_EMAILS" in out and not calls and not c.psycopg(), out
    code, out, calls = c.run("members", TESTER_EMAILS="a@x.io o'brien@x.io")
    assert code == 0, out
    assert ["aws", "--region", "us-east-1", "ssm", "get-parameter", "--with-decryption", "--name",
            "/model-inference/pg_journal_url", "--query", "Parameter.Value", "--output", "text",
            "token=-", None] in calls, calls
    log = c.psycopg()
    assert log[0]["argv"][0] == "-" and CANARY not in json.dumps(log[0]["argv"])   # the program came on stdin
    assert log[1] == {"connect": CANARY}
    assert all(CANARY not in json.dumps(x) for x in calls) and CANARY not in out
    sql = [x for x in log if "sql" in x]
    by = "operator:Scratch Operator P-08 launch-v1"
    assert [x["params"] for x in sql] == [[by], [by, "a@x.io"], [by, "o'brien@x.io"], None], sql
    for x in sql:
        assert "o'brien" not in x["sql"] and "a@x.io" not in x["sql"] and "Scratch" not in x["sql"], x
    assert "insert into infrx.provider_orgs" in sql[0]["sql"] and "%s" in sql[0]["sql"]
    assert "p.provider_org_id" in sql[1]["sql"] and "u.email = %s" in sql[1]["sql"]
    assert log[-1] == {"committed": True}
    assert "a@x.io\tdeveloper\t2026-10-01 00:00:00+00" in out
    (tmp_path / "psycopg.log").unlink()
    c.params.write_text(json.dumps({**json.loads(c.params.read_text()), "/other/dsn": CANARY + "2"}))
    code, out, _ = c.run("members", TESTER_EMAILS="a@x.io", OPS_DSN_PARAM="/other/dsn",
                         OPERATOR_NAME="O'Neil", WINDOW="P-08:2026-10-02")
    assert code == 0, out
    log = c.psycopg()
    assert log[1] == {"connect": CANARY + "2"}
    assert [x["params"] for x in log if "sql" in x][0] == ["operator:O'Neil P-08 P-08:2026-10-02"]


def test_lab_release__main_fast_forwards_main_to_the_release_only(tmp_path):
    """main pushes RELEASE:main after a fetch, only as a fast-forward; claude/consumer-v1 is never
    pushed; a RELEASE main is not an ancestor of is refused with main unmoved."""
    c = Checkout(tmp_path)
    origin = ["git", "--git-dir", str(tmp_path / "origin.git")]

    def heads():
        return subprocess.run([*origin, "for-each-ref", "--format=%(refname) %(objectname)"],
                              capture_output=True, text=True, check=True).stdout.split("\n")[:-1]

    code, out, _ = c.run("main")
    assert code == 0, out
    assert heads() == [f"refs/heads/main {c.release}"]
    c.git("checkout", "-q", "--orphan", "side")
    other = c.commit("unrelated")
    code, out, _ = c.run("main", RELEASE=other)
    assert code == 1 and "not an ancestor" in out, out
    assert heads() == [f"refs/heads/main {c.release}"]


def test_lab_release__preflight_reads_names_only_and_refuses_a_foreign_release(tmp_path):
    """preflight is read-only: a RELEASE off claude/consumer-v1 is refused before any call; on it,
    SSM is asked for parameter NAMES (describe-parameters), never a value."""
    c = Checkout(tmp_path)
    c.git("checkout", "-q", "--orphan", "side")
    other = c.commit("unrelated")
    code, out, calls = c.run("preflight", RELEASE=other)
    assert code == 2 and "not on claude/consumer-v1" in out and not calls, out
    code, out, calls = c.run("preflight")
    assert code == 0, out
    ssm = [x[3:5] for x in calls if x[0] == "aws"]
    assert ssm == [["ssm", "describe-parameters"]], calls


def test_lab_release__the_launch_v1_shim_forwards_with_a_deprecation_note(tmp_path):
    """INFRA-09: launch-v1.sh is a two-line shim for one wave: it names lab-release.sh and execs it,
    `vercel` becoming `web`."""
    c = Checkout(tmp_path)
    c.params.write_text(json.dumps({**json.loads(c.params.read_text()),
                                    "/callgideon/prod/VERCEL_TOKEN": "good-token"}))
    code, out, calls = c.run("vercel", script="launch-v1.sh", SUPABASE_URL="https://ref.supabase.co")
    assert code == 0 and "deprecated" in out and "lab-release.sh" in out, out
    assert ["vercel", "--prod", "--yes", "token=good-token", None] in calls
    code, out, _ = c.run("members", script="launch-v1.sh")
    assert code == 2 and "set TESTER_EMAILS" in out, out
