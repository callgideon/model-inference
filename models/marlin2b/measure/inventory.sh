#!/usr/bin/env bash
# W3 measurement 1 of 3 - READ-ONLY inventory of the running Marlin engine.
#
# What it answers (every line is `key=value` or a raw quoted block under a `### section`):
#   * the running image's identity against the pin in serving-version.json (full digest,
#     closing the ⚠️ that I1B could only see 12 hex digits of);
#   * the flags the container was started with, its port binding and mounts;
#   * the engine's /version, /health and served model;
#   * KV capacity as the engine itself reports it (E1B protocol: num_gpu_blocks x block
#     size, from /metrics `vllm:cache_config_info` and the start-up log) - never computed;
#   * GPU memory, and the sha256 of the served bytes (and, only if HF_TOKEN is exported,
#     the registry's own oids for the same files - the token is sent on stdin, never argv).
#
# It changes nothing: docker inspect/logs, GETs to the engine, sha256sum, nvidia-smi.
# Runs as root or as a docker-group user. Base64-wrappable for `aws ssm send-command`:
#   CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=/opt/dlami/nvme/marlin2b \
#     bash inventory.sh
set -uo pipefail    # not -e: one failing probe must not hide the others

CONTAINER=${CONTAINER:-marlin2b-8000}
ENGINE=${ENGINE:-http://127.0.0.1:8000}
WEIGHTS=${WEIGHTS:-/opt/dlami/nvme/marlin2b}
# serving-version.json runtime_image: the manifest list, and its linux/amd64 image config
# (the id a non-containerd docker image store reports for the same pull).
PIN_INDEX=sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42
PIN_CONFIG=sha256:38e8c0e60293aefc23edee5c074107e44b89d64f26d5399cd2f5956801b6ef24

section() { printf '\n### %s\n' "$*"; }

section when
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)"
echo "docker=$(docker version --format '{{.Server.Version}}' 2>&1) storage=$(docker info --format '{{.Driver}}' 2>&1)"

section image
image=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>&1)
echo "container=$CONTAINER container_image=$image"
docker image inspect --format 'image_id={{.Id}} repo_digests={{.RepoDigests}} repo_tags={{.RepoTags}} created={{.Created}}' "$image" 2>&1
echo "vllm_build_commit=$(docker image inspect --format '{{index .Config.Labels "ai.vllm.build.commit"}}' "$image" 2>&1)"
echo "pin_index=$PIN_INDEX pin_amd64_config=$PIN_CONFIG"
id=$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null)
case "$image $id" in
  *"$PIN_INDEX"*|*"$PIN_CONFIG"*) echo "image_equals_pin=yes" ;;
  *) echo "image_equals_pin=no" ;;
esac

section "flags as served"
echo "args=$(docker inspect --format '{{json .Args}}' "$CONTAINER" 2>&1)"
echo "ports=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$CONTAINER" 2>&1)"
echo "mounts=$(docker inspect --format '{{range .Mounts}}{{.Source}}:{{.Destination}}:{{.RW}} {{end}}' "$CONTAINER" 2>&1)"
echo "started=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER" 2>&1)"

section engine
echo "version=$(curl -sS -m 5 "$ENGINE/version" 2>&1)"
echo "health_status=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "$ENGINE/health" 2>&1)"
echo "models=$(curl -sS -m 5 "$ENGINE/v1/models" 2>&1 | head -c 600)"

section "kv capacity (engine-reported)"
curl -sS -m 5 "$ENGINE/metrics" 2>&1 | grep -E '^vllm:cache_config_info|^vllm:(num_gpu_blocks|kv_cache|gpu_cache)' | head -20
docker logs "$CONTAINER" 2>&1 | grep -E -i 'GPU KV cache size|Maximum concurrency|num_gpu_blocks|# GPU blocks|block_size|Available KV cache memory|mamba' | head -40

section gpu
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv 2>&1

section "served bytes (sha256)"
( cd "$WEIGHTS" && sha256sum model-00001-of-00002.safetensors model-00002-of-00002.safetensors \
    tokenizer.json chat_template.jinja generation_config.json config.json ) 2>&1

section "registry oids"
if [ -n "${HF_TOKEN:-}" ]; then
  printf 'Authorization: Bearer %s\n' "$HF_TOKEN" \
    | curl -sS -m 30 -H @- https://huggingface.co/api/models/NemoStation/Marlin-2B/tree/main \
    | python3 -c 'import json,sys; [print(e["path"], e["lfs"]["oid"], e["lfs"]["size"]) for e in json.load(sys.stdin) if e.get("lfs")]' 2>&1
else
  echo "not_run=HF_TOKEN unset (research/workloads/marlin-sop.md §1.3)"
fi
