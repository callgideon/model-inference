# APP-0024-WIRE: App consumers of migration 0024 (C0 WR-5, U1R WR-3(a)/(b)/(c))

- Lane: APP-0024-WIRE, branch `codex/app-0024-wire`, worktree `.claude/worktrees/codex-app-0024-wire`.
- Base `9e4e34ca` (the D10-MERGE-2 lane head). Code head `d5e90cee`; this evidence and the update file are committed after it.
- Commits: `f27a2eb3` (C0 WR-5), `d5e90cee` (U1R WR-3).
- No SQL, no migration, no contracts change. D10-MERGE-2's files (`tests/u/request-pg.test.ts`, `tests/c/actions-postgrest.test.ts`, `tests/u/operator_stack.py`) are untouched.

## What each wiring line became

| 0024 wiring line (D10-APP-SQL §Wiring) | Now |
|---|---|
| **C0**: `creditLedger` uses `rpc("consumer_credit_ledger", { p_after, p_limit })` with `limit + 1 ≤ 100` | `lib/services/console.ts`: `ledger` and `requests` share one reader, `rpcPage(fn, query, project)`. A limit over 100 is **refused** `invalid_request` before any call; this is the contract's page bound, as before. The look-ahead `limit + 1` is **clamped** to 100, as `requests` already did: at `limit = 100` the RPC is asked for 100, and a full capped page carries a cursor (the next page may be empty). We clamp rather than refuse because 100 is a valid contract limit, so refusing it would break every caller that asks for the maximum. The database never answers 400 in normal use. `creditLedgerEntryOf` is unchanged: amount is a decimal string, unit CREDIT only. D10's cursor is passed through as is. |
| **U1R**: "Spent" unchanged; U1R's own P02 lines land | The credits-in read is unchanged and still goes through `console_credit_ledger` with `kind <> 'inference_debit'`. U1R's WR-3(b) lines are in `tests/u/credit-pg.test.ts` P02: `EXPLAIN` of that read, run as the browser principal, uses `credit_ledger_wallet_credits_in_idx`. The adapter's "ponytail" note about the missing aggregate is replaced by the index note. |
| **U1R**: `usage/` filters → `p_model`, `p_key_id`, `p_from`, `p_to`; remove "not available yet" | `usage-controls.tsx` is now a GET form with the fields `range`, `key` (the personal org's keys, from the new `CreditReads.keys(orgId)` read of `api_keys` under RLS) and `model` (free text: there is no browser catalog, C0 UI-2). Applying the form restarts the walk because the form has no cursor field. `jobsPageRequest(filters, now)` sends the window `[now − range, now)` as `p_from`/`p_to` and "all" as no bounds, the same convention as the legacy U1 `from = to − range`. The client-side window cut is removed because the database applies the window inside its keyset scan. `jobs()` sends only the filters that are set, so an unfiltered call stays 0021's exact call; U4's detail read shares this adapter. The copy is removed. Rows carry no key id or key name (note 3). |
| **U1R** WR-3(c): the ledger page moves to `consumer_credit_ledger` | `credit-reads.ts` `ledger(page)` calls the RPC. The wallet argument is dropped, because the database takes the wallet from the JWT: `billing/page.tsx` loses one argument and the fixture one parameter. Also removed: the regex that guarded the cursor splice, `pageOf`, `Filter.or`, and the view's order/limit. |

## Fails-before / passes-after

The before runs use the new tests with the base (`9e4e34ca`) versions of the changed source files swapped in; the files were restored afterwards (`git diff` checked).

| Test | Before (base source) | After |
|---|---|---|
| `consumer.test.ts` "the CREDIT ledger pages through consumer_credit_ledger…" | fail: no RPC call (the page read the view) | pass |
| `consumer.test.ts` "the ledger's look-ahead is clamped at the read's 100 cap…" | fail | pass |
| C0 real stack: "a page 2,000 entries deep is O(limit) through consumer_credit_ledger, with the view's rows" | **fail**: first page 1,635 shared blocks, 2,000 deep 597 (the view's bitmap and top-N sort) | **pass**: first page 106–107 blocks, 2,000 deep 106–107. The rows equal the view's `range(2000, 2099)` read as the same principal. |
| C0 real stack: "limit 100 is clamped… 101 is refused without one" | pass (the old view page asked for 101 without a cap) | pass. Mutant C0-LEDGER-05 (clamp dropped) makes the RPC refuse and is killed. |
| C0 real stack: "another individual's session never sees these rows, even replaying their cursor" | fail: CONSUMER_1 read through the view scoped to LARGE's wallet got nothing of their own | pass: CONSUMER_1 and EMPTY each read only their own entries (C1's sum to their wallet total); replaying LARGE's cursor lists none of LARGE's rows |
| `credit-reads.test.ts` R02 (ledger RPC), R08 (filters), R09 (keys) | fail: SyntaxError, no export `KEYS_BOUND` (file-level) | 9/9 |
| `usage-credits-view-model.test.ts` U05/U06/U07 | 3 fail (window request, filter hrefs and form, filtered empty text) | 8/8 |
| U1R real world P02 / P05 / P06 / P07 | 4 fail (P02 and P05 read the view via the old signature; P06 and P07 had no `now` in the request) | 7/7 |
| U1R real world P07 with mutant U1R-M34 (p_to ignored) applied | — | fail (P07), 6/1 |
| U1R real world P07 with mutant U1R-M35 (from/to swapped) applied | — | fail (P06, P07), 5/2 |

P07 on real PostgreSQL checks each of the following against the durable `infrx.jobs` rows:

- each model name, through the requested string or the revision; an unknown model gives an empty page;
- the individual's own key; another individual's key gives an empty page;
- the window `[max(created_at) − 30d, max(created_at))`. The harness clock is frozen, so jobs share instants and the newest jobs fall out because the end is exclusive; `+1 ms` brings them all back;
- the window and the model combined;
- the form's empty filter (`key=all`, `model=`), which equals the unfiltered walk. `jobs({…all null})` returns exactly what `jobs({limit, cursor})` returns.

## Mutants

| Id | Defect | Killed by |
|---|---|---|
| C0-LEDGER-05 | look-ahead not clamped (`ask = limit + 1`) | "…clamped at the read's 100 cap…", "requests: … 100-row cap" |
| C0-LEDGER-06 | the ledger pages the view (`keysetPage(… "credit_ledger_page" …)`) instead of the RPC | "the CREDIT ledger pages through consumer_credit_ledger…" |
| C0-LEDGER-07 | amount parsed as a number | same |
| C0-LEDGER-01 | retired: the cursor is D10's own and is scoped by the JWT subject, with no HMAC scope left to unbind | — |
| C0-LEDGER-04 | re-pointed to the keys/port-failure case (the ledger no longer goes through the port) | "a port failure is dependency_unavailable (keys, the ledger RPC)…" |
| U1R-M05 | the ledger pages `console_credit_ledger` again | R02 |
| U1R-M06 | ledger amount parsed as a number | R02 |
| U1R-M07 | the ledger asks for no probe row | R02 |
| U1R-M08 | jobs asks for no probe row (find re-aimed) | R03 |
| U1R-M29 | the next page resumes from the first row (now shared by ledger and jobs) | R03, R02 |
| U1R-M34 | `p_to` ignored | R08 (and P07 on real PG) |
| U1R-M35 | `p_from`/`p_to` passed in the wrong order | R08 (and P06/P07 on real PG) |
| U1R-M36 | model and key passed to each other's parameter | R08 |
| U1R-M37 | an unset filter is sent anyway | R08 |
| U1R-M38 | the page never sends the window's end | U05 |
| U1R-M39 | a non-uuid key (the form's "all") reaches the database | U05 |
| U1R-M40 | the next page drops the key filter | U06 |
| U1R-M19, M26, M27, M28 | retired with their code: the RPC has no `actor`; there is no client-side window cut and no `withJobRange` | — |

## Commands (worktree root; all at code head `d5e90cee`)

| Command | Exit | Result |
|---|---|---|
| `make console-test` | 0 | `# tests 661 / pass 606 / fail 0 / skipped 55 / todo 0` |
| `make console-lint` | 0 | 0 errors, 2 warnings (both pre-existing: `lib/contracts/conformance.ts`, `lib/contracts/fake-services.ts`) |
| `make console-typecheck` | 0 | typegen + `tsc --noEmit` clean |
| `make console-built` | 0 | `/billing`, `/usage` `ƒ`; i2a 22/22 |
| `make console-mutants` | 0 | contracts self-tests 14/14, contracts 212/212; V 40/40; **U 2/2 self-checks, 207/207 killed**; **C self-tests 4/4, 185/185 killed, 0 stale**; A2 46/46 (self-checks 2/2); A3 catalog 46/46 |
| `make console-c0-real` (INFRX_D_TASK=app-c0, 55451) | 0 | 15/15 (was 12; +3 WR-5 cases); diagnostic `blocks: first page 106, 2,000 deep 107` |
| `cd apps/infrx-api && INFRX_D_TASK=app-c0 uv run --frozen python ../app/tests/u/credit_world.py` (console-pg, U1R half) | 0 | 7/7 (P07 new) |
| `cd apps/infrx-api && INFRX_D_TASK=app-c0 uv run --frozen python ../app/tests/u/request_world.py` (console-pg, U4 half) | 0 | 8/8, todo 0 |
| `make console-c3a-real` | 0 | 9/9, todo 0 |
| `make console-u3-real` | 0 | 9/9, todo 0 |
| `node tests/c/run-mutants.mjs --only C0-LEDGER-04..07,C0-REQ-04` | 0 | 5/5 killed |
| `node tests/u/run-mutants.mjs --only U1R-M05..M09,M29,M34..M40` | 0 | 13/13 killed |
| C0 stack, base `console.ts` swapped in | 2 | 13/2 (the two WR-5 cases above) |
| U1R world, base sources swapped in | 1 | 3/4 |
| U1R world with M34 / M35 applied | 1 / 1 | 6/1 / 5/2 |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |
| `git diff --stat 9e4e34ca..HEAD` | 0 | 14 files, all under `apps/app` owned paths (+ this evidence and the update file) |

Environment:

- The console-pg worlds ran on this lane's namespace, `app-c0` (127.0.0.1:55451). The make recipe pins `app-u1r`/`app-u4`, so the underlying commands were run with `INFRX_D_TASK=app-c0` instead.
- `console-c3a-real` and `console-u3-real` assert their own blocks (55452/55453), so they ran through `make`. No container of another checkout was present, and none was left behind.
- Nothing hosted was touched: no Supabase, Vercel, pilot box, AWS or SSM. No secrets.

## Wiring requests / notes for the coordinator

- **WR-0024W-1 (contracts doc, §10, the ruling numbered at the 0024 merge).** Proposed text:
  - The consumer ledger page is `consumer_credit_ledger` in the individual's own session. The App refuses a limit over 100 before the call and clamps the look-ahead to 100.
  - The usage filters are `consumer_jobs`' own: model (requested or revision), key id, and the window `[now − range, now)`. An unset filter is not sent. The job row names no key.
- **Note: `lib/services/query.ts` `credit_ledger_page`.** No product read uses it any more; only the C0 executor tests do. Its removal is optional and belongs to the C1/C0 owner.
- **Note: `createConsumerReads`' `cursorSecret`.** The consumer reads no longer mint cursors, so the secret is only validated there. Removing it touches `consumerSessionFrom`, C0-SES-03 and the tests, so it is left for the C0 owner.
- **Note: page sizes.** U1R's adapter asks for `limit + 1` with fixed page sizes of 25, so there is no cap issue. A caller asking the U1R adapter for 100 would be refused by 0024; C0's reader clamps.
- **Skipped: custom date inputs.** The window keeps the presets (all / 24h / 7d / 30d). Add `from`/`to` date fields when someone asks for arbitrary dates; `p_from`/`p_to` already carry them.

## Deviations

- The brief's `apps/app/lib/billing/credit-reads.ts` is `apps/app/app/(console)/billing/credit-reads.ts`. Its fixture (`credit-fixture.ts`) and `billing/page.tsx` changed by one parameter and one argument.
- U1R's "P02 lines" live in `tests/u/credit-pg.test.ts`, not in `credit-reads.ts`.

## Remaining effort

Lane: 0 h, pending review. Coordinator: merge after D10-MERGE-2 (the paths do not overlap), then number the ruling (about 0.25 h). Estimate:

- Optimistic 0.25 h, likely 0.5 h, pessimistic 1.5 h.
- Confidence: medium.
- Basis: every named check is green at `d5e90cee`. What remains is merge order and review.

## Verification log

- 2026-09-26T05:25Z: written at code head `d5e90cee`; every command above was run at that head.
