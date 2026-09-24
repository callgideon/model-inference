# F2C.c — public capability and accounting identity (published-model projection)

Lane F2C-C, consumer v1 wave (program 22). Task F2C slice c; closes the contract half of
RV-01 and the P-22 decision, and answers the coordinator's S3 finding F11 (one public rate
identity). No route, catalog or SQL implementation here: G7/D10/A3/E1C/G8 consume it.

| | |
|---|---|
| Base | `dff31efc` (main) |
| Head (code) | `d414607d` (commits `d62b1bd5`, `d414607d`); this file and the update JSON follow in the next commit |
| Branch / worktree | `codex/f2c-catalog` / `.claude/worktrees/codex-f2c-catalog` |
| Changed paths (all new) | `apps/infrx-api/infrx/contracts/v2/published_model.py`, `…/v2/published_fixtures.py`, `…/v2/published/{published_marlin_credit,published_marlin_legacy_usd,serving_profile_marlin,cases,alias_compatibility}.json`, `apps/infrx-api/tests/contracts/v2/test_published_model.py`, `apps/app/lib/contracts/v2/published-model.ts`, `apps/app/tests/contracts/v2/published-model.test.ts`, this file, `research/plan/evidence/coordinator/updates/F2C-C-20260924T2215Z.json` |
| Existing files edited | none (exports and cross-references are wiring requests below) |

## 1. The contract

`PublishedModel` (Python `infrx.contracts.v2.published_model`, TS `lib/contracts/v2/published-model.ts`),
closed (`extra="forbid"` / unknown key refused), `schema_version: 2`, strict scalars (no `"82"`,
`0` for `false`, `true` for `1`, `400` or `"400"` for an amount):

| Field | Meaning | Marlin as deployed (bda1586) |
|---|---|---|
| `object`, `id`, `created`, `owned_by` | OpenAI list entry; `id` = canonical public id | `model`, `nemostation/marlin-2b`, 1788220800, `nemostation` |
| `model_revision` | canonical resolved identity `<id>@<revision label>` (R62) | `nemostation/marlin-2b@2026-09-01` |
| `aliases` | spellings that resolve to this record: only `id` (floating, highest listing) and `model_revision` | both |
| `listing_version`, `deployment_revision_id` | the catalog listing and deployment revision resolution lands on | 1, `c0000004-…` |
| `serving` (`ServingIdentity`) | immutable serving revision: repo, commit, per-shard weight digests, tokenizer + chat-template digests, digest source, prompt harness, preprocessor profile, runtime image ref/digest, engine-options digest, precision | from the F2P serving fixture (S2M/W3 pins) |
| `capability.input_modalities` / `output_modalities` | | text, video → text |
| `capability.execution_modes` | `stream` = SSE output; `async` = explicit jobs | async, stream, sync |
| `capability.parameters` / `unsupported_parameters` | exact allow-list (`validate.SUPPORTED`) / refused by name (`validate.UNSUPPORTED`: tools, tool_choice, functions, function_call, parallel_tool_calls, response_format, logprobs, top_logprobs, logit_bias, price_snapshot) | 12 / 10 |
| `capability.max_request_bytes`, `max_context_tokens`, `max_input_tokens`, `max_output_tokens` | `Validator.ceilings`: the deployment's, never past the pilot limits | 100663296, 32768, 30720, 2048 |
| `capability.video` | `max_seconds` **82**, `max_bytes` 67108864, `max_per_request` 1, `mime_types` (validator ∩ MediaProfile) mp4/quicktime/webm, `live_stream` **false** (no native live-video input); declared constraints `profile_version` marlin2b.video.v1, `fps` 2, `min_frames` 4, `max_frames` 240, `max_pixels_per_frame` 200704 | |
| `pricing.regime` | the regime admission charges in | `credit` (target) / `legacy_usd` (pilot today) |
| `pricing.credit` (`CreditRate`) | the one public rate identity: `rate_card_version` + unit `CREDIT` + meter + rates + `effective_at` + `provisional` | `rc_marlin2b_2026_09_provisional`, 400/1200 per million, provisional |
| `pricing.legacy_usd` (`UsdPrice`) | separate USD price identity, keyed by the canonical `model_revision` (P-22); never derived from a CREDIT card | `pv_marlin2b_usd_2026_09_r1`, 0.10/0.30, tr-1 |
| `retention` (`ServingRetention`) | `zero_data_retention` and `capture_off_deletes_serving_content` are **always false** (type-enforced); `trace_capture_default` off; `content_stored` request_payload/source_media/prepared_media/result/stream_journal; logical TTLs result 86400, stream journal 3600, idempotency 86400, processing cache 604800; `physical_deletion_bound_s` absent = no committed bound (P-25) | |
| `availability`, `availability_as_of` | accepting requests now; separate from capability; no capacity/latency figure (P-18) | available |

