#!/usr/bin/env bash
# launch-v1.sh — the operator's script for the v1 INTERNAL-TESTING launch of the Lab.
#
# Runs the three actions the coordinator's sandbox refuses (Production Deploy): the hosted
# migration window (runbook 08 §2), the box's Lab units (§4) and the Lab web on Vercel (§6),
# plus the tester memberships (§7). Every step is idempotent and re-runnable; every hosted or
# box write goes through the existing gates (lab-migrate.sh, ssm.sh) which refuse on their own
# preconditions. Secrets are read from SSM by name on the box, or typed with `read -rs` here —
# never on a command line, never printed.
#
# Usage (from the repo root, on the coordinator host, with the default AWS profile):
#   infra/lab/rollout/launch-v1.sh preflight      # everything read-only
#   infra/lab/rollout/launch-v1.sh window          # R151 window: the reviewed patch + w6b + w7
#   infra/lab/rollout/launch-v1.sh box             # L1, L3–L6s on the pilot box
#   infra/lab/rollout/launch-v1.sh vercel          # the Lab web project + domain
#   infra/lab/rollout/launch-v1.sh members         # provider org + memberships (SQL you confirm)
#   infra/lab/rollout/launch-v1.sh main            # fast-forward main to the release (the App rebuilds)
#   infra/lab/rollout/launch-v1.sh all             # preflight window box vercel members main
#
# Hosted windows (R151; R264): each window runs from a pinned release whose newest migration is
# THROUGH, driven by THIS (the tip's) script: line `cd "$(git rev-parse --show-toplevel)"` makes
# the script operate on the cwd's checkout, so the fixed script drives the older release. The
# first window (0052-0056):
#   git worktree add /tmp/launch-0056 a58eb0d66f82a5239c3f6c1cb0d992e43aa4045c && cd /tmp/launch-0056 && \
#     RELEASE=a58eb0d66f82a5239c3f6c1cb0d992e43aa4045c THROUGH=0056 WINDOW=P-08:<date> \
#     <the tip checkout>/infra/lab/rollout/launch-v1.sh preflight      # then: window
# `window` commits on that detached release and pushes HEAD as launch/window-<THROUGH> (never
# claude/consumer-v1); the coordinator merges that branch onto the tip. The second window's
# release (THROUGH=0059) is the first claude/consumer-v1 commit carrying both the
# known-good-reproof-4 merge (#66) and launch/window-0056.
#
# Inputs (exported or prompted):
#   RELEASE       the 40-hex commit to launch (default: the tip of claude/consumer-v1)
#   WINDOW        the P-08 window reference for the coordinator log, e.g. P-08:2026-09-30
#   SUPABASE_URL  https://<project-ref>.supabase.co  (the App's project)
#   LAB_DB_HOST   the hosted Postgres host for the transaction pooler (…pooler.supabase.com)
#   BOX_RELEASE   the consumer release installed on the box (default: origin/main) — its drain.sh pauses/resumes the edge
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
aws() { command env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '\n== %s\n' "$*"; }
need() { [ -n "${!1:-}" ] || { printf 'set %s\n' "$1" >&2; exit 2; }; }
RELEASE=${RELEASE:-$(git rev-parse claude/consumer-v1)}
[[ $RELEASE =~ ^[0-9a-f]{40}$ ]] || { echo "RELEASE must be a 40-hex sha" >&2; exit 2; }
NEWEST=$(ls apps/app/supabase/migrations | grep -E '^00[0-9]{2}_' | sort | tail -1)   # e.g. 0056_lab_control_grants.sql
# The window applies every migration after hosted-at that this checkout carries (lab-migrate.sh
# refuses otherwise), so the checkout's newest migration must be the newest RE-PROVEN one
# (R151 condition 1). Newer LOCAL-ONLY migrations land on the tip before their re-proof: run
# the window from a worktree at the last release whose newest migration is THROUGH, e.g.
# `git worktree add /tmp/launch-$THROUGH $WINDOW_RELEASE_HINT && cd /tmp/launch-$THROUGH`
# (an empty hint: the coordinator names that release after the previous window; R264).
# THROUGH picks the window: the re-proven level, hosted's level before it (the applied-list
# anchor the patched hosted-migrate.sh stops on) and the reviewed patch that gets it there.
THROUGH=${THROUGH:-0059}
case "$THROUGH" in
  0056) HOSTED_AT="0051 lab_import_jobs"; PATCH=infra/lab/rollout/hosted-migrate-0052-0056.patch   # KNOWN-GOOD-REPROOF-3; WR-KGR3-3
        WINDOW_RELEASE_HINT=a58eb0d66f82a5239c3f6c1cb0d992e43aa4045c ;;
  0059) HOSTED_AT="0056 lab_control_grants"; PATCH=infra/lab/rollout/hosted-migrate-0057-0059.patch   # KNOWN-GOOD-REPROOF-4; applies after the 0056 window's
        WINDOW_RELEASE_HINT='' ;;   # WR-KGR4-3: no release carries both #66 and launch/window-0056 yet
  *) echo "THROUGH=$THROUGH is not a re-proven level (0056 or 0059)" >&2; exit 2 ;;
