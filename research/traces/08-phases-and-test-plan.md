# Deep traces — phases and test plan

Plan date **2026-09-20**. Turns [`01-requirements.md`](01-requirements.md)
through [`07-console-spec.md`](07-console-spec.md) into an ordered set of
**small, independently verifiable blocks**. Every block names what it creates,
the tests that gate it, an exit criterion that is a measurement or a drill,
and how to roll it back. Nothing in a later phase is needed for an earlier
phase to be useful on its own.

Rule adopted from [`../production-api/09-blueprint.md`](../production-api/09-blueprint.md) §8:
*each phase has an exit criterion that is a measurement, and each is
independently shippable and independently reversible.*

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend). Effort sizes are
`est.` engineering days for one person who has read this tree; ⚠️ they are
planning numbers, not commitments.

---

## 0. Map

```
Phase 0  Foundations      vLLM flags · Supabase migration · infrx-obs (ClickHouse) · S3 bucket · baseline bench
Phase 1  Metadata rows    traces.py (row only) → spool → ClickHouse · every status path · replay · H1 A/B
Phase 2  Full content     content blob → S3 · level resolution · key toggle in console · byte-exactness
Phase 3  Viewer           lib/clickhouse.ts + proxy + presign · Traces list · detail · docs · settings(part)
Phase 4  Feedback         POST /v1/feedback · console thumbs/rating/correction · score tiles
Phase 5  Judge            judge.py dry-run → live batches · consent · admin tab · calibration study
Phase 6  Operations       retention/TTL drills · tenant deletion · metrics/alerts · logrotate · replay timer
Later    Engine spans (D5) · PII · datasets · Usage tiles on ClickHouse
```

Dependencies on the production-api programme ([`../production-api/09-blueprint.md`](../production-api/09-blueprint.md) §8),
none of which block a phase here:

| Their phase | Touches | Rule |
|---|---|---|
| P0 (gateway fixes) | `gateway.py` hot path | land theirs first if both are open; ours is additive |
| P1 (media stage, `media.py`) | `prepare_video` → `MediaRef` | whichever lands second adopts the other's seam: the trace reads `MediaRef.sha256/s3_key/hit` when it exists, else computes `sha256` itself ([`05`](05-gateway-capture-spec.md) §4) |
| P2 (queue, `usage.py`, job ids) | `Inference-Id` becomes the uuid half of `job_…`; `usage.py` owns the record seam | no schema change (`UUID` either way); `traces.py` moves under `shared/` as a `git mv` |
| P3 (`/metrics`) | counters | `infrx_traces_*` register on the same registry; until then they are JSON log lines ([`05`](05-gateway-capture-spec.md) §9) |

## 1. Phase 0 — Foundations (no customer-visible change)

Goal: every piece of infrastructure exists and has been exercised **before**
any capture code is written, so Phase 1 debugging is about our code only.

