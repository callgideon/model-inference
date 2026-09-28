#!/usr/bin/env python3
"""I6/I7 (LAB-WORKERS, PIPELINE-BUDGET), the security lens: a Lab pipeline or rollout role's
env file is refused before its unit starts unless every external endpoint is approved
(P-10 teacher, P-11 training connector), every paid adapter has a USD budget within its
approval and the approval's named payer, every secret is the role's own and present, and the
egress allowlist names only the object store and the approved host. No docker.

    uv run --frozen pytest -q tests/i/lab_pipeline/test_preflight.py
"""
from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
SCRIPT = REPO / "infra" / "lab" / "workers" / "training" / "preflight.py"
PF = runpy.run_path(str(SCRIPT))
SECRET = "MARKER-SECRET-9f1c"
PAYER = "lab:payer:nemo:0001"
HOST = "api.trainer.example"
APPROVED = {
    "training": [{"adapter": "trainer-x", "host": HOST, "approval": "P-11 test fixture",
                  "payer_ref": PAYER, "budget_usd": "500.00"}],
    "annotation": [{"adapter": "teacher-y", "host": "api.teacher.example",
                    "approval": "P-10 test fixture", "payer_ref": PAYER,
                    "budget_usd": "40.00"}],
}
BASE = {"INFRX_IMAGE": "sha256:" + "a" * 64,
        "LAB_DATABASE_URL": f"postgresql://lab:{SECRET}@pooler.example:6543/postgres",
        "LAB_S3_BUCKET": "lab-bucket", "LAB_S3_ENDPOINT": "https://s3.us-east-1.amazonaws.com",
        "LAB_EGRESS_ALLOW": "s3.us-east-1.amazonaws.com"}


def paid(**changes) -> dict[str, str]:
    env = {**BASE, "LAB_TRAINING_CONNECTOR": "trainer-x",
           "LAB_TRAINING_CONNECTOR_URL": f"https://{HOST}/v1",
           "LAB_TRAINING_CONNECTOR_TOKEN": SECRET, "LAB_TRAINING_BUDGET_USD": "250.00",
           "LAB_TRAINING_PAYER_REF": PAYER,
           "LAB_EGRESS_ALLOW": f"s3.us-east-1.amazonaws.com,{HOST}", **changes}
    return {k: v for k, v in env.items() if v is not None}


def check(role: str, env: dict[str, str], approvals=None) -> list[str]:
    return PF["check"](role, env, APPROVED if approvals is None else approvals)


def test_i6_the_shipped_approvals_are_empty_so_every_role_is_local_or_manual_only() -> None:
    """P-10/P-11 are not approved (15-pending-inputs.md:20-21). Failure oracle: a shipped
    approval (automatic submission or a live teacher silently allowed), or the defaults (the
    manual bundle, the dry-run teacher, no adapter for rollout) refused."""
    shipped = PF["load_approvals"](PF["APPROVALS"])
    assert shipped == {"annotation": [], "training": []}
    for role in ("annotation", "training", "rollout"):
        assert check(role, BASE, shipped) == [], role
    assert check("training", {**BASE, "LAB_TRAINING_CONNECTOR": "manual-bundle"}, shipped) == []
    assert check("annotation", {**BASE, "LAB_ANNOTATION_TEACHER": "dry-run"}, shipped) == []
    assert check("training", paid(), shipped) == [
        "LAB_TRAINING_CONNECTOR: this adapter has no P-11 approval",
        "LAB_EGRESS_ALLOW: entry 2 is neither the object store nor an approved endpoint"]


def test_i6_an_approved_paid_adapter_needs_its_budget_payer_host_and_secret() -> None:
    """PIPELINE-BUDGET. Failure oracle: paid execution with no budget, a zero, negative,
    non-finite or over-approval budget, a payer other than the approval's, a lookalike or
    plaintext endpoint, or a failed secret lookup (absent or empty token) that starts anyway
    instead of failing closed."""
    assert check("training", paid()) == []
    assert check("training", paid(LAB_TRAINING_BUDGET_USD="500.00")) == []
    budget = "LAB_TRAINING_BUDGET_USD: a USD amount above 0 and within the approval's 500.00"
    for bad in (None, "0", "-1", "NaN", "Infinity", "500.01", "1e9", "ten"):
        assert check("training", paid(LAB_TRAINING_BUDGET_USD=bad)) == [budget], bad
    payer = "LAB_TRAINING_PAYER_REF: not the approval's named payer"
    for bad in (None, "", "lab:payer:other:0002"):
        assert check("training", paid(LAB_TRAINING_PAYER_REF=bad)) == [payer], bad
    unnamed = {"training": [{**APPROVED["training"][0], "payer_ref": ""}]}
    assert check("training", paid(LAB_TRAINING_PAYER_REF=""), unnamed) == [payer]
    url = f"LAB_TRAINING_CONNECTOR_URL: not https://{HOST}"
    for bad in (None, f"http://{HOST}/v1", "https://api.trainer.example.evil.com/v1",
                f"https://{HOST}@evil.example/v1", "https://evil.example/v1"):
        assert check("training", paid(LAB_TRAINING_CONNECTOR_URL=bad)) == [url], bad
    for bad in (None, ""):
        assert check("training", paid(LAB_TRAINING_CONNECTOR_TOKEN=bad)) == [
            "LAB_TRAINING_CONNECTOR_TOKEN: the secret lookup failed (absent or empty)"], bad
    assert check("annotation", {**BASE, "LAB_ANNOTATION_TEACHER": "teacher-y",
                                "LAB_ANNOTATION_TEACHER_URL": "https://api.teacher.example/",
                                "LAB_ANNOTATION_TEACHER_TOKEN": SECRET,
                                "LAB_ANNOTATION_BUDGET_USD": "40",
                                "LAB_ANNOTATION_PAYER_REF": PAYER,
                                "LAB_EGRESS_ALLOW": "api.teacher.example"}) == []


