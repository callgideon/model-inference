#!/usr/bin/env bash
# lab-release.sh — the maintained Lab release tool (was launch-v1.sh, the v1 INTERNAL-TESTING launch).
#
# The actions the coordinator's sandbox refuses (Production Deploy): the box's Lab units (runbook
# 08 §4), the Lab web on Vercel (§6), the tester memberships (§7) and main's fast-forward. Every
# step is idempotent and re-runnable; every box write goes through ssm.sh, whose steps refuse on
# their own preconditions. Secrets are read from SSM by NAME (or a pasted Vercel token, read with
# `read -rs` from the terminal) — never on a command line, never printed.
#
# Usage (from the repo root, on the coordinator host, with the default AWS profile):
#   infra/lab/rollout/lab-release.sh preflight   # read-only: RELEASE on claude/consumer-v1, SSM names, App health
#   infra/lab/rollout/lab-release.sh box         # L0 checkout, L1, L3-L6s on the pilot box
#   infra/lab/rollout/lab-release.sh web         # the Lab web project infrx-lab + domain (Vercel)
#   infra/lab/rollout/lab-release.sh members     # provider org infrx-internal + tester memberships
#   infra/lab/rollout/lab-release.sh main        # fast-forward main to RELEASE (the App rebuilds)
#
# Hosted migration windows are NOT this tool's (the 2026-09-30 windows 0052-0056 and 0057-0059
# ran from launch-v1.sh's window(), kept in git history at 08983639; their patches are
# research/plan/evidence/i/hosted-migrate-*.patch): the next window is a KNOWN-GOOD re-proof plus
# a reviewed edit of hosted-migrate.sh, then lab-migrate.sh — runbook 08 §2 "Next window".
#
# Inputs (exported):
#   RELEASE        the 40-hex commit to release (default: the tip of claude/consumer-v1)
#   SUPABASE_URL   https://<project-ref>.supabase.co (the App's project; box, web)
#   VERCEL_SCOPE   the Vercel team of infrx-lab (default callgideon, the App's team; web)
#   TESTER_EMAILS  space-separated tester emails, each already signed up in the App (members)
#   OPERATOR_NAME  the audit column's operator (default: git config user.name; members)
#   OPS_DSN_PARAM  the SSM NAME of the owner DSN (default /model-inference/pg_journal_url; members)
#   WINDOW         the P-08 reference for the audit column (default launch-v1; members)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
aws() { command env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '\n== %s\n' "$*"; }
need() { [ -n "${!1:-}" ] || { printf 'set %s\n' "$1" >&2; exit 2; }; }
RELEASE=${RELEASE:-$(git rev-parse claude/consumer-v1)}
[[ $RELEASE =~ ^[0-9a-f]{40}$ ]] || { echo "RELEASE must be a 40-hex sha" >&2; exit 2; }
SSM_CONTROL_DSN=/model-inference/lab/control_database_url
SSM_ANON=/model-inference/lab/supabase_anon_key
SSM_VERCEL=/callgideon/prod/VERCEL_TOKEN

preflight() {
  say "preflight at $RELEASE"
  git merge-base --is-ancestor "$RELEASE" claude/consumer-v1 || { echo "RELEASE is not on claude/consumer-v1" >&2; exit 2; }
  [ -x apps/infrx-api/.venv/bin/python ] || make api-env
  say "SSM names present (values never shown)"
  aws ssm describe-parameters --parameter-filters "Key=Name,Values=$SSM_CONTROL_DSN,$SSM_ANON,/model-inference/infrx_lab_control_password" --query 'Parameters[].[Name,Type,Version]' --output text
  say "App health"; curl -s -o /dev/null -w 'https://marlin2b.callbill.ai/health %{http_code}\n' https://marlin2b.callbill.ai/health || true
}