Functions (Python; TS twin in brackets): `split_model` [`splitModel`] — the 0008 grammar,
malformed = `not_found`; `resolve` [`resolveModel`]; `price` [`priceRequest`] → `PricedResolution`
(requested string verbatim + canonical identity + exactly one price identity); `project`
(Python only) — refuses private/non-active (`not_found`), unpriced/mispriced/USD keyed by a
spelling/capability beyond the serving revision or deployment (`invalid_request`);
`serving_profile` (Python) — the enforced profile from runtime values; `violations`
[`profileViolations`] — the profile-vs-projection check; `catalog_problems` (Python) — aliases
resolve back to their record, revisions unique; `parsePublishedModel`/`parseServingProfile`/
`canonicalJson` (TS) — strict parse and the canonical bytes.

**Disabled/unpriced fails closed:** `project` never produces a record for a private,
proposed, draining or retired deployment, nor one without the active regime's price identity;
`Pricing` itself cannot be constructed unpriced, and in the legacy regime no CREDIT rate is
shown beside the USD one.

## 2. One public rate identity (S3 F11)

The public rate identity of a published revision is `(rate_card_version, CREDIT)` of **the card
its effective catalog listing names** (`catalog_listings.rate_card_version`) — the card 0008
`resolve_admission_pins` pins. `project` must be handed that card; a card that prices another
deployment is refused (`test_the_g6b_minted_card_is_not_the_seeded_deployment_s_public_rate`).

- Hosted catalog (W7b seed): `rc_marlin2b_2026_09_provisional` — the public identity.
- `rc_marlin2b_20260901T000000Z_provisional_p01` — minted by `marlin_release()`
  (`infrx/operations/service.py:441`) and computed **locally** by `certify.published_release()`
  (`tests/integration/backend/certify.py:258-268`) and `endpoint_doc.py:253,269` with a
  placeholder provider org; it prices G6B's own `stable_id` deployment and was never written to
  the hosted catalog. It is not a public identity. If G6B `publish` ever persists a card, its
  listing move makes that card the identity for **new** admissions only.
- History: every CREDIT job/hold/usage row keeps its own pinned `rate_card_version` (R78) and
  settles/reads through it (0018 `debit_credit(j.rate_card_version)`); no mapping table, no
  rewritten row. Hosted has no CREDIT history (`credit_admission` false): every hosted job is
  `legacy_usd` and carries its own `price_version` snapshot. Both card ids stay resolvable as
  immutable rows and both are provisional (P-01).

## 3. Alias compatibility (P-22) — every model string in code, fixtures and results

`before` = what the hosted pilot did at bda1586 (legacy_usd; USD looked up by the literal
string); `after` = `price()` under resolve-then-price. Recorded in
`published/alias_compatibility.json`; `test_no_historical_resolution_or_price_changes` (Python)
and the console's `alias … resolves and prices as in Python` hold it;
`test_the_table_covers_every_model_string_in_fixtures_and_results` fails if a new spelling appears
in results/fixtures/E4B doc/0002 seed without a row.

