# Ship one run directory (plus its transcript) to the project bucket for the branch.
set -euo pipefail
: "${DIR:?}" "${LOGF:=}"
N=$(basename "$DIR")
T=/opt/dlami/nvme/w4-logs/$N.tar.gz
tar -C /opt/dlami/nvme -czf "$T" "$(realpath --relative-to=/opt/dlami/nvme "$DIR")" ${LOGF:+"$(realpath --relative-to=/opt/dlami/nvme "$LOGF")"}
sha256sum "$T"; ls -l "$T"
aws s3 cp "$T" "s3://llm-bootcamp-641134885443/w4/$N.tar.gz" --region us-east-1 --only-show-errors
echo "uploaded=s3://llm-bootcamp-641134885443/w4/$N.tar.gz"
du -sh "$DIR"; find "$DIR" -type f -size +1M -exec ls -l {} \;
