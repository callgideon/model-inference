# Step 3: build sop-synth-v1 into the corpus cache with the pinned ffmpeg, then verify
# against the COMMITTED manifest (build rewrites it; the rewrite is kept as a log and reverted).
set -uo pipefail
cd /opt/dlami/nvme/w3-checkout
export CORPUS_CACHE=/opt/dlami/nvme/w3-corpus PYTHONDONTWRITEBYTECODE=1
L=/opt/dlami/nvme/w4-logs
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) head=$(git -c safe.directory='*' rev-parse --short HEAD)"
/opt/pytorch/bin/python3 models/marlin2b/corpus-synth/synth.py build; echo "build_exit=$?"
cp models/marlin2b/corpus-synth/manifest.json $L/synth-manifest.after-build.json
git -c safe.directory='*' diff --stat -- models/marlin2b/corpus-synth/manifest.json
git -c safe.directory='*' checkout -- models/marlin2b/corpus-synth/manifest.json
git -c safe.directory='*' status --porcelain
echo "### verify (committed manifest)"
/opt/pytorch/bin/python3 models/marlin2b/corpus-synth/synth.py verify; echo "verify_exit=$?"
echo "### sha256 of the rendered clips"
(cd $CORPUS_CACHE && sha256sum sop-synth-v1/*.mp4)
echo "### corpus/build.py verify (licensed corpus, candidate.sh's precondition)"
/opt/pytorch/bin/python3 models/marlin2b/corpus/build.py verify 2>&1 | tail -3; echo "corpus_verify_exit=${PIPESTATUS[0]}"
echo "### parity.py --check"
/opt/pytorch/bin/python3 models/marlin2b/measure/parity.py --check --cache $CORPUS_CACHE; echo "parity_check_exit=$?"
echo "done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
