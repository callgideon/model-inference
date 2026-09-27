#!/usr/bin/env bash
# E4C-runbook §2/§3 and H6 on the box, as root: fill the six committed bases from the served build into
# /opt/dlami/nvme/e4b/e4c/ (FILL values only, nothing else; R133), write keys-certify.json from H6's
# inventory (key-id prefixes only), validate every placed profile with bench --validate-only inside the
# certify image (no network, no key) and print certify --hashes from the checkout for freeze.json.
# Needs 76-e4c-prepare.sh first.
#   infra/rollout/ssm.sh infra/rollout/steps/77-e4c-profiles.sh RELEASE=<40-hex> MIGRATION_VERSION=0026 \
#     MAINTENANCE_WINDOW=<window id> KEYS_TAKEN_AT=<H6 as_of> KEYS_SOURCE_SHA256=<H6 dry-run sha256> \
#     ACTIVE_PREFIXES=142c7d81          (certify-h6.sh prints the last three as one line)
# Every identity is read here: deployed_sha from both /metrics, the image from docker, config_version =
# the sha256 of the env file (the hash only, never the file). Exit 2 before a file is written when a read
# disagrees; exit 3 when a placed profile does not validate (runnable false or an error).
# The heredoc is infra/rollout/certify-fill.py verbatim (ssm.sh ships this text only).
set -euo pipefail
: "${RELEASE:?}" "${MIGRATION_VERSION:?}" "${MAINTENANCE_WINDOW:?}" "${KEYS_TAKEN_AT:?}" "${KEYS_SOURCE_SHA256:?}"
: "${ACTIVE_PREFIXES:?the H6 key-id prefixes, e.g. 142c7d81}"
nvme=${NVME:-/opt/dlami/nvme}; e4c=$nvme/e4b/e4c; repo=$nvme/w3-checkout; env_file=${ENV_FILE:-/etc/marlin2b-gateway.env}
refuse() { echo "refused: $*" >&2; exit 2; }
g() { git -c safe.directory='*' -C "$repo" "$@"; }
[[ $KEYS_SOURCE_SHA256 =~ ^[0-9a-f]{64}$ ]] || refuse "KEYS_SOURCE_SHA256 is the dry-run JSON's 64-hex sha256"
[[ $ACTIVE_PREFIXES =~ ^[0-9a-f]{8,12}(,[0-9a-f]{8,12})*$ ]] || refuse "ACTIVE_PREFIXES: comma list of 8-12 hex key-id prefixes"
[ "$(g rev-parse HEAD)" = "$RELEASE" ] && [ -z "$(g status --porcelain)" ] || refuse "w3-checkout is not a clean $RELEASE (76-e4c-prepare.sh)"
rev() { curl -s -m 5 "127.0.0.1:$1/metrics" | sed -n 's/^infrx_build_info{.*revision="\([0-9a-f]*\)".*/\1/p' | head -n 1; }
gw=$(rev 8001); wk=$(rev 8002)
[ "$gw" = "$RELEASE" ] && [ "$wk" = "$RELEASE" ] || refuse "infrx_build_info revision: gateway '$gw', worker '$wk'"
img=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")
[ "$(docker inspect --format '{{.Image}}' infrx-gateway)" = "$img" ] || refuse "infrx-gateway does not run infrx-runtime:$RELEASE"
for u in marlin2b-vllm infrx-worker infrx-valkey; do [ "$(systemctl is-active "$u")" = active ] || refuse "$u is not active"; done
docker image inspect "infrx-certify:$RELEASE" > /dev/null || refuse "no infrx-certify:$RELEASE (76-e4c-prepare.sh)"
export RELEASE DEPLOYED_SHA=$gw IMAGE_DIGEST=$img MIGRATION_VERSION MAINTENANCE_WINDOW
CONFIG_VERSION=sha256:$(sha256sum "$env_file" | cut -d' ' -f1); export CONFIG_VERSION
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 - "$repo" "$tmp" E4C-box.json=E4C-box.base.json E4C-edge.json=E4C-edge.overload.base.json \
  E1B-direct.json=E1B-direct.base.json E1B-box.json=E1B-box.base.json \
  E1B-box-forms.json=E1B-box.forms.base.json E1B-sop.json=E1B-sop.base.json <<'PY'