def test_i6_an_adapter_is_approved_per_role_and_nothing_is_enabled_silently() -> None:
    """Failure oracle: one role using another role's approval (a teacher approval running a
    training job), a default role carrying a stray endpoint, token, budget or payer (enabled
    by editing one line later), or a secret lookup failure that falls back to another
    provider instead of refusing."""
    assert check("annotation", {**BASE, "LAB_ANNOTATION_TEACHER": "trainer-x"}) == [
        "LAB_ANNOTATION_TEACHER: this adapter has no P-10 approval"]
    for stray in ("LAB_TRAINING_CONNECTOR_URL", "LAB_TRAINING_CONNECTOR_TOKEN",
                  "LAB_TRAINING_BUDGET_USD", "LAB_TRAINING_PAYER_REF"):
        assert check("training", {**BASE, stray: "x"}) == [
            f"{stray}: set while LAB_TRAINING_CONNECTOR is manual-bundle"], stray
    assert check("training", paid(LAB_TRAINING_CONNECTOR_TOKEN=None,
                                  LAB_TRAINING_CONNECTOR="manual-bundle",
                                  LAB_EGRESS_ALLOW=BASE["LAB_EGRESS_ALLOW"])) == [
        f"{name}: set while LAB_TRAINING_CONNECTOR is manual-bundle" for name in
        ("LAB_TRAINING_BUDGET_USD", "LAB_TRAINING_CONNECTOR_URL", "LAB_TRAINING_PAYER_REF")]


@pytest.mark.parametrize("role,name", [
    ("training", "LAB_ANNOTATION_TEACHER_TOKEN"),        # another purpose's secret
    ("annotation", "LAB_TRAINING_CONNECTOR_TOKEN"),
    ("rollout", "LAB_TRAINING_CONNECTOR"),               # rollout has no adapter at all
    ("rollout", "LAB_ROLLOUT_BUDGET_USD"),
    ("rollout", "AWS_ACCESS_KEY_ID"),                    # no automatic capacity purchases
    ("training", "SUPABASE_SERVICE_ROLE_KEY"),           # the consumer's secrets
    ("training", "DATABASE_URL"),
    ("annotation", "https_proxy"),                       # an egress override, any case
    ("annotation", "No_Proxy"),
    ("training", "LAB_EVAL_CONCURRENCY"),                # another Lab role's knob
    ("training", "ALL_PROXY"),                           # the proxy httpx reads last
    ("annotation", "DOCKER_HOST"),                       # the unit's docker CLI, elsewhere
    ("training", "PYTHONPATH"),                          # the preflight's own interpreter
    ("rollout", "LD_PRELOAD"),
    ("training", "SSL_CERT_FILE"),                       # a trusted interception CA
    ("training", "AWS_ENDPOINT_URL"),                    # the object store redirected
])
def test_i6_every_setting_is_named_for_its_role_and_purpose(role, name) -> None:
    """Failure oracle: an env file that smuggles a consumer secret, another purpose's secret,
    cloud credentials (capacity purchases), a proxy override (egress bypass) or a setting that
    steers the unit's docker CLI, the preflight's interpreter or TLS trust past the preflight
    because the name was not on the role's own list. EnvironmentFile= reaches every Exec line
    of the unit, not only the container."""
    assert check(role, {**BASE, name: "x"}) == [f"{name}: not a {role} setting"]


def test_i6_the_image_is_a_local_content_addressed_id_never_a_flag_or_a_pull() -> None:
    """`${INFRX_IMAGE}` is one argv word of `docker run`, before the command. Failure oracle:
    a docker flag there (`--privileged` and the image becomes `python`), an absent or empty
    one (docker then runs the public `python` image), or a registry reference (the daemon
    pulls from that registry: egress the container's deny proxy never sees). Only the
    release's local image id, as the consumer's `image_id` (deploy/preflight.py)."""
    image = "INFRX_IMAGE: not a local image id (sha256:<64 hex>)"
    for bad in (None, "", "--privileged", "-v/:/host", "python", "evil.example/x:latest",
                "ghcr.io/x@sha256:" + "a" * 64, "sha256:" + "a" * 63, "sha256:" + "A" * 64,
                "sha256:" + "a" * 64 + " --privileged"):
        env = {k: v for k, v in {**BASE, "INFRX_IMAGE": bad}.items() if v is not None}
        assert check("training", env) == [image], bad