| Requested | Found in | Before (hosted) | After, legacy_usd | After, credit |
|---|---|---|---|---|
| `nemostation/marlin-2b` | config `MODEL_ID` default (omitted model), 0002 seed id/snippets, provider-models.json, seed listing, W7c | c…04, `pv_marlin2b_usd_2026_09` 0.10/0.30 tr-1 | c…04, `…_r1` 0.10/0.30 tr-1 (same rates; new label) | c…04, `rc_marlin2b_2026_09_provisional` |
| `nemostation/marlin-2b@2026-09-01` | v1/v2 fixtures, E4B doc, certify, E1B/E4B results (bench `--model`), W7e | c…04, `pv_marlin2b_usd_2026_09_r1` | identical | c…04, seed card |
| `nemostation/marlin-2b-dev@2026-09-01` | v2 fixtures `DEV_REQUESTED_MODEL` | not_found | not_found | not_found |
| `nemostation/marlin-2b-dev` | R101 private naming | not_found | not_found | not_found |
| `marlin2b` | bench.py/smoke.py default (vLLM served name), results/bench.jsonl (direct only) | not_found | not_found | not_found |
| `NemoStation/Marlin-2B` | serving `model_repo`, research/matrix | not_found | not_found | not_found |
| `marlin-2b@2026-09-01` | console view-model tests (R62 unprefixed form) | not_found | not_found | not_found |
| `nemostation/marlin-2b@tokcost` | tests/w/test_prep_worker.py | not_found | not_found | not_found |
| `deepseek-ai/DeepSeek-V4.1-Flash`, `Qwen/Qwen3.8-27B`, `moonshotai/Kimi-K3` | 0002 coming_soon rows | not_found | not_found | not_found |

Private dev resolution for a `provider_dev` credential (R101, `state/catalog.py` `_PRIVATE`) is
outside the public projection and unchanged. Excluded as not client model strings: unit-test
placeholders (`m`, `m@1`, `other@1`, `other/model@2026-09-01`), the judge's `claude-opus-5`,
other experiments' direct-vLLM strings, docs placeholders (`your-model-name`, `%s`). Run1 of the
box certification (before W7e) refused the labelled spelling: that was the P-22 defect, not a
behaviour to preserve.

## 4. Proposed amendment for `15-pending-inputs.md` P-22 (coordinator applies)

> **P-22 — DECIDED (proposed by F2C.c, 2026-09-24): resolve, then price.** A request's model
> string is resolved exactly once, by the catalog grammar `<public_model_id>[@<revision_label>]`
> (0008 `resolve_admission_pins`; contract `contracts.v2.published_model.split_model`/`resolve`),
> before any price is read; the price is a property of the resolved revision, not of the
> spelling. **CREDIT:** admission pins the resolved deployment revision and the card the
> effective catalog listing names — the one public rate identity `(rate_card_version, CREDIT)`
> (unchanged from 0011). **Legacy USD:** `infrx.price_versions` is read by the resolved canonical
> `model_revision` (`<public_model_id>@<revision_label>`), never by the literal request string
> and never by converting a CREDIT card; one row per revision. Every admitted job records the
> caller's `requested_model` verbatim beside the immutable canonical `model_revision`,
> deployment/serving revision and the one price identity of its regime (an omitted `model`
> records the served default the ingress substituted). **Nothing is rewritten:** `price_versions`
> rows are immutable and stay referenced by the jobs that snapshotted them; admitted jobs keep
> their pinned snapshot or card. The W7c row (`pv_marlin2b_usd_2026_09`, keyed
> `nemostation/marlin-2b`) is no longer read by new admissions (the operator may set its
> `effective_to`); the W7e row (`pv_marlin2b_usd_2026_09_r1`, keyed
> `nemostation/marlin-2b@2026-09-01`) is the canonical row, so W7e becomes the permanent rule
> "one USD row per revision" and "one row per spelling" ends. **Compatibility**
> (`published/alias_compatibility.json`, 11 spellings): both spellings that priced on the pilot
> resolve to the same deployment at identical rates and token rules (0.10/0.30 USD per million,
> tr-1); the unlabelled spelling's future requests record `…_r1` instead of
> `pv_marlin2b_usd_2026_09`; every spelling refused before stays `not_found`. **Rate identity
> (S3 F11):** the public identity is the listing's card, `rc_marlin2b_2026_09_provisional` on the
> hosted catalog; `marlin_release()`'s `rc_marlin2b_<ts>_provisional_p01` is a locally computed
> label, not persisted on the hosted catalog and not a public identity; history maps through each
> job's pinned id. Owners: D10 (new migration: `admit_legacy_usd` resolves through the listing
> before `price_versions`, records `requested_model` for legacy jobs; `CatalogDirectory`
> returns the listing's card), G7 (discovery from the projection), G8 (publication moves the
> listing to the published card atomically). Evidence:
> `research/plan/evidence/f/F2C-catalog-d414607.md`.

