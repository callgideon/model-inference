#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L3 (box): the Lab release image, built from the checked-out
# RELEASE with the runtime's Dockerfile (the Lab code is the same package), tagged
# infrx-lab:<RELEASE>; its content-addressed id goes to /etc/infrx-lab/image, from which the
# control and role env files take INFRX_LAB_IMAGE / INFRX_IMAGE (never the App's INFRX_IMAGE).
#   TIMEOUT_S=3600 infra/rollout/ssm.sh infra/lab/rollout/steps/20-lab-image.sh RELEASE=<40 hex>
# Idempotent: an existing infrx-lab:<RELEASE> is reused. Exit 2 = not RELEASE; 4 = no image id.
# Rollback: nothing to undo (an image; the App's is untouched).
set -euo pipefail
STEP=20-lab-image
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
at_release
tag=infrx-lab:$RELEASE
id=$(docker image inspect --format '{{.Id}}' "$tag" 2>/dev/null) || id=   # absent: build it
if [ -z "$id" ]; then
  say "building $tag"
  id=$(docker build --provenance=false -q -f "$repo/apps/infrx-api/deploy/Dockerfile" -t "$tag" \
         "$repo/apps/infrx-api")
fi
[[ $id =~ ^sha256:[0-9a-f]{64}$ ]] || die 4 "docker gave no image id for $tag"
install -d -m 0755 "$LAB_ETC"
tmp=$(mktemp "$IMAGE_FILE.XXXXXX"); echo "$id" > "$tmp"; chmod 0644 "$tmp"; mv -f "$tmp" "$IMAGE_FILE"
say "Lab image $id ($tag) -> $IMAGE_FILE"