"""E4C-runbook §3 and §5.0: fill the committed run-profile bases - FILL values only, nothing else.
    python3 infra/rollout/certify-fill.py <checkout> <dest dir> NAME=BASE [NAME=BASE ...]
77-e4c-profiles.sh carries this file verbatim (its heredoc; tests/i/test_rollout.py pins that).
Values come from the environment (the §2 reads): RELEASE, DEPLOYED_SHA, IMAGE_DIGEST,
CONFIG_VERSION, MIGRATION_VERSION, MAINTENANCE_WINDOW, and TENANT2_PREFIX for a two-tenant
base. weights_sha256 / processor_sha256 are computed from <checkout>'s serving-version.json
(R148 formula, runbook §2), and the base's tokenizer/template digests are checked against it.
Prints one line per filled field and the sha256 of every base and filled copy. Exit 2 on any
mismatch, before a file is written."""
import hashlib, json, os, re, sys

repo, dest, pairs = sys.argv[1], sys.argv[2], [a.split("=", 1) for a in sys.argv[3:]]
env = os.environ
sv = json.load(open(f"{repo}/models/marlin2b/serving-version.json"))["model"]
weights = "sha256:" + hashlib.sha256(json.dumps(sv["weight_shard_digests"],
                                                separators=(",", ":")).encode()).hexdigest()
identity = {"source_sha": env["RELEASE"], "deployed_sha": env["DEPLOYED_SHA"],
            "image_digest": env["IMAGE_DIGEST"], "weights_sha256": weights,
            "processor_sha256": sv["processor_config_digest"],
            "migration_version": env["MIGRATION_VERSION"], "config_version": env["CONFIG_VERSION"]}
faults = {"FILL worker unit": "infrx-worker", "FILL valkey unit": "infrx-valkey"}
problems = []
if env["DEPLOYED_SHA"] != env["RELEASE"]:
    problems.append(f"deployed_sha {env['DEPLOYED_SHA']} != RELEASE {env['RELEASE']}")
if not re.fullmatch(r"\d{4}", env["MIGRATION_VERSION"]) or int(env["MIGRATION_VERSION"]) < 26:
    problems.append("migration_version must be 0026 or newer (R147, runbook §2)")
if not env.get("MAINTENANCE_WINDOW", "").strip():
    problems.append("MAINTENANCE_WINDOW is empty")
out, log = {}, []
for name, base in pairs:
    path = f"{repo}/models/marlin2b/profiles/{base}"
    raw = open(path, "rb").read()
    p = json.loads(raw)
    ident, target = p["identity"], p["target"]
    for k, v in (("tokenizer_sha256", sv["tokenizer_digest"]),
                 ("template_sha256", sv["chat_template_digest"])):
        if ident[k] != v:
            problems.append(f"{base}: {k} {ident[k]} != serving-version.json {v}")
    for k, v in identity.items():
        if str(ident.get(k, "")).startswith("FILL"):
            ident[k] = v
            log.append(f"{name} identity.{k} = {v}")
    if str(target["maintenance_window"]).startswith("FILL"):
        target["maintenance_window"] = env["MAINTENANCE_WINDOW"]
        log.append(f"{name} target.maintenance_window = {env['MAINTENANCE_WINDOW']}")
    for i, v in enumerate(target["allowed_fault_targets"]):
        if v in faults:
            target["allowed_fault_targets"][i] = faults[v]
            log.append(f"{name} target.allowed_fault_targets[{i}] = {faults[v]}")
    for i, v in enumerate(target["test_key_ids"]):
        if v.startswith("FILL"):
            t2 = env.get("TENANT2_PREFIX", "")
            if not re.fullmatch(r"[0-9a-f]{8,12}", t2):
                problems.append(f"{base}: test_key_ids[{i}] needs TENANT2_PREFIX (8-12 hex, §5.0)")
            target["test_key_ids"][i] = t2
            log.append(f"{name} target.test_key_ids[{i}] = {t2}")
    if "FILL" in json.dumps(p):
        problems.append(f"{base}: a FILL remains")
    out[name] = (hashlib.sha256(raw).hexdigest(), base, json.dumps(p, indent=1) + "\n")
