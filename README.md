# model-inference

New session? Start with [`HANDOFF.md`](HANDOFF.md) — live systems, access, measured
numbers and the implementation plan as of 2026-09-20.

Per-experiment inference and benchmarking code for the Gideon GPU work.

## Layout

One directory per experiment, one branch per experiment. `main` carries the
shared tooling and every experiment's metadata; experiment branches are where
that experiment's serve configs, benchmarks and results diverge.

```
models/common/          shared logic — download, env. Fix things here, not five times.
models/deepseek41f/     deepseek-ai/DeepSeek-V4.1-Flash          ~511GB
models/deepseek41fnvfp4/ nvidia/DeepSeek-V4.1-Flash-NVFP4        ~492GB
models/qwen3827b/       Qwen/Qwen3.8-27B                          ~54GB
models/kimik3/          moonshotai/Kimi-K3                      ~1400GB
models/marlin2b/        NemoStation/Marlin-2B (gated)              ~5GB
apps/app/               customer console (Next.js, Vercel, Supabase)
apps/infrx-api/         AWS-side API gateway + deployment (spec: apps/README.md)
research/               sizing, scaling and platform research
```

Each experiment directory holds:

- `model.env` — metadata only: HF repo, S3 prefix, size, gated flag.
- `download.sh` — fetch the weights.

## Downloading weights

```bash
./models/deepseek41f/download.sh       # S3 if reachable, else Hugging Face
SOURCE=hf ./models/qwen3827b/download.sh # force Hugging Face
DEST=/data/w ./models/kimik3/download.sh # somewhere other than WEIGHTS_ROOT
```

S3 is tried first when `S3_BUCKET` is exported, because on an AWS node that copy
is in-region over a gateway endpoint — free, and far faster than Hugging Face.
With `S3_BUCKET` unset, or off AWS entirely, it falls through to Hugging Face
rather than failing. That is what makes the same script usable on bare metal.

`S3_BUCKET` is not hardcoded on purpose: this repo is public and the bucket name
embeds an AWS account id. Export it on machines that should use the fast path.

Weights default to `$WEIGHTS_ROOT/$EXP`, where `WEIGHTS_ROOT` is `/mnt/nvme` —
instance-store NVMe on a p6 node. Override it anywhere that path is wrong.

`marlin2b` is **gated**: accept the licence on Hugging Face and export
`HF_TOKEN`, or the download fails with a 403.

## Research

Fact-checked GPU-inference research for these five experiments × the 8 GPUs
in `research/gpus/`: fit, parallelism, executed weight format, throughput,
latency and $/1M tokens, each numerically audited against the model vendor's
own API price. Start at [`research/README.md`](research/README.md) — the
index, legend and a headline table of the best GPU per model. The two
matrices worth bookmarking: [`research/matrix/fit-matrix.md`](research/matrix/fit-matrix.md)
(what fits where) and [`research/matrix/cost-matrix.md`](research/matrix/cost-matrix.md)
($/1M tokens, every cell linked to its source). All formulas live in
[`research/METHODOLOGY.md`](research/METHODOLOGY.md).
For running these models at scale in production — cluster, serving, cost,
reliability, and how commercial inference providers do it — see
[`research/scaling/`](research/scaling/README.md). For taking the Marlin-2B
endpoint to real users on AWS today — no dropped requests, autoscaling,
caching, optimization — see [`research/production-api/`](research/production-api/README.md).
For the closed-loop model-replacement platform built on top of this —
tracing, annotation, distillation, evals, A/B-gated promotion — see
[`research/platform/`](research/platform/README.md).

## Notes

- `S3_DIR` in `model.env` intentionally differs from the directory name. The S3
  mirror was populated before these names existed; changing it means moving
  objects, not editing a string.
- `kimik3` is ~1.4TB (1,561GB on disk). It fits one 8×B300 node only because the
  checkpoint is natively MXFP4 — do not assume an FP8 variant will. Note the
  node has ~2,144GB usable (268GB/GPU on HGX/DGX B300 and AWS p6-b300), not
  8×288GB = 2304GB; 288GB/GPU is the GB300 NVL72 figure. See `research/`.
