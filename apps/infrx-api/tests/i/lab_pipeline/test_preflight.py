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
        "LAB_TRAINING_CONNECTOR: trainer-x has no P-11 approval",
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
        "LAB_ANNOTATION_TEACHER: trainer-x has no P-10 approval"]
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
])
def test_i6_every_setting_is_named_for_its_role_and_purpose(role, name) -> None:
    """Failure oracle: an env file that smuggles a consumer secret, another purpose's secret,
    cloud credentials (capacity purchases) or a proxy override (egress bypass) past the
    preflight because the name was not on the role's own list, or an image setting that is a
    docker flag (`${INFRX_IMAGE}` is one argv word of the unit, before the command)."""
    assert check(role, {**BASE, name: "x"}) == [f"{name}: not a {role} setting"]
    assert check(role, {**BASE, "INFRX_IMAGE": "--privileged"}) == [
        "INFRX_IMAGE: an image reference, not a docker flag"]


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
        DATABASE_URL=SECRET).items()))

    def cli(env_file: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "--role", "training", "--env-file",
                               str(env_file), "--approvals", str(approvals)],
                              capture_output=True, text=True)

    ok, refused = cli(good), cli(bad)
    assert (ok.returncode, refused.returncode) == (0, 1), (ok.stdout, refused.stdout)
    assert "FAIL" in refused.stdout and "PASS" in ok.stdout
    for out in (ok, refused):
        assert SECRET not in out.stdout + out.stderr