| Block | Creates / changes | Exit (measured) | Effort |
|---|---|---|---|
| **S0.1 vLLM flags** | `models/marlin2b/serve.sh` + `deploy/marlin2b-vllm.service`: `--enable-prompt-tokens-details`, `--enable-request-id-headers`; `serve.sh` writes `ARTIFACT_ID` into the env file | one `curl` with `X-Request-Id: test-123` on a video request returns `"id":"chatcmpl-test-123"` and `usage.prompt_tokens_details.multimodal_tokens > 0`. **Closes [`01`](01-requirements.md) OQ 2**; if `multimodal_tokens` is null on the pinned nightly, record it and switch [`05`](05-gateway-capture-spec.md) §5 to the fallback | 0.5 d |
| **S0.2 Supabase migration** | `apps/app/supabase/migrations/0003_traces.sql` ([`04`](04-data-model.md) §5) applied via the pooler; `gateway.py` auth `select` gains `trace_level,judge_enabled,organizations(media_copy,trace_retention_days)`; `test_gateway_auth.py` fixture row gains the columns | `pytest apps/infrx-api/tests` green; a key updated in SQL to `full` is visible in `_keys` after 60 s | 0.5 d |
| **S0.3 infrx-obs** | EC2 (2 vCPU / 8 GB, EBS gp3 100 GB, SG: `:8123` from the gateway SG, `:443` public for the proxy), `apps/infrx-api/deploy/obs/docker-compose.yml` (ClickHouse at a pinned digest, Caddy), `deploy/obs/ddl.sql` ([`04`](04-data-model.md) §2), `deploy/obs/users.xml` (five users, [`04`](04-data-model.md) §2.4), `deploy/obs/backup.sh` + timer, `deploy/obs/install.sh`, `HANDOFF.md` §1 row | DDL applies cleanly (**closes [`04`](04-data-model.md)'s ⚠️ on skip-index forms**); each user can do exactly its grants and nothing else (scripted check); `BACKUP` to S3 then `RESTORE` into a fresh container reproduces a 1,000-row fixture | 1.5 d |
| **S0.4 S3 bucket** | `infrx-traces`: SSE-S3, public access block, lifecycle rules per `ttl` tag {7,30,90,180,365} + `frames/` 30 d + `backups/` 35 d; IAM: gateway instance-role policy (Put on `content/*`, `media/*`), obs role (judge), a console IAM user (Get on `content/*`, `frames/*`) with keys in SSM | `aws s3api put-object --tagging ttl=7` succeeds from the gateway role and `get-object` is denied; the console user can `get` and cannot `put`; lifecycle rules listed | 0.5 d |
| **S0.5 Baseline bench** | `models/marlin2b/results/bench.jsonl` rows: `bench.py -c 8 -n 32 --distinct` through the gateway with the current code (no tracing), 1080p and 480p sets, `BASE_URL` recorded | rows committed; these are the "before" numbers for H1 | 0.5 d (shares the run with production-api B0.1 if that is open) |

Rollback: none needed — nothing serves traffic differently.

## 2. Phase 1 — Metadata rows

Goal: one row per request in ClickHouse, for every key, at level `metadata`;
prove the async path, the durability path and the hot-path budget with the
smallest payload.

| Block | Creates / changes | Tests | Exit | Effort |
|---|---|---|---|---|
| **S1.1 `traces.py`** (row only) | `apps/infrx-api/traces.py`: `effective_level`, `assemble_row`, `Worker` (queue → spool → batch → ClickHouse), `ship_rows`, counters; `deploy/replay_traces.py`; `tests/test_traces.py` | [`05`](05-gateway-capture-spec.md) §11 G-series: level truth table, row assembly, spool append, batch flush by size/age, ship retry then park, replay idempotency, QueueFull drop counting | all G-tests green with no network (`httpx.MockTransport`) | 2 d |
| **S1.2 Gateway wiring** | `gateway.py`: auth columns (S0.2), `requested_model` capture, `X-Request-Id` on both upstream calls, `concurrency_at_admission`, media `sha256` concurrent with `ffprobe`, `finish()` replacing `log()` so **every** return path emits (200/400/401/429/502/503/client abort), stream accumulation counters | G-tests for one-row-per-status-path; existing `test_gateway_auth.py` and `test_media.py` unchanged and green | `python3 -m pytest apps/infrx-api/tests -q` green | 1.5 d |
| **S1.3 Deploy + drills** | `install.sh` writes the new env vars; `serve.sh` flags from S0.1 live; `traces_failed.jsonl` on the EBS root | **H1** (below) at `off` vs `metadata`; **H2**: stop ClickHouse 10 min under `bench.py --rate 2`, restart, run `replay_traces.py`; **H3**: `systemctl restart marlin2b-gateway` mid-run | `count(traces)` == `count(usage_events)` over the drill window after replay (**T1**); H1 within NF1; H3 leaves no duplicate `id` after `OPTIMIZE … FINAL` | 1 d |

Exit for the phase: T1, T4 (row fields non-null on a streamed video request),
T8, T9, T12, NF1, NF2, NF6 all demonstrated on the live box and recorded in
`models/marlin2b/results/notes.md` ("Measured" note per repo convention).

Rollback: `TRACE_ENABLED=false` env (worker never starts, `finish()` skips
`emit`); nothing else changes.

## 3. Phase 2 — Full content

Goal: `trace_level=full` captures byte-exact content to S3 with the row
pointing at it; the opt-in is real because the console can set it.

| Block | Creates / changes | Tests | Exit | Effort |
|---|---|---|---|---|
| **S2.1 Content capture** | `traces.py`: `assemble_content` ([`04`](04-data-model.md) §3.1), media reference substitution, header allowlist, `metadata` validation (T10), `schema_valid` + `tool_calls` parsing (T11), `think` split, S3 put (gzip, `ttl` tag), ship order blob-then-row, `media_copy` path (T6) | G-series: byte-exactness invariants (`prompt_hash`, `content_sha256`, `content_bytes`), `data:` URL never copied, stream join equals client output, think captured pre-strip, blob-before-row, `metadata` limits | green | 2 d |
| **S2.2 Key toggle** | console `api-keys`: `trace_level` select + `judge_enabled` toggle ([`07`](07-console-spec.md) §6 actions), owners only; docs section stub | [`07`](07-console-spec.md) §9 U-series: owner vs member action tests | toggle → gateway sees it within 60 s (**C3**) | 1 d |
| **S2.3 Deploy + verify** | live | **H1** at `off` vs `full`; **H4**: synthetic 500 req/s burst to a fake upstream to hit `QueueFull`, assert drops are counted not blocking; manual: `aws s3 cp` a blob, `gunzip`, compare `messages` hash with the row | NF1 at `full`; T5/T6 invariants on 100 real traces (script) | 1 d |

Exit: success criterion 1 of [`01`](01-requirements.md) §8 **minus the
viewer** — the trace is retrievable by `clickhouse-client` + `s3 cp` within
60 s and the request was not measurably slower.

Rollback: set keys to `metadata` (console) or `TRACE_CONTENT_ENABLED=false`.

## 4. Phase 3 — Viewer

| Block | Creates / changes | Tests | Exit | Effort |
|---|---|---|---|---|
| **S3.1 Access path** | Caddy route + tokens on `infrx-obs` ([`07`](07-console-spec.md) §1), `apps/app/lib/clickhouse.ts` (org-bound query helper), `lib/s3-presign.ts`, Vercel env vars | U-series: org-injection unit test (a query without `org` fails to compile/throws), presign known-vector test | from Vercel preview: a parameterised `SELECT count()` returns; a presigned GET opens a blob | 1.5 d |
| **S3.2 Traces list** | `(console)/traces/page.tsx`, controls, table, sidebar entry ([`07`](07-console-spec.md) §2–§4) | U-series | 10k-row org lists in < 1 s p95 (**C1**); filters match ClickHouse counts | 2 d |
| **S3.3 Trace detail** | `traces/[id]/page.tsx`, content client component, waterfall, tokens, think panel, cURL copy | U-series; **C7 crafted-id test** (another org's id → 404) | a `full` trace renders every panel; a `metadata` trace shows the C2 notice | 2 d |
| **S3.4 Docs + settings (part)** | docs "Traces" section ([`07`](07-console-spec.md) §4a copy); `settings/page.tsx` with `trace_retention_days`, `media_copy` only | — | text reviewed by the owner | 0.5 d |

Exit: **success criterion 1 in full** (turn on → request → see it in 60 s).

Rollback: remove the sidebar entry; the route stays behind auth.

## 5. Phase 4 — Feedback

| Block | Creates / changes | Tests | Exit | Effort |
|---|---|---|---|---|
| **S4.1 API** | `gateway.py`: `POST /v1/feedback`, `GET /v1/feedback`, `GET /v1/traces` export ([`05`](05-gateway-capture-spec.md) §7); score items through the same worker; ownership cache | G-series: validation table, ownership 404, rate limit, score ship | curl thumbs → row in `scores` within 5 s (**F1, F2, F4**) | 1.5 d |
| **S4.2 Console** | detail page feedback controls, `postFeedback` action via writer token ([`07`](07-console-spec.md) §6), score badges in the list, tiles (pass rate, thumbs ratio) | U-series member/owner; optimistic UI | a console thumb is visible via `GET /v1/feedback` and vice versa (**F3**) | 1.5 d |

Exit: F1–F4; the "programmatic developer" journey of [`01`](01-requirements.md) §3.

## 6. Phase 5 — Judge

| Block | Creates / changes | Tests | Exit | Effort |
|---|---|---|---|---|
| **S5.1 Dry run** | `apps/infrx-api/judge/judge.py` on `infrx-obs` (selection, budget, frames, prompt assembly, batch **building without submitting**), `JUDGE_DRY_RUN=1` writes `judge_items.status='dry_run'` and the would-be cost; systemd timer | [`06`](06-feedback-and-judge-spec.md) §5 Q-series: selection policy on a synthetic day, budget cut-off, frame timestamps, prompt determinism, schema validation | a dry run over one real day selects exactly what J1 predicts; frames appear under `frames/`; **no egress happened** (`judge_runs.batch_id` null) | 2.5 d |
| **S5.2 Live** | submit + collect timers, `scores` writes, `judge_runs` accounting; console: settings page gains judge budget/sample/rubric/**consent** (C4), admin Judge tab (C5); docs consent text (C6) | Q-series result-state machine, egress predicate, idempotent re-run; one integration test on a recorded batch fixture | first live run on one consenting org: `n_succeeded/n_selected ≥ 0.95`, `cost_usd_actual` within 25 % of `cost_usd_est`, every trace in the batch has `judge_enabled ∧ judge_egress_ok` (**J5**), scores visible in the detail page | 2 d |
| **S5.3 Calibration** | 50 traces hand-labelled by the owner in the console (rating + correction), agreement report script ([`06`](06-feedback-and-judge-spec.md) §3.9) | — | κ / Spearman reported per criterion; badge "calibration pending" removed only above the threshold set in [`06`](06-feedback-and-judge-spec.md) | 1 d + labelling time |

Exit: J1–J7; success criteria 2 and 4.

Rollback: timer disabled; `judge_egress_ok=false` on the org stops selection immediately.

## 7. Phase 6 — Operations

| Block | Creates / changes | Exit | Effort |
|---|---|---|---|
| **S6.1 Retention** | verify TTL: insert rows with `ts` 14 months back → gone after `OPTIMIZE … FINAL` / merge; S3 lifecycle: objects tagged `ttl=7` gone after 8 days (**O2**) | drill recorded | 0.5 d |
| **S6.2 Tenant deletion** | `deploy/obs/delete_org.sh`: lightweight `DELETE` on all tables + S3 prefix delete + `trace_deletions` row (**O3**) | drill on `e2e-test` org: zero rows/objects remain | 0.5 d |
| **S6.3 Observability** | counters on `/metrics` (with production-api P3) or JSON lines; alerts: `infrx_traces_parked_total > 0 for 10 m`, `infrx_traces_dropped_total > 0`, ClickHouse disk < 20 %, judge run failed, backup missed (**O4**) | alerts fire in a drill | 1 d |
| **S6.4 Hygiene** | logrotate for `traces.jsonl` (24 h, delete), a systemd timer for `replay_traces.py`, ClickHouse upgrade runbook, restore runbook in `apps/infrx-api/README.md` | runbooks reviewed | 0.5 d |

Exit: O1–O5.

## 8. Later (designed for, not scheduled)

| Item | Precondition | Where designed |
|---|---|---|
| Engine spans (`queue/prefill/decode` per request) | production-api P3 collector | [`03`](03-architecture.md) §10.1; join key laid by T9 |
| PII detection / redaction | customer need | [`03`](03-architecture.md) §10.2; `redaction_status` column exists |
| Implicit signals (regenerate, abandonment) | ≥ weeks of session ids | [`06`](06-feedback-and-judge-spec.md) §2c |
| Dataset export (Parquet) | S3/S4 of the platform | [`03`](03-architecture.md) §10.3 |
| Usage tiles from ClickHouse | — | [`03`](03-architecture.md) §10.4 |

## 9. Cross-cutting drills (H-series)

These are run in the phase that first makes them meaningful and re-run at
every later deploy that touches `traces.py`.

| id | Drill | Pass | First run |
|---|---|---|---|
| **H1** | A/B hot path: `bench.py -c 8 -n 32 --distinct` through the gateway, same 32 clips, once per key level (`off`, `metadata`, `full`), interleaved ABAB to cancel drift; compare `wall_s` p50/p99 and `ttft_s` p50 | Δp50 ≤ 1 ms, Δp99 ≤ 3 ms, ΔTTFT p50 ≤ 0 ± noise (NF1). Record in `results/notes.md` | S1.3 (`off`/`metadata`), S2.3 (`full`) |
| **H2** | ClickHouse (and separately S3) unreachable for 10 min under `--rate 2` | zero request failures; `traces_failed.jsonl` grows; after `replay_traces.py`, `count(traces FINAL)` == requests sent (NF2) | S1.3 |
| **H3** | `systemctl restart marlin2b-gateway` under load, twice, once with `kill -9` | graceful: zero loss; `kill -9`: loss ≤ items in queue (report the number); never a duplicate `id` after merge (NF6) | S1.3 |
| **H4** | `QueueFull`: fake upstream at 500 req/s with the worker paused | requests unaffected; `infrx_traces_dropped_total` == observed drops; no disk write on the request path (strace/`inotify` on the spool during the burst) | S2.3 |
| **H5** | Spool rotation under load | no gap in `id`s across the rotation boundary | S6.4 |
| **H6** | Cross-org read attempt in the console (crafted id, crafted `org` param) | 404 / rejected at the helper (C7) | S3.3 |
| **H7** | Judge egress audit | every `judge_items` row joins to a key with `judge_enabled` and an org with `judge_egress_ok` at `ts` (J5) | S5.2 |

## 10. Test-id index

| Series | Document | Scope | Runner |
|---|---|---|---|
| G1–G22 | [`05`](05-gateway-capture-spec.md) §11 | capture, worker, feedback API (unit + fake-vLLM integration) | `pytest apps/infrx-api/tests` — no network |
| Q1–Q11, QI1 | [`06`](06-feedback-and-judge-spec.md) §5 | judge selection, sampling, budget, frames, prompt, schema, result state machine, egress, idempotency, cost estimator; QI1 = recorded-batch integration | `pytest apps/infrx-api/judge/tests` — no network |
| U1–U4, E1 | [`07`](07-console-spec.md) §9 | U1 org-binding + `param_` form fields, U2 SigV4 known vector, U3 gzip round-trip + content guard, U4 filter parsing / operator-only `?org=`; E1 = 9-step manual E2E checklist (C1–C7, F3, T2/T3, J5) | `pnpm test` in `apps/app` (matches the existing `*.test.ts` runner) |
| H1–H7 | this file §9 | live drills | manual, recorded in `results/notes.md` |
| T/F/J/C/O/NF | [`01`](01-requirements.md) | acceptance | closed by the block that names them |

## 11. Effort summary (`est.`, ⚠️)

| Phase | Days | Cumulative |
|---|---:|---:|
| 0 Foundations | 3.5 | 3.5 |
| 1 Metadata rows | 4.5 | 8 |
| 2 Full content | 4 | 12 |
| 3 Viewer | 6 | 18 |
| 4 Feedback | 3 | 21 |
| 5 Judge | 5.5 + labelling | 26.5 |
| 6 Operations | 2.5 | 29 |

About six working weeks for one engineer, front-loaded so that value is
visible after Phase 3 (~3.5 weeks). Phases 4 and 5 can proceed in parallel
with 6.

## 12. What "we did not diverge" means, checked at every phase gate

1. Every column written exists in [`04`](04-data-model.md) §2 with the same
   type; every content key exists in §3.1. A new field is a `04` change first.
2. Every requirement id closed by the phase is ticked in a copy of
   [`01`](01-requirements.md) §4 kept in the PR description.
3. H1 is re-run whenever `gateway.py` or `traces.py` changes on the request
   path; the number goes in `results/notes.md`.
4. No new package on the gateway's request path (NF10) — checked by diffing
   `install.sh`'s pip line.
5. The trace worker's failure counters are zero on a healthy day; a non-zero
   counter is a bug or an outage, never "expected noise".

---

## Verification log

- 2026-09-20 — plan written against `main` at `5210c67`; phase boundaries chosen so that Phase 1 proves the async/durability path with rows only (cheapest possible failure), Phase 2 adds content, Phase 3 adds the viewer, and the judge (the only component with external egress and real spend) comes after feedback exists to calibrate it against. Effort numbers are unmeasured estimates. Test ids reference the series defined in `05` §11, `06` §5 and `07` §9; those documents were written in parallel with this one and their exact id lists should be checked against §10 at review (⚠️).