esac
HOSTED_N=${HOSTED_AT:0:4}
if [ "${NEWEST:0:4}" != "$THROUGH" ]; then
  if [ -z "$WINDOW_RELEASE_HINT" ]; then
    printf "this checkout carries %s (its newest migration is not the re-proven %s): the second window's release is the first claude/consumer-v1 commit carrying both merge #66 and the first window's launch/window-0056 branch — the coordinator names it in RESUME-NOW and 09 after the first window\n" "$NEWEST" "$THROUGH" >&2
    exit 2
  fi
  printf 'this checkout carries %s (its newest migration is not the re-proven %s): run from a worktree at %s\n' "$NEWEST" "$THROUGH" "$WINDOW_RELEASE_HINT" >&2
  printf '  git worktree add /tmp/launch-%s %s && cd /tmp/launch-%s && RELEASE=%s THROUGH=%s %s %s\n' "$THROUGH" "$WINDOW_RELEASE_HINT" "$THROUGH" "$WINDOW_RELEASE_HINT" "$THROUGH" "$0" "${1:-preflight}" >&2
  exit 2
fi
NEWEST_N=${NEWEST:0:4}; NEWEST_NAME=${NEWEST:5}; NEWEST_NAME=${NEWEST_NAME%.sql}
PENDING=$(ls apps/app/supabase/migrations | grep -E '^00[0-9]{2}_' | sort | awk -v at="$HOSTED_N" 'substr($0,1,4) > at {printf "%s%s", sep, substr($0,1,4); sep=", "}')
SSM_CONTROL_DSN=/model-inference/lab/control_database_url
SSM_ANON=/model-inference/lab/supabase_anon_key
SSM_VERCEL=/callgideon/prod/VERCEL_TOKEN

preflight() {
  say "preflight at $RELEASE (newest migration $NEWEST; pending after $HOSTED_N: $PENDING)"
  git merge-base --is-ancestor "$RELEASE" claude/consumer-v1 || { echo "RELEASE is not on claude/consumer-v1" >&2; exit 2; }
  [ -x apps/infrx-api/.venv/bin/python ] || make api-env
  say "R151 condition 1: both rollback targets KNOWN-GOOD at $NEWEST_N (needs the re-proof through $THROUGH merged)"
  for t in bda15866e5700f3856d7142580da842fba9bbd23 422631591845fbd66b590c73d5ff4150318d9d7a; do
    apps/infrx-api/.venv/bin/python infra/rollout/known-good.py "$t" --applied "$NEWEST_N" && echo "  $t KNOWN-GOOD at $NEWEST_N"
  done
  say "R151 condition 2: hosted-migrate.sh carries the reviewed patch (EXPECTED_PENDING=\"$PENDING\")"
  grep -q "^EXPECTED_PENDING=\"$PENDING\"" infra/rollout/hosted-migrate.sh && echo "  patch present" || echo "  patch NOT present: run 'window' (it applies and shows the diff before anything hosted)"
  say "SSM names present (values never shown)"
  aws ssm describe-parameters --parameter-filters "Key=Name,Values=$SSM_CONTROL_DSN,$SSM_ANON,/model-inference/infrx_lab_control_password" --query 'Parameters[].[Name,Type,Version]' --output text
  say "App health"; curl -s -o /dev/null -w 'https://marlin2b.callbill.ai/health %{http_code}\n' https://marlin2b.callbill.ai/health || true
}