box() {
  need SUPABASE_URL
  say "L0 the box's checkout becomes $RELEASE (fetch; refuses if the engine script differs)"
  infra/rollout/ssm.sh infra/lab/rollout/lab-checkout.sh RELEASE="$RELEASE"
  say "L1 inventory"; infra/rollout/ssm.sh infra/lab/rollout/steps/10-lab-preflight.sh RELEASE="$RELEASE"
  say "L3 Lab image (up to 1 h)"; TIMEOUT_S=3600 infra/rollout/ssm.sh infra/lab/rollout/steps/20-lab-image.sh RELEASE="$RELEASE"
  say "L4 units (inert)"; infra/rollout/ssm.sh infra/lab/rollout/steps/30-lab-units.sh RELEASE="$RELEASE"
  say "L5 control service ON (DSN and anon key by SSM name)"
  infra/rollout/ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=on RELEASE="$RELEASE" \
    CONTROL_DSN_PARAM="$SSM_CONTROL_DSN" ANON_KEY_PARAM="$SSM_ANON" SUPABASE_URL="$SUPABASE_URL" LAB_ORIGIN=https://lab.callbill.ai
  say "L5s smoke"; infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh
  say "L6 the control origin on the edge (DNS lab-control.callbill.ai → the box is already set)"
  infra/rollout/ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=on RELEASE="$RELEASE"
  curl -s -o /dev/null -w 'https://lab-control.callbill.ai/lab/v1/releases %{http_code} (401 = the control service answers through the edge; /readyz is loopback-only)\n' https://lab-control.callbill.ai/lab/v1/releases || true
  say "L6s smoke + the App's external checks"; infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh; infra/rollout/verify-external.sh || true
  echo "L7 (eval/judge/datasets roles) is NOT run: no role login exists yet (WR-LDP-7); the control unit serves every Lab family (R237, 0056)."
}

