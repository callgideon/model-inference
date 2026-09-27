#!/usr/bin/env bash
# Operator-run (terminal, repo root of the claude/consumer-v1 tip checkout, after make api-env): the E4C
# certify window and E1B's cells on the live RELEASE, "on the side" (certify-prep.md §4 A1-A7; E4C-runbook
# §1a H4-H6, §2-§6; E1B-protocol §7.2). Every step logs to $LOGDIR/<step>.log and $LOGDIR/window.log, and
# the script stops at the first failed stop condition naming what to do.
#   infra/rollout/certify-window.sh                                     # everything, new LOGDIR
#   infra/rollout/certify-window.sh --step <step> --logdir <that LOGDIR>   # resume from a step
#   infra/rollout/certify-window.sh --only <step> --logdir <that LOGDIR>   # one step (e.g. h6-close)
#   DRY_RUN=1 infra/rollout/certify-window.sh                           # the plan and stop conditions; calls nothing
# Inputs: TENANT2_USER=<tenant-2 user uuid> (create-test-user.py --json, H4) for h4-check; VIDEO_FILE (an
# in-cap clip on this host) for journey-legs; CORPUS_CACHE (the host corpus cache) for wc9.
# Long cells (76, 80, E1B, WC-6/7, WC-9) run detached (setsid -f) and are polled: a resume re-attaches
# to one still running and never starts it twice. The certify run is detached on the box; `report` polls
# 78-e4b-report.sh with its RUN. Skipped by the user's decision: O4-O6 (alerts) and the canary.
# BLOCKED and recorded: the SSE journey + replay (MEDIA_BASE_URL unnamed), the canary re-enable (P-24).
# Secrets: the certify key goes from SSM into a 0600 file for `statement --key-file` and is shredded;
# tenant 2's key is issue-key's 0600 file, shredded after WC-9; keys reach bench/curl through the
# environment only, never argv. LOGDIR is 0700.
set -euo pipefail
RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
CERTIFY_ORG=15e766d0-8c4d-47a8-986f-22ed32f390c3     # the certify tenant (key id 142c7d81)
CERTIFY_KEY_PARAM=/model-inference/e4b_api_key
MIGRATION_VERSION=0026
EDGE=https://marlin2b.callbill.ai/v1
BUCKET=llm-bootcamp-641134885443
LOGDIR=${LOGDIR:-$HOME/infrx-e4c/$(date -u +%Y%m%dT%H%M%SZ)}
STEP=${STEP:-prep76}; ONLY=""
DRY=${DRY_RUN:-0}
POLL_S=${POLL_S:-30}                  # a detached cell's poll
REPORT_POLL_S=${REPORT_POLL_S:-1200}  # 78's poll during the 4.5-6 h certify run
REPORT_MAX_S=${REPORT_MAX_S:-28800}
while [ $# -gt 0 ]; do case "$1" in
  --step) STEP=$2; shift 2;; --only) STEP=$2; ONLY=1; shift 2;; --logdir) LOGDIR=$2; shift 2;;
  *) echo "unknown argument $1 (use --step|--only <step> --logdir <dir>)" >&2; exit 2;; esac; done
ORDER=(prep76 o3 h4-check h5 h6 profiles77 freeze wc0-start certify report fetch h6-close e1b wc6a wc7
       wc6b h6-close2 tenant2-key two-tenant-fill journey-legs wc9 wc0-stop drills cleanup-dry cleanup)
