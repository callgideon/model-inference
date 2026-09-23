#!/usr/bin/env bash
# Serve Marlin-2B on one GPU with vLLM's OpenAI-compatible server.
#
# Marlin ships as `MarlinForConditionalGeneration`, a thin subclass of Qwen3.5's
# class that vLLM does not register; --hf-overrides remaps it to the native
# Qwen3.5 loader (research/models/marlin2b/architecture.md §8.1). The custom
# .caption()/.find() helpers are transformers-only, so callers send the canonical
# prompt themselves — see smoke.py, which reads it from modeling_marlin.py.
#
# The serving version is pinned (W3, serving-version.json beside this file): the image
# is pulled by registry digest, the engine listens on loopback only and takes no API
# key (the gateway and the worker are its only clients), and two settings are read
# under the names the gateway reads, so each fact has one place:
#
#   ENGINE_MAX_NUM_SEQS    concurrent sequences (08 §5); 8 until measured. ⚠️ the pilot
#                          box ran 32 (I1B); measure/concurrency.sh decides.
#   PROCESSING_CACHE_DIR   prepared-media root (R61 (2)): mounted read-only at the same
#                          path and passed as --allowed-local-media-path. Unset: no
#                          local media, and the worker refuses every video.
#
# Both EOS ids [248044, 248046] are re-supplied by every request (stop_token_ids).
#
#   ./marlin2b/serve.sh                          # foreground, 127.0.0.1:8000
#   PORT=8001 GPU=1 ./marlin2b/serve.sh          # another GPU/port
#   ./marlin2b/serve.sh --enable-log-requests    # other vLLM flags pass through
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../common/env.sh
. "$here/../common/env.sh"
# shellcheck source=model.env
. "$here/model.env"

WEIGHTS=${WEIGHTS:-$WEIGHTS_ROOT/$EXP}
PORT=${PORT:-8000}
GPU=${GPU:-0}
# vllm/vllm-openai:nightly-a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b, resolved 2026-09-22
# (Qwen3.5 needs vLLM main; the 2026-09-19 rows were measured on this build).
# The environment cannot replace the image: `unset` first, so this `${IMAGE:-…}` default
# (the form I0's preflight reads) is the only value.
unset IMAGE
IMAGE=${IMAGE:-vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42}
# 240 frames x 196 tokens per 2-frame temporal patch at 448x448 = ~23.5K video
# tokens, so 32K covers the longest input the model was trained on and keeps the
# KV budget for batching instead of an idle 262K window.
# Recorded in serving-version.json, so not an environment knob either.
MAX_MODEL_LEN=32768
ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-8}

test -f "$WEIGHTS/config.json" || { echo "no weights at $WEIGHTS — run ./marlin2b/download.sh" >&2; exit 1; }

# A pinned setting has one source: the caller cannot pass a second, conflicting value.
for arg in "$@"; do
  case "$arg" in
    --max-num-seqs*|--allowed-local-media-path*|--api-key*)
      echo "serve.sh: ${arg%%=*} is pinned here; set ENGINE_MAX_NUM_SEQS or PROCESSING_CACHE_DIR" >&2
      exit 2 ;;
  esac
done
[[ $ENGINE_MAX_NUM_SEQS =~ ^[1-9][0-9]*$ ]] || {
  echo "serve.sh: ENGINE_MAX_NUM_SEQS must be a positive integer" >&2; exit 2; }

media=() flags=()
if [ -n "${PROCESSING_CACHE_DIR:-}" ]; then
  # The engine may open any file under it by file://: absolute and canonical (no `..`,
  # `//` or trailing `/` to walk out of the checks below; realpath of a relative path is
  # absolute, so it never compares equal), never a system directory or the weights mount.
  [ "$(realpath -m -s -- "$PROCESSING_CACHE_DIR")" = "$PROCESSING_CACHE_DIR" ] || {
    echo "serve.sh: PROCESSING_CACHE_DIR must be an absolute, canonical path" >&2; exit 2; }
  case "$PROCESSING_CACHE_DIR" in
    /|/etc|/etc/*|/home|/home/*|/model|/model/*)
      echo "serve.sh: PROCESSING_CACHE_DIR may not be $PROCESSING_CACHE_DIR" >&2; exit 2 ;;
  esac
  test -d "$PROCESSING_CACHE_DIR" || { echo "serve.sh: no directory at PROCESSING_CACHE_DIR" >&2; exit 1; }
  media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR:ro")
  flags=(--allowed-local-media-path "$PROCESSING_CACHE_DIR")
fi

exec docker run --rm --name "marlin2b-$PORT" --gpus "\"device=$GPU\"" --ipc=host \
  -p "127.0.0.1:$PORT:8000" \
  -v "$WEIGHTS:/model:ro" "${media[@]}" \
  -e VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-INFO}" \
  "$IMAGE" /model \
  --served-model-name marlin2b \
  --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len "$MAX_MODEL_LEN" \
  --max-num-seqs "$ENGINE_MAX_NUM_SEQS" \
  --gpu-memory-utilization 0.90 \
  --limit-mm-per-prompt '{"video":1,"image":4}' \
  --dtype bfloat16 \
  "${flags[@]}" "$@"
