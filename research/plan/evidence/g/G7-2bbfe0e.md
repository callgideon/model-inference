# G7 — truthful endpoint contract and persisted result expiry

Lane G7, consumer v1 wave (program 22), task G7 (CATALOG-TRUTH, RESULT-EXPIRY, API-MODES,
API-STREAM). Closes the route half of RV-01, RV-11 and P-22/S3 F11; answers I8's WR-I8-3.
No hosted database, box, edge or AWS access; no push.

| | |
|---|---|
| Base | `dff31efc` (main) + coordinator patch `640cedd4` (task-local ports, `git am`) |
| Code head | `2bbfe0e3` (this file and the update JSON follow in the next commit) |
| Branch / worktree | `codex/g7-catalog` / `.claude/worktrees/codex-g7-catalog` |
| Siblings merged (plain merges of committed SHAs) | F2C-C `042ea49d`, integration `claude/consumer-v1` `d23e0dd3` (F2C-C fix round + its wiring), F2C-L `c058835a` then `1b9c7411` (slices a, b, d) |

## Commits (one per point, TDD)

| Point | Commit | What |
|---|---|---|
| 1 | `dcb5bd42`, `7e8e291f` | `GET /v1/models` = F2C.c `PublishedModel` via `published_model.project` over the consumer's resolution of `MODEL_ID`, the enforced `serving_profile` (validator allow-list, pilot limits, validator ∩ media-profile MIME, sampling, served modes), readiness-derived availability; published only if `violations()` against the approved release profile (F2C.c's deployed 82 s profile) is empty; CREDIT shows only `ACTIVE_RATE_CARD_VERSION`'s card; legacy shows the canonical revision's USD identity when the catalog can read it (`usd_price`, D10 seam); catalog outage = 503; rows cached `price_ttl`. `openrouter/provider-models.json` deleted; `provider_document()` renders the OpenRouter v2.4 document from the same entry (no USD in CREDIT, concurrency = per-key admission limit, `zdr` from retention = false). `release_violations`/`assert_release_profile` for the startup refusal (WR-1). |
| 2 | `9e9d37a0`, `e3ba286f` | Ingress refuses, in CREDIT, a resolution whose card is not `ACTIVE_RATE_CARD_VERSION` before admission (no job/hold/idempotency/reservation); spelling handed to admission verbatim. `models.price_check`: the readiness probe asking each regime for its own price (WR-2). |
| 3 | `7cb56622` | `Jobs.result_expiry` recomputation removed; status/result/DELETE apply `lifecycle.read_outcome(outcome, db_now)` over the persisted `result_expires_at`; the sync answer (idempotent replay, recovery through `_resume`) 410s past the persisted expiry or with none, on the store clock. State table in `routes/jobs.py` docstring. |
| 4 | `fa74d53f`, `05e5f610` | Route conformance (429/503 + retry guidance in sync/SSE/async/Prefer, E1C's upload sequence, tenant 404s, SSRF, documented examples vs admission); WR-I8-3 large-body gauges + drain counter. |
| G8 finding | `2bbfe0e3` | Revocation bound on the job read routes stated and pinned: a revoked key reads/cancels its own jobs for at most KEY_TTL = 60 s after the last fetch (admission refuses its next POST at once). |

Changed paths (own): `apps/infrx-api/infrx/gateway/routes/{models,validate,jobs,relay,intake,ingress}.py`, `apps/infrx-api/openrouter/provider-models.json` (deleted), `apps/infrx-api/tests/g/{test_catalog_truth,test_alias_pricing,test_route_conformance,test_intake_drain,relay_support,mutants,test_mutants}.py`, `apps/infrx-api/tests/g/jobs/{test_result_expiry,test_revocation,jobs_mutants,test_jobs_mutants}.py`. Merge resolutions: `apps/infrx-api/infrx/contracts/v2/__init__.py` and `apps/app/lib/contracts/v2/types.ts` (union of the F2C-C and F2C-L export lines, as instructed). `tests/g/ops/` and the host-metrics test untouched.

## The six read states (RESULT-EXPIRY)

| `read_outcome` | `GET /v1/jobs/{h}/result` | status `result_available` / `result_expires_at` | sync answer / replay |
|---|---|---|---|
| pending | 409 `result_pending` | false / absent | waits |
| available | 200 with `response` | true / the persisted instant | 200 with content |
| no_result, held_unknown | 200 without `response` | false / absent | the refusal its cause maps to (G2, unchanged) |
| expired (now ≥ persisted, equality passed) | 410 `result_expired` | false / absent | 410 `result_expired` |
| unavailable (no persisted expiry: pre-F2C.b row) | 410 `result_expired` | false / absent | 410 `result_expired` |
| not owned / unknown / malformed handle | one identical 404 `not_found` | same | n/a |

Metadata (state, cause, usage, usage_certainty) stays readable in every row.

## Commands

All from `apps/infrx-api` unless noted; `make api-env` once (exit 0).

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/g --ignore=tests/g/ops` (at `2bbfe0e3`) | 0 | **567 passed** (baseline at dff31efc: 527) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py tests/g/uploads/test_uploads_mutants.py tests/g/jobs/test_jobs_mutants.py` (at `e3ba286f`, detached, 36 min) | 0 | **505 passed** = every mutant of the G, uploads and jobs lists killed + list/self tests |
| `uv run --frozen python -m tests.g.jobs.jobs_mutants revocation_outlives_key_ttl` (at `2bbfe0e3`) | 0 | 1/1 killed (the one mutant added after the full run) |
| `INFRX_D_TASK=g7 uv run --frozen pytest -q` (= `make api-test` on the g7 PG port 55446, at `e3ba286f`, 47 min) | 1 | **3912 passed, 1 failed, 27 skipped, 5 xfailed**. The failure is `tests/d/test_pgharness.py::test_the_decoy_is_the_tasks_own_and_d1s_is_unchanged`: `AssertionError: ('d2', 55473)` - the coordinator's task-local patch `640cedd4` reserves `g8.valkey = 55473`, which is D2's decoy port in `tests/d/pgharness`. Not G7 code; coordinator fix. |
| `uv run --frozen pytest -q tests/contracts/v2 tests/contracts/test_config_and_imports.py --deselect tests/contracts/v2/test_v1_projection_pg.py` | 0 | 691 passed (after the F2C-L merge) |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_endpoint_doc.py` (repo root) | 0 | 10 passed (route modules still document) |
| `make console-typecheck` / `make console-test` (repo root; after the types.ts union) | 0 / 0 | tsc clean; **351/351** |
| `uvx ruff check --select F,E9 --line-length 100` on every changed module | 0 | clean (one pre-existing unused `import sys` in tests/g/mutants.py) |
| trial: `assert_release_profile` in `models.register` for pilot (reverted) | 1 | 43 failures (see WR-1) |

## Failed-then-passed regressions

| Test file | Red at | Red result | Green at head |
|---|---|---|---|
| `tests/g/test_catalog_truth.py` (12 at point 1) | `dff31efc` route + static JSON | **12 failed** (record refused: 25 validation errors / `KeyError: 'object'`, 200 instead of 503 on an outage, no approved-profile gate) | 13 passed |
| `tests/g/test_alias_pricing.py` | `7e8e291f` (point 1 only) | **1 failed**: an unapproved card was 202 with a 14.7456 CREDIT hold (3 characterization cases passed) | 4 passed |
| `tests/g/jobs/test_result_expiry.py` | `a822c701` (F2C-L merged, route unchanged) | **4 failed**: retune moved the promised instant; a pre-F2C.b success served 200; sync replay served content past expiry; table rows `unavailable` answered 200 | 4 passed |
| `tests/g/test_route_conformance.py` | `dff31efc` discovery | **1 failed** (documented examples vs discovery); 11 characterization passed | 12 passed |
| `tests/g/test_intake_drain.py` WR-I8-3 cases | before `05e5f610` | collection error (`intake.SLOTS_IN_USE` absent) | 8 passed |
| `models.price_check` case | before `e3ba286f` | 1 failed (`AttributeError`) | passed |

Mutants: 33 new mutants in the G list (item 1: 18, item 2: 5 incl. the probe, item 4: 6, WR-I8-3: 4) + `refusal_not_drained` re-anchored; 6 new in the jobs list (5 RESULT-EXPIRY + `revocation_outlives_key_ttl`) + 4 re-anchored to the new code (`status_ignores_the_ttl`, `unknown_usage_served_as_result`, `expired_result_served`, `result_ttl_on_gateway_clock`). All killed. The CI check's point is shown by two admission-side mutants (`admission_ceiling_below_advertised`, `admission_refuses_an_advertised_mime`) and one docs-side (`documented_parameter_refused`): admission drifting from discovery/docs fails `tests/g`. Default subsets gained `approved_profile_gate_removed`, `admission_ceiling_below_advertised`, `expiry_recomputed_from_settings`, `sync_replay_ignores_expiry`.

## E1B / drain (point 4, S3 F5)

`tests/g/test_intake_drain.py` reruns green on this code (real uvicorn sockets). The valid E1B
acceptance cells on `bda1586` (`models/marlin2b/results/E1B-box-bda1586/`) hold **zero 429s**
(LARGE_BODY_LIMIT=8 admitted everything): they prove no transport loss at limit 8, not
drained-429 delivery through Caddy. That public-edge measurement is E4C's P4 cell; it is not
claimed here.

## Wiring requests (coordinator; none applied)

1. **WR-1 — startup refusal past the approved release profile** (`infrx/gateway/app.py`, coordinator; plus a default). After `rt.mode = validate_runtime(rt.settings)` (app.py:74):
   ```python
   if rt.mode == "pilot":
       models.assert_release_profile(rt.settings)      # routes.models; RuntimeMisconfigured names MAX_VIDEO_SECONDS etc.
   ```
   It cannot land alone: with the code default `MAX_VIDEO_SECONDS=120` (contracts/limits.py:62, config.py:50 and :99) a trial application refused **43** cases (8 outside tests/g: `tests/contracts/test_config_and_imports.py::test_pilot_starts_with_authentication_and_metering`, 7 in `tests/m/test_pilot_media.py`; the rest the G mutant/startup lists whose copies inherit it). Pick one: (a) change the default to 82 (limits.py:62, config.py:50/:99, the expectation at test_config_and_imports.py:26; P-06/P-20/P-23 routing keeps 82 as the qualified cap) - preferred; or (b) set `max_video_seconds=82` in every pilot fixture. Proof: `tests/g/test_catalog_truth.py::test_catalog_truth__a_runtime_past_the_approved_release_profile_advertises_nothing` (asserts the refusal names `MAX_VIDEO_SECONDS`) plus `pilot_app(support.settings())` raising once (a) or (b) is in. Until then a pilot left at 120 publishes **nothing** on `/v1/models` (fail closed) but still admits 83-120 s clips the engine refuses.
2. **WR-2 — price_source probe** (`infrx/gateway/pilot.py:287`): `"price_source": Probe(models.price_check(catalog, settings))` in place of `Probe(price_check(catalog, settings.model_id, deployment.accounting_regime, pilot.active_rate_card_version))` (then `pilot.price_check` is unused). Must land with D10's `usd_price` (WR-3): in the legacy regime the new probe reads unavailable without it and a pilot refuses to start. Proof: `test_catalog_truth__readiness_asks_the_price_admission_actually_reads`.
3. **WR-3 — D10 (state/catalog.py, jobstore.py, new migration)**: (a) `PgCatalogDirectory.usd_price(model_revision) -> PriceSnapshot | None` = the effective `infrx.price_versions` row keyed by the canonical revision (P-22 as decided); discovery in the legacy regime and WR-2 call it by that name; (b) `active_rate_card` = the card the effective listing names (F2C-C finding; discovery and the ingress call it); (c) the owned reads (`get_owned`, `get_owned_credit`, `lookup`, `cancel` via `_OUTCOME_FIELDS` + `job_admission`'s outcome document) carry `result_expires_at`. **Ordering: G7's point 3 must merge with or after (c)** - against today's PgJobStore every success has no persisted expiry and reads `unavailable` (410 on /result and on the sync answer); (d) optionally the listing version resolution landed on (routes/models.py `LISTING_VERSION = 1` placeholder) and a card-approved flag (`PROVISIONAL = True` placeholder).
4. **WR-4 — WR-I8-3 families** (`infrx/observe/metrics.py` FAMILIES, after `infrx_inflight_limit`, I3B/I8):
   ```python
   "infrx_large_body_slots_in_use": Spec("gauge", "Large request and upload bodies holding a slot now (LARGE_BODY_LIMIT)."),
   "infrx_large_body_slots_limit": Spec("gauge", "The large-body slots configured (LARGE_BODY_LIMIT)."),
   "infrx_large_body_refused_total": Spec("counter", "Large bodies refused 429 because every slot was held."),
   "infrx_intake_drained_total": Spec("counter", "Refused bodies read to their declared end so the caller reads the refusal.",
       (("code", frozenset({"invalid_api_key", "request_too_large", "capacity_exhausted", "deadline_exceeded"})),)),
   ```
   The intake records them only once declared (no raise before). Proof: `tests/g/test_intake_drain.py::test_ops_alert__the_large_body_gauges_move_under_a_held_slot` (declares them via monkeypatch exactly as above). Optional: `pilot.py:285` may pass `registry=rt.metrics` to `LargeBodies`; the ingress attaches it anyway. Free slots = `limit - in_use`.
5. **WR-5 — retire MODELS_DOC** (config.py:30,60,108; README.md:28,50; deploy/preflight.py:205): nothing reads it since `provider-models.json` is deleted. Optional hygiene.
6. **WR-6 — E (endpoint_doc.py)**: the regenerated `E4B-endpoint.md` Limits table must state the approved 82 s ceiling (today `max_video_seconds | 120 | ... P-20 applies 72`, from `limits.DEFAULTS`) and the rate card from `published_model.price()` (F2C-C matrix). The examples already pass G7's docs check (`test_api_modes__every_documented_example_answers_what_the_document_says`).
7. **WR-7 — A3**: `apps/app/app/(console)/docs/page.tsx` (120 s, "never stored") and the models page must read the projection (F2C-C matrix); G7 serves it at `/v1/models`.
8. **Composition points `pilot.py:212` / `:280` (F2C-L update)**: G7's routes need nothing new there beyond WR-2/WR-4: the relay's `jobs` must keep `db_now()` (PgJobStore has it) and return persisted expiries (WR-3c); the upload routes take whatever `rt.media_store` M5 composes (the `create_upload/put_upload/finalize_upload` signatures are unchanged on codex/m5-uploads); discovery needs only `rt.ingress.catalog` with WR-3a/b.

## Open items waiting on siblings / decisions

- **D10 (hard dependency of point 3):** persisted `result_expires_at` on the PG owned reads (WR-3c); `usd_price` and the listing's card (WR-3a/b). Until then the legacy-regime discovery publishes nothing (no USD identity readable) - fail closed, as `project` requires.
- **F2C-L `admit_ready` (update 3):** the relay still calls `admit`/`admit_credit`; moving it to `ReadinessStore.admit_ready(request, idem, AdmissionExpectation)` waits for D10's store implementing it. The ingress's pre-admission card check (point 2) becomes the expectation's card then.
- **M5:** not merged here; `tests/g/test_route_conformance.py`'s E1C sequence and tenant cases must be rerun on M5's `MediaUploads` at integration (signatures unchanged; mutant `foreign_upload_owned`'s anchor `.org_id != org_id:` exists once in both versions).
- **held_unknown on the sync answer:** G2's money-N2 serves a held-unknown success's text without usage on the sync path (now only before its persisted expiry), while `read_outcome` classifies `held_unknown` as no content for `/result`. Pre-existing divergence, kept; a product ruling if the sync path should follow the table.
- **OpenRouter:** in the CREDIT regime the provider document carries no USD price (none may be derived) - product decision whether the listing is withdrawn at the CREDIT cutover (F2C-C open issue); OpenRouter itself is deferred and nothing serves the document.
- **Unauthenticated /v1/models load:** catalog rows cached `price_ttl` (300 s) with one refresh at a time; a card/alias change reaches discovery within that window (admission is unaffected).
- **E4B-endpoint.md** Limits table (WR-6) and the App docs (WR-7) still state 120 s.
- **Revocation bound (G8 finding):** stated at KEY_TTL = 60 s for the read routes while the identity source answers. During an identity-source outage `auth/keys.py` keeps serving a key cached before the outage past its TTL (F1's availability choice): a revoked key is then usable until the source answers again. Decision for the coordinator/product (fail closed = every tenant 503s during a Supabase outage). `PublishedModel` is closed, so the bound cannot ride in the projection; the U2/A3 copy states "revocation stops new requests immediately and read access within 60 seconds".
- **Coordinator patch defect:** `640cedd4` reserves port 55473 for g8 valkey, colliding with `tests/d` D2's decoy (the one api-test failure).

## Proposed ruling text (coordinator numbers it)

> **Discovery and result reads (G7).** `GET /v1/models` publishes only `PublishedModel` records produced by `published_model.project` over a consumer credential's resolution of the served model and the `serving_profile` built from the values admission reads, and only when `violations()` against the approved release profile is empty; nothing is read from a static document. In the CREDIT regime discovery and the ingress accept only the card `ACTIVE_RATE_CARD_VERSION` names, and the ingress refuses any other before admission (no hold); admission still re-resolves and pins, and the relay's post-admission recheck stays. Every read of a job's result - status, result, DELETE's status, a sync answer or idempotent replay, and any console read port - classifies with `lifecycle.read_outcome` over the persisted `result_expires_at` on the store clock; content only for `available`, `result_expired` for `expired` and `unavailable`, metadata kept; an expiry is never recomputed from configuration.

## Remaining effort

Optimistic 1 h, likely 3 h, pessimistic 8 h; confidence medium. Basis: all four points and both coordinator follow-ups (WR-I8-3, revocation bound) are implemented and green; left are review fixes, re-running the conformance cases on M5's and D10's adapters when they merge (point 3 is inert-to-fail-closed until WR-3c), and whatever WR-1's default decision changes in the G tests (the pessimistic case: the startup refusal lands with fixture changes across tracks and the G subset needs re-anchoring).

## Verification log

- 2026-09-25: written by lane G7 at code head `2bbfe0e3`; every count from this worktree; no hosted database, box, edge or AWS access.
