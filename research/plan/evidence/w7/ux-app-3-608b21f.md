# ux-app-3 (UX-07 follow-up / AP-09, C-07) — consumer data-use controls — evidence at 608b21f

Lane ux-app-3 of wave 7 (LW7 batch 4), branch `codex/w7-ux-app-3`, base `29b9df84`, code head `608b21ff` (this file and the update JSON are committed after it). No tasklocal key (fixtures only; no composed parity case added, so `app-u1r` was not used); no Docker, hosted Supabase, box, AWS, Vercel or secret touched. Nothing is enabled: the controls render only when `GET /console/v1/data-use` answers, i.e. when the coordinator mounts AP-07a's routes (`CONSOLE_DATA_USE`, default off). With the switch off (production today) Settings shows the honest unavailable state.

## Pre-check (the brief's "verify first")

`packages/api-client/src/consumer.ts` at the base carries `GET /console/v1/data-use` (`DataUseDoc`), `PUT /console/v1/keys/{key_id}/capture` (`CaptureRequest` -> `DataUseDoc`), `GET/POST /console/v1/data-grants` and `DELETE /console/v1/data-grants/{grant_id}`; `apps/infrx-api/openapi/consumer.json` documents the same four paths. **The brief's `/console/v1/data-use/grants` does not exist** — the grants live at `/console/v1/data-grants`, which is what `infrx/gateway/routes/console_data_use.py` serves and what `research/design/v1/05-service-contracts.md` (Privacy/settings row) names. I built against the committed paths; nothing is missing for grant/withdraw capture.

## Slices (each: the failing seam test first, then the change, then its mutants)