vercel_lab() {
  command -v vercel >/dev/null || vercel() { npx --yes vercel@latest "$@"; }   # no global install (EACCES on this host)
  say "the Vercel credential: paste a new token for SSM $SSM_VERCEL (proven by vercel whoami before it is stored), or Enter = the stored token or the CLI login"
  T=""; { read -rs -p "token (hidden): " T </dev/tty 2>/dev/tty; } 2>/dev/null || true; echo   # the prompt on the terminal; no terminal (the `!` runner): the stored token
  if [ -n "$T" ]; then
    t=$(umask 077; mktemp); trap 'shred -u "$t" 2>/dev/null || rm -f "$t"' EXIT
    printf '%s' "$T" > "$t"; T=""
    VERCEL_TOKEN=$(cat "$t") vercel whoami >/dev/null 2>&1 || { echo "the pasted token is not valid (vercel whoami): the stored one is untouched" >&2; exit 2; }
    aws ssm put-parameter --name "$SSM_VERCEL" --type SecureString --overwrite --value "file://$t" >/dev/null
    shred -u "$t" 2>/dev/null || rm -f "$t"; trap - EXIT
  fi
  export VERCEL_TOKEN; VERCEL_TOKEN=$(aws ssm get-parameter --name "$SSM_VERCEL" --with-decryption --query Parameter.Value --output text)
  if ! vercel whoami >/dev/null 2>&1; then          # the stored token is invalid: the CLI's own login (npx vercel login, device flow)
    unset VERCEL_TOKEN
    vercel whoami >/dev/null 2>&1 || { echo "no valid Vercel credential: run 'npx vercel@latest login' once on this host (device flow), or store a token at $SSM_VERCEL" >&2; exit 2; }
    say "using the CLI's own login (the SSM token is invalid)"
  fi
  ANON=$(aws ssm get-parameter --name "$SSM_ANON" --with-decryption --query Parameter.Value --output text)
  need SUPABASE_URL
  SCOPE=${VERCEL_SCOPE:-callgideon}   # the App's team (infrx-app lives there), never the login's default team
  say "project infrx-lab in team $SCOPE (Root Directory apps/lab, set in the dashboard; linked and deployed from the REPO ROOT so packages/shared, a link: dependency, is uploaded)"
  vercel link --yes --project infrx-lab --scope "$SCOPE" >/dev/null 2>&1 \
    || { vercel project add infrx-lab --scope "$SCOPE" && vercel link --yes --project infrx-lab --scope "$SCOPE" >/dev/null; }
  for kv in "NEXT_PUBLIC_LAB_URL=https://lab.callbill.ai" "NEXT_PUBLIC_SUPABASE_URL=$SUPABASE_URL" "NEXT_PUBLIC_SUPABASE_ANON_KEY=$ANON" \
      "LAB_CONTROL_URL=https://lab-control.callbill.ai" "LAB_TRACES_API_URL=https://lab-control.callbill.ai" "LAB_EVALS_API_URL=https://lab-control.callbill.ai" \
      "LAB_PIPELINES_API_URL=https://lab-control.callbill.ai" "LAB_RELEASES_API_URL=https://lab-control.callbill.ai" "LAB_DATASETS_API_URL=https://lab-control.callbill.ai"; do
    k=${kv%%=*}; v=${kv#*=}; vercel env rm "$k" production --yes >/dev/null 2>&1 || true; printf '%s' "$v" | vercel env add "$k" production >/dev/null; done
  vercel --prod --yes | tail -3
  vercel domains add lab.callbill.ai >/dev/null 2>&1 || true
  unset VERCEL_TOKEN ANON
  curl -s -o /dev/null -w 'https://lab.callbill.ai/ %{http_code}\n' https://lab.callbill.ai/ || true
  echo "Supabase Auth redirect URLs already include https://lab.callbill.ai/auth/callback** (set 2026-09-29)."
}

members() {
  need TESTER_EMAILS
  OP=${OPERATOR_NAME:-$(git config user.name || true)}; need OP
  OPS_DSN_PARAM=${OPS_DSN_PARAM:-/model-inference/pg_journal_url}
  say "provider org infrx-internal + memberships (the owner DSN from SSM $OPS_DSN_PARAM by name, never on argv or printed)"
  OPERATIONS_DATABASE_URL=$(aws ssm get-parameter --with-decryption --name "$OPS_DSN_PARAM" --query Parameter.Value --output text)
  [ -n "$OPERATIONS_DATABASE_URL" ] || { echo "SSM read failed (name: $OPS_DSN_PARAM)" >&2; exit 3; }
  export OPERATIONS_DATABASE_URL
  # shellcheck disable=SC2086   # one argument per email
  apps/infrx-api/.venv/bin/python - "operator:$OP P-08 ${WINDOW:-launch-v1}" $TESTER_EMAILS <<'PY'
import os, sys, psycopg
by, emails = sys.argv[1], sys.argv[2:]
with psycopg.connect(os.environ["OPERATIONS_DATABASE_URL"]) as c:   # one transaction; every value bound
    c.execute("insert into infrx.provider_orgs (slug, display_name, created_by) "
              "values ('infrx-internal', 'infrx internal testing', %s) on conflict (slug) do nothing", [by])
    for email in emails:
        c.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
                  "select p.provider_org_id, u.id, 'developer', %s from infrx.provider_orgs p, auth.users u "
                  "where p.slug = 'infrx-internal' and u.email = %s on conflict do nothing", [by, email])
    for row in c.execute("select u.email, m.role, m.granted_at from infrx.provider_memberships m "
                         "join infrx.provider_orgs p using (provider_org_id) join auth.users u on u.id = m.user_id "
                         "where p.slug = 'infrx-internal' and m.revoked_at is null order by u.email"):
        print(*row, sep="\t")
PY
  unset OPERATIONS_DATABASE_URL
}

main_ff() {
  say "fast-forward main to $RELEASE (the App's Vercel project builds main; every Lab switch is OFF there)"
  git fetch -q origin main; git merge-base --is-ancestor origin/main "$RELEASE" || { echo "main is not an ancestor of RELEASE" >&2; exit 1; }
  git push origin "$RELEASE:main"
}

case "${1:-}" in
  preflight) preflight ;; box) box ;; web) vercel_lab ;; members) members ;; main) main_ff ;;
  *) sed -n 2,29p "$0"; exit 2 ;;
esac
