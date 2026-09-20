#!/usr/bin/env python3
"""Re-post usage_events rows the gateway could not deliver.

    sudo systemctl show -p Environment marlin2b-gateway   # where the vars come from
    set -a; . /etc/marlin2b-gateway.env; set +a
    /opt/pytorch/bin/python apps/infrx-api/deploy/replay_usage.py [path]

Idempotent: the row id is the Inference-Id, so a 409 from Postgres means the
row is already there and counts as done. The file is moved aside first, so a
running gateway can keep appending; rows that still fail are appended back for
the next run, and the file ends up empty when everything landed.
"""
import json, os, sys

import httpx

PATH = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "USAGE_FAILED_LOG", "/opt/dlami/nvme/logs/usage_failed.jsonl")
URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def main():
    if not os.path.exists(PATH) or os.path.getsize(PATH) == 0:
        print(f"{PATH}: nothing to replay")
        return 0
    os.replace(PATH, PATH + ".replaying")  # the gateway may keep appending to PATH
    rows = [json.loads(l) for l in open(PATH + ".replaying") if l.strip()]
    left, done = [], 0
    with httpx.Client(base_url=f"{URL}/rest/v1", timeout=30,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                               "Content-Type": "application/json", "Prefer": "return=minimal"}) as c:
        for row in rows:
            try:
                r = c.post("/usage_events", json=row)
                if r.status_code < 300 or r.status_code == 409:
                    done += 1
                    continue
                print(f"{row.get('id')}: {r.status_code} {r.text[:200]}")
            except Exception as e:
                print(f"{row.get('id')}: {type(e).__name__}: {e}")
            left.append(row)
    if left:
        with open(PATH, "a") as f:
            for row in left:
                f.write(json.dumps(row) + "\n")
    os.unlink(PATH + ".replaying")
    open(PATH, "a").close()
    print(f"{PATH}: {done} inserted, {len(left)} left")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