def test_i6_a_refusal_never_echoes_a_value_pasted_as_a_name_or_an_adapter() -> None:
    """Secret leakage into the journal. Failure oracle: a pasted DSN (`...?sslmode=require`
    has an `=`, so its password lands in the name) or a token pasted into the adapter setting
    printed back by the refusal naming it."""
    env, refusals = PF["parse"](f"postgresql://lab:{SECRET}@h:6543/db?sslmode=require\n"
                                f"Bearer {SECRET}=x\nLAB_S3_BUCKET=b\n")
    assert env == {"LAB_S3_BUCKET": "b"}
    assert refusals == ["line 1: not a setting name (not printed)",
                        "line 2: not a setting name (not printed)"]
    refused = check("training", {**BASE, "LAB_TRAINING_CONNECTOR": SECRET})
    assert refused == ["LAB_TRAINING_CONNECTOR: this adapter has no P-11 approval"]


def test_i6_the_egress_allowlist_is_exact_hosts_of_the_object_store_and_the_approval() -> None:
    """Failure oracle: a wildcard (`*` turns the deny proxy off entirely), a broad suffix, a
    host with no approval, a port or scheme smuggled in, or the approved adapter's host
    allowed while the adapter is off."""
    allow = "LAB_EGRESS_ALLOW"
    for bad in ("*", ".amazonaws.com", "amazonaws.com", "api.other.example",
                "s3.us-east-1.amazonaws.com:443", "https://s3.us-east-1.amazonaws.com", HOST):
        assert check("training", {**BASE, allow: f"s3.us-east-1.amazonaws.com,{bad}"}) == [
            f"{allow}: entry 2 is neither the object store nor an approved endpoint"], bad
    assert check("training", {**BASE, allow: ""}) == []
    assert check("training", {**BASE, allow: " s3.us-east-1.amazonaws.com , "}) == []
    assert check("training", paid(LAB_EGRESS_ALLOW=HOST)) == []


def test_i6_the_env_file_is_parsed_like_docker_and_refuses_a_bare_name() -> None:
    """Failure oracle: a bare `NAME` line (docker's --env-file copies that name from the
    calling environment, unchecked) or a commented, blank or indented line misread."""
    env, refusals = PF["parse"]("# c\n\nLAB_S3_BUCKET=a=b\nAWS_SECRET_ACCESS_KEY\n")
    assert env == {"LAB_S3_BUCKET": "a=b"}
    assert refusals == ["line 4: a bare name copies the caller's environment"]
    assert PF["parse"]("  # indented comment\n") == ({}, [])


def test_i6_the_cli_exits_1_on_a_refusal_and_never_prints_a_value(tmp_path) -> None:
    """Failure oracle: a refused env file that exits 0 (the unit starts anyway), or a secret
    value echoed to the journal."""
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps(APPROVED))
    good, bad = tmp_path / "good.env", tmp_path / "bad.env"
    good.write_text("".join(f"{k}={v}\n" for k, v in paid().items()))
    bad.write_text("".join(f"{k}={v}\n" for k, v in paid(
        LAB_TRAINING_CONNECTOR_URL=f"https://{SECRET}.evil.example/",
        LAB_TRAINING_CONNECTOR=SECRET, DATABASE_URL=SECRET).items()))
    good.chmod(0o600), bad.chmod(0o600)

    def cli(env_file: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "--role", "training", "--env-file",
                               str(env_file), "--approvals", str(approvals)],
                              capture_output=True, text=True)

    ok, refused = cli(good), cli(bad)
    assert (ok.returncode, refused.returncode) == (0, 1), (ok.stdout, refused.stdout)
    assert "FAIL" in refused.stdout and "PASS" in ok.stdout
    for out in (ok, refused):
        assert SECRET not in out.stdout + out.stderr


def test_i6_the_cli_refuses_an_env_file_another_account_can_read(tmp_path) -> None:
    """The env file holds the role's token and database password. Failure oracle: a group- or
    world-readable env file (`install` without `-m 0600`, a `cp` under umask 022) that starts
    anyway."""
    env_file = tmp_path / "training.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in BASE.items()))
    for mode, code in ((0o600, 0), (0o400, 0), (0o640, 1), (0o604, 1)):
        env_file.chmod(mode)
        out = subprocess.run([sys.executable, str(SCRIPT), "--role", "training",
                              "--env-file", str(env_file)], capture_output=True, text=True)
        assert out.returncode == code, (oct(mode), out.stdout)
    assert "FAIL training: the env file is readable by another account (chmod 0600)" in \
        out.stdout