START=""; for i in "${!ORDER[@]}"; do [ "${ORDER[$i]}" = "$STEP" ] && START=$i; done
[ -n "$START" ] || { echo "unknown step $STEP (one of ${ORDER[*]})" >&2; exit 2; }
umask 077; mkdir -p "$LOGDIR"; chmod 700 "$LOGDIR"
if [ "$DRY" != 1 ]; then
  [ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
  git rev-parse --verify -q "$RELEASE^{commit}" > /dev/null || { echo "RELEASE $RELEASE is not in this repository" >&2; exit 2; }
fi
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOGDIR/window.log"; }
fail() { say "$1 STOP — $2"; exit 1; }
SSM=infra/rollout/ssm.sh; CLI=infra/rollout/operator-cli.sh; H6=infra/rollout/certify-h6.sh; ST=infra/rollout/steps
# run <name> <cmd…>: logged to $LOGDIR/<name>.log. DRY_RUN prints it instead.
run() {
  local name=$1; shift
  if [ "$DRY" = 1 ]; then printf 'plan %s:' "$name"; printf ' %q' "$@"; printf '\n'; return 0; fi
  say "== $name"
  if "$@" > "$LOGDIR/$name.log" 2>&1; then say "$name ok"; else local rc=$?; say "$name exit $rc — $LOGDIR/$name.log"; return $rc; fi
}
# long <name> <executable…>: detached (setsid -f) and polled; a finished PASS is kept, a live one is
# re-attached (never started twice), a failed one is moved aside and started again.
long() {
  local name=$1; shift; local b=$LOGDIR/$name
  if [ "$DRY" = 1 ]; then printf 'plan %s (detached):' "$name"; printf ' %q' "$@"; printf '\n'; return 0; fi
  if [ "$(cat "$b.rc" 2>/dev/null)" = 0 ]; then say "$name already PASS ($b.log)"; return 0; fi
  if [ ! -e "$b.rc" ] && [ -s "$b.pid" ] && kill -0 "$(cat "$b.pid")" 2>/dev/null; then
    say "$name still running (pid $(cat "$b.pid")): polling"
  else
    if [ -e "$b.rc" ]; then mv "$b.log" "$b.failed-$(date -u +%H%M%S).log"; rm -f "$b.rc"; fi
    rm -f "$b.pid"; say "== $name (detached; $b.log)"
    setsid -f bash -c 'b=$1; shift; echo $$ > "$b.pid"; "$@" > "$b.log" 2>&1; echo $? > "$b.rc.new"; mv "$b.rc.new" "$b.rc"' _ "$b" "$@"
    for _ in $(seq 100); do [ -s "$b.pid" ] && break; sleep 0.1; done
  fi
  until [ -e "$b.rc" ]; do
    kill -0 "$(cat "$b.pid" 2>/dev/null || echo none)" 2>/dev/null || [ -e "$b.rc" ] \
      || { say "$name died without an exit status ($b.log)"; return 1; }
    sleep "$POLL_S"
  done
  local rc; rc=$(cat "$b.rc"); say "$name exit $rc — $b.log"; return "$rc"
}
# expect <name> <ERE> <why>: a line of <name>'s log matches, or STOP with <why>
expect() { if [ "$DRY" = 1 ]; then echo "  expect $1: /$2/ else STOP: $3"; return 0; fi
           grep -Eq -- "$2" "$LOGDIR/$1.log" || fail "$1" "$3"; }
# pick <var> <name> <sed ERE with one group>: a value from <name>'s log (DRY_RUN: <var>)
pick() { local -n _v=$1; if [ "$DRY" = 1 ]; then _v="<$1>"; return 0; fi
         _v=$(sed -En "s/$3/\1/p" "$LOGDIR/$2.log" | head -n 1); [ -n "$_v" ] || fail "$2" "no $1 in $LOGDIR/$2.log"; }
# The last JSON document of a log that starts a line (the operator CLI prints one line, bench
# --validate-only an indented document; stderr lines around them are skipped).
J='import json,re,sys; from decimal import Decimal
t=open(sys.argv[1]).read(); docs=[]
for m in re.finditer(r"^[{]", t, re.M):
    try: docs.append(json.JSONDecoder().raw_decode(t[m.start():])[0])
    except ValueError: pass
d=docs[-1]
'
jexpect() { if [ "$DRY" = 1 ]; then echo "  expect $1: $2 else STOP: $3"; return 0; fi
            python3 -c "$J"'sys.exit(0 if eval(sys.argv[2]) else 1)' "$LOGDIR/$1.log" "$2" || fail "$1" "$3"; }
jget() { local -n _j=$1; if [ "$DRY" = 1 ]; then _j="<$1>"; return 0; fi
         _j=$(python3 -c "$J"'print(eval(sys.argv[2]))' "$LOGDIR/$2.log" "$3") && [ -n "$_j" ] || fail "$2" "no $1 in $LOGDIR/$2.log"; }
once() { [ -s "$LOGDIR/$1" ] || printf '%s\n' "$2" > "$LOGDIR/$1"; cat "$LOGDIR/$1"; }   # a value fixed at first use
secret_to_file() { aws ssm get-parameter --with-decryption --name "$1" --query Parameter.Value --output text > "$2" && [ -s "$2" ]; }
shred_keys() { for k in "$LOGDIR/certify.key" "$@"; do [ ! -e "$k" ] || shred -u "$k"; done; }
trap 'shred_keys' EXIT
tenant_keys() {  # both tenants' keys into this process's environment (bench/curl read them by name)
  INFRX_API_KEY=$(aws ssm get-parameter --with-decryption --name "$CERTIFY_KEY_PARAM" --query Parameter.Value --output text) \
    && [ -n "$INFRX_API_KEY" ] || fail KEYS "SSM read of $CERTIFY_KEY_PARAM failed"
  [ -s "$LOGDIR/tenant2.key" ] || fail KEYS "no $LOGDIR/tenant2.key (tenant2-key)"
  INFRX_API_KEY_B=$(tr -d '\n' < "$LOGDIR/tenant2.key"); export INFRX_API_KEY INFRX_API_KEY_B; unset MARLIN_API_KEY
}
MW=$(once window-id "e4c-side-${RELEASE:0:8}-$(date -u +%Y%m%dT%H%MZ)")
say "certify-window from $STEP${ONLY:+ (only)}; LOGDIR=$LOGDIR; window $MW$([ "$DRY" != 1 ] || echo "; DRY_RUN: nothing is called")"

do_prep76() {
  long prep76 "$SSM" "$ST/76-e4c-prepare.sh" RELEASE="$RELEASE" \
    || fail A1 "76 refused (exit 2: the edge is not 200, a dirty w3-checkout or a bundle failing its sha256 — nothing changed; exit 3: the engine is not the pinned image)"
  expect prep76 "^A1 edge /health from the box: 200" "the edge is not open"
  expect prep76 "^w3-checkout at $RELEASE \(clean\)" "w3-checkout is not RELEASE"
  expect prep76 "^image_equals_pin=yes" "the served engine is not the pinned image"
}
do_o3() {
  run o3 "$SSM" "$ST/71-pool-budget.sh" || fail O3 "the installed configuration can exhaust the pooler (exit 1)"
  say "O4-O6 SKIPPED (alerts and the canary: the user's decision) — record it in ops.md"
}
do_h4-check() {
  [ -s "$LOGDIR/tenant2.user" ] || [ -z "${TENANT2_USER:-}" ] || echo "$TENANT2_USER" > "$LOGDIR/tenant2.user"
  local u2; u2=$(cat "$LOGDIR/tenant2.user" 2>/dev/null || true); [ "$DRY" = 1 ] && u2=${u2:-<tenant2-user>}
  [ -n "$u2" ] || fail H4 "export TENANT2_USER=<the tenant-2 user uuid from create-test-user.py --json>"
  run h4-grant "$CLI" grant --user "$u2" --idempotency-key grant-tenant2-20260925 --reason "E4C second test tenant" \
    || fail H4 "grant failed"
  run h4-account "$CLI" account --user "$u2" || fail H4 "account failed"
  jexpect h4-account "d['verification_evidence_ref'] is not None and d['credit']['available'] == '10000.00000000'" \
    "tenant 2 is not a verified individual holding exactly 10,000 CREDIT; issue no key"
}
do_h5() {
  run h5-key secret_to_file "$CERTIFY_KEY_PARAM" "$LOGDIR/certify.key" || fail H5 "SSM read of $CERTIFY_KEY_PARAM failed"
  if ! run h5-statement "$CLI" statement --key-file "$LOGDIR/certify.key"; then shred_keys; fail H5 "statement failed"; fi
  shred_keys
  jexpect h5-statement "d['org_id'] == '$CERTIFY_ORG'" "the certify key's org is not $CERTIFY_ORG: nothing funded"
  local u1; jget u1 h5-statement "d['user_id']"
  run h5-account-before "$CLI" account --user "$u1" || fail H5 "account failed"
  run h5-adjust "$CLI" adjust --user "$u1" --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation" \
    || fail H5 "adjust failed"
  run h5-account-after "$CLI" account --user "$u1" || fail H5 "account failed"
  jexpect h5-adjust "d['amount'] == '40000.00000000'" "the adjustment is not +40000.00000000 CREDIT"
  [ "$DRY" = 1 ] || python3 -c "$J"'
a = d; b, c = ([json.loads(l) for l in open(f) if l.startswith("{")][-1] for f in sys.argv[2:4])
delta = Decimal(c["credit"]["available"]) - Decimal(b["credit"]["available"])
print("available", b["credit"]["available"], "->", c["credit"]["available"], "replayed", a["replayed"])
sys.exit(0 if a["replayed"] or delta == Decimal("40000") else 1)' \
    "$LOGDIR/h5-adjust.log" "$LOGDIR/h5-account-before.log" "$LOGDIR/h5-account-after.log" | tee -a "$LOGDIR/window.log" \
    || fail H5 "the balance did not move by +40,000 (and the adjustment was not a replay)"
}
do_h6() { run h6 "$H6" "$LOGDIR" h6 || fail H6 "STOP line in $LOGDIR/h6.log: the operator revokes the extra key, then --only h6"; }
do_profiles77() {
  local args; pick args h6 '^(KEYS_TAKEN_AT=.*)$'
  # shellcheck disable=SC2086 # three NAME=VALUE words, as certify-h6.sh prints them
  run profiles77 "$SSM" "$ST/77-e4c-profiles.sh" RELEASE="$RELEASE" MIGRATION_VERSION="$MIGRATION_VERSION" \
    MAINTENANCE_WINDOW="$MW" $args || fail A3 "77 refused (exit 2: a read disagrees, nothing written; exit 3: a profile does not validate). Never edit a filled copy (R133)"
  expect profiles77 '"engine_options_digest": "sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147"' \
    "certify --hashes does not carry the pinned engine options"
}
do_freeze() {  # E4C-runbook §7: freeze.json + profiles.sha256, from 76/77's output and this checkout
  [ "$DRY" = 1 ] && { echo "plan freeze: $LOGDIR/freeze.json + profiles.sha256 from prep76/profiles77 logs, the SSM command ids and the launchers' sha256"; return 0; }
  python3 - "$LOGDIR" "$RELEASE" "$MW" "$MIGRATION_VERSION" <<'PY' || fail FREEZE "freeze.json could not be written"
import glob, hashlib, json, os, re, sys
logdir, release, window, migration = sys.argv[1:]
text = lambda n: open(os.path.join(logdir, n + ".log")).read()
p77, p76 = text("profiles77"), text("prep76")
identity = dict(re.findall(r"^E4C-box\.json identity\.(\w+) = (\S+)$", p77, re.M))
tail = p77[p77.index("== certify --hashes"):]
hashes = json.JSONDecoder().raw_decode(tail[tail.index("{"):])[0]
files = ["infra/rollout/certify-window.sh", "infra/rollout/e4c-certify.sh", "infra/rollout/e1b-window.sh",
         "infra/rollout/certify-h6.sh", "infra/rollout/certify-fill.py", "infra/rollout/steps/76-e4c-prepare.sh",
         "infra/rollout/steps/77-e4c-profiles.sh", "infra/rollout/steps/78-e4b-report.sh", "infra/rollout/steps/79-wc0-scrape.sh",
         "infra/rollout/steps/80-e4b-fetch.sh", "models/marlin2b/e1b/l8ref.sh", "models/marlin2b/e1b/l8served.sh"]
commands = {}
for log in sorted(glob.glob(os.path.join(logdir, "*.log"))):
    for cid, step in re.findall(r"^command (\S+) \((\S+) on ", open(log).read(), re.M):
        commands.setdefault(os.path.basename(log)[:-4], []).append({"step": step, "command_id": cid})
doc = {"release": release, "maintenance_window": window, "migration_version": migration,
       "identity": identity, "certify_hashes": hashes,
       "inventory": re.search(r"^inventory ([0-9a-f]{64}) (\d+) lines", p76, re.M).groups(),
       "release_bundle_sha256": (re.search(r"^bundle ([0-9a-f]{64}) ", p76, re.M) or [None, None])[1],
       "launchers_sha256": {f: hashlib.sha256(open(f, "rb").read()).hexdigest() for f in files},
       "ssm_commands": commands, "skipped": ["O4-O6 alerts (the user's decision)", "the canary (P-24)"]}
json.dump(doc, open(os.path.join(logdir, "freeze.json"), "w"), indent=1)
sums = re.findall(r"^(?:base|filled) ([0-9a-f]{64}) (\S+)$", p77, re.M)
open(os.path.join(logdir, "profiles.sha256"), "w").write("".join(f"{s}  {n}\n" for s, n in sums))
missing = [k for k in ("source_sha", "deployed_sha", "image_digest", "weights_sha256", "processor_sha256",
                       "migration_version", "config_version") if k not in identity]
print("freeze.json", hashlib.sha256(open(os.path.join(logdir, "freeze.json"), "rb").read()).hexdigest(), "missing", missing)
sys.exit(1 if missing or identity.get("source_sha") != release or len(sums) != 12 else 0)
PY
}
do_wc0-start() {
  run wc0-start "$SSM" "$ST/79-wc0-scrape.sh" ACTION=start || fail WC-0 "79 start failed (exit 2: already running; exit 3: lines=0)"
  expect wc0-start "lines=[1-9]" "the sidecar wrote no scrape line"
}
do_certify() {
  if [ "$DRY" != 1 ] && grep -q '^out=' "$LOGDIR/certify.log" 2>/dev/null; then
    fail CERTIFY "a certify run was already launched from this LOGDIR ($(grep -m1 '^out=' "$LOGDIR/certify.log")): a relaunch kills it — poll it with --step report, or move certify.log aside for a new qualifying run"
  fi
  run certify "$SSM" infra/rollout/e4c-certify.sh RELEASE="$RELEASE" || fail CERTIFY "e4c-certify.sh refused at launch (exit 2: key.env, MARLIN_API_KEY, the e4c files or pg_journal_url)"
  expect certify '^out=/opt/dlami/nvme/e4b/[0-9]{8}T[0-9]{6}Z' "no out= line"
}
do_report() {
  local r code t0=$SECONDS; pick r certify '^out=\/opt\/dlami\/nvme\/e4b\/([0-9]{8}T[0-9]{6}Z).*$'
  while :; do
    run report "$SSM" "$ST/78-e4b-report.sh" RUN="$r" ONLY=report.json || fail REPORT "78 failed (exit 3: no run $r on the box)"
    [ "$DRY" = 1 ] && { echo "  poll every ${REPORT_POLL_S}s until certify.log ends 'exit N'; N=1 (a FAIL cell) STOPs: 05 §7 fix loop"; return 0; }
    code=$(grep -Eo '^exit [0-9]+' "$LOGDIR/report.log" | tail -n 1 | cut -d' ' -f2 || true)
    if [ -n "$code" ]; then
      [ "$code" != 1 ] || fail REPORT "certify exit 1: a FAIL cell — the 05 §7 fix loop (a rerun is a new qualifying run; criteria unchanged)"
      say "certify finished: exit $code"; return 0
    fi
    [ $((SECONDS - t0)) -lt "$REPORT_MAX_S" ] || fail REPORT "no 'exit N' after ${REPORT_MAX_S}s: read $LOGDIR/report.log"
    sleep "$REPORT_POLL_S"
  done
}
do_fetch() {
  local r sha n dest; pick r certify '^out=\/opt\/dlami\/nvme\/e4b\/([0-9]{8}T[0-9]{6}Z).*$'
  long fetch "$SSM" "$ST/80-e4b-fetch.sh" RUN="$r" || fail FETCH "80 failed (exit 3: no report.json yet)"
  pick sha fetch '^uploaded s3:\/\/[^ ]+ ([0-9a-f]{64}).*$'
  dest=$(ls -d models/marlin2b/results/E4C-box-"${RELEASE:0:7}"/run*-"$r" 2>/dev/null | head -n 1 || true)
  if [ -z "$dest" ]; then
    n=$( (ls -d models/marlin2b/results/E4C-box-"${RELEASE:0:7}"/run* 2>/dev/null || true) | wc -l); n=$((n + 1))
    dest=models/marlin2b/results/E4C-box-${RELEASE:0:7}/run$n-$r
  fi
  run fetch-get aws s3 cp "s3://$BUCKET/w4/e4b-box/$r.tgz" "$LOGDIR/$r.tgz" --only-show-errors || fail FETCH "download failed"
  [ "$DRY" = 1 ] || [ "$(sha256sum < "$LOGDIR/$r.tgz" | cut -d' ' -f1)" = "$sha" ] || fail FETCH "the archive's sha256 is not the box's $sha"
  run fetch-unpack bash -c 'mkdir -p "$1" && tar -xzf "$2" -C "$1"' _ "$dest" "$LOGDIR/$r.tgz" || fail FETCH "unpack failed"
  run cells python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); s={e["stage"]: e["status"] for e in r["stages"]}
want=("e4b.a.dataset-resume","e4b.b.envelope","e4b.b.soak","e4b.b.overload")
[print(c, s.get(c)) for c in want]; print("max_video_seconds", r["target"].get("max_video_seconds"))
sys.exit(0 if all(s.get(c) == "PASS" for c in want) and r["target"].get("max_video_seconds") == 82.0 else 1)' "$dest/report.json" \
    || fail REPORT "a certify cell is not PASS or target.max_video_seconds != 82.0 ($LOGDIR/cells.log): the 05 §7 fix loop"
}
do_h6-close() { run h6-after-s4 "$H6" "$LOGDIR" h6-after-s4 || fail H6 "a spending key appeared during §4: every cell after its created_at is INVALID"; }
do_e1b() {
  local u1; jget u1 h5-statement "d['user_id']"
  run e1b-budget "$CLI" account --user "$u1" || fail E1B "account failed"
  jexpect e1b-budget "Decimal(d['credit']['available']) >= Decimal('8096.5632')" "below E1B §7.2's 8,096.5632 CREDIT"
  long e1b env TIMEOUT_S=7200 "$SSM" infra/rollout/e1b-window.sh RELEASE="$RELEASE" \
    || fail E1B "e1b-window.sh failed ('refused before …': the engine is not at max_num_seqs 8, a certify container or a leftover cell)"
  expect e1b '^done out=' "the window did not finish"
  [ "$DRY" = 1 ] || ! grep -Eq ' exit=[1-9]' "$LOGDIR/e1b.log" || fail E1B "a cell exited non-zero (cells.tsv)"
}
drill_line() { local id; id=$(sed -En 's/^command (\S+) .*/\1/p' "$LOGDIR/$1.log" | head -n 1)
               grep -E '^drill=' "$LOGDIR/$1.log" | sed "s/ssm=FILL/ssm=$id/" >> "$LOGDIR/drills.md" || true; }
