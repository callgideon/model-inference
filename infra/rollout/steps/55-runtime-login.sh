#!/usr/bin/env bash
# RV-09 / D10 wiring 6 / R127: put the installed runtime on 0021's dedicated logins. Run after
# W7 (0021 created infrx_runtime and infrx_monitor NOLOGIN) and after every 50-install (W10b):
# install.sh rewrites the env file from SSM, where DATABASE_URL is pg_journal_url, the owner login.
#  1. reads, by NAME, the two passwords (RUNTIME_PASSWORD_PARAM, MONITOR_PASSWORD_PARAM) and the
#     owner DSN (OWNER_DSN_PARAM: preflight's pg_journal_url, the migration login on the session
#     pooler) into a 0600 file - never an argument, never printed;
#  2. in the installed runtime image (no psql on the box), through the owner DSN: ALTER ROLE ...
#     WITH LOGIN PASSWORD for both (a SCRAM verifier computed client-side, so the plain text is
#     not in the server's statement log), then logs in as each on the transaction pooler (:6543,
#     `<role>.<project-ref>` as Supavisor names it) and checks current_user;
#  3. stages the env file with DATABASE_URL = infrx_runtime and MONITOR_DATABASE_URL =
#     infrx_monitor (the worker's reconciliation gauges, WR-W5F5-2), runs envcheck on the staged
#     bytes, then replaces the file by one rename (0600, owner kept, the old one saved 0600 under
#     BACKUPS), restarts gateway and worker and waits for both /readyz;
#  4. writes MONITOR_DATABASE_URL alone into /etc/infrx-observe.env (0600, by rename): observe.sh
#     reads the monitor DSN only there, else the env file's DATABASE_URL - now infrx_runtime,
#     which 0021 grants none of durable.py's tables. 50-install never touches that file.
# Idempotent: the same passwords give the same file, and an unchanged file restarts nothing.
# Exit 2 = refused before any change; 1 = the SQL or a login check failed (the env file untouched;
# a password already set stays set, harmless: nothing logs in with it); 3 = the staged file
# failed envcheck (nothing replaced);
# 4 = not ready on the logins: the previous file is put back and the units restarted on it.
# Step 4 runs only on success (and on an `unchanged` rerun); exits 1-4 leave that file as it was.
# Rollback: that same put-back (the saved file), or re-run 50-install (pg_journal_url again).
set -euo pipefail
RUNTIME_PASSWORD_PARAM=${RUNTIME_PASSWORD_PARAM:-/model-inference/infrx_runtime_password}
MONITOR_PASSWORD_PARAM=${MONITOR_PASSWORD_PARAM:-/model-inference/infrx_monitor_password}
OWNER_DSN_PARAM=${OWNER_DSN_PARAM:-/model-inference/pg_journal_url}
env_file=${ENV_FILE:-/etc/marlin2b-gateway.env}
backups=${BACKUPS:-/var/backups/infrx}
observe_env=${OBSERVE_ENV:-/etc/infrx-observe.env}
image=$(sed -n 's/^INFRX_IMAGE=//p' "$env_file")
mode=$(sed -n 's/^INFRX_MODE=//p' "$env_file")
[[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "no INFRX_IMAGE in $env_file: run 50-install first" >&2; exit 2; }
work=$(umask 077; mktemp -d)
trap 'rm -rf "$work"' EXIT
for spec in OWNER_DSN="$OWNER_DSN_PARAM" RUNTIME_PASSWORD="$RUNTIME_PASSWORD_PARAM" \
            MONITOR_PASSWORD="$MONITOR_PASSWORD_PARAM"; do
  value=$(aws ssm get-parameter --region us-east-1 --with-decryption --name "${spec#*=}" \
            --query Parameter.Value --output text)
  printf '%s=%s\n' "${spec%%=*}" "$value" >> "$work/secrets.env"
done
unset value
docker run --rm -i --network host --env-file "$work/secrets.env" "$image" python - \
  > "$work/logins.env" <<'PY'
import os
import urllib.parse

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

owner = os.environ["OWNER_DSN"]
where = conninfo_to_dict(owner)
user = where.get("user") or ""
suffix = "." + user.split(".", 1)[1] if "." in user else ""     # Supavisor: <role>.<project-ref>
lines = []
with psycopg.connect(owner, autocommit=True) as conn:
    for env, role, secret in (("DATABASE_URL", "infrx_runtime", "RUNTIME_PASSWORD"),
                              ("MONITOR_DATABASE_URL", "infrx_monitor", "MONITOR_PASSWORD")):
        password = os.environ[secret]
        verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(),
                                                b"scram-sha-256").decode()
        conn.execute(sql.SQL("alter role {} with login password {}").format(
            sql.Identifier(role), sql.Literal(verifier)))
        quote = lambda part: urllib.parse.quote(part, safe="")      # noqa: E731
        dsn = (f"postgresql://{quote(role + suffix)}:{quote(password)}@{where['host']}:6543/"
               f"{where.get('dbname') or 'postgres'}?sslmode={where.get('sslmode') or 'require'}")
        with psycopg.connect(dsn, autocommit=True) as login:
            (current,) = login.execute("select current_user").fetchone()
        if current != role:
            raise SystemExit(f"{env}: logged in as {current}, not {role}")
        lines.append(f"{env}={dsn}\n")
print("".join(lines), end="")
PY
[ "$(cut -d= -f1 "$work/logins.env" | tr '\n' ' ')" = "DATABASE_URL MONITOR_DATABASE_URL " ] \
  || { echo "the login check did not return both DSNs" >&2; exit 3; }
monitor_for_observe() {
  local tmp; tmp=$(umask 077; mktemp "$observe_env.XXXXXX")
  grep '^MONITOR_DATABASE_URL=' "$work/logins.env" > "$tmp"; chmod 0600 "$tmp"; mv -f "$tmp" "$observe_env"
}
staged=$(umask 077; mktemp "$env_file.XXXXXX")
grep -v -e '^DATABASE_URL=' -e '^MONITOR_DATABASE_URL=' "$env_file" > "$staged"
cat "$work/logins.env" >> "$staged"
chown --reference="$env_file" "$staged"; chmod 0600 "$staged"
if cmp -s "$staged" "$env_file"; then
  rm -f "$staged"; monitor_for_observe
  echo "unchanged: $env_file already names infrx_runtime and infrx_monitor"; exit 0
fi
docker run --rm -i --network none "$image" python /app/deploy/preflight.py envcheck \
  --mode "$mode" --env-file /dev/stdin < "$staged" \
  || { rm -f "$staged"; echo "envcheck refused the staged env file; nothing replaced" >&2; exit 3; }
install -d -m 0700 "$backups"
saved=$backups/runtime-login-$(date -u +%Y%m%dT%H%M%SZ).env
cp -p "$env_file" "$saved"
mv -f "$staged" "$env_file"
systemctl restart marlin2b-gateway infrx-worker
for port in 8001 8002; do
  ready=0
  for _ in $(seq "${READY_S:-180}"); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$port/readyz"; then ready=1; break; fi
    sleep 1
  done
  if [ "$ready" = 0 ]; then
    cp -p "$saved" "$env_file"; systemctl restart marlin2b-gateway infrx-worker
    echo "not ready on :$port with the dedicated logins; the previous env file is back ($saved)" >&2
    exit 4
  fi
done
monitor_for_observe
echo "runtime on infrx_runtime, gauges on infrx_monitor (pooler :6543); previous env file $saved"