window() {
  need WINDOW
  say "R151 condition 2: the reviewed EXPECTED_PENDING patch (hosted is 0001-$HOSTED_N; pending $PENDING)"
  XFAIL_TEST=apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py
  if grep -q "^EXPECTED_PENDING=\"$PENDING\"" infra/rollout/hosted-migrate.sh; then
    echo "  already applied"
  else
    git apply --check "$PATCH" || { echo "hosted-migrate.sh is not in the state $PATCH expects (for 0059: the 0056 window's patch committed first)" >&2; exit 2; }
    git apply "$PATCH"
  fi
  grep -qF "case \"\$HOSTED_APPLIED\" in *\"$HOSTED_AT\")" infra/rollout/hosted-migrate.sh \
    || { echo "hosted-migrate.sh's applied-list anchor is not *\"$HOSTED_AT\"" >&2; exit 2; }
  # Between windows the tree-as-it-stands case is xfail(strict) naming the next window's patch (R264); every
  # window drops the one that names its own patch, in the same commit as the patch (else it XPASSes).
  if grep -q '^@pytest.mark.xfail(strict=True, reason="R151 condition 2 for the' "$XFAIL_TEST"; then
    python3 - "$XFAIL_TEST" <<'PY'   # the strict xfail would XPASS (fail) once the patch lands: drop it in the same commit
import sys; p=sys.argv[1]; L=open(p).read().split('\n')
i=L.index('def test_ldp__todays_hosted_migrate_carries_the_reviewed_patch():'); j=i-1
while not L[j].startswith('@'): j-=1
assert L[j].startswith('@pytest.mark.xfail(strict=True'), L[j]
del L[j:i]; s='\n'.join(L)
s=s.replace('EXPECTED_PENDING 0052, its W7 post-check `*"0052 lab_control_reject"`', 'EXPECTED_PENDING 0052-0056, its W7 post-check `*"0056 lab_control_grants"`')
open(p,'w').write(s)
PY
  fi
  if [ "$THROUGH" = 0059 ]; then   # the tree-as-it-stands case follows hosted to 0056
    sed -i 's/"--hosted-at", "0051", "--window", "P-08:dry"/"--hosted-at", "0056", "--window", "P-08:dry"/' "$XFAIL_TEST"
  fi
  git --no-pager diff --stat -- infra/rollout/hosted-migrate.sh "$XFAIL_TEST"; git --no-pager diff -- infra/rollout/hosted-migrate.sh "$XFAIL_TEST"
  read -r -p "commit this reviewed patch and continue to w6b? [y/N] " ok; [ "$ok" = y ] || exit 1
  git add infra/rollout/hosted-migrate.sh "$XFAIL_TEST"
  git commit -q -m "rollout: hosted-migrate.sh expects the Lab migrations $PENDING (R151 window $WINDOW, hosted $HOSTED_N -> $THROUGH; the reviewed $PATCH; the test_ldp case follows it)" || true
  say "coordinator log entry for condition 3 (append; never rewrite)"
  printf -- '- %s (operator, %s): **R151 window %s** — hosted %s–%s (Lab), never reverted; P-08 record: lab.callbill.ai / lab-control.callbill.ai; release %s.\n' \
    "$(date -u +%FT%H:%MZ)" "$(git config user.name)" "$WINDOW" "${PENDING%%,*}" "$NEWEST_N" "$RELEASE" >> research/plan/evidence/coordinator/2026-09-24-session-03.md
  git add research/plan/evidence/coordinator/2026-09-24-session-03.md; git commit -q -m "plan: R151 window $WINDOW opened (${PENDING%%,*}–$NEWEST_N)" || true
  say "W6 + W6b on a fresh hosted dump (no hosted write)"
  infra/lab/rollout/lab-migrate.sh --release "$RELEASE" --hosted-at "$HOSTED_N" --window "$WINDOW" --through w6b | tee /tmp/launch-v1-w6b.log
  DIG=$(grep -o 'COPY_DIGEST=[0-9a-f]*' /tmp/launch-v1-w6b.log | tail -1 | cut -d= -f2); [ -n "$DIG" ] || { echo "no COPY_DIGEST in the w6b log" >&2; exit 1; }
  # 95-maintenance/56-resume run the INSTALLED consumer release's drain.sh on the box (extracted by
  # 30-pause at its window): that is main's release, not this window's Lab release (window 2026-09-30).
  BOX_RELEASE=${BOX_RELEASE:-$(git rev-parse origin/main 2>/dev/null || git rev-parse main)}
  say "W7 needs the public /health 503: maintenance on (box release $BOX_RELEASE), apply, resume (App requests queue for ~1 min)"
  infra/rollout/ssm.sh infra/rollout/steps/95-maintenance.sh RELEASE="$BOX_RELEASE"
  infra/lab/rollout/lab-migrate.sh --release "$RELEASE" --hosted-at "$HOSTED_N" --window "$WINDOW" --through w7 --expect "$DIG" | tee /tmp/launch-v1-w7.log
  infra/rollout/ssm.sh infra/rollout/steps/56-resume.sh RELEASE="$BOX_RELEASE"
  grep -q "W7 PASS: hosted 0001-$NEWEST_N" /tmp/launch-v1-w7.log && echo "hosted 0001-$NEWEST_N applied (digest $DIG)"
  git push origin "HEAD:refs/heads/launch/window-$THROUGH"   # R264: never claude/consumer-v1 from the detached release
  echo "coordinator: merge launch/window-$THROUGH onto claude/consumer-v1 (between windows the test_ldp case is xfail(strict) naming the next window's patch)"
}