1. **The data-use service** `apps/app/lib/services/data-use/index.ts` (new, server-only): `readDataUse(api)` -> `ready | unavailable | forbidden | signed_out` (401 = the session ended: the routes answer an API key with 401 `invalid_api_key`, but the App forwards only the session bearer, so that case is unreachable from the web; a 404/503/network/unknown mode or grant state = `unavailable`, never Off or an empty list; no consent yet -> the API's default 30-day retention). `setCapture(api, input)` = one `PUT /console/v1/keys/{id}/capture` with the submission's `Idempotency-Key`, body `{mode, consent_version, retention_days, evaluation_consent: false}` (capture implies none of the other purposes; evaluation consent is never given from here); allowlist refuses any other field (an `org_id`, `evaluation_consent`) before a call. `withdrawGrant(api, input)` = one `DELETE /console/v1/data-grants/{id}` with its `Idempotency-Key`. Fixed App text for every refusal (`state_conflict` "changed since this page loaded; reload", `org_suspended`, `forbidden` owner-only, `not_found`, 401 "session has ended; sign in again", anything else "not confirmed; try again - the same choice is applied once"); the API's message never reaches the page. `dataUseActions(deps)`: Origin check (`sameOrigin`, reused from `lib/services/actions.ts`) before anything, revalidate `/settings` only after an acknowledged change. Note: AP-07a's replays are state-based (its docstring: no Idempotency-Key store); the key is sent per R270 and is harmless there.
2. **The Settings section** (C-07): `settings/data-use.tsx` (`DataUseSection`, server-renderable from fixtures; actions passed in as props), `settings/data-use-controls.tsx` ("use client": per key a labelled `<select>` Off / Metadata only / Full content + Save — never a toggle that looks saved; a "Recording now: …" note when the gateway's effective mode differs; per active grant a Withdraw button; each form holds one idempotency key, reuses it after an unacknowledged answer and rotates only after an acknowledged one), `settings/actions.ts` ("use server": `setKeyCapture`, `withdrawDataGrant`, one delegation each), `settings/loading.tsx`, `page.tsx` (reads data use only for a ready account through `apiSource()`'s client), `view-model.ts` copy (capture "Off unless you turn it on", sharing "Off unless you grant it"; serving retention unchanged — "This is not zero data retention", and the section says "Turning capture off does not change what we store to run a request"). Suspended: select and Save disabled with the reason; Withdraw stays available (R33). Grant creation is NOT offered (no provider directory in the App; YAGNI) — grants made elsewhere are listed and can be withdrawn.
3. **Mutants**: `tests/ux/settings/run-mutants.mjs` (a copy of UX-07's runner, ponytail-marked: fold the three copies when the UX lanes merge) + `mutants.json` (56).

U pins re-anchored (owned "the settings pins"): `tests/u/keys-source.test.ts` U2-S04 (the only controls are the two data-use forms, each submit one server action; no checkbox/switch/onChange anywhere), `tests/u/settings-view-model.test.ts` U2-P01/U2-P03 (new statuses and copy), `tests/u/run-mutants.mjs` (case names; U2-M15's `find`).

## Changed paths

Owned: `apps/app/lib/services/data-use/index.ts` (new), `apps/app/app/(console)/settings/{page.tsx, view-model.ts, data-use.tsx (new), data-use-controls.tsx (new), actions.ts (new), loading.tsx (new)}`, `apps/app/tests/ux/settings/{settings.test.ts, run-mutants.mjs, mutants.json}` (new), `apps/app/tests/u/{keys-source.test.ts, settings-view-model.test.ts, run-mutants.mjs}` (settings pins only). `apps/app/lib/api/` untouched (the generated client already types every call).

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `make console-test` (base 29b9df84) | 0 | 589 tests, 587 pass, 0 fail, 2 skipped |
| `node --test tests/ux/settings/settings.test.ts` before slice 1 | 1 | red: module load (`lib/services/data-use/index.ts` absent), 0 pass / 1 fail |
| same, before slice 2 | 1 | red: 6 pass / 6 fail (UXS-07 fixture fixed; UXS-08..12: no section, actions, loading, copy) |
| same, at head | 0 | 12/12 |
| `node --test tests/u/*.test.ts` after slice 2, before re-anchoring | 1 | U2-S04, U2-P01, U2-P03 red (the old "no controls"/"Not offered" pins) -> re-anchored, 126/126 |
| `make console-test` (head) | 0 | 601 tests, 601 pass, 0 fail, 0 skipped (+12 UXS; the 2 base skips are the I2A built cases, unskipped once `.next` exists) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (`lib/contracts/conformance.ts`, `fake-services.ts`) |
| `make console-typecheck` | 0 | clean (one test-literal typing fix on the way) |
| `make console-built` | 0 | build OK (`ƒ /settings`); tests/i2a 22/22 |
| `node --test tests/boundary/*.test.ts` | 0 | 6/6 (API-BOUNDARY 0 findings; every `api.call` is a documented consumer operation) |
| `node tests/ux/settings/run-mutants.mjs` | 0 | 2/2 self-checks; 56 mutants, 56 killed, 0 not killed; every UXS case declared |
| `node tests/u/run-mutants.mjs --only U2-M15..U2-M22` | 0 | 2/2 self-checks; 8/8 killed |
| `make console-mutants` with WR-UXA3-1 applied transiently | 0 | contracts 212/212; U 218/218; C 66/66; A 62/62; catalog 46/46; matrix 27/27; C3F 17/17; UX 42/42; first-call 36/36; usage 41/41; settings 56/56 (0 survivors) |
| `uv run --frozen pytest -q tests/integration/test_makefile_mutant_lists.py` (patch applied, then `git checkout Makefile`) | 0 | 7 passed |
| `make api-lint` / `make api-typecheck` | n/a | no Python touched |
| `python3 research/plan/scripts/validate_plan.py` (with this file) | 0 | PASS (918 links, 510 documents) |

## Wiring requests

- **WR-UXA3-1 Makefile `console-mutants`** (coordinator): after `\tcd apps/app && node tests/ux/usage/run-mutants.mjs` add `\tcd apps/app && node tests/ux/settings/run-mutants.mjs`. Composed test: `make console-mutants` exit 0 with the line (settings 2/2 self-checks, 56/56 killed); `tests/integration/test_makefile_mutant_lists.py` 7 passed with it.
- **Enablement (not a patch; production track)**: the controls go live only when the gateway composes `rt.data_use` (`CONSOLE_DATA_USE` on) together with AP-09's IDENTITY_API/AUTH_FACADE/CONSOLE_READS — and capture itself records nothing until `TRACE_PUMPS` is on (P-09). Both stay off; this lane enables nothing.

## Schema requests

None.

## Open items

- Brief path typo: `/console/v1/data-use/grants` -> the real `/console/v1/data-grants` (recorded above).
- Grant creation (`POST /console/v1/data-grants`) not surfaced: needs a provider directory / recipient choice the App does not have; add when a consumer-facing provider listing exists.
- `evaluation_consent` is always `false` from the App (a PUT therefore also withdraws an evaluation consent set elsewhere — the safe direction); retention is submitted as read (no editor). Ponytail: add both controls when the product offers them.
- The three ux runners (`tests/ux/{first-call,usage,settings}/run-mutants.mjs`) are one script copied; fold into one parametrized runner.
- The client fake (`lib/fake-api.ts`, not owned) answers the data-use paths 404, so the INFRX_CONSOLE_PREVIEW preview shows the unavailable state; a preview world for data use is a one-route addition by its owner if wanted.
- Not run: a composed parity case on `app-u1r` (optional per brief); E3A/Playwright (Docker gate, coordinator's).

## Proposed ruling (unnumbered)

A consumer data-use control is a choice plus an explicit Save whose acknowledgment comes from the API; a failed or absent data-use read never renders a mode, an "Off", or an empty list (UXS-02/UXS-09 are the oracles).

## Estimate (remaining)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium — review round and WR-UXA3-1 at merge; live verification waits on CONSOLE_DATA_USE + the AP-09 cutover (coordinator, production track, not in this estimate).