## 5. Proposed ruling R109 (08 §10; coordinator numbers and appends)

> | R109 | Published-model projection: one resolution, one price identity, no overclaim (F2C.c) | The public catalog entry is `contracts.v2.published_model.PublishedModel` (TS twin `lib/contracts/v2/published-model.ts`), produced only by `project` from trusted rows; a private, non-active or unpriced deployment is never published (`not_found` / `invalid_request`, R69/R70). A model string resolves only by `split_model` + `resolve` (0008's grammar; the unlabelled id takes the highest effective listing, a pin its own revision) and is priced after resolution: CREDIT by the card the effective listing names — the one public rate identity `(rate_card_version, CREDIT)` — and legacy USD by the `price_versions` row keyed by the canonical `model_revision`; the admission pin records the requested string verbatim beside the canonical identity and exactly one price identity (P-22). No price is converted between units. `availability` is separate from `capability`, and no capacity or latency figure is published. A published projection must pass `violations(projection, serving_profile(<runtime>))`: caps at or below the enforced value, vocabularies (modalities, modes, parameters, MIME types) as subsets, a named refusal really refused, live video only if served, the preprocessing constraints and every retention figure exactly; `ServingRetention` cannot state zero data retention or that turning capture off deletes serving content. Both languages parse the same closed record (unknown or missing field, coerced number/flag, non-canonical or wrong-unit amount → refusal) and re-serialize it to identical canonical bytes. |

## 6. Consumer matrix

| Consumer | Consumes | Code to replace (file:line at dff31efc) |
|---|---|---|
| **G7** (discovery, alias pricing) | `project`, `serving_profile`, `violations`, `catalog_problems`, `resolve`/`price`; every `PublishedModel` field for `GET /v1/models` (the entries are the records; an OpenRouter document is rendered from `capability`/`pricing`, never hand-kept) | `infrx/gateway/routes/models.py:6-15` (returns the static file); `openrouter/provider-models.json:11` (2-minute copy), `:29` (120 s), `:50` (`tools`), `:61-64` (concurrency 16, 240 rpm, `is_ready`), `:69` (`zdr: true`); `infrx/gateway/pilot.py:117-126` (`price_check` in the legacy regime checks a CREDIT card, not the USD price admission reads); `infrx/state/catalog.py:47-52` + `infrx/gateway/routes/catalog.py:73` (`active_rate_card` = newest card for the deployment, while 0008 pins the listing's card — must be the listing's); `infrx/config.py:50,99` (code default 120 s: build the profile from the running settings, `test_the_code_default_120s_profile_is_not_the_deployed_one`); add the seam test (appendix A) to `tests/g/` |
| **A3** (catalog, docs, examples) | `parsePublishedModel`; `capability` (modalities, parameters/unsupported, `video` caps and sampling, `execution_modes`), `pricing.regime` + `pricing.credit` (CREDIT, never `$`, `provisional` disclosed), `retention` (what is stored, TTLs, no ZDR, deletion bound pending P-25), `availability`, `model_revision` for snippets | `app/(console)/models/page.tsx:17` (reads `public.models`), `:64-66,95-101` (USD via `money()`); `app/(console)/docs/page.tsx:18-19` (cites provider-models.json), `:28` ("max is 120s"), `:49` (`public.models`), `:118` (`limits.max_video_seconds ?? 120`), `:127` (frames: keep, from the projection), `:147` ("We never store prompts, videos or completions"); `lib/types.ts:9-16` (Model USD fields/limits); `supabase/migrations/0002_seed_models.sql:14,18,21` are immutable (2-minute copy, USD, 120) — stop reading them, do not edit |
| **D10** (pricing seam, SQL) | `PricedResolution` shape (requested verbatim, canonical `model_revision`, deployment/serving, one price identity); `UsdPrice` keyed by the canonical revision; `alias_compatibility.json` as a real-PostgreSQL acceptance table (resolve + price every row on the seeded catalog, before/after the new migration) | `supabase/migrations/0011_admission.sql:440-445` (USD lookup by literal `r->>'model_revision'`) and `:461` (legacy job `model_revision` = literal; `requested_model` not recorded) — by a **new** migration (0001–0018 immutable); `0003_pilot_durable_schema.sql:72-88` rows kept as is; `infrx/state/catalog.py:47-52`; `infra/runbooks/rollout.md:74` (W7e text → the canonical-row rule; W7c `effective_to`) |
| **E1C** (client, load) | `model_revision` (what the gateway target sends), `capability.video` (82 s cap, bytes, MIME, fps/frames for segmentation), `max_output_tokens`, `execution_modes` (async), `PricedResolution` (per-item price identity for reconciliation) | `models/marlin2b/bench.py:399` and `smoke.py:20` (default `marlin2b` is the engine's served name: valid for `--target direct` only; the gateway target defaults to the projection's `model_revision`); `tests/integration/backend/certify.py:258-268`, `endpoint_doc.py:253,269` (rate identity from `marlin_release()`: read `price()` from the projection instead), regenerated `research/plan/evidence/e/E4B-endpoint.md:14,63` (G6B-minted card; 120 s "P-20 applies 72") |
| **G8** (card publication, transition dry-run) | the one-public-rate-identity rule (§2); `project` refuses a card for another deployment; dry-run = `price()` for every alias before/after publication | `infrx/operations/service.py:337-375` (`publish` writes the card, then `move_alias`, which picks the newest effective card at move time, `infrx/state/operations.py:246-262` — the listing must name the published card), `:405-455` (`marlin_release` id minting is a label) |
| M, W, I8 | none directly; media profile values are inputs to `serving_profile`; I8/P-25 supply `physical_deletion_bound_s` | S2M `research/workloads/marlin-sop.md` §2.5 lists `video/mpeg`, which the code accepts nowhere (doc drift) |

## 7. Commands (worktree root unless noted)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env |
| `cd apps/app && pnpm install --frozen-lockfile` | 0 | |
| `cd apps/infrx-api && uv run --frozen python -m infrx.contracts.v2.published_fixtures --write` | 0 | wrote the 5 fixtures; rerun: "would change nothing" |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts/v2/test_published_model.py` | 0 | **89 passed** (new) |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts --deselect tests/contracts/v2/test_v1_projection_pg.py` | 0 | **1164 passed** (1075 existing + 89 new), 3 deselected |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts` | 1 | 1164 passed, 3 failed = `test_v1_projection_pg.py` `HarnessBusy` (port 55432 held by `codex-e2c-verify`; "Nothing was altered"); environment, not product; not rerun (no port assigned to this lane) |
| `cd apps/app && node --test tests/contracts/v2/published-model.test.ts` | 0 | **52/52** (new) |
| `make console-test` | 0 | **341/341** (289 existing + 52 new), 0 skipped |
| `make console-typecheck` | 0 | |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (conformance.ts:4294, fake-services.ts:81) |
| `python3 scratchpad/f2c-c/mutate.py <worktree>` | 0 | **18/18 ad-hoc mutants killed** (12 Python, 6 TS: ZDR literal, cap comparisons, video caps, requested string on the pin, highest listing, legacy CREDIT guard, deployment state guard, canonical amounts, USD key, video/modality, retention loop, tool capability; TS pricing check, caps, listing, closed record, canonical amount, ZDR) |
| trial wiring (applied, verified, reverted): `v2/__init__.py` + `v2/types.ts` patches below | 0 | `v2.published_model` reachable; `tests/contracts/test_config_and_imports.py` + `tests/contracts/v2` 602 passed; `v2.parsePublishedModel` via `lib/contracts/types.ts` 1/1; `tsc --noEmit` 0; console 341/341 |

Not run: `make api-mutants` / `make console-mutants` (no mutated file changed), the full `make
api-test` (Docker suites need a port range this lane was not assigned).

## 8. Failed-then-passed

1. **Seam regression at the old behaviour (G7's to adopt, appendix A):** run against dff31efc's
   `GET /v1/models` → **2 failed**: the served entry is not a `PublishedModel` (25 validation
   errors: `schema_version` "2.4", no revision/aliases/serving/capability/pricing/retention/
   availability), and the static document's claims mapped onto the projection give
   `capability.parameters: advertised ['tools']` and `capability.video.max_seconds: advertised
   120, enforced 82` (its `zdr: true` is refused at parse). It stays red until G7 replaces the
   route; it is not committed in this lane (tests/g is G7's).
2. First run of the new Python suite: 2 failed — `"400"` and `400` parsed as CREDIT amounts
   (pydantic's lax decimal), where the console refuses both. Fixed with a canonical-wire-string
   rule for `CreditRate`/`UsdPrice` amounts (as `UsageRecordV2.charged_amount`); 88 → 89 passed.
3. First TS run: Node's strip-only mode refused a parameter property in `ContractRefusal`;
   rewritten as a plain field.

## 9. Wiring requests (coordinator; verified by trial application, then reverted)

1. `apps/infrx-api/infrx/contracts/v2/__init__.py`:
   ```diff
   -_SUBMODULES = ("fixtures", "money_units", "ports", "records")
   +_SUBMODULES = ("fixtures", "money_units", "ports", "published_fixtures", "published_model",
   +               "records")
   ```
   Proof: `python -c "import infrx.contracts.v2 as v2; v2.published_model"`;
   `pytest tests/contracts/test_config_and_imports.py tests/contracts/v2` 602 passed.
2. `apps/app/lib/contracts/v2/types.ts`, after `export * from "./money-units.ts";`:
   ```diff
   +// F2C.c: the published-model projection travels with the revision (`v2.parsePublishedModel`).
   +export * from "./published-model.ts";
   ```
   (No name collides inside the `v2` namespace; `published-model.ts` restates v1's
   `EXECUTION_MODES` rather than importing `../types.ts`, which would cycle; a test holds them
   equal.) Proof: `import { v2 } from "lib/contracts/types.ts"; v2.parsePublishedModel` 1/1,
   `tsc --noEmit` 0, console 341/341.
3. Apply the P-22 amendment (§4) to `research/plan/15-pending-inputs.md` and number R109 (§5) in
   `research/plan/08-contracts-v1-encoding.md` §10; optionally list `published/` in
   `infrx/contracts/README.md` (fixture location: beside the module, because
   `tests/contracts/test_fixtures.py` guards `contracts/fixtures/` to hold exactly the F2P base).
4. G7: adopt appendix A in `tests/g/` (red now; green when discovery serves the projection).
   D10: run `alias_compatibility.json` against real PostgreSQL before/after its migration.
5. Coordinator-only box check (this lane may not): confirm on the hosted catalog that the
   effective `nemostation/marlin-2b` listing names `rc_marlin2b_2026_09_provisional` and that
   `infrx.price_versions` holds exactly the two rows of `HOSTED_USD_ROWS` (names/rates only).

## 10. Open issues

- `physical_deletion_bound_s` is absent until P-25 commits a bound; A3 must say "stored, deletion
  bound not yet committed", not a number.
- The OpenRouter provider document prices in USD (`cost_usd`). In the credit regime there is no
  USD price for new requests and none may be derived from CREDIT: product decision whether that
  listing is withdrawn at the CREDIT cutover or publishes the legacy regime only while it lasts.
- Video transport (http(s) URL, `data:`, `infrx-upload:`) is not in the projection: uploads do not
  yet survive gateway reconstruction (RV-02), so advertising them is D10/G7's call after RV-02.
- The hosted row values in `HOSTED_USD_ROWS` are transcribed from session-02:895/903 and handoff 20
  §14.1, not read from the database (this lane may not): wiring request 5.
- `tests/contracts/v2/test_v1_projection_pg.py` did not run (shared harness busy; no port for this
  lane).

## 11. Remaining effort (this slice)

Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h; confidence medium. Basis: code, fixtures and both
suites are done; left are review fixes, applying the wiring requests, and possible reshaping of
field names once G7/A3 read the record in their own lanes (the pessimistic case: a reviewer asks
for the OpenRouter rendering or upload transport to move into the contract).

## Appendix A — G7 seam regression (red at dff31efc)

```python
"""GET /v1/models publishes only what the deployed profile admits (F2C.c handoff)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import records as v1
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2 import published_model as pm
from infrx.gateway.routes import models, validate
from infrx.media.prepare import MediaProfile

from . import support


def _profile(settings):
    return pm.serving_profile(
        limits=settings.pilot, deployment=v2fix.BUILDERS["deployment_revision_public.json"](),
        serving=v2fix.BUILDERS["serving_revision.json"](), parameters=validate.SUPPORTED,
        refused=validate.UNSUPPORTED,
        video_mime=set(settings.allowed_video_mime) & set(MediaProfile().allowed_mime),
        fps=int(settings.fps), min_frames=settings.min_frames, max_frames=settings.max_frames,
        max_pixels_per_frame=settings.px_per_frame, execution_modes=list(v1.ExecutionMode))


def test_v1_models_publishes_only_what_the_deployed_profile_admits():
    rt = support.runtime(support.settings(max_video_seconds=82.0))
    app = FastAPI()
    models.register(app, rt)          # G7: register with whatever the new route needs
    body = TestClient(app).get("/v1/models").json()
    assert body["data"], "the catalog is empty"
    profile = _profile(rt.settings)
    for entry in body["data"]:
        assert pm.violations(pm.PublishedModel.model_validate(entry), profile) == []
```

## Verification log

- 2026-09-24: written by lane F2C-C at code head `d414607d` (base `dff31efc`); every count above
  is from this worktree; no hosted database, box, edge or AWS access.

## Fix round (2026-09-24, one round; code head `35d1e32a`, base `dff31efc`, previous head `042ea49d`)

Changed paths (all lane-owned, created by this lane): `apps/infrx-api/infrx/contracts/v2/published_model.py`,
`…/v2/published_fixtures.py`, `…/v2/published/cases.json` (regenerated; the two record fixtures
and the profile are byte-identical), `apps/infrx-api/tests/contracts/v2/test_published_model.py`,
`apps/app/tests/contracts/v2/published-model.test.ts`, this section,
`research/plan/evidence/coordinator/updates/F2C-C-20260924T2228Z.json`. No existing file edited
(`git diff --stat dff31efc..HEAD` lists only the lane's new files).

| Finding | Fix | Regression (fails before / under the mutant, passes after) |
|---|---|---|
| 0-F1, 2-F2C-C-R1: the visibility half of `project()`'s publish guard was untested | test only: `(Visibility.private, DeploymentState.active)` added to `test_a_private_or_deactivated_deployment_is_never_published` (`records.py:415-424` constructs it) | mutant `project-no-visibility` (visibility clause → `False`): SURVIVED 89 passed at `042ea49d` → KILLED |
| 0-F2: modality, stream-output and serving-revision guards in `project()` untested | new `test_a_projection_the_serving_revision_does_not_back_is_refused` (4 cases: video on a text-only revision, text output undeclared (`CapabilityRecord.output_modalities` has no min length), stream on `stream_output=False`, deployment pinning another serving revision). `test_a_capability_the_serving_revision_does_not_declare_is_refused` now passes a profile that accepts the claim, so only the declaration or the deployment ceiling can refuse | `project-in-modality`, `project-out-modality`, `project-stream-out`, `project-serving-mismatch`: SURVIVED → KILLED |
| 0-F2: `violations()` modality/mode subsets and "video, profile has none" untested (both languages) | `cases.json` violation cases carry `profile_patches` (applied to the profile doc by both suites; `[]` for the old cases): "SSE streaming on a deployment without it" (profile modes async/sync → `capability.execution_modes`), "video input on a text-only deployment" (profile text-only, no video → `capability.input_modalities`, `capability.video`). `output_modalities` is `Literal["text"]` with min length 1 on both sides, so its subset rule cannot fire through a parsed record; the input half kills the SUBSETS mutant | `viol-subsets-no-modalities`, `viol-subsets-no-modes`, `viol-video-none`, `ts-subsets-no-modalities`, `ts-subsets-no-modes`, `ts-video-none`: SURVIVED → KILLED |
| 2-F2C-C-R2: `project()` could emit an overclaiming record | `project(..., capability, profile: ServingProfile, ...)`: the `retention` argument is gone (published retention = `profile.retention`, which `violations` requires exactly anyway); after building the record, `violations(record, profile)` non-empty → `InvalidRequest("the projection advertises what the serving profile refuses: …")` (`published_model.py:450-454`). `published_fixtures.published()` passes the deployed profile | before: `project(capability=<82 s profile with max_seconds 120>)` at `042ea49d` returned a record with `max_seconds` 120 (`violations` = `capability.video.max_seconds: advertised 120, enforced 82`). New `test_project_refuses_what_the_serving_profile_refuses` (stale 120 s cap; tools declared by the revision but refused by the validator), `match=` the violated path, plus an understated 60 s cap still publishes. Mutant `project-no-profile-check` → KILLED |

Failed-then-passed: the updated Python suite against the unfixed module (`042ea49d` code) gave
**36 failed, 60 passed** (`project()` has no `profile` argument; `cases.json` has no
`profile_patches`); after the fix **98 passed**.

| Command (worktree root unless noted) | Exit | Result |
|---|---|---|
| `cd apps/infrx-api && uv run --frozen python -m infrx.contracts.v2.published_fixtures --write`, then again without `--write` | 0 | wrote `cases.json` only; rerun "would change nothing" (deterministic) |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts/v2/test_published_model.py` | 0 | **98 passed** (89 + 9: 1 visibility, 4 declaration, 2 profile check, 2 violation cases) |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts --deselect tests/contracts/v2/test_v1_projection_pg.py` | 0 | **1173 passed**, 3 deselected (port 55432 held by `infrx-d1-postgres`; not this lane's) |
| `cd apps/app && node --test tests/contracts/v2/published-model.test.ts` | 0 | **54/54** (52 + 2 violation cases) |
| `make console-test` | 0 | **343/343**, 0 skipped |
| `make console-typecheck` | 0 | clean |
| `make console-lint` | 0 | 0 errors, the 2 pre-existing warnings |
| `python3 <scratchpad>/f2c-fix/mutants.py` (apply-then-revert) | 0 | **15/15 killed**: the 12 reviewer mutants above plus `project-structured`, `project-live-video`, `project-deploy-ceiling` (declaration tests now isolate each guard) |
| `python3 <scratchpad>/f2c-c/mutate.py <worktree>` (the lane's original set) | 0 | **18/18 killed** (unchanged) |

Proposed R109 (§5) wording change for the coordinator: replace "produced only by `project` from
trusted rows" with "produced only by `project` from trusted rows and the enforced
`serving_profile`, which refuses any record `violations` flags", and replace "A published
projection must pass `violations(projection, serving_profile(<runtime>))`:" with "`project`
refuses, and G7's CI check re-asserts, any `violations(projection, serving_profile(<runtime>))`:".
Consumer matrix (§6): G7 now calls `project(..., capability=profile.capability (or an
understatement), profile=serving_profile(<runtime>))`; there is no `retention` argument.
Wiring requests §9 unchanged (re-verify item 1's 602-count after this round: the v2 suite grew
by 9).

Remaining effort (this slice): optimistic 0.25 h, likely 1 h, pessimistic 3 h; confidence
medium. Basis: all four findings closed with regressions; left are the coordinator's wiring
requests and R109/P-22 application.

- 2026-09-24: fix round by the F2C-C fixer at code head `35d1e32a`; counts from this worktree;
  no hosted database, box, edge or AWS access.