do_wc6a() {
  local eutc; eutc=$(once wc6.eutc "$(date -u +%Y%m%dT%H%M%SZ)")
  long wc6a "$SSM" models/marlin2b/e1b/l8ref.sh EUTC="$eutc" || true
  expect wc6a '^restored=yes' "restored=no: the engine is down — restart.md § Engine; the window stops"
  [ "$DRY" = 1 ] || drill_line wc6a
}
do_wc7() {
  long wc7 "$SSM" infra/rollout/e1b-window.sh RELEASE="$RELEASE" CELLS=WC-7 || fail WC-7 "refused (see the log)"
  expect wc7 '^WC-7 cold exit=0' "WC-7 did not exit 0"
}
do_wc6b() {
  local eutc; eutc=$(once wc6.eutc "$(date -u +%Y%m%dT%H%M%SZ)")
  long wc6b "$SSM" models/marlin2b/e1b/l8served.sh EUTC="$eutc" || fail WC-6b "l8served.sh failed"
  printf 'R=/opt/dlami/nvme/w3-checkout; python3 $R/research/plan/evidence/w/box/box-lane/l8compare.py /opt/dlami/nvme/e1b-logs/L8-%s $R/models/marlin2b/measure\n' \
    "$eutc" > "$LOGDIR/wc6-compare.sh"
  run wc6b-compare "$SSM" "$LOGDIR/wc6-compare.sh" || fail WC-6b "the compare failed"
}
do_h6-close2() { run h6-after-s4a "$H6" "$LOGDIR" h6-after-s4a || fail H6 "a spending key appeared during §4a: the cells after its created_at are INVALID"; }
do_tenant2-key() {
  local u2; u2=$(cat "$LOGDIR/tenant2.user" 2>/dev/null || true); [ "$DRY" = 1 ] && u2=${u2:-<tenant2-user>}
  [ -n "$u2" ] || fail A6 "no tenant-2 user (h4-check)"
  if [ "$DRY" != 1 ] && [ -s "$LOGDIR/tenant2.key" ] && grep -q '"key_id"' "$LOGDIR/tenant2-key.log" 2>/dev/null; then
    say "tenant-2 key already issued ($LOGDIR/tenant2-key.log)"
  else
    run tenant2-key "$CLI" issue-key --user "$u2" --name e4c-tenant2 --secret-file "$LOGDIR/tenant2.key" \
      --idempotency-key key-tenant2-20260925 --reason "E4C second test tenant" || fail A6 "issue-key failed"
    [ "$DRY" = 1 ] || [ -s "$LOGDIR/tenant2.key" ] || fail A6 "issue-key wrote no secret (a replay?): revoke that key, then issue with a new idempotency key"
  fi
  local t2; jget t2 tenant2-key "d['key_id'][:8]"; say "TENANT2_PREFIX = key_id[:8] = $t2 (not the secret-derived prefix field)"
}
do_two-tenant-fill() {
  local t2 cfg img inv; jget t2 tenant2-key "d['key_id'][:8]"
  pick cfg profiles77 '^E4C-box\.json identity\.config_version = (sha256:[0-9a-f]{64})$'
  pick img profiles77 '^E4C-box\.json identity\.image_digest = (sha256:[0-9a-f]{64})$'
  run rel-tree bash -c 'rm -rf -- "$1" && mkdir -p "$1" && git archive "$2" models/marlin2b | tar -x -C "$1"' _ "$LOGDIR/rel" "$RELEASE" \
    || fail A6 "git archive failed"
  run two-tenant-fill env RELEASE="$RELEASE" DEPLOYED_SHA="$RELEASE" IMAGE_DIGEST="$img" CONFIG_VERSION="$cfg" \
    MIGRATION_VERSION="$MIGRATION_VERSION" MAINTENANCE_WINDOW="$MW" TENANT2_PREFIX="$t2" \
    python3 infra/rollout/certify-fill.py "$LOGDIR/rel" "$LOGDIR/e4c" \
    E4C-two-tenant.json=E4C-box.two-tenant.base.json E1B-two-tenant.json=E1B-edge.two-tenant.base.json \
    || fail A6 "certify-fill.py refused (nothing written)"
  run h6-journey env EXPECT="142c7d81,$t2" "$H6" "$LOGDIR" h6-journey || fail A6 "the journey inventory is not exactly 142c7d81 + $t2"
  pick inv h6-journey '^INVENTORY (.*)$'
  [ "$DRY" = 1 ] || printf '%s\n' "$inv" > "$LOGDIR/e4c/keys-journey.json"
}
block() {  # the first ```bash block after the line starting with $1 in the E4C runbook (§5.1)
  python3 -c 'import re,sys; t=open(sys.argv[1]).read(); t=t[t.index("\n"+sys.argv[2]):]; print(re.search(r"```bash\n(.*?)```", t, re.S).group(1))' \
    models/marlin2b/results/E4C-runbook.md "$1"
}
do_journey-legs() {
  say "SSE journey + replay: BLOCKED (MEDIA_BASE_URL unnamed; the frozen journey forms include video_url, R133) — P-17 check 5 stays false"
  [ "$DRY" = 1 ] || [ -s "${VIDEO_FILE:-}" ] || fail JOURNEY "export VIDEO_FILE=<an in-cap clip on this host>, then --step journey-legs"
  [ "$DRY" = 1 ] || tenant_keys
  local t
  for t in a b; do
    [ "$DRY" = 1 ] || if [ $t = a ]; then export INFRX_TEST_KEY=$INFRX_API_KEY; else export INFRX_TEST_KEY=$INFRX_API_KEY_B; fi
    run "journey-async-$t" infra/rollout/verify-journey.sh || fail JOURNEY "tenant $t async leg failed"
    expect "journey-async-$t" '^failures: 0' "tenant $t async leg: failures"
  done
  unset INFRX_TEST_KEY
  run journey-sync bash -c "$(block 'Sync (non-stream).')" || fail JOURNEY "the sync leg failed"
  [ "$DRY" = 1 ] || [ "$(grep -Ec 'sync replay 200 .*[Ii]dempotency-[Rr]eplayed: true' "$LOGDIR/journey-sync.log")" = 2 ] \
    || fail JOURNEY "sync: not 200 + Idempotency-Replayed: true for both tenants"
  run journey-foreign bash -c "$(block 'Foreign calls.')" || fail JOURNEY "the foreign-call leg failed"
  [ "$DRY" = 1 ] || { [ "$(grep -Ec '^foreign (GET|DELETE) 404 not_found' "$LOGDIR/journey-foreign.log")" = 6 ] \
    && [ "$(grep -c '^owner state succeeded' "$LOGDIR/journey-foreign.log")" = 2 ]; } \
    || fail JOURNEY "foreign calls: not 404 not_found x3 per tenant, or an owner job did not succeed"
  run journey-discovery curl -s "$EDGE/models" || fail JOURNEY "discovery failed"
  expect journey-discovery '"nemostation/marlin-2b"' "the public catalog does not list nemostation/marlin-2b"
}
do_wc9() {
  [ "$DRY" = 1 ] || [ -n "${CORPUS_CACHE:-}" ] || fail WC-9 "export CORPUS_CACHE=<the host corpus cache> (E4C-runbook §5), then --step wc9"
  [ "$DRY" = 1 ] || tenant_keys
  local w9=(apps/infrx-api/.venv/bin/python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset full
    --base-url "$EDGE" --target gateway --model nemostation/marlin-2b --rate 0.5 --requests 135 --seed 20260922 --forms video_b64
    --max-tokens 128,512,1024 --retries 0 --tenant-keys INFRX_API_KEY,INFRX_API_KEY_B --dataset-version e1b-w1-2t
    --profile "$LOGDIR/e4c/E1B-two-tenant.json" --key-inventory "$LOGDIR/e4c/keys-journey.json")
  run wc9-validate "${w9[@]}" --validate-only || true
  jexpect wc9-validate "d['runnable'] and not d['errors'] and not d['blocks']" "WC-9 does not validate: refused"
  long wc9 "${w9[@]}" --out "$LOGDIR/e4c/e1b-2t.jsonl" --raw "$LOGDIR/e4c/e1b-2t-raw.jsonl" || fail WC-9 "bench exited non-zero"
  [ "$DRY" = 1 ] && { echo "plan: shred $LOGDIR/tenant2.key"; return 0; }
  unset INFRX_API_KEY INFRX_API_KEY_B; shred_keys "$LOGDIR/tenant2.key"
  say "tenant-2 key file shredded; revoke that key before any certify rerun, then retake H6 (E4C-runbook §4)"
}
do_wc0-stop() { run wc0-stop "$SSM" "$ST/79-wc0-scrape.sh" ACTION=stop || fail WC-0 "79 stop failed"; say "canary re-enable: BLOCKED (P-24) — it stays off"; }
# E4C-runbook §6 drills: two have a box form here (each prints one drill= line, measured on the box); the
# others are the runbook's own procedures, recorded NOT RUN unless the operator runs them. Each asks first.
DRILLS=("engine-restart|infra/runbooks/restart.md#engine|300|systemctl restart marlin2b-vllm.service|8001/readyz"
        "worker-sigkill|infra/runbooks/restart.md#worker|30|systemctl kill -s KILL infrx-worker.service; systemctl start infrx-worker.service|8002/readyz"
        "valkey-index-loss|infra/runbooks/index-loss.md|30||" "db-object-store-stall|E3C s08|45||"
        "restore|infra/runbooks/restore.md (hosted A-steps: the W6 block)|0||" "known-good-rollback|infra/runbooks/rollback.md#known-good-rollback-drill (steps 1-7)|0||")
