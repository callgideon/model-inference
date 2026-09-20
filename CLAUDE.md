# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`HANDOFF.md` is the current state and plan; read it before changing
anything live.

Per-experiment inference and benchmarking for the Gideon GPU work, plus the
research that sizes it. Five model experiments under `models/`, one directory each:
`deepseek41f`, `deepseek41fnvfp4`, `qwen3827b`, `kimik3`, `marlin2b`; shared
scripts in `models/common/`. `apps/app` is the customer console (Next.js on
Vercel, Supabase auth/DB) and `apps/infrx-api` the AWS-side API gateway and
deployment files; `apps/README.md` is their spec.
Target hardware is 8×B300 HGX nodes (268 GB/GPU as deployed, ~2,144 GB per
node; 288 GB is the GB300 NVL72 figure, not ours) with local NVMe, plus AWS
p6 nodes for burst. Development happens on AWS GPU instances until the
bare-metal cluster exists.

## Branch convention

`main` carries `models/` (shared tooling in `models/common/`, every
experiment's metadata and scripts), `apps/`, and `research/`. Each experiment
has a branch of the same name (`marlin2b`, `kimik3`, …) where that
experiment's serve configs, benchmarks and results diverge. After every change to
`main`, merge `main` into each experiment branch (`git merge main`; it
fast-forwards until the branch has commits of its own). Never merge an
experiment branch back into `main`; land tooling fixes on `main` directly.
Measured results go in `<exp>/results/` on the branch, with a short
"Measured" note in `research/models/<exp>/README.md` on `main`.

## Commands

```bash
./models/marlin2b/download.sh          # weights: S3 mirror if S3_BUCKET is exported, else Hugging Face
SOURCE=hf ./models/qwen3827b/download.sh # force Hugging Face
DEST=/data/w ./models/kimik3/download.sh # somewhere other than $WEIGHTS_ROOT (/mnt/nvme)
```

Each `models/<exp>/download.sh` is a one-line shim calling
`models/common/download.sh`; put logic in `common/`, data in `model.env`. It compares
free space in bytes on purpose (`SIZE_GB` is decimal GB, `df` reports GiB).
`marlin2b` is a gated Hugging Face repo: `HF_TOKEN` must be exported and the
account approved. `S3_DIR` in `model.env` deliberately differs from the
directory name; the mirror predates the names.

There is no build, lint or test suite; scripts are bash with
`set -euo pipefail` plus small Python clients. On the `marlin2b` branch:
`./models/marlin2b/serve.sh` (vLLM in docker), `models/marlin2b/smoke.py`
(one request), `bench.py` (load test), `reference.py` (transformers path),
`tokens.py` (video token budget); `apps/infrx-api/gateway.py` is the public
OpenAI-compatible gateway (systemd + Caddy, `apps/infrx-api/deploy/`). The dev box is a
`g6e.2xlarge` (`i-0e8449a4ffca29bab`, us-east-1d) with the DLAMI's PyTorch
env at `/opt/pytorch` and NVMe at `/opt/dlami/nvme`; see `models/marlin2b/README.md`.

## AWS access from this host

The shell exports stale `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` that fail
with `InvalidClientTokenId`. The working credentials are the default profile
in `~/.aws/credentials` (user `sofia-admin`, account 641134885443,
us-east-1), so run the CLI as
`env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws …`.
There is no `session-manager-plugin`; use `aws ssm send-command` for remote
execution, or SSH with the `rey-admin` key pair. GPU capacity in us-east-1 is
scarce: `g6e` needs AZ retries (1d worked), `p4d`/`p5`/`p6-b200`/`p6-b300`
are offered. The S3 weights mirror is `s3://llm-bootcamp-641134885443`
under `weights/` (currently only `deepseek-v41`).

## research/ conventions

`research/METHODOLOGY.md` is the single source of formulas, units and pinned
inputs; every number elsewhere links to it or to a primary source. Rules that
took several fact-check passes to establish, so do not regress them:

- Capacities as deployed, dense TFLOPS only, bytes computed per tensor group
  (NVFP4 = 0.5625 B/param, MXFP4 = 0.53125), GiB unless a column says GB.
- Unknowns are marked `⚠️ TO BE VERIFIED` with the estimation method;
  estimates are labelled `est.`, measurements `meas.` with a source.
- Every doc ends with a verification/sweep/audit log; append to it, do not
  rewrite history.
- `research/matrix/pairs.json` is the machine-readable contract for the 40
  (model, GPU) pairs; `output_tokens_per_s_per_gpu` is decode-only.
- Prices come only from `research/cross-cutting/cloud-pricing.md`.

Tree: `gpus/` (8 GPU references), `cross-cutting/` (kernels, quantization,
engines, pricing, serving optimizations, InferenceX API), `models/<exp>/`
(architecture + one doc per GPU + README), `matrix/` (fit, cost,
optimizations, recommendations), `scaling/` (bare-metal cluster through
autoscaling and cold start, blueprint `10`, playbook `11`, providers `12`),
`platform/` (the closed-loop distillation platform: goal `00`, components
`01`–`09`, roadmap `10`, thesis memo `11`). Each subtree has a README index.

Findings that constrain engineering decisions: DeepSeek-V4.1-Flash experts
are MXFP4 and the NVFP4 build is larger with no published speedup; its
890 B/token KV cache only executes on sm_100/sm_103; Kimi-K3 needs 32 GPUs
minimum on H100/A100; Marlin-2B loads in vLLM only with
`--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'`
and ships no MTP weights.
