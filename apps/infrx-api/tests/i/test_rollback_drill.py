"""I8 slice 6: the rollback drill - a known-good target by its tree and its record, the box's
readiness to reinstall it, the proof that the rolled-back (and rolled-forward) runtime
SERVES a real in-cap video job with its result and settleable usage, and timings that never
pass a readiness for a cold start.

Failure oracles: a tree without the preparation loop (27af05a) or with unapplied migrations,
unknown config names or no recorded evidence is NOT known-good; a backup is shown holding
the release it really holds; the journey fails on a public 503 after readiness, a failed or
empty job, or unsettleable usage, and in maintenance on anything admitted; install.sh labels
an engine that was not restarted as NOT a cold start.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tarfile
from pathlib import Path

from . import support
from .test_ops_steps import STEPS, calls, run_step, stubs
from .test_scripts import Host

ROLLOUT = support.REPO / "infra" / "rollout"
KNOWN_GOOD = runpy.run_path(str(ROLLOUT / "known-good.py"))
MAIN = "apps/infrx-api/infrx/worker/__main__.py"
PREP = "apps/infrx-api/infrx/worker/preparation.py"
PREFLIGHT = "apps/infrx-api/deploy/preflight.py"
MIG = "apps/app/supabase/migrations"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release")
    return _git(repo, "rev-parse", "HEAD")


def test_ops_recover__a_known_good_target_is_judged_by_its_tree_and_its_record(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    preflight = 'TUNABLE = (\n    "MAX_VIDEO_SECONDS", "WORKER_CONCURRENCY",\n)\nKey("DATABASE_URL", "x", "pg_dsn")\n'
    old = _commit(repo, {MAIN: "WorkerLoop()\n", f"{MIG}/0018_x.sql": "", PREFLIGHT: preflight,
                         "evidence/old.md": "x"})
    good = _commit(repo, {MAIN: "PreparationRunner(jobs)\n", PREP: "class PreparationRunner: ...\n",
                          "evidence/good.md": "x"})
    ahead = _commit(repo, {f"{MIG}/0019_y.sql": ""})
    registry = {"releases": [
        {"sha": old, "known_good": True, "evidence": ["evidence/old.md"]},   # record alone is not enough
        {"sha": good, "known_good": True, "evidence": ["evidence/good.md"]},
        {"sha": ahead, "known_good": True, "evidence": ["evidence/missing.md"]}]}
    judge = KNOWN_GOOD["judge"]

    def failed(sha, applied="0018", sets=("MAX_VIDEO_SECONDS",), bundles=None):
        result = judge(sha, applied, list(sets), bundles, registry, repo)
        return result["verdict"], [c["check"] for c in result["checks"] if not c["ok"]]

    assert failed(good) == ("KNOWN-GOOD", [])
    assert failed(old) == ("NOT-KNOWN-GOOD", ["preparation"])                # 27af05a's shape
    assert failed(ahead) == ("NOT-KNOWN-GOOD", ["migrations", "record"])     # 0019 unapplied
    assert failed(good, sets=("MAX_VIDEO_SECONDS", "LARGE_BODY_LIMIT")) == (
        "NOT-KNOWN-GOOD", ["config"])
    assert failed("f" * 40)[1] == ["commit"]
    beyond = judge(good, "0020", ["MAX_VIDEO_SECONDS"], None, registry, repo)
    assert beyond["verdict"] == "KNOWN-GOOD" and "applied beyond the tree: 0019-0020" in json.dumps(beyond)
    unlisted = judge(good, "0018", [], None, {"releases": []}, repo)
    assert unlisted["verdict"] == "NOT-KNOWN-GOOD"
    # the release bundle, listed read-only in the release prefix
    stub = stubs(tmp_path, "aws", outputs={"aws": f"2026-09-24 12:00:00 1 {good}.bundle\n"
                                                  f"2026-09-24 12:00:00 1 {good}.sha256\n"})
    path = os.environ["PATH"]
    os.environ["PATH"] = f"{stub}{os.pathsep}{path}"
    try:
        assert failed(good, bundles="s3://b/releases/") == ("KNOWN-GOOD", [])
        (stub / "aws.out").write_text(f"2026-09-24 12:00:00 1 {good}.sha256\n")
        assert failed(good, bundles="s3://b/releases/") == ("NOT-KNOWN-GOOD", ["bundle"])
    finally:
        os.environ["PATH"] = path
    assert calls(stub)[0]["argv"][-3:] == ["s3", "ls", f"s3://b/releases/{good}."]


def test_ops_recover__the_box_check_shows_what_each_backup_really_holds(tmp_path):
    target = "b" * 40
    releases = tmp_path / "releases"
    releases.mkdir()
    (releases / f"{target}.bundle").write_bytes(b"bundle")
    digest = hashlib.sha256(b"bundle").hexdigest()
    (releases / f"{target}.sha256").write_text(f"{digest}  {target}.bundle\n")
    backups = tmp_path / "backups" / "20260924T200647.1Z-bda15866e5700f3856d7142580da842fba9bbd23"
    backups.mkdir(parents=True)
    env = (f"INFRX_RELEASE_SHA=27af05a8cb06ff446d62d13bfbd83f6e573d3461\n"
           f"DATABASE_URL=postgresql://u:{support.MARKER}@h/d\n").encode()
    with tarfile.open(backups / "files.tar", "w") as tar:
        info = tarfile.TarInfo("etc/marlin2b-gateway.env")
        info.size = len(env)
        tar.addfile(info, io.BytesIO(env))
    stub = stubs(tmp_path, "git", "docker")
    env_vars = {"TARGET": target, "RELEASES_DIR": str(releases), "BACKUPS": str(tmp_path / "backups"),
                "STUB_EXIT_docker": "1"}
    done = run_step((STEPS / "85-known-good-box.sh").read_text(), stub, env=env_vars)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "matches its sha256" in done.stdout and "not cached" in done.stdout
    assert f"{backups.name} -> 27af05a8cb06ff446d62d13bfbd83f6e573d3461" in done.stdout
    assert support.MARKER not in done.stdout + done.stderr
    (releases / f"{target}.bundle").write_bytes(b"tampered")
    assert run_step((STEPS / "85-known-good-box.sh").read_text(), stub, env=env_vars).returncode == 1
    assert run_step((STEPS / "85-known-good-box.sh").read_text(), stub,
                    env={**env_vars, "TARGET": "bda1586"}).returncode == 2


CURL_API = '''#!{python}
import json, os, pathlib, stat, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
url = args[-1]
def arg(flag):
    return args[args.index(flag) + 1] if flag in args else None
header = arg("-H")
record = {{"argv": args}}
if header and header.startswith("@"):
    record["header"] = pathlib.Path(header[1:]).read_text()
    record["mode"] = oct(stat.S_IMODE(os.stat(header[1:]).st_mode))
with (here / "curl.log").open("a") as log:
    log.write(json.dumps(record) + "\\n")
E = os.environ.get
code, body, extra = "200", {{}}, ""
if url.endswith("/health"):
    code = E("J_HEALTH", "200")
elif url.endswith("/v1/jobs") and E("J_SUBMIT") == "503":
    code, body, extra = "503", {{"error": {{"code": "dependency_unavailable"}}}}, "Retry-After: 5\\r\\n"
elif url.endswith("/v1/jobs"):
    code, body = E("J_SUBMIT", "202"), {{"job_handle": "job_abc", "request_id": "5b7c0a9e-0000-4000-8000-000000000001"}}
elif url.endswith("/result"):
    body = {{"response": {{"choices": [{{"message": {{"content": E("J_CONTENT", "a person walks")}}}}]}},
            "usage": {{"prompt_tokens": 1043, "completion_tokens": 12}}}}
elif "/v1/jobs/" in url:
    body = {{"state": E("J_STATE", "succeeded"), "usage_certainty": E("J_CERTAINTY", "authoritative")}}
if arg("-o"):
    pathlib.Path(arg("-o")).write_text(json.dumps(body))
if arg("-D"):
    pathlib.Path(arg("-D")).write_text(f"HTTP/1.1 {{code}}\\r\\n{{extra}}")
if arg("-w"):
    sys.stdout.write(code)
'''


def _journey(tmp_path, **env):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "curl").write_text(CURL_API.format(python=sys.executable))
    (stub / "curl").chmod(0o755)
    (stub / "curl.log").unlink(missing_ok=True)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42clip")
    key = f"{support.MARKER}-test-key"
    done = run_step((ROLLOUT / "verify-journey.sh").read_text(), stub, env={
        "INFRX_TEST_KEY": key, "VIDEO_FILE": str(clip), "POLL_S": "0", "EDGE_LAG_MAX_S": "2",
        "HOME": str(tmp_path), **env})
    log = [json.loads(line) for line in (stub / "curl.log").read_text().splitlines()]
    assert key not in done.stdout + done.stderr
    assert not any(key in a for entry in log for a in entry["argv"]), "a key reached argv"
    return done, log, key


def test_ops_recover__the_journey_proves_a_real_video_job_its_result_and_settleable_usage(
        tmp_path):
    done, log, key = _journey(tmp_path)
    assert done.returncode == 0, done.stdout
    for line in ("PASS public edge open", "PASS video job accepted (202)", "PASS job succeeded",
                 "PASS result fetched", "PASS usage authoritative", "timing edge_open_s=",
                 "timing job_s=", "drift.py --request-id 5b7c0a9e-0000-4000-8000-000000000001"):
        assert line in done.stdout, line
    submit = next(e for e in log if e["argv"][-1].endswith("/v1/jobs"))
    assert f"Authorization: Bearer {key}" in submit["header"] and submit["mode"] == "0o600"
    assert "Idempotency-Key: verify-journey-" in submit["header"]            # fresh, never a replay
    for env, message in (({"J_HEALTH": "503"}, "503-after-readiness"),
                         ({"J_STATE": "failed"}, "FAIL job state 'failed'"),
                         ({"J_CONTENT": ""}, "FAIL result"),
                         ({"J_CERTAINTY": "unknown"}, "FAIL usage certainty")):
        done, _, _ = _journey(tmp_path, **env)
        assert done.returncode >= 1 and message in done.stdout, (env, done.stdout)


def test_ops_recover__during_the_window_nothing_new_is_admitted(tmp_path):
    done, log, _ = _journey(tmp_path, EXPECT="maintenance", J_SUBMIT="503")
    assert done.returncode == 0 and "nothing new is admitted" in done.stdout
    assert [e["argv"][-1] for e in log] == ["https://marlin2b.callbill.ai/v1/jobs"]
    done, _, _ = _journey(tmp_path, EXPECT="maintenance", J_SUBMIT="202")
    assert done.returncode == 1 and "the window is not closed" in done.stdout


def test_ops_recover__an_install_never_reports_readiness_as_a_cold_start(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    done = host.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=host.pilot_preflight())
    assert done.returncode == 0, done.stderr
    assert "(engine kept running: NOT a cold start)" in done.stdout
    assert "timing runtime_ready_s=" in done.stdout and "readiness only" in done.stdout
    cold = Host(tmp_path / "cold", monkeypatch)
    done = cold.run("install.sh", INFRX_MODE="pilot", ENGINE="restart", PREFLIGHT=cold.pilot_preflight())
    assert done.returncode == 0 and "(cold start: engine restarted, weights loaded)" in done.stdout
