# Step 6a: E1B cell L0 (latency floor) on the engine candidate.sh's restore just started:
# bench.py --target direct -c 1 -n 12 --subset full, the frozen profile, GPU sampled through
# bench's --sampler hook (nvidia-smi), results in the checkout's results/bench.jsonl + raw/.
set -uo pipefail
: "${EUTC:?}"
NVME=/opt/dlami/nvme; REPO=$NVME/w3-checkout; PY=/opt/pytorch/bin/python3; D=$NVME/e1b-logs
mkdir -p $D
cat > $D/nvsampler.py <<'PY'
"""bench.py --sampler for the pilot box: host load and GPU memory/utilisation (nvidia-smi).
A failed read leaves the series absent for that sample - never a made-up value."""
import os
import subprocess


def make():
    def sample():
        out = {}
        try:
            out["load1"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        try:
            used, util = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.split(",")[:2]
            out["gpu_mem_mib"], out["gpu_util_pct"] = float(used), float(util)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return out
    return sample
PY
sha256sum $D/nvsampler.py
cd $REPO
label=e1b-L0-$EUTC
echo "label=$label utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) repo_sha=$(git -c safe.directory='*' rev-parse --short HEAD)"
echo "engine_started=$(docker inspect --format '{{.State.StartedAt}}' marlin2b-8000) image=$(docker inspect --format '{{.Image}}' marlin2b-8000)"
echo "engine_args=$(docker inspect --format '{{json .Args}}' marlin2b-8000)"
echo "### engine counters before (a fresh engine has served nothing)"
curl -sS -m 5 http://127.0.0.1:8000/metrics | grep -E '^vllm:(prefix_cache_queries|prefix_cache_hits|mm_cache_queries|mm_cache_hits|request_success)' 
PYTHONPATH=$D CORPUS_CACHE=$NVME/w3-corpus PYTHONDONTWRITEBYTECODE=1 $PY models/marlin2b/bench.py --target direct \
  --base-url http://127.0.0.1:8000/v1 --corpus models/marlin2b/corpus/manifest.json --subset full \
  --seed 20260922 --max-tokens 128,512,1024 --engine-state restarted -c 1 -n 12 \
  --dataset-version e1b-2026-09-22 --profile-version v1 --label $label \
  --out models/marlin2b/results/bench.jsonl --raw models/marlin2b/results/raw/$label.jsonl \
  --sampler nvsampler:make --sample-interval 2
echo "bench_exit=$?"
echo "### report"
$PY models/marlin2b/bench.py --report models/marlin2b/results/bench.jsonl
echo "### engine counters after"
curl -sS -m 5 http://127.0.0.1:8000/metrics | grep -E '^vllm:(prefix_cache_queries|prefix_cache_hits|mm_cache_queries|mm_cache_hits|request_success)'
echo "done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
