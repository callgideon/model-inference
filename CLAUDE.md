# CLAUDE.md

Repository conventions for all implementation sessions; the filename is historical.

## What this repo is

Read `research/platforms/README.md` for the current two-product architecture and
`research/plan/16-fresh-session-handoff.md` and `12-complete-build-plan.md` before continuing implementation.
Current execution scope is Marlin backend first; see `research/plan/18-marlin-backend-first.md`.
Complete E3B/E4B endpoint integration/recovery/optimization before App feature work; Lab follows the App. `15-pending-inputs.md` records all remaining inputs.
`HANDOFF.md` contains historical operational context. Read manifest v4, the
platform-split amendment, shared contracts and your assigned brief before coding.
Wave 2 is imported at `271add9`; read `research/plan/10-wave2-platform-audit.md`
and `11-wave3-revision-handoffs.md`. Preserve completed v1 evidence; new revision
tasks gate product-v2 integration. No live-state claim is implied.

Per-experiment inference and benchmarking for the Gideon GPU work, plus the
research that sizes it. Five model experiments under `models/`, one directory each:
`deepseek41f`, `deepseek41fnvfp4`, `qwen3827b`, `kimik3`, `marlin2b`; shared
scripts in `models/common/`. `apps/app` is the consumer inference product (Next.js,
Supabase auth/DB); `apps/lab` is the planned provider product, currently a README
scaffold. `apps/infrx-api` is their shared inference gateway/runtime. Consumer
signup receives 10,000 CREDIT once per individual user. Preserve historical USD
separately. Provider roles and source-data permissions are distinct from consumer
ownership. `research/platforms/` is the current product specification.
Target hardware is 8×B300 HGX nodes (268 GB/GPU as deployed, ~2,144 GB per
node; 288 GB is the GB300 NVL72 figure, not ours) with local NVMe, plus AWS
p6 nodes for burst. Development happens on AWS GPU instances until the
bare-metal cluster exists.

## Branch convention

`main` carries `models/` (shared tooling in `models/common/`, every
experiment's metadata and scripts), `apps/`, and `research/`. Each experiment
has a branch of the same name (`marlin2b`, `kimik3`, …) where that
experiment's serve configs, benchmarks and results diverge. Implementation tasks use
isolated `codex/<task>-<slug>` branches from a coordinator-recorded committed base;
follow `research/plan/03-execution-protocol.md`. Do not automatically merge main into
unrelated experiment branches. Never merge an experiment branch into main.
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

Checks: run the canonical targets from the repo root — `make api-env` (pinned
`uv sync --frozen` into `apps/infrx-api/.venv`), `make api-test`, `make api-mutants`,
`make console-test`, `make console-lint`, `make console-typecheck` (`next typegen`
then `tsc`), `make console-mutants`, `make bench-test`, or `make check` for all.
Console tests are discovered recursively (`lib/`, `tests/`, `app/`, `components/`);
track suites live in `apps/infrx-api/tests/<track>/` and `apps/app/tests/<track>/`.
Track tests never import the legacy `gateway` shim; they build apps with
`infrx.gateway.app.create_app()`. The shared contracts are frozen in
`apps/infrx-api/infrx/contracts/` and `apps/app/lib/contracts/`; binding rulings are
`research/plan/08-contracts-v1-encoding.md` §10. Scripts also use
`set -euo pipefail` plus small Python clients. Marlin tools:
`./models/marlin2b/serve.sh` (vLLM in docker), `models/marlin2b/smoke.py`
(one request), `bench.py` (load test), `reference.py` (transformers path),
`tokens.py` (video token budget); `apps/infrx-api/gateway.py` is the compatibility entry point of the public
OpenAI-compatible gateway (the code lives in `apps/infrx-api/infrx/`) (systemd + Caddy, `apps/infrx-api/deploy/`). The dev box is a
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

## Verification log

- 2026-09-20: Updated implementation entry point, worktree rules and existing-test guidance; application behavior unchanged.
- 2026-09-21: Commands updated after F1/F2 integration (pinned environment, make targets, recursive console discovery, contracts location); application behavior unchanged.
- 2026-09-21: Wave 2 merged on `claude/infrx-impl`; entry point for the next session is `research/plan/evidence/coordinator/2026-09-21-wave2-handoff.md`; `make check` now runs eight Python mutant lists (D's needs Docker and skips visibly) and four console lists; application behavior unchanged on `main`.
