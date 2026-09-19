#!/usr/bin/env bash
# Fetch one experiment's weights. Called by each experiment's own download.sh,
# which passes its directory — the logic lives here so there is one copy to fix
# rather than one per experiment.
#
# S3 first, Hugging Face second. On an AWS node the S3 copy is in-region over a
# gateway endpoint: free, and far faster than pulling from HF. Off AWS — a
# bare-metal box — S3 may be unreachable or uncredentialed, so HF is a fallback
# rather than a failure.
#
#   ./download.sh                  # auto: S3 if reachable, else HF
#   SOURCE=hf ./download.sh        # force Hugging Face
#   DEST=/data/w ./download.sh     # somewhere other than WEIGHTS_ROOT
set -euo pipefail

exp_dir=${1:?usage: download.sh <experiment-dir>}
common_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=env.sh
. "$common_dir/env.sh"
# shellcheck source=/dev/null
. "$exp_dir/model.env"

DEST=${DEST:-$WEIGHTS_ROOT/$EXP}
SOURCE=${SOURCE:-auto}
mkdir -p "$DEST"

cat <<EOF
experiment : $EXP
hf repo    : $HF_REPO
s3         : ${S3_BUCKET:+s3://$S3_BUCKET/$S3_PREFIX/$S3_DIR}${S3_BUCKET:-<unset, will use Hugging Face>}
dest       : $DEST  (~${SIZE_GB}GB)
source     : $SOURCE
EOF

# Checked up front rather than 400GB in: a disk-full failure halfway through is
# indistinguishable from a network failure and wastes the whole transfer.
need=$(( SIZE_GB + 20 ))
avail=$(df -BG --output=avail "$DEST" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ "${avail:-0}" -lt "$need" ]; then
  echo "FATAL: need ~${need}GB free at $DEST, have ${avail:-0}GB" >&2
  exit 1
fi

from_s3() {
  [ -n "$S3_BUCKET" ] || { echo "S3_BUCKET unset — skipping S3"; return 1; }
  command -v s5cmd >/dev/null 2>&1 || { echo "s5cmd not installed"; return 1; }
  s5cmd ls "s3://$S3_BUCKET/$S3_PREFIX/$S3_DIR/" >/dev/null 2>&1 || {
    echo "S3 prefix unreachable or empty"; return 1; }
  echo "--- pulling from S3"
  s5cmd --numworkers "${WORKERS:-64}" cp "s3://$S3_BUCKET/$S3_PREFIX/$S3_DIR/*" "$DEST/"
}

from_hf() {
  if [ "${GATED:-no}" = yes ] && [ -z "${HF_TOKEN:-}" ]; then
    echo "FATAL: $HF_REPO is gated — accept the licence on HF and export HF_TOKEN" >&2
    return 1
  fi
  echo "--- pulling from Hugging Face"
  python3 -c 'import huggingface_hub' 2>/dev/null || pip install -q "huggingface_hub[hf_transfer]"
  HF_HUB_ENABLE_HF_TRANSFER=1 hf download "$HF_REPO" \
    --local-dir "$DEST" --max-workers "${WORKERS:-16}"
}

case "$SOURCE" in
  s3)   from_s3 || { echo "FATAL: SOURCE=s3 but S3 is unavailable" >&2; exit 1; } ;;
  hf)   from_hf ;;
  auto) from_s3 || { echo "falling back to Hugging Face"; from_hf; } ;;
  *)    echo "unknown SOURCE=$SOURCE (want s3|hf|auto)" >&2; exit 1 ;;
esac

du -sh "$DEST"
echo "done: $EXP -> $DEST"
