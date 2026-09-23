# Ship E1B's box outputs: e1b-logs/ (L0 log, L8 dir, nvsampler.py) and the checkout's results rows.
set -euo pipefail
T=/opt/dlami/nvme/w4-logs/e1b-box-20260923T214244Z.tar.gz
git -c safe.directory='*' -C /opt/dlami/nvme/w3-checkout status --porcelain
tar -C /opt/dlami/nvme -czf $T e1b-logs w3-checkout/models/marlin2b/results/bench.jsonl w3-checkout/models/marlin2b/results/raw
sha256sum $T; ls -l $T
aws s3 cp $T s3://llm-bootcamp-641134885443/w4/e1b-box-20260923T214244Z.tar.gz --region us-east-1 --only-show-errors
echo uploaded
