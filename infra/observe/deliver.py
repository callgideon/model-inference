#!/usr/bin/env python3
"""Deliver the evaluator's firing alerts to the operator's destination (I8 slices 3-4).

    python -m infrx.observe.alerts ... | deliver.py --state /var/lib/infrx/metrics/delivered.json
    deliver.py --test                  # slice 4: one clearly marked TEST message, then
    deliver.py --test-resolve <nonce>  #          its recovery

The destination is ALERT_WEBHOOK_URL (EnvironmentFile /etc/infrx-alert.env, root 0600): an
HTTPS webhook that accepts a JSON body with a `text` field (Slack-compatible). It is P-25's
input - the owner, the destination and its authorization - and is never printed (a webhook
URL is a credential). Without it nothing is sent: the message goes to UNDELIVERED (bounded)
and the exit is 3, "BLOCKED", so the timer's unit fails visibly instead of silently.

Only changes are sent: a newly firing alert, a resolved one, and every page still firing
after --repeat-s. A failed send leaves the state untouched, so the next run retries. A
message names alerts, severities, closed-vocabulary labels, values and runbooks - never a
prompt, key, DSN, signed URL or identifier (the rules and exporters carry none).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid

BLOCKED, SEND_FAILED = 3, 4
UNDELIVERED_KEEP = 200
RUNBOOK = "infra/runbooks/observe.md"


def key(alert: dict) -> str:
    return alert["alert"] + json.dumps(alert.get("labels", {}), sort_keys=True)


def plan(firing: list[dict], state: dict, now: float, repeat_s: float) -> tuple[list, list]:
    """(lines to send, keys that go out as firing). Pure."""
    current = {key(a): a for a in firing}
    lines, sent = [], []
    for k, alert in sorted(current.items()):
        last = state.get(k)
        repeat = alert["severity"] == "page" and last is not None and now - last >= repeat_s
        if last is None or repeat:
            labels = ",".join(f"{n}={v}" for n, v in sorted(alert.get("labels", {}).items()))
            lines.append(f"[{'FIRING' if last is None else 'STILL FIRING'} {alert['severity']}] "
                         f"{alert['alert']}{{{labels}}} value={alert.get('value')} - "
                         f"{alert.get('summary', '')} runbook: {alert.get('runbook', '')}")
            sent.append(k)
    for k in sorted(set(state) - set(current)):
        lines.append(f"[RESOLVED] {k}")
    return lines, sent


def post(text: str, extra: dict | None = None) -> int:
    """HTTP status of the POST, or 0 when there is no destination."""
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return 0
    if not url.startswith("https://"):
        raise SystemExit("ALERT_WEBHOOK_URL must be https (the value is not printed)")
    body = json.dumps({"text": text, **(extra or {})}).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:   # noqa: S310
            return answer.status
    except urllib.error.HTTPError as refused:
        return refused.code
    except (urllib.error.URLError, OSError):
        return -1


def keep_undelivered(path: str, text: str) -> None:
    lines = []
    if os.path.exists(path):
        with open(path) as handle:
            lines = handle.read().splitlines()[-(UNDELIVERED_KEEP - 1):]
    lines.append(json.dumps({"at": time.time(), "text": text}))
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default="/var/lib/infrx/metrics/delivered.json")
    ap.add_argument("--undelivered", default="/var/lib/infrx/metrics/undelivered.jsonl")
    ap.add_argument("--repeat-s", type=float, default=3600)
    ap.add_argument("--rules-version", default="?")
    ap.add_argument("--test", action="store_true", help="send one marked TEST alert")
    ap.add_argument("--test-resolve", metavar="NONCE", help="send the TEST alert's recovery")
    a = ap.parse_args(argv)
    host = socket.gethostname()
    owner = os.environ.get("ALERT_OWNER", "UNSET (P-25)")
    escalation = os.environ.get("ALERT_ESCALATION", "UNSET (P-25)")
    if a.test or a.test_resolve:
        nonce = a.test_resolve or uuid.uuid4().hex[:12]
        word = "RESOLVED" if a.test_resolve else "FIRING"
        text = (f"[TEST {word}] infrx alert delivery test {nonce} from {host} - NO ACTION "
                f"REQUIRED. Owner: {owner}. Escalation: {escalation}. Runbook: "
                f"{RUNBOOK}#delivery-test")
        status = post(text, {"test": True, "nonce": nonce})
        print(f"test {word.lower()} nonce={nonce} http={status or 'BLOCKED: no ALERT_WEBHOOK_URL (P-25)'}")
        return 0 if 200 <= status < 300 else BLOCKED if status == 0 else SEND_FAILED
    firing = [json.loads(line) for line in sys.stdin if line.strip()]
    state = {}
    if os.path.exists(a.state):
        with open(a.state) as handle:
            state = json.load(handle)
    now = time.time()
    lines, sent = plan(firing, state, now, a.repeat_s)
    if not lines:
        return 0
    text = (f"infrx alerts (rules v{a.rules_version}, {host}); owner {owner}, escalation "
            f"{escalation}\n" + "\n".join(lines))
    status = post(text)
    print(f"delivery: {len(lines)} line(s), http={status or 'BLOCKED: no ALERT_WEBHOOK_URL (P-25)'}")
    if not 200 <= status < 300:
        keep_undelivered(a.undelivered, text)
        return BLOCKED if status == 0 else SEND_FAILED
    current = {key(alert) for alert in firing}
    new_state = {k: v for k, v in state.items() if k in current}
    new_state.update({k: now for k in sent})
    temporary = f"{a.state}.{os.getpid()}.tmp"
    with open(temporary, "w") as handle:
        json.dump(new_state, handle)
    os.replace(temporary, a.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
