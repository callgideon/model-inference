"""P-25's second destination form: deliver.py publishes to an SNS topic (instance role)
when ALERT_SNS_TOPIC_ARN is set, instead of the webhook. boto3 is faked here: no network.

Failure oracles:
* an SNS failure that is not kept in UNDELIVERED (and not exit 4) loses the alert;
* both destinations configured is accepted (which one got the page?) - must be BLOCKED;
* neither configured is not BLOCKED; the subject breaks SNS's 100-char, one-line rule;
* the SNS path without boto3 crashes or reads as success instead of BLOCKED;
* the webhook path changes behaviour or prints its URL.
"""
from __future__ import annotations

import io
import json
import os
import runpy
import subprocess
import sys
import types

from . import support

DELIVER = support.REPO / "infra" / "observe" / "deliver.py"
TOPIC = "arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts"
FIRING = [{"alert": "StuckHolds", "severity": "page", "value": 1, "labels": {},
           "summary": "s" * 200, "runbook": "infra/runbooks/observe.md#stuck-holds"}]


class ClientError(Exception):
    pass


class BotoCoreError(Exception):
    pass


def _fake_boto3(monkeypatch, fail=None,
                answer=lambda: {"MessageId": "m", "ResponseMetadata": {"HTTPStatusCode": 200}}):
    published, regions = [], []

    class Client:
        def publish(self, **kw):
            if fail:
                raise fail
            published.append(kw)
            return answer()

    def client(service, region_name):
        assert service == "sns"
        regions.append(region_name)
        return Client()
    boto3 = types.ModuleType("boto3")
    boto3.client = client
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError, exceptions.BotoCoreError = ClientError, BotoCoreError
    botocore.exceptions = exceptions
    for name, module in (("boto3", boto3), ("botocore", botocore),
                         ("botocore.exceptions", exceptions)):
        monkeypatch.setitem(sys.modules, name, module)
    return published, regions


def _run(monkeypatch, tmp_path, *args, topic=TOPIC, url=None, alerts=FIRING):
    posted = []
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout: posted.append(request) or _Ok())
    for name, value in (("ALERT_SNS_TOPIC_ARN", topic), ("ALERT_WEBHOOK_URL", url)):
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(a) + "\n" for a in alerts)))
    code = runpy.run_path(str(DELIVER))["main"](
        ["--state", str(tmp_path / "state.json"),
         "--undelivered", str(tmp_path / "undelivered.jsonl"), *args])
    return code, posted


class _Ok:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ops_alert_sns__publishes_one_subject_and_message_with_the_instance_role(
        monkeypatch, tmp_path, capsys):
    published, regions = _fake_boto3(monkeypatch)
    monkeypatch.setenv("ALERT_OWNER", "o" * 120)                        # a first line > 100
    code, posted = _run(monkeypatch, tmp_path)
    assert code == 0 and posted == [] and regions == ["us-east-1"] and len(published) == 1
    call = published[0]
    assert set(call) == {"TopicArn", "Subject", "Message"} and call["TopicArn"] == TOPIC
    assert len(call["Subject"]) <= 100 and "\n" not in call["Subject"]
    assert call["Subject"].startswith("infrx alerts") and "[FIRING page] StuckHolds" in call["Message"]
    assert "sns=200 topic=infrx-pilot-alerts" in capsys.readouterr().out
    assert "StuckHolds" in json.loads((tmp_path / "state.json").read_text()).popitem()[0]
    assert not (tmp_path / "undelivered.jsonl").exists()


def test_ops_alert_sns__a_failed_publish_is_kept_and_retried(monkeypatch, tmp_path, capsys):
    """F2/F3: any exception (not only botocore's) and an answer without an explicit 2xx
    are failed sends: exit 4, kept, and the exception's text is never printed."""
    secret = support.MARKER + "-in-exception"
    failures = [dict(fail=ClientError("AuthorizationError")), dict(fail=BotoCoreError("creds")),
                dict(fail=RuntimeError(secret)), dict(answer=lambda: {"MessageId": "m"}),
                dict(answer=lambda: None)]
    for failure in failures:
        _fake_boto3(monkeypatch, **failure)
        code, _ = _run(monkeypatch, tmp_path)
        assert code == 4 and not (tmp_path / "state.json").exists(), failure  # retried next run
    out = capsys.readouterr()
    assert "sns=RuntimeError topic=infrx-pilot-alerts" in out.out
    assert secret not in out.out + out.err
    kept = (tmp_path / "undelivered.jsonl").read_text().splitlines()
    assert len(kept) == len(failures) and all("StuckHolds" in line for line in kept)