do_drills() {
  local d name book bound act probe ok hours
  hours=$(( ($(date +%s) - $(stat -c %Y "$LOGDIR/window-id")) / 3600 + 1 ))
  for d in "${DRILLS[@]}"; do
    IFS='|' read -r name book bound act probe <<< "$d"
    if [ -z "$act" ]; then
      say "drill $name: no box form here — run $book by hand if the user wants it"
      [ "$DRY" = 1 ] || echo "drill=$name runbook=$book ssm=- verdict=NOT RUN correctness=-" >> "$LOGDIR/drills.md"; continue
    fi
    if [ "$DRY" = 1 ]; then echo "plan drill-$name (asks first; an outage on the live platform): $act; /$probe 200 within ${bound}s"; continue; fi
    read -rp "drill $name on the LIVE platform ($book, bound ${bound}s) — type yes to run, anything else skips: " ok
    if [ "$ok" != yes ]; then echo "drill=$name runbook=$book ssm=- verdict=NOT RUN correctness=-" >> "$LOGDIR/drills.md"; continue; fi
    printf '%s\n' '#!/usr/bin/env bash' 'set -uo pipefail' 't0=$(date -u +%s)' "$act" \
      "for i in \$(seq $((bound * 2))); do curl -fs -o /dev/null -m 5 127.0.0.1:$probe && break; sleep 1; done" \
      't1=$(date -u +%s); m=$((t1 - t0)); curl -fs -o /dev/null -m 5 127.0.0.1:'"$probe"' && up=yes || up=no' \
      "v=FAIL; [ \$up = yes ] && [ \$m -le $bound ] && v=PASS" \
      "echo \"drill=$name runbook=$book ssm=FILL t0=\$(date -u -d @\$t0 +%FT%TZ) recovered=\$(date -u -d @\$t1 +%FT%TZ) measured_s=\$m bound_s=$bound verdict=\$v correctness=see-drift\"" \
      > "$LOGDIR/drill-$name.sh"
    run "drill-$name" "$SSM" "$LOGDIR/drill-$name.sh" || true
    drill_line "drill-$name"
    expect "drill-$name" '^drill=.* verdict=PASS' "drill $name FAILED its ${bound}s bound: restart.md; the window stops"
    run "drift-$name" apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --hours "$hours" || true
    expect "drift-$name" '^wallet drift rows +\[\(0,\)\]' "wallet drift after $name"
    expect "drift-$name" '^credit-wallet drift rows +\[\(0,\)\]' "credit-wallet drift after $name"
  done
  run drift-end apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --hours "$hours" || true
  expect drift-end '^wallet drift rows +\[\(0,\)\]' "wallet drift at the window's end"
  expect drift-end '^credit-wallet drift rows +\[\(0,\)\]' "credit-wallet drift at the window's end"
}
do_cleanup-dry() { run cleanup-dry "$SSM" "$ST/86-cleanup.sh" || fail CLEANUP "86 (dry run) failed"; }
do_cleanup() {
  [ "$DRY" = 1 ] || { local ok; read -rp "cleanup-dry.log reviewed (allowlisted paths only)? type yes: " ok; [ "$ok" = yes ] || fail CLEANUP "not confirmed; nothing removed"; }
  run cleanup "$SSM" "$ST/86-cleanup.sh" DRY_RUN=0 || fail CLEANUP "86 failed"
}
last=""
for ((i = START; i < ${#ORDER[@]}; i++)); do
  last=${ORDER[$i]}; "do_$last"
  [ -z "$ONLY" ] || break
done
say "done through $last. E4C-runbook §7: copy freeze.json, profiles.sha256, drills.md and the h6 inventories into research/plan/evidence/e/E4C-${RELEASE:0:7}/; BACKEND-READY stays PENDING (P-17 checks 1, 5, 7)."
