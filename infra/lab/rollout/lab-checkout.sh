#!/usr/bin/env bash
# LAB-DEPLOY-PREP step L0 (box; not under steps/ — it runs BEFORE the checkout carries lib.sh): the checkout becomes RELEASE so the Lab ships from it (runbook 08
# §4: "the Lab ships from the same checkout"). Fetches the integration branch, refuses when the
# engine's serve.sh or its pin differ from the running checkout (a restart of marlin2b-vllm would
# then run a script the installed unit does not match — see 40-checkout.sh), then 40-checkout.
#   infra/rollout/ssm.sh infra/lab/rollout/lab-checkout.sh RELEASE=<40 hex>
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=${REPO:-/home/ubuntu/model-inference}
g() { sudo -u ubuntu git -C "$repo" "$@"; }
g fetch --quiet origin "${BRANCH:-claude/consumer-v1}"
g cat-file -e "$RELEASE^{commit}" || { echo "RELEASE $RELEASE is not on origin/${BRANCH:-claude/consumer-v1}" >&2; exit 2; }
if [ "$(g rev-parse HEAD)" = "$RELEASE" ]; then echo "already at $RELEASE"; exit 0; fi
g diff --quiet HEAD "$RELEASE" -- models/marlin2b/serve.sh models/marlin2b/serving-version.json \
  || { echo "the engine script or its pin differ between HEAD and RELEASE: a consumer window (30-pause … 50-install), not a Lab checkout" >&2; exit 2; }
RELEASE="$RELEASE" bash "$repo/infra/rollout/steps/40-checkout.sh"