box() {
  need SUPABASE_URL
  say "L0 the box's checkout becomes $RELEASE (fetch; refuses if the engine script differs)"
  infra/rollout/ssm.sh infra/lab/rollout/steps/05-lab-checkout.sh RELEASE="$RELEASE"
  say "L1 inventory"; infra/rollout/ssm.sh infra/lab/rollout/steps/10-lab-preflight.sh RELEASE="$RELEASE"
  say "L3 Lab image (up to 1 h)"; TIMEOUT_S=3600 infra/rollout/ssm.sh infra/lab/rollout/steps/20-lab-image.sh RELEASE="$RELEASE"
  say "L4 units (inert)"; infra/rollout/ssm.sh infra/lab/rollout/steps/30-lab-units.sh RELEASE="$RELEASE"
  say "L5 control service ON (DSN and anon key by SSM name)"
  infra/rollout/ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=on RELEASE="$RELEASE" \
    CONTROL_DSN_PARAM="$SSM_CONTROL_DSN" ANON_KEY_PARAM="$SSM_ANON" SUPABASE_URL="$SUPABASE_URL" LAB_ORIGIN=https://lab.callbill.ai
  say "L5s smoke"; infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh
  say "L6 the control origin on the edge (DNS lab-control.callbill.ai → the box is already set)"
  infra/rollout/ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=on RELEASE="$RELEASE"
  curl -s -o /dev/null -w 'https://lab-control.callbill.ai/healthz %{http_code}\n' https://lab-control.callbill.ai/healthz || true
  say "L6s smoke + the App's external checks"; infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh; infra/rollout/verify-external.sh || true
  echo "L7 (eval/judge/datasets roles) is NOT run: no role login exists yet (WR-LDP-7); the control unit serves every Lab family (R237, 0056)."
}

