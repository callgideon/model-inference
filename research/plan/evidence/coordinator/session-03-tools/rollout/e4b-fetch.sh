#!/usr/bin/env bash
set -euo pipefail
cd /opt/dlami/nvme/e4b/20260924T165408Z && tar czf /tmp/e4b-20260924T165408Z.tgz report.json certify.log work 2>/dev/null || tar czf /tmp/e4b-20260924T165408Z.tgz report.json certify.log
aws s3 cp /tmp/e4b-20260924T165408Z.tgz s3://llm-bootcamp-641134885443/w4/e4b-box/20260924T165408Z.tgz --region us-east-1 --only-show-errors && echo "uploaded s3://llm-bootcamp-641134885443/w4/e4b-box/20260924T165408Z.tgz"; rm -f /tmp/e4b-20260924T165408Z.tgz
ls -la /opt/dlami/nvme/e4b/20260924T165408Z | head; du -sh /opt/dlami/nvme/e4b/20260924T165408Z/work 2>/dev/null
