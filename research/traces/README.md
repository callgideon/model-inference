# research/traces/ — deep traces: requirements, design, phased plan

## 1. What this is

The specification set for **opt-in, per-request deep tracing** of the
inference API (`apps/infrx-api/gateway.py`): capturing each call's input,
output, configuration, timings, tokens and cost **asynchronously, with no
hot-path cost**, into a platform trace store (ClickHouse + S3) with a Traces
page in the console, a feedback API, and an asynchronous LLM-as-judge — so
the production model's performance, relevance and accuracy can be analysed
on real traffic. Eight documents, `01`–`08`, each with its own verification
log. **Decision date 2026-09-20.**

It sits between two existing trees: [`../platform/01-observability-and-tracing.md`](../platform/01-observability-and-tracing.md)
(the closed-loop research that defines *what a trace must be* for training —
its §1.5 schema is adopted here; its §6.3 "adopt Langfuse" verdict is
superseded by decision D2) and [`../production-api/`](../production-api/README.md)
(the ops plane: queue, metrics, autoscaling — this tree adds the content /
quality plane and names every seam it shares with that refactor).

## 2. Legend

From [`../METHODOLOGY.md` §Legend](../METHODOLOGY.md#legend): `[src]` + URL =
primary source; **⚠️ TO BE VERIFIED** = estimate, method stated; `est.` =
derived; `meas.` = measured.

## 3. Reading order

1. **[`01-requirements.md`](01-requirements.md)** — the decisions already
   taken (D1–D8), users and journeys, functional requirements `T`/`F`/`J`/`C`/`O`
   with acceptance checks, non-functional requirements `NF1`–`NF11` (hot path
   ≤ 1 ms p50, durability, exactness, tenancy, privacy, cost), constraints
   from the systems that exist, what is out of scope, open questions.
2. **[`03-architecture.md`](03-architecture.md)** — the system: the request
   path and what it does *not* do, the in-process trace worker (spool → S3 →
   ClickHouse), the store, the console read path, feedback and judge paths,
   sizing and cost at pilot (1M/mo) and platform (50M/mo) scale, security and
   tenancy, failure modes, evolution.
3. **[`08-phases-and-test-plan.md`](08-phases-and-test-plan.md)** — seven
   phases of small, independently verifiable blocks (foundations → metadata
   rows → full content → viewer → feedback → judge → operations), each with
   tests, a measured exit criterion and a rollback; the H-series live drills;
   the test-id index; effort.
4. Then the detail, in any order:
   - [`02-metrics-catalogue.md`](02-metrics-catalogue.md) — every field we can
     track per call (93 rows): canonical name, type, vantage (gateway / engine /
     client / post-hoc), OTel/OpenInference/vendor mapping, phase, consumer;
     the Marlin-only fields; vLLM metrics; the quality-signal taxonomy; per-product
     data models with sources; async techniques.
   - [`04-data-model.md`](04-data-model.md) — ClickHouse DDL (`traces`,
     `scores`, `judge_runs`, `judge_items`), users and grants, S3 object
     schemas (content blob, media copy, judge frames), the spool line format,
     the Supabase migration `0003_traces.sql`.
   - [`05-gateway-capture-spec.md`](05-gateway-capture-spec.md) — the capture
     seam in today's `gateway.py` and after the production-api refactor,
     config, level resolution, request-path changes with insertion points,
     `assemble()`, the worker, `POST /v1/feedback`, replay, hot-path
     accounting and the A/B benchmark, unit tests G-series.
   - [`06-feedback-and-judge-spec.md`](06-feedback-and-judge-spec.md) — the
     score model, feedback surfaces, `judge.py` (selection policy, frames,
     prompt and rubric, Message Batches, result collection, egress gate,
     cost model, validity study), tests Q-series.
   - [`07-console-spec.md`](07-console-spec.md) — Vercel → ClickHouse access
     path, routes and files, queries, page specs, types, server actions,
     security checklist, docs copy, tests U-series.

## 4. The design in ten sentences

1. Every request already gets an `Inference-Id`; that id is the trace id in
   ClickHouse, in S3, in `usage_events`, and — via `X-Request-Id` — in vLLM's
   own response id, so engine-side spans can be joined later without a schema change.
2. Opt-in is **per API key** (`off` / `metadata` / `full`) with a per-request
   header that can only lower the level; new keys default to `metadata`.
3. The request path builds one dict and calls `put_nowait`; a background task
   in the same process appends it to a local spool (durability), then ships
   batches: content blob to S3 first, row to ClickHouse second, with retry
   and a parked-failures file that `replay_traces.py` drains — the same
   shape as today's `usage_events` ingestion.
4. Content is byte-exact and never truncated; media is stored by sha256
   reference, never inline; the stripped `<think>` block is captured and
   labelled as never sent to the client.
5. vLLM's `--enable-prompt-tokens-details` gives per-request `cached_tokens`
   and `multimodal_tokens`, which is the cheap detector for the 1.9× video
   token-budget fork.
6. ClickHouse runs single-node in docker on a small EC2 (`infrx-obs`) with
   `ORDER BY (org_id, ts, id)`, monthly partitions, 13-month TTL, nightly
   backups to S3; content lives in S3 with per-org retention by object tag.
7. The console reaches ClickHouse only through a token-authenticated proxy
   as a read-only user, binds `org_id` from the session in a helper no query
   can bypass, and mints 60-second presigned S3 GETs only after the row check.
8. Feedback (`thumb`, `rating`, `correction`) is an append-only `scores` table
   keyed by trace id, written by `POST /v1/feedback` and by the console; a
   trace row never mutates.
9. The judge is a timer on `infrx-obs` that selects 100 % of failures plus a
   uniform sample, sends sampled frames + prompt + answer to `claude-opus-5`
   through the **Message Batches API** with structured output, under a
   per-org daily budget and a **separate egress consent**, and writes scores.
10. Hot-path cost is a measured number (A/B with `bench.py`, ≤ 1 ms p50), not
    an assumption, and it is re-measured at every phase gate.

## 5. Headline numbers (`est.`, see [`03`](03-architecture.md) §7)

| | Pilot 1M req/mo | Platform 50M req/mo |
|---|---:|---:|
| ClickHouse rows resident (13 mo) | ~6.5 GB | ~325 GB |
| Content blobs (Marlin shape, 100 % `full`) | ~5 GB/mo | ~250 GB/mo |
| Infra (EC2 + EBS + S3 + backups) | ≈ $75–85/mo ⚠️ | ≈ $470–600/mo ⚠️ |
| Judge at 5 % sampled, K = 8 frames, Opus 5 batch ([`06`](06-feedback-and-judge-spec.md) §3.8) | ≈ $1,480/mo | ≈ $74k/mo — hence the per-org budget knob |

## 6. Open questions (consolidated; each doc keeps its own)

1. `multimodal_tokens` populated for video on the pinned vLLM nightly — Phase 0 S0.1 closes it.
2. Judge frame source before the production-api media stage exists (`media_copy` per org vs text-only judge).
3. Vercel → ClickHouse: token proxy (v1) vs a private path.
4. 90-day content retention is a product default, not derived.
5. The judge rubric is a draft until validated against ~50 human labels.
6. EC2/EBS prices for `infrx-obs` are unsourced.

---

## Verification log

- 2026-09-20 — tree written in one session against `main` at `5210c67`. `01`, `03`, `04`, `08` and this index by the orchestrating session; `02`, `05`, `06`, `07` by parallel sub-sessions sharing the same context; cross-document consistency (column names, test-id series, env var names, requirement ids) was checked in a final pass recorded in each document's own log. No code was written.
