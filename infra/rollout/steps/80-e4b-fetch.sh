#!/usr/bin/env bash
# Box, read-only for the run (E4C-runbook §7): pack one certify run's report.json, certify.log and work/
# and upload it to the S3 mirror as w4/e4b-box/<RUN>.tgz; the coordinator host then unpacks it into
# models/marlin2b/results/E4C-box-<release7>/run<N>-<RUN>/ (certify-window.sh fetch). session-03's
# e4b-fetch3.sh (kept there unchanged: run3's record) with the run as its argument instead of run3's dir.
#   infra/rollout/ssm.sh infra/rollout/steps/80-e4b-fetch.sh RUN=<the certify run's UTC dir name>
# Prints the object URL and the archive's sha256 (the host checks its copy against it).
set -euo pipefail
: "${RUN:?the certify run (e4c-certify.sh prints out=/opt/dlami/nvme/e4b/<RUN>)}"
[[ $RUN =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "RUN is a certify run's UTC directory name" >&2; exit 2; }
O=${E4B_ROOT:-/opt/dlami/nvme/e4b}/$RUN
[ -s "$O/report.json" ] || { echo "no report.json in $O (the run has not finished)" >&2; exit 3; }
tgz=$(mktemp --suffix=.tgz); trap 'rm -f "$tgz"' EXIT
parts=(report.json certify.log); [ -d "$O/work" ] && parts+=(work)
tar -czf "$tgz" -C "$O" "${parts[@]}"
url=s3://${BUCKET:-llm-bootcamp-641134885443}/w4/e4b-box/$RUN.tgz
aws s3 cp "$tgz" "$url" --region us-east-1 --only-show-errors
echo "uploaded $url $(sha256sum < "$tgz" | cut -d' ' -f1)"
ls -la "$O" | head; du -sh "$O/work" 2> /dev/null || true
grep -E '^exit [0-9]+' "$O/certify.log" | tail -n 1 || true
