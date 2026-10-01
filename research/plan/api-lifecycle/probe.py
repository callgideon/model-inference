#!/usr/bin/env python3
"""Bounded deployed API audit; no GPU submissions, service changes or key creation.

Use the API venv. Auth is optional: --token-file is a 0600 GoTrue session token;
--membership-file is a 0600 array from the legacy membership RPC (NOT API proof).
No tokens, private response bodies, account names or provider IDs enter the report.
This does not certify deployment/inference/judging. See verification.md for that gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import stat
import time

import httpx

APP = "https://marlin2b.callbill.ai"
LAB = "https://lab-control.callbill.ai"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--token-file", type=pathlib.Path)
    p.add_argument("--membership-file", type=pathlib.Path)
    p.add_argument("--output", type=pathlib.Path, required=True)
    p.add_argument("--check-registration", action="store_true",
                   help="Try the real register API only after this workspace lists no models")
    a = p.parse_args()
    if bool(a.token_file) != bool(a.membership_file):
        p.error("Supply both session and membership files, or neither")
    for secret_file in (a.token_file, a.membership_file):
        if secret_file and stat.S_IMODE(secret_file.stat().st_mode) & 0o077:
            p.error("Credential/context files must not be group/world accessible")
    token = a.token_file.read_text().strip() if a.token_file else None
    memberships = json.loads(a.membership_file.read_text()) if a.membership_file else []
    if memberships and len(memberships) != 1:
        p.error("This bounded audit requires exactly one explicitly selected membership")
    provider = memberships[0]["provider_org_id"] if memberships else None
    rows: list[dict] = []

    with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
        def call(surface, method, path, *, params=None, body=None, auth=False,
                 label="existing route", safe_public=False):
            origin = APP if surface == "consumer" else LAB
            shown = path.replace(provider, "{provider}") if provider else path
            row = {"surface": surface, "method": method, "path": shown,
                   "authenticated": auth, "kind": label}
            started = time.monotonic()
            data = None
            try:
                r = client.request(method, origin + path, params=params, json=body,
                                   headers={"Authorization": f"Bearer {token}"} if auth else {})
                row["status"] = r.status_code
                try:
                    data = r.json()
                except ValueError:
                    data = None
                if isinstance(data, dict):
                    row["top_level_fields"] = sorted(data)
                    if isinstance(data.get("data"), list):
                        row["record_count"] = len(data["data"])
                    if isinstance(data.get("refusal"), str) and re.fullmatch(r"[a-z_]{1,80}", data["refusal"]):
                        row["error_reason"] = data["refusal"]
                    if isinstance(data.get("error"), dict):
                        err = data["error"]
                        for key in ("reason", "type", "code"):
                            value = err.get(key)
                            if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_:-]{1,80}", value):
                                row["error_" + key] = value
                    if safe_public and r.status_code == 200:
                        row["public_response"] = data
                elif isinstance(data, list):
                    row["record_count"] = len(data)
            except httpx.HTTPError as e:
                row["transport_error"] = type(e).__name__
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            rows.append(row)
            print(surface, method, shown, row.get("status", row.get("transport_error")), flush=True)
            time.sleep(0.15)
            return row, data

        call("consumer", "GET", "/health")
        call("consumer", "GET", "/readyz")
        call("consumer", "GET", "/v1/models", safe_public=True)
        call("consumer", "GET", "/openapi.json", label="documentation intentionally disabled in source")
        call("consumer", "POST", "/v1/chat/completions", body={
            "model": "nemostation/marlin-2b", "messages": [{"role": "user", "content": "Reply OK"}],
            "max_tokens": 1}, label="unauthenticated refusal; no inference expected")
        call("lab", "GET", "/health")
        call("lab", "GET", "/readyz")
        call("lab", "GET", "/openapi.json", label="documentation intentionally disabled in source")
        call("lab", "GET", "/lab/v1/control/models", label="unauthenticated refusal")
        if token and provider:
            q = {"provider_org_id": provider}
            models_row, models = call("lab", "GET", "/lab/v1/control/models", params=q, auth=True)
            for path in ("/lab/v1/control/deployments", "/lab/v1/control/proposals",
                         "/lab/v1/control/aggregates", "/lab/v1/traces",
                         f"/lab/v1/providers/{provider}/datasets/versions",
                         "/lab/v1/evaluations/catalog", "/lab/v1/evaluations/runs",
                         "/lab/v1/evaluations/experiments", "/lab/v1/evaluations/subscriptions",
                         "/lab/v1/pipelines/training-runs", "/lab/v1/pipelines/checkpoints",
                         "/lab/v1/pipelines/teacher-batches", "/lab/v1/releases",
                         "/lab/v1/optimizations"):
                call("lab", "GET", path, params=q, auth=True)
            for surface, path in (("consumer", "/console/v1/me"),
                                  ("consumer", "/console/v1/keys"),
                                  ("consumer", "/console/v1/credits"),
                                  ("lab", "/lab/v1/workspaces"),
                                  ("lab", "/lab/v1/artifacts"),
                                  ("lab", "/lab/v1/judge/runs")):
                call(surface, "GET", path, params=q if surface == "lab" else None, auth=True,
                     label="proposed API namespace; absence corroborated by source, not status alone")
            if a.check_registration:
                if models_row.get("status") != 200 or models != {"data": []}:
                    rows.append({"kind": "registration probe skipped", "reason": "workspace not verified empty"})
                else:
                    repo = pathlib.Path(__file__).resolve().parents[3]
                    pin = json.loads((repo / "models/marlin2b/serving-version.json").read_text())
                    call("lab", "POST", "/lab/v1/control/register", params=q, auth=True, body={
                        "name": "marlin2b", "artifact_digest": pin["model"]["weight_shard_digests"][0],
                        "runtime": pin["runtime_image"]["ref"], "schema_version": "chat.v1"},
                         label="new workspace onboarding attempt with actual Marlin pins")
                    call("lab", "GET", "/lab/v1/control/models", params=q, auth=True,
                         label="registration postcondition")
                    call("lab", "GET", "/lab/v1/control/deployments", params=q, auth=True,
                         label="registration postcondition")

    report = {"observed_at": dt.datetime.now(dt.UTC).isoformat(),
              "origins": {"consumer": APP, "lab": LAB},
              "scope": "API read/refusal audit; not a successful full lifecycle test",
              "session_bootstrap": "existing GoTrue identity; legacy membership RPC used outside FastAPI" if token else "none",
              "inference_submissions": 0, "deployments_launched": 0, "judge_calls": 0,
              "full_lifecycle": "BLOCKED", "requests": rows}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0  # audit collected; explicitly NOT a lifecycle acceptance exit code


if __name__ == "__main__":
    raise SystemExit(main())
