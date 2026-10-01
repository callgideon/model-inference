# Marlin-2B serving and measurements

Marlin-2B is the first hosted model: finite video/text → text, aimed at recorded-video analysis and future SOP verification. This directory contains the serving recipe, reference path, dataset client and measurement tools. [Current deployment and limits](../../STATUS.md) are distinct from historical experiments.

## Current serving scope

Public model `nemostation/marlin-2b` is served by the durable [inference runtime](../../apps/infrx-api/README.md), not a direct unmetered engine proxy. Use a consumer API key issued by the App or audited operator tool; the old shared SSM gateway key is not a pilot credential.

The published profile currently permits one finite clip up to 82 seconds and 64 MiB, sampled at 2 fps with bounded geometry. Sync JSON, streamed text and explicit async jobs/uploads are implemented. Discover current limits/rates through `/v1/models`. Native live video, robot actions and SOP accuracy are not certified features.

The GPU host is last reported as one L40S. Engine and runtime are pinned separately; use [STATUS.md](../../STATUS.md) and the [rollout runbook](../../infra/rollout/README.md) for their actual identities and operating windows. Do not restart the serving host with an experiment command during certification.

## Tools

| File | Purpose |
|---|---|
| `model.env`, `download.sh` | Immutable model metadata / shared download tooling; gated weights require approved access |
| `serve.sh`, `serving-version.json` | Pinned vLLM image, launch options and measured identity/history |
| `reference.py` | Transformers reference path and canonical provider prompts |
| `smoke.py` | Single-request protocol/response inspection |
| `bench.py`, `runprofile.py` | Bounded profiling, fresh-generation/replay accounting and workload identity |
| `dataset.py` | Resumable per-item dataset workflow |
| `corpus/`, `corpus-synth/` | Versioned test corpus definitions |
| `measure/`, `profiles/`, `results/` | Experiment procedures, run profiles and dated artifacts |

The model architecture is remapped to vLLM's `Qwen3_5ForConditionalGeneration` loader using the pinned `--hf-overrides`. Its custom caption/find helpers belong to the reference path; serving sends the canonical prompt. Qualify processor/frame parity and task behavior whenever the engine, model, sampling or precision changes. [Architecture research](../../research/models/marlin2b/README.md).

## Development and measurement

Use an isolated GPU target and current script `--help`/profile definitions. Downloads use `models/common/download.sh`: `S3_BUCKET` selects an available mirror, `SOURCE=hf` forces Hugging Face, and `WEIGHTS_ROOT`/`DEST` choose storage. Credentials remain in the authorized environment/secret store.

From the repository root, `make bench-test` runs the local tests. GPU capacity, soak, faults and parity need their allocated target and approved bounds; a local fake-engine test is not a throughput measurement. [Client/load protocol](../../research/plan/consumer-v1/05-client-and-load-testing.md).

`serving-version.json` and older result directories retain dated measurements, including rejected/conditional candidates. They are not the current approved limit or an SLA. Use the latest accepted report for performance claims; current E4C acceptance is pending. The later hosting/optimization work is in [roadmap 23](../../research/plan/23-inference-hosting-roadmap.md).
