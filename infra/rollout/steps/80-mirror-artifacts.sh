#!/usr/bin/env bash
# I8 slice 5, box, read-only for the service: mirror the SERVED model artifacts - weights,
# processor, tokenizer, chat template, configs - to the approved durable prefix, with a
# digest manifest (infra/runbooks/artifacts.py) that also records the engine image by
# registry digest, the runtime image id, the release it is built from and the env NAMES.
# Instance NVMe is not durable (a stop wipes it); this is the copy a restore fetches.
# Refused (exit 2) when the directory does not serve the bytes serving-version.json pins.
# Needs RELEASE (the checkout) and MIRROR_URL=s3://<bucket>/<prefix>/ - the prefix the
# bucket's owner approved (infra/README.md §6: llm-bootcamp-641134885443 is another
# project's bucket), no default. The upload is verified by listing it back (sizes) and
# re-reading the manifest (sha256). Cost: one ~5 GB PUT set in-region.
set -euo pipefail
: "${RELEASE:?the release commit}" "${MIRROR_URL:?s3://bucket/prefix/ the owner approved}"
[[ $MIRROR_URL =~ ^s3://[a-z0-9.-]+/([A-Za-z0-9._-]+/)+$ ]] || { echo "MIRROR_URL must be s3://bucket/prefix/ (a prefix, ending in /)" >&2; exit 2; }
repo=${REPO:-/home/ubuntu/model-inference}
weights=${WEIGHTS:-/opt/dlami/nvme/marlin2b}
[ "$(git -c safe.directory="$repo" -C "$repo" rev-parse HEAD)" = "$RELEASE" ] || { echo "the checkout is not $RELEASE" >&2; exit 2; }
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
python3 "$repo/infra/runbooks/artifacts.py" manifest --weights "$weights" \
  --serving-version "$repo/models/marlin2b/serving-version.json" --release "$RELEASE" \
  --env-file "${ENV_FILE:-/etc/marlin2b-gateway.env}" --serve-script "$repo/models/marlin2b/serve.sh" --out "$out"
aws() { command aws --region us-east-1 "$@"; }
start=$(date +%s)
aws s3 sync --only-show-errors --no-progress --exclude '.cache/*' "$weights/" "${MIRROR_URL}weights/"
aws s3 cp --only-show-errors "$out/manifest.json" "${MIRROR_URL}manifest.json"
aws s3 cp --only-show-errors "$out/SHA256SUMS" "${MIRROR_URL}SHA256SUMS"
echo "uploaded in $(( $(date +%s) - start )) s"
# verify: every manifest file listed back with its size, and the manifest reads back equal
bucket=${MIRROR_URL#s3://}; bucket=${bucket%%/*}; prefix=${MIRROR_URL#s3://$bucket/}
aws s3api list-objects-v2 --bucket "$bucket" --prefix "${prefix}weights/" \
  --query 'Contents[].[Key,Size]' --output text > "$out/listed"
python3 - "$out/manifest.json" "$out/listed" "${prefix}weights/" <<'PY'
import json, sys
files = json.load(open(sys.argv[1]))["files"]
listed = {}
for line in open(sys.argv[2]):
    if line.strip() and line.strip() != "None":
        key, size = line.rsplit(None, 1)
        listed[key[len(sys.argv[3]):]] = int(size)
bad = [f["path"] for f in files if listed.get(f["path"]) != f["bytes"]]
print(f"listed back: {len(listed)} objects, {len(files) - len(bad)}/{len(files)} match the manifest")
sys.exit(1 if bad else 0)
PY
aws s3 cp --only-show-errors "${MIRROR_URL}manifest.json" "$out/readback.json"
cmp -s "$out/manifest.json" "$out/readback.json" || { echo "manifest read back DIFFERENT from the one uploaded" >&2; exit 1; }
echo "manifest read back equal: $(sha256sum < "$out/manifest.json" | cut -c1-64)"
echo "== access (read-only; 'unknown' when the role may not read it)"
aws s3api get-bucket-versioning --bucket "$bucket" --output text 2>/dev/null || echo "versioning: unknown"
aws s3api get-public-access-block --bucket "$bucket" --output text 2>/dev/null || echo "public access block: unknown"
aws s3api get-bucket-encryption --bucket "$bucket" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' --output text 2>/dev/null || echo "default encryption: unknown"
