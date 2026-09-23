# Step 2: update the measurement checkout to the integration head from the bundle.
set -euo pipefail
mkdir -p /opt/dlami/nvme/w4-logs
B=/opt/dlami/nvme/w4-logs/backend-impl-37a0d8a.bundle
aws s3 cp s3://llm-bootcamp-641134885443/w4/backend-impl-37a0d8a.bundle "$B" --region us-east-1 --only-show-errors
sha256sum "$B"
G="git -c safe.directory=* -C /opt/dlami/nvme/w3-checkout"
$G bundle verify "$B" 2>&1 | tail -1
echo "before=$($G rev-parse HEAD) branch=$($G rev-parse --abbrev-ref HEAD)"
$G status --porcelain | head -5
$G fetch -q "$B" +refs/heads/claude/backend-impl:refs/remotes/bundle/backend-impl
$G checkout -q --detach 37a0d8ae3671eb9f40db04669b9d0a3fa0320fe8
echo "after=$($G rev-parse HEAD)"
$G status --porcelain | head -5
cd /opt/dlami/nvme/w3-checkout/models/marlin2b
ls measure/
grep -c 'ENGINE_STATE' measure/concurrency.sh
sha256sum measure/candidate.sh measure/parity.py measure/decide.py measure/concurrency.sh serve.sh bench.py reference.py
