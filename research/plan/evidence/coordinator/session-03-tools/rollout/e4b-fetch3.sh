#!/usr/bin/env bash
# Coordinator op (read-only export): pack run3's report/log/work and upload to the S3 mirror.
set -euo pipefail
O=/opt/dlami/nvme/e4b/20260924T202924Z
cd "$O" && { tar czf /tmp/e4b-20260924T202924Z.tgz report.json certify.log work 2>/dev/null || tar czf /tmp/e4b-20260924T202924Z.tgz report.json certify.log; }
aws s3 cp /tmp/e4b-20260924T202924Z.tgz s3://llm-bootcamp-641134885443/w4/e4b-box/20260924T202924Z.tgz --region us-east-1 --only-show-errors && echo "uploaded s3://llm-bootcamp-641134885443/w4/e4b-box/20260924T202924Z.tgz"
rm -f /tmp/e4b-20260924T202924Z.tgz
ls -la "$O" | head; du -sh "$O/work" 2>/dev/null; grep -E '^exit [0-9]+' "$O/certify.log" | tail -1