vercel_lab() {
  command -v vercel >/dev/null || npm i -g vercel >/dev/null
  say "a valid Vercel token in SSM $SSM_VERCEL (the stored one is invalid): paste a new one now, or Enter to use the stored one"
  read -rs -p "token (hidden): " T; echo
  if [ -n "$T" ]; then umask 077; printf '%s' "$T" > ~/.vt; aws ssm put-parameter --name "$SSM_VERCEL" --type SecureString --overwrite --value "file://$HOME/.vt" >/dev/null; shred -u ~/.vt; fi
  export VERCEL_TOKEN; VERCEL_TOKEN=$(aws ssm get-parameter --name "$SSM_VERCEL" --with-decryption --query Parameter.Value --output text)
  ANON=$(aws ssm get-parameter --name "$SSM_ANON" --with-decryption --query Parameter.Value --output text)
  need SUPABASE_URL
  say "project infrx-lab, root apps/lab"
  (cd apps/lab && vercel link --yes --project infrx-lab >/dev/null 2>&1 || vercel project add infrx-lab >/dev/null && vercel link --yes --project infrx-lab >/dev/null)
  (cd apps/lab && for kv in "NEXT_PUBLIC_LAB_URL=https://lab.callbill.ai" "NEXT_PUBLIC_SUPABASE_URL=$SUPABASE_URL" "NEXT_PUBLIC_SUPABASE_ANON_KEY=$ANON" \
      "LAB_CONTROL_URL=https://lab-control.callbill.ai" "LAB_TRACES_API_URL=https://lab-control.callbill.ai" "LAB_EVALS_API_URL=https://lab-control.callbill.ai" \
      "LAB_PIPELINES_API_URL=https://lab-control.callbill.ai" "LAB_RELEASES_API_URL=https://lab-control.callbill.ai" "LAB_DATASETS_API_URL=https://lab-control.callbill.ai"; do
      k=${kv%%=*}; v=${kv#*=}; vercel env rm "$k" production --yes >/dev/null 2>&1 || true; printf '%s' "$v" | vercel env add "$k" production >/dev/null; done
    vercel --prod --yes | tail -3
    vercel domains add lab.callbill.ai >/dev/null 2>&1 || true)
  unset VERCEL_TOKEN ANON
  curl -s -o /dev/null -w 'https://lab.callbill.ai/ %{http_code}\n' https://lab.callbill.ai/ || true
  echo "Supabase Auth redirect URLs already include https://lab.callbill.ai/auth/callback** (set 2026-09-29)."
}

members() {
  say "provider org + memberships (the owner DSN is typed, never echoed)"
  read -rs -p "OPERATIONS_DATABASE_URL (hidden): " DSN; echo
  read -r -p "tester emails (space-separated): " EMAILS
  read -r -p "operator name for the audit column: " OP
  psql "$DSN" -v ON_ERROR_STOP=1 <<SQL
insert into infrx.provider_orgs (slug, display_name, created_by)
values ('infrx-internal', 'infrx internal testing', 'operator:$OP P-08 ${WINDOW:-launch-v1}')
on conflict (slug) do nothing;
SQL
  for e in $EMAILS; do psql "$DSN" -v ON_ERROR_STOP=1 -c "insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
select p.id, u.id, 'developer', 'operator:$OP P-08 ${WINDOW:-launch-v1}' from infrx.provider_orgs p, auth.users u where p.slug='infrx-internal' and u.email='$e'
on conflict do nothing;"; done
  psql "$DSN" -c "select provider_org_id, user_id, role, granted_at from infrx.provider_memberships where revoked_at is null;"
  unset DSN
}

main_ff() {
  say "fast-forward main to $RELEASE (the App's Vercel project builds main; every Lab switch is OFF there)"
  git fetch -q origin main; git merge-base --is-ancestor origin/main "$RELEASE" || { echo "main is not an ancestor of RELEASE" >&2; exit 1; }
  git push origin "$RELEASE:main"
}

case "${1:-}" in
  preflight) preflight ;; window) window ;; box) box ;; vercel) vercel_lab ;; members) members ;; main) main_ff ;;
  all) preflight; window; box; vercel_lab; members; main_ff ;;
  *) sed -n 2,36p "$0"; exit 2 ;;
esac