if problems:
    print("\n".join(f"refused: {x}" for x in problems), file=sys.stderr)
    sys.exit(2)
os.makedirs(dest, exist_ok=True)
for name, (base_sha, base, text) in out.items():
    with open(f"{dest}/{name}", "w") as f:
        f.write(text)
    print(f"base {base_sha} {base}")
    print(f"filled {hashlib.sha256(text.encode()).hexdigest()} {name}")
print("\n".join(log))
PY
python3 -c 'import json,sys; print(json.dumps({"active_key_id_prefixes": sys.argv[1].split(","), "taken_at": sys.argv[2], "source": "G8 credit-transition --dry-run sha256:" + sys.argv[3]}))' \
  "$ACTIVE_PREFIXES" "$KEYS_TAKEN_AT" "$KEYS_SOURCE_SHA256" > "$tmp/keys-certify.json"
mkdir -p "$e4c"; chmod 755 "$e4c"
for f in "$tmp"/*.json; do install -m 0644 "$f" "$e4c/"; done   # replaces only these seven names
( cd "$e4c" && sha256sum E4C-box.json E4C-edge.json E1B-direct.json E1B-box.json E1B-box-forms.json E1B-sop.json keys-certify.json )
v() {  # bench --validate-only in the certify image: no network, no key; exit 3 unless runnable with no error
  local verdict
  verdict=$(docker run --rm --network none -v "$repo:/repo:ro" -v "$e4c:/e4c:ro" -w /repo "infrx-certify:$RELEASE" \
    python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --seed 20260922 \
      --max-tokens 128,512,1024 --retries 0 --key-inventory /e4c/keys-certify.json "$@" --validate-only) || true
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("runnable", d["runnable"], "errors", d["errors"], "blocks", d["blocks"], "warnings", d["warnings"], "projected", d["derived"].get("projected_spend")); sys.exit(0 if d["runnable"] and not d["errors"] else 3)' "$verdict"
}
BOX="--target gateway --model nemostation/marlin-2b --base-url http://127.0.0.1:8001/v1"
echo "== E4C-box (§3, 0.5 x 3600)";  v $BOX --forms video_b64 --rate 0.5 --requests 3600 --dataset-version e4c-1 --profile /e4c/E4C-box.json
echo "== E4C-edge (§3, P4 burst 32)"; v --target gateway --model nemostation/marlin-2b --base-url https://marlin2b.callbill.ai/v1 --forms video_b64 --rate 1000.0 --requests 32 --burst 32 --dataset-version e4c-1-overload --profile /e4c/E4C-edge.json
echo "== E1B-box";       v $BOX --forms video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-box --profile /e4c/E1B-box.json
echo "== E1B-box-forms"; v $BOX --forms upload,video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-forms --profile /e4c/E1B-box-forms.json
echo "== E1B-direct (engine direct: one block + 'blocks do not apply to a local or fake target' expected)"
v --target direct --model marlin2b --base-url http://127.0.0.1:8000/v1 --forms video_b64 -c 1 -n 64 --dataset-version e1b-w1-direct --profile /e4c/E1B-direct.json
echo "== certify --hashes (freeze.json)"
docker run --rm --network none -v "$repo:/repo:ro" -w /repo "infrx-certify:$RELEASE" python tests/integration/backend/certify.py --hashes
