#!/usr/bin/env bash
# E4C-runbook H6 and its closing re-checks (after §4, after §4a) and §5.0 step 3, coordinator host, repo
# root, read-only: a fresh `credit-transition --dry-run` through operator-cli.sh (it writes nothing), its
# JSON sha256 and `as_of`, and the active key-id prefixes.
#   infra/rollout/certify-h6.sh <LOGDIR> <tag>   # writes <LOGDIR>/<tag>-dryrun.json (0600)
#   EXPECT=142c7d81 (default) | EXPECT=142c7d81,<tenant-2 key-id prefix> for §5.0 step 3
# Consumer (spending) keys only: key_id[:8] of each key with revoked_at null and audience != operator.
# The keys block's `prefix` field is the SECRET's first 17 characters (service.py:44-46) and never goes
# into an inventory. Operator keys cannot spend (contracts/v2/ports.py:94); the operator key stays active
# for H5 and §5.0 and is printed on its own `operator … (excluded)` line for the H6 record.
# Prints the INVENTORY JSON line and the KEYS_TAKEN_AT=… KEYS_SOURCE_SHA256=… ACTIVE_PREFIXES=… line for
# 77-e4c-profiles.sh. Exit 1 with `STOP:` on a non-zero dry run or any other active spending key.
set -euo pipefail
dir=${1:?LOGDIR}; tag=${2:?tag}; expect=${EXPECT:-142c7d81}
umask 077; f=$dir/$tag-dryrun.json
rc=0; infra/rollout/operator-cli.sh credit-transition --dry-run --card rc_marlin2b_20260925_launch \
  --input-rate 400 --output-rate 1200 > "$f" || rc=$?
echo "dry-run exit $rc (0 = no blockers)"
[ "$rc" = 0 ] || { echo "STOP: the dry run exited $rc (see $f)" >&2; exit 1; }
python3 - "$f" "$expect" <<'PY'
import hashlib, json, sys
raw = open(sys.argv[1], "rb").read()
d = json.loads(raw)
sha = hashlib.sha256(raw).hexdigest()
print("blockers", d["blockers"], "drift", d["inventory"]["drift"])
active = [k for k in d["inventory"]["keys"] if k["revoked_at"] is None]
for k in active:
    print("active", k["key_id"][:8], k["audience"], "org", k["org_id"][:8], "created", k["created_at"],
          "(excluded: an operator key spends nothing)" if k["audience"] == "operator" else "")
spenders = sorted(k["key_id"][:8] for k in active if k["audience"] != "operator")
inv = {"active_key_id_prefixes": spenders, "taken_at": d["as_of"],
       "source": "G8 credit-transition --dry-run sha256:" + sha}
print("INVENTORY " + json.dumps(inv))
print(f"KEYS_TAKEN_AT={d['as_of']} KEYS_SOURCE_SHA256={sha} ACTIVE_PREFIXES={','.join(spenders)}")
want = sorted(sys.argv[2].split(","))
if spenders != want:
    sys.exit(f"STOP: active spending keys {spenders} != {want}: the operator revokes the extra ones, then retake")
print("OK: the active spending keys are exactly", want)
PY
