#!/usr/bin/env python3
"""What the operator side can read about hosted recovery and pooler limits (I8 slice 5).
READ-ONLY; coordinator host. Two sources, each reported for what it is:

1. The Supabase Management API (needs SUPABASE_ACCESS_TOKEN - a personal access token of
   an account with the project; never printed):
     GET /v1/projects/{ref}/database/backups       -> pitr_enabled, walg_enabled, backups
     GET /v1/projects/{ref}/config/database/pooler -> pool_mode, default_pool_size,
                                                      max_client_conn (pool_budget.py's limits)
   The pooler answer's connection string is never read out.
2. The coordinator's own logical dumps (`infra/runbooks/pgrestore.py dump`, restore.md A3):
   the newest ~/infrx-backups/hosted-* directory, its age and whether SHA256SUMS is there.

    read -rs SUPABASE_ACCESS_TOKEN; export SUPABASE_ACCESS_TOKEN
    apps/infrx-api/.venv/bin/python infra/runbooks/supabase_policy.py [--ref fcbnscgsymzdykendbrc]

Prints one JSON document: the facts, and an RPO statement derived ONLY from them (PITR on:
minutes, est.; daily physical backups: <= 24 h, est.; neither: the age of the newest
logical dump, i.e. unbounded between manual dumps). Exit 0 when the API answered, 3
(BLOCKED) without a token - the local part is still reported - and 1 when the API refused.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.supabase.com"
REF = "fcbnscgsymzdykendbrc"
BLOCKED = 3


def get(api: str, path: str, token: str):
    request = urllib.request.Request(f"{api}{path}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as answer:     # noqa: S310
        return json.loads(answer.read())


def summarize_backups(doc: dict, now: float) -> dict:
    done = [b for b in doc.get("backups", []) if b.get("status") == "COMPLETED"]
    newest = max((b["inserted_at"] for b in done), default=None)
    age_h = None
    if newest:
        stamp = calendar.timegm(time.strptime(newest[:19], "%Y-%m-%dT%H:%M:%S"))
        age_h = round((now - stamp) / 3600, 1)
    return {"pitr_enabled": doc.get("pitr_enabled"), "walg_enabled": doc.get("walg_enabled"),
            "completed_backups": len(done), "physical": sum(1 for b in done if b.get("is_physical_backup")),
            "newest_completed_backup_age_h": age_h,
            "physical_backup_range": doc.get("physical_backup_data") or None}


def summarize_pooler(entries) -> list[dict]:
    return [{k: e.get(k) for k in ("database_type", "pool_mode", "db_port", "default_pool_size",
                                   "max_client_conn")} for e in (entries or [])]


def local_dumps(root: Path, now: float) -> dict:
    dumps = sorted(root.glob("hosted-*")) if root.is_dir() else []
    if not dumps:
        return {"newest": None}
    newest = dumps[-1]
    return {"newest": newest.name, "age_h": round((now - newest.stat().st_mtime) / 3600, 1),
            "sha256sums": (newest / "SHA256SUMS").is_file(), "count": len(dumps)}


def rpo(backups: dict | None, local: dict) -> str:
    if backups and backups.get("pitr_enabled"):
        return "PITR enabled: RPO minutes (est.; WAL archive interval) - TO BE MEASURED by a restore drill"
    if backups and backups.get("completed_backups"):
        return "daily backups, no PITR: RPO <= 24 h (est.) - TO BE MEASURED by a restore drill"
    if local.get("newest"):
        return (f"no hosted backup seen: RPO is the age of the newest manual dump "
                f"({local['age_h']} h) and unbounded between dumps")
    return "no backup of any kind seen: RPO unbounded"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default=REF)
    ap.add_argument("--api", default=API)
    ap.add_argument("--dumps", default=str(Path.home() / "infrx-backups"))
    a = ap.parse_args(argv)
    now = time.time()
    report = {"project": a.ref, "local_dumps": local_dumps(Path(a.dumps), now)}
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    code = 0
    backups = None
    if not token:
        report["api"] = "BLOCKED: SUPABASE_ACCESS_TOKEN not set"
        code = BLOCKED
    else:
        try:
            backups = summarize_backups(get(a.api, f"/v1/projects/{a.ref}/database/backups", token), now)
            report["backups"] = backups
            report["pooler"] = summarize_pooler(get(a.api, f"/v1/projects/{a.ref}/config/database/pooler", token))
        except urllib.error.HTTPError as refused:
            report["api"] = f"refused: HTTP {refused.code}"
            code = 1
        except (urllib.error.URLError, OSError) as failed:
            report["api"] = f"unreachable: {type(failed).__name__}"
            code = 1
    report["rpo"] = rpo(backups, report["local_dumps"])
    print(json.dumps(report, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
