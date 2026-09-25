#!/usr/bin/env python3
"""ROLLOUT-PREP: `deploy/release-bundle.sh`, the release route to the box through the bucket.

Run on a throwaway three-commit repository with a fake `aws` (it records its argv and its
environment, and serves `s3 cp` from a local directory), and a fake `sudo` for the box step:
the bundle carries the commit, its manifest is its sha256, the upload never uses the shell's
stale keys, and the box step refuses a bundle that does not match its manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

from . import support

SCRIPT = support.API_DIR / "deploy" / "release-bundle.sh"

FAKE_AWS = """#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
here = pathlib.Path(__file__).resolve().parent
with (here / "aws.log").open("a") as log:
    log.write(json.dumps({"argv": sys.argv[1:],
                          "stale_keys": "AWS_ACCESS_KEY_ID" in os.environ}) + "\\n")
args = [a for a in sys.argv[1:] if not a.startswith("--") and a not in ("us-east-1",)]
if args[:2] == ["s3", "cp"]:
    src, dst = args[2], args[3]
    store = here / "bucket"
    if src.startswith("s3://"):
        shutil.copy(store / src[5:].replace("/", "_"), dst)
    else:
        store.mkdir(exist_ok=True)
        shutil.copy(src, store / dst[5:].replace("/", "_"))
"""
FAKE_SUDO = '#!/usr/bin/env bash\n[ "$1" = -u ] && shift 2\nexec "$@"\n'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def world(tmp_path):
    """A repository holding the script, three commits, and a PATH of fakes."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "deploy" / "release-bundle.sh")
    git(repo, "init", "-q")
    for n in (1, 2, 3):
        (repo / "f").write_text(str(n))
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", str(n))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("aws", FAKE_AWS), ("sudo", FAKE_SUDO)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "OUT": str(tmp_path / "out"),
           "AWS_ACCESS_KEY_ID": "stale-shell-key"}
    return repo, bin_, env


def run(repo, env, *args):
    return subprocess.run(["bash", str(repo / "deploy" / "release-bundle.sh"), *args],
                          env=env, capture_output=True, text=True)


def test_backend_deploy__a_release_bundle_reaches_the_box_checked_against_its_manifest(
        tmp_path, monkeypatch):
    # E2C (RV-12): run under the default that broke this case on a developer host
    # (init.defaultBranch=main) instead of whatever this host's git config says.
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, bin_, env = world(tmp_path)
    sha = git(repo, "rev-parse", "HEAD~1")               # not HEAD: the commit named is shipped
    done = run(repo, env, sha)
    assert done.returncode == 0, done.stderr
    out = tmp_path / "out"
    bundle = (out / f"{sha}.bundle").read_bytes()
    assert (out / f"{sha}.sha256").read_text() == \
        f"{hashlib.sha256(bundle).hexdigest()}  {sha}.bundle\n"
    calls = [json.loads(line) for line in (bin_ / "aws.log").read_text().splitlines()]
    assert [c["argv"][-1] for c in calls] == [
        f"s3://llm-bootcamp-641134885443/releases/{sha}.bundle",
        f"s3://llm-bootcamp-641134885443/releases/{sha}.sha256"]
    assert not any(c["stale_keys"] for c in calls), "uploaded with the shell's stale keys"
    assert git(repo, "for-each-ref", "refs/infrx") == "", "the script added a ref here"
    step = out / f"{sha}.fetch.sh"
    assert step.read_text() in done.stdout

    # The box step, as ssm.sh runs it: a checkout at the first commit gets the second -
    # and only that one, not HEAD.
    box = tmp_path / "box"
    git(repo, "tag", "old", "HEAD~2")
    # E2C (RV-12): name the unborn branch, so a host whose init.defaultBranch is `main`
    # does not make the fetch below target the checked-out branch (git refuses that).
    git(tmp_path, "init", "-q", "--initial-branch=unborn", str(box))
    git(box, "fetch", "-q", str(repo), "refs/tags/old:refs/heads/main")
    git(box, "checkout", "-q", "main")
    assert subprocess.run(["git", "-C", str(box), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode != 0
    box_env = {**env, "RELEASES_DIR": str(tmp_path / "releases"), "BOX_REPO": str(box)}
    fetched = subprocess.run(["bash", str(step)], env=box_env, capture_output=True, text=True)
    assert fetched.returncode == 0, fetched.stderr
    assert git(box, "rev-parse", f"refs/infrx/releases/{sha}") == sha
    head = git(repo, "rev-parse", "HEAD")
    assert subprocess.run(["git", "-C", str(box), "cat-file", "-e", head],
                          capture_output=True).returncode != 0, "shipped HEAD, not the commit"
    assert git(box, "rev-parse", "HEAD") == git(repo, "rev-parse", "old")
    assert git(box, "status", "--porcelain") == ""

    # A bundle that does not match its manifest is refused before git reads it.
    stored = bin_ / "bucket" / f"llm-bootcamp-641134885443_releases_{sha}.bundle"
    stored.write_bytes(bundle[:-1] + bytes([bundle[-1] ^ 1]))
    git(box, "update-ref", "-d", f"refs/infrx/releases/{sha}")
    refused = subprocess.run(["bash", str(step)], env=box_env, capture_output=True, text=True)
    assert refused.returncode != 0 and "FAILED" in refused.stdout + refused.stderr
    assert git(box, "for-each-ref", "refs/infrx") == ""


def test_backend_deploy__a_release_bundle_refuses_what_is_not_a_commit(tmp_path):
    repo, bin_, env = world(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    for bad in (head[:12], head.upper(), "f" * 40):
        done = run(repo, env, bad)
        assert done.returncode == 2, (bad, done.stdout, done.stderr)
    assert not (bin_ / "aws.log").exists(), "a refusal uploaded something"
