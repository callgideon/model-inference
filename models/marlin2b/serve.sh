#!/usr/bin/env bash
# Serve Marlin-2B on one GPU with vLLM's OpenAI-compatible server.
#
# Marlin ships as `MarlinForConditionalGeneration`, a thin subclass of Qwen3.5's
# class that vLLM does not register; --hf-overrides remaps it to the native
# Qwen3.5 loader (research/models/marlin2b/architecture.md §8.1). The custom
# .caption()/.find() helpers are transformers-only, so callers send the canonical
# prompt themselves — see smoke.py, which reads it from modeling_marlin.py.
#
#   ./marlin2b/serve.sh                      # foreground, port 8000
#   PORT=8001 GPU=1 ./marlin2b/serve.sh      # another GPU/port
#   ./marlin2b/serve.sh --max-num-seqs 64    # extra vLLM flags pass through
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../common/env.sh
. "$here/../common/env.sh"
# shellcheck source=model.env
. "$here/model.env"

WEIGHTS=${WEIGHTS:-$WEIGHTS_ROOT/$EXP}
PORT=${PORT:-8000}          # BIND=0.0.0.0 to expose beyond localhost (the gateway fronts it)
GPU=${GPU:-0}
IMAGE=${IMAGE:-vllm/vllm-openai:nightly}   # Qwen3.5 needs vLLM main (base model card)
# 240 frames x 196 tokens per 2-frame temporal patch at 448x448 = ~23.5K video
# tokens, so 32K covers the longest input the model was trained on and keeps the
# KV budget for batching instead of an idle 262K window.
MAX_MODEL_LEN=${MAX_MODEL_LEN:-32768}

test -f "$WEIGHTS/config.json" || { echo "no weights at $WEIGHTS — run ./marlin2b/download.sh" >&2; exit 1; }

exec docker run --rm --name "marlin2b-$PORT" --gpus "\"device=$GPU\"" --ipc=host \
  -p "${BIND:-127.0.0.1}:$PORT:8000" \
  -v "$WEIGHTS:/model:ro" \
  -e VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-INFO}" \
  "$IMAGE" /model \
  --served-model-name marlin2b \
  --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "${GPU_MEM:-0.90}" \
  --limit-mm-per-prompt '{"video":1,"image":4}' \
  --dtype bfloat16 \
  "$@"
