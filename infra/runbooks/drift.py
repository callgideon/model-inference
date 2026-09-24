#!/usr/bin/env python3
"""reconcile.md#drift from the coordinator host, READ-ONLY, aggregates only.

    apps/infrx-api/.venv/bin/python infra/runbooks/drift.py            # the hosted pilot database
    apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --hours 6  # the job window to count

The password is read from SSM (`/INFRX-SUPABASE-PROD/db_password`) into this process only and
is never printed; the one transaction is `SET TRANSACTION READ ONLY`, so no statement here can write. Output is counts: wallet drift rows, credit-wallet drift rows,
non-terminal jobs by state, jobs by state in the window. No row content, no identifiers.
This is the one hosted read the coordinator runs after a drill or a rollout (runbooks:
reconcile.md, rollback.md step 7, rollout.md W12); it exists so a permission rule can name it.
"""
import argparse
import subprocess
import sys

# Port 6543 = the pooler's TRANSACTION mode: the pilot runtime's pools hold every one of the 15
# session-mode slots on 5432 (EMAXCONNSESSION), so operator reads use transaction mode.
HOSTED = ("host=aws-0-us-east-2.pooler.supabase.com port=6543 "
          "user=postgres.fcbnscgsymzdykendbrc dbname=postgres sslmode=require")
PASSWORD_PARAMETER = "/INFRX-SUPABASE-PROD/db_password"

QUERIES = (
    ("wallet drift rows",
     "select count(*) from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0"),
    ("credit-wallet drift rows",
     "select count(*) from infrx.credit_wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0"),
    ("non-terminal jobs by state",
     "select state, count(*) from infrx.jobs where state not in ('succeeded','failed','cancelled','expired') group by 1 order by 1"),
    ("jobs by state in the window",
     "select state, count(*) from infrx.jobs where created_at > now() - (%s || ' hours')::interval group by 1 order by 1"),
)


def password() -> str:
    out = subprocess.run(["env", "-u", "AWS_ACCESS_KEY_ID", "-u", "AWS_SECRET_ACCESS_KEY", "-u", "AWS_SESSION_TOKEN",
                          "aws", "--region", "us-east-1", "ssm", "get-parameter", "--name", PASSWORD_PARAMETER,
                          "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--conninfo", default=HOSTED, help="libpq conninfo without a password (default: the hosted pilot)")
    ap.add_argument("--hours", type=int, default=3, help="the job window to count (default 3)")
    a = ap.parse_args()
    import psycopg  # the pinned environment's driver
    conn = psycopg.connect(a.conninfo, password=password())
    try:
        with conn, conn.cursor() as c:
            c.execute("set transaction read only")   # this transaction only: valid in transaction mode
            for label, sql in QUERIES:
                c.execute(sql, (str(a.hours),) if "%s" in sql else None)
                print(f"{label:32} {c.fetchall()}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
