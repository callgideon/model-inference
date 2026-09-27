# Non-NVIDIA backend investment qualification — discovery draft (X5)

**Task X5**, lane `discovery`, wave LW1, 2026-09-27. Slices X5.a–c of
[14-expansion-gates.md](../../plan/14-expansion-gates.md) §X5. Oracle **BACKEND-CONTRACT**
([04-verification.md](../../plan/04-verification.md):117). Pending input **P-15**
([15-pending-inputs.md](../../plan/15-pending-inputs.md):25).

**Manifest status:** planned
**Trial status:** BLOCKED: P-15 — no owner has chosen a chip/model/runtime or provided trial hardware access, so no measured go/no-go exists (§4). X6 may not start. No purchase was made or requested.

Conventions: [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — dense TFLOPS only, GB vs
GiB as labelled, `meas.` / `est.` / **⚠️ TO BE VERIFIED**; prices only from
[`cloud-pricing.md`](../../cross-cutting/cloud-pricing.md). Every chip, engine and model
fact below is taken from this repository's fact-checked research (dated 2026-09-19) and cited
by section. **No GPU run, cloud operation, purchase or code change was made.** The acceptance
wants "a measured investment decision" (tasks.json X5 `acceptance`); without hardware access
none can exist, so this document delivers the candidate, the adapter contract and the gates
that the measurement must pass.

---

## 0. Summary

1. **Candidate studied: AMD Instinct MI355X × vLLM-ROCm × Marlin-2B BF16 × the finite-video
   SOP workload (profile `v1`).** It is the only non-NVIDIA accelerator with a repository
   reference ([gpus/mi355x.md](../../gpus/mi355x.md)) and a per-model study
   ([models/marlin2b/mi355x.md](../../models/marlin2b/mi355x.md)), and Marlin-2B is the only
   served model. It is a **candidate, not a selection**: choosing it is P-15. Other
   non-NVIDIA targets (Trainium, TPU, Gaudi, others) have no repository research and are
   ⚠️ not evaluated.
2. **The software path is unverified end to end.** No engine has loaded this checkpoint on
   ROCm; video ingestion for `qwen3_5` on ROCm is unconfirmed; 18 of 24 layers (Gated
   DeltaNet) run the slowest available tier on gfx950, with a known GDN crash under
   dp-attention (models/marlin2b/mi355x.md §0 item 5, §6 items 1–4).
3. **Economics have an idle-capacity trap.** Marlin-2B's weights are 4.426 GB against
   259.2 GB usable per GPU (1.7 %), TP1 is the right shape, yet the only published on-demand
   MI355X offer is an **8-GPU** OCI shape at $8.60/GPU-hr = **$68.80/h**
   ([cloud-pricing.md](../../cross-cutting/cloud-pricing.md) §5.13 and the 8-GPU node table).
   A cost figure that prices one busy GPU and omits the other seven is the failure oracle's
   "economics omit idle capacity".
4. **No AWS path.** AWS, GCP and Azure offer no MI355X (cloud-pricing.md §5.13); the repo's
   development account (CLAUDE.md "AWS access") cannot host the trial.

---

## 1. X5.a — Candidate and its constraints (primary sources via repository research)

| Constraint | Candidate value | Source |
|---|---|---|
| Chip | MI355X, CDNA 4, LLVM target `gfx950`; 288 GB HBM3E, 8.0 TB/s; BF16 2,500 / FP8 5,000 / MXFP4 & MXFP6 10,100 TFLOPS dense; 1,400 W, liquid-cooled; scale-up domain 8 | gpus/mi355x.md §0 summary, §1, §11 |
| Driver and toolchain | ROCm ≥ 7.0.1 (AMD acceptance floor), amdgpu ≥ 30.10.0; `gfx942` binaries do not run on `gfx950`; container `rocm/vllm:rocm10.0.0_ubuntu24.04_py3.14_pytorch_2.12.0_vllm_0.27.0` | gpus/mi355x.md §1 "Driver / ROCm minimums", §7 "Containers" |
| Runtime | vLLM-ROCm ≥ 0.27.0 or SGLang-ROCm ≥ 0.5.15; TensorRT-LLM and Dynamo unavailable | gpus/mi355x.md §7; models/marlin2b/mi355x.md §0 item 1 |
| Model load | `--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'`, **never run on ROCm** | models/marlin2b/mi355x.md §0 item 1, §6 item 1 |
| Executed dtype | BF16 (no FP8/MXFP4/NVFP4/AWQ checkpoint of Marlin-2B exists; INT4 on gfx950 is a Triton dequant slower than BF16) | models/marlin2b/mi355x.md §0 item 3; gpus/mi355x.md §6, §11 |
| Operators | Full attention (6 layers, GQA 8:2, head_dim 256): CK FA2 via `ROCM_AITER_FA`; GDN (18 layers): Triton tier only, FlashQLA/FA3/FA4 unavailable; fused gfx950 GDN prefill still an open PR (aiter#5606) | models/marlin2b/mi355x.md §2 table |
| Video path | `qwen3_5` video ingestion on ROCm unverified; Path A vs Path B token budget (1.91×) must be pinned to profile `v1` | models/marlin2b/mi355x.md §6 items 2–3; [marlin-sop.md](../marlin-sop.md) §1.5 |
| Licensing | Marlin-2B `license: apache-2.0` (model card front matter). ROCm/vLLM licence terms ⚠️ TO BE VERIFIED per pinned component; method: read each repository's LICENSE at the pinned tag | [MODEL_CARD.md](../../models/marlin2b/MODEL_CARD.md):2 |
| Known failures | GDN + dp-attention `HIP error: invalid configuration argument` on MI355X (closed, "disable dp-attention"); default chunked-prefill 196,608 crashed, 32,768 resolved | models/marlin2b/mi355x.md §6 item 4; gpus/mi355x.md §11 |

---

## 2. X5.b — Adapter contract and gates

### 2.1 Engine port (what an adapter must implement)

The worker drives any engine through `Engine` (`apps/infrx-api/infrx/contracts/ports.py:362`):
`generate(lease, prepared)` returns canonical `EngineEvent`s whose usage event carries the
**authoritative** `Usage` (`contracts/records.py:403`, total = prompt + completion);
`cancel(lease)`, `health()`, `drain()`. The shipped implementation `VllmEngine`
(`infrx/worker/engine.py:461`) speaks vLLM's OpenAI-compatible HTTP and also exposes
`capabilities()`. vLLM-ROCm serves the same HTTP surface, so the candidate needs **no new
adapter class**, only a separately registered serving variant (R3.a) with its own engine
pin — a claim that stays ⚠️ until a ROCm engine answers the existing conformance suite.

### 2.2 Artifact conversion

None for BF16: the same safetensors load with the same override (§1). Any FP8/MXFP4 build
would be a new artifact with no accuracy evaluation behind it (models/marlin2b/mi355x.md §6
item 5) and is out of scope for a first trial.

### 2.3 Gates the trial must pass (method fixed here; thresholds are P-15)

| Gate | Method | Failure that voids certification |
|---|---|---|
| Load profile | the same held-out corpus and measured load profile as the NVIDIA reference, pinning engine build, dtype, hardware and preprocessing (R3.b, tasks.json) | throughput from incomparable inputs (different clips, profile, output length or arrival model) |
| Quality parity | caption-event parity against the reference path at profile `v1` (the provisional P-18 parity method, [marlin-sop.md](../marlin-sop.md) §5.2) | missing operator or silent Path B fallback changes events |
| Capacity | sustained video-seconds processed per second per GPU with the duration mix, p50/p95 per the sample-sufficiency rule (marlin-sop.md §5.2) | a pN quoted without the samples behind it |
| Cost including idle capacity | allocated GPU-hours (whole shape, incl. idle) × published price ÷ successful video-hours | pricing one busy GPU of an 8-GPU shape |
| Failure recovery | crash, drain, fencing, cancellation and late output under the existing W3/E contracts; exact usage survives (BACKEND-CONTRACT) | usage loss, double settlement or a claimed completion after a crash |

---

## 3. X5.c — Prototype scope, allocation, go/no-go

- **Prototype scope (proposed, pending P-15):** one combination only — MI355X × vLLM-ROCm
  0.27.0 container × Marlin-2B BF16 × profile `v1` × TP1; no generic heterogeneous placement
  (tasks.json X5.c). Other chips/models stay unsupported (14-expansion-gates.md §X6).
- **Resource allocation:** none. The trial needs time on an 8-GPU MI355X shape from a
  provider in cloud-pricing.md §5.13 or loaned access; lead time ⚠️ (gpus/mi355x.md §11
  "Cloud quota reality").
- **Go/no-go:** the decision compares the §2.3 measurements with the NVIDIA reference
  variant using R3 experiment records (R3.b/R3.c); the thresholds that decide it are the
  owner's.
- **Proposed X6 split (draft):** X6.a1 register the ROCm serving variant and engine pin;
  X6.a2 run the existing engine conformance against it; X6.b1 drain/fencing/cancel under
  fault injection; X6.c1 held-out parity + sustained/burst benchmark; X6.c2 cost with idle
  capacity and rollback evidence.

---

## 4. Contract fields

The BACKEND-CONTRACT failure oracle names lost operators, incomparable throughput and idle
capacity; X5.a–c name the rest. [`../video/check_discovery.py`](../video/check_discovery.py)
enforces that every row is sourced or BLOCKED.

| Field | Value | Source |
|---|---|---|
| Chip | BLOCKED: P-15 — the owner's chip choice (MI355X is the studied candidate, §0) | — |
| Runtime | BLOCKED: P-15 — the owner's runtime choice and pinned build (vLLM-ROCm 0.27.0 is the candidate) | — |
| Model | BLOCKED: P-15 — the owner's model choice (Marlin-2B BF16 is the candidate) | — |
| Workload | BLOCKED: P-15 — the workload the investment is for (finite-video SOP profile `v1` is the candidate) | — |
| Supported operators | Full attention via CK FA2 / `ROCM_AITER_FA`; GDN via Triton only; no FA3/FA4/FlashQLA on ROCm | models/marlin2b/mi355x.md §2 |
| Supported dtypes | BF16 executed; FP8/MXFP4 native on gfx950 but no such Marlin checkpoint; INT4 slower than BF16 | models/marlin2b/mi355x.md §0; gpus/mi355x.md §6 |
| Licensing | Model Apache-2.0; ROCm/vLLM component licences ⚠️ to be read at the pinned tags | MODEL_CARD.md:2 |
| Driver and toolchain | ROCm ≥ 7.0.1, amdgpu ≥ 30.10.0, target `gfx950`, pinned ROCm 10.0.0 / vLLM 0.27.0 container | gpus/mi355x.md §1, §7 |
| Artifact conversion | None for BF16; same override flag; untested on ROCm | models/marlin2b/mi355x.md §0, §6 |
| Engine port | Existing `Engine` protocol (generate/cancel/health/drain, authoritative usage); `VllmEngine` over OpenAI HTTP | `contracts/ports.py:362`; `worker/engine.py:461` |
| Load profile | Same held-out corpus and measured load profile as the reference, all pins recorded (§2.3) | tasks.json R3.b |
| Quality parity | BLOCKED: P-15 — the parity threshold the owner accepts (method: caption-event parity, §2.3) | — |
| Capacity | BLOCKED: P-15 — measured sustained video-s/s per GPU needs trial hardware | — |
| Cost including idle capacity | Whole allocated shape × price ÷ successful video-hours; only published on-demand price $8.60/GPU-hr, 8-GPU OCI shape $68.80/h | cloud-pricing.md §5.13 |
| Failure recovery | Crash/drain/fencing/cancel/late output keep exact usage and single settlement (§2.3) | 04-verification.md:117; 14-expansion-gates.md §X6 |
| Prototype scope | BLOCKED: P-15 — approval of the single §3 combination | — |
| Resource allocation | BLOCKED: P-15 — hardware access (no AWS/GCP/Azure offer) and budget owner | — |
| Go/no-go criteria | BLOCKED: P-15 — thresholds for quality, capacity and cost versus the NVIDIA reference | — |
| Trial access | BLOCKED: P-15 — a named provider or loan and the time window | — |

---

## 5. What this document does not claim

No AMD support, no measured throughput or cost on MI355X, no investment decision, no
purchase, and no generic heterogeneous placement. The `est.` figures in
models/marlin2b/mi355x.md stay estimates there; none is promoted here.

---

## Audit log

- 2026-09-27 (X5, lane `discovery`, branch `codex/w5-discovery`, base `9a6c3685`): created.
  X5.a–c drafted from `gpus/mi355x.md`, `models/marlin2b/mi355x.md` and
  `cloud-pricing.md` (all dated 2026-09-19; not re-fetched) plus the engine port in code.
  Nine method/fact fields are sourced; the ten decision, threshold, measurement and
  access fields are BLOCKED on P-15. No GPU run, cloud call, purchase or code change.