def test_ops_alert_sns__a_malformed_webhook_url_is_kept_and_never_printed(tmp_path):
    """F1: a URL urllib cannot use (control character, non-numeric port) is a failed send
    (exit 4) and a non-https one is BLOCKED (exit 3) - the alert kept either way, and no part
    of the URL in stdout/stderr (a traceback would carry it into journald)."""
    token = support.MARKER + "TOK"
    for url, expected in ((f"https://hooks.example.invalid/{token} x", 4),
                          (f"https://hooks.example.invalid:{token}/x", 4),
                          (f"http://hooks.example.invalid/{token}", 3)):
        undelivered = tmp_path / "undelivered.jsonl"
        undelivered.unlink(missing_ok=True)
        done = subprocess.run(
            [sys.executable, "-X", "dev", str(DELIVER), "--state", str(tmp_path / "state.json"),
             "--undelivered", str(undelivered)],
            input=json.dumps(FIRING[0]) + "\n", capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "ALERT_WEBHOOK_URL": url})
        assert done.returncode == expected, (url.replace(token, "<t>"), done.returncode)
        assert token not in done.stdout + done.stderr
        assert "hooks.example" not in done.stdout + done.stderr
        assert "StuckHolds" in undelivered.read_text()


def test_ops_alert_sns__exactly_one_destination_or_blocked(monkeypatch, tmp_path, capsys):
    published, _ = _fake_boto3(monkeypatch)
    url = "https://hooks.example.invalid/" + support.MARKER
    code, posted = _run(monkeypatch, tmp_path, url=url)                   # both
    out = capsys.readouterr().out
    assert code == 3 and posted == [] and published == []
    assert "ALERT_WEBHOOK_URL" in out and "ALERT_SNS_TOPIC_ARN" in out and support.MARKER not in out
    code, posted = _run(monkeypatch, tmp_path, topic=None)                # neither
    assert code == 3 and posted == [] and published == []
    assert len((tmp_path / "undelivered.jsonl").read_text().splitlines()) == 2
    code, posted = _run(monkeypatch, tmp_path, topic="not-an-arn")        # malformed
    assert code == 3 and published == []
    code, posted = _run(monkeypatch, tmp_path, topic=None, url=url)       # webhook only
    assert code == 0 and len(posted) == 1 and published == []
    assert support.MARKER not in capsys.readouterr().out


def test_ops_alert_sns__without_boto3_the_sns_path_is_blocked(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "boto3", None)                       # import fails
    code, _ = _run(monkeypatch, tmp_path)
    assert code == 3 and "boto3 is not installed" in capsys.readouterr().out
    assert "StuckHolds" in (tmp_path / "undelivered.jsonl").read_text()


def test_ops_alert_sns__the_test_alert_and_its_recovery_go_to_the_topic(
        monkeypatch, tmp_path, capsys):
    published, _ = _fake_boto3(monkeypatch)
    code, _ = _run(monkeypatch, tmp_path, "--test", alerts=[])
    out = capsys.readouterr().out
    nonce = out.split("nonce=")[1].split()[0]
    assert code == 0 and published[0]["Subject"].startswith("[TEST FIRING]")
    assert nonce in published[0]["Message"] and "NO ACTION REQUIRED" in published[0]["Message"]
    code, _ = _run(monkeypatch, tmp_path, "--test-resolve", nonce, alerts=[])
    assert code == 0 and published[1]["Subject"].startswith("[TEST RESOLVED]")
    assert nonce in published[1]["Subject"] or nonce in published[1]["Message"]
