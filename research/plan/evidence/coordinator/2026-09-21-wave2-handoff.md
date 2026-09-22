# Wave-2 handoff — state of the infrx free-pilot implementation on 2026-09-21

> **Superseded continuation instructions:** This document is the original wave-2 evidence. Main was actually imported at `271add9` (the `2c64dc2` prose below is an older snapshot). Continue from [the product audit](../../10-wave2-platform-audit.md) and [new revision handoffs](../../11-wave3-revision-handoffs.md), which replace §§4–5 sequencing and single-console assumptions. Preserve the historical findings and counts below.

**Start here in a fresh session.** This page is the entry point; it supersedes the "Immediate Next Steps" of [COORDINATOR.md](../../COORDINATOR.md) and the status paragraph of [README.md](../../README.md). The pipeline board and append-only log are [STATUS.md](STATUS.md); the task manifest is [tasks.json](../../tasks.json); binding rulings are [08 §10](../../08-contracts-v1-encoding.md) R1–R60 with their corrections. Nothing described here is deployed, applied to a Supabase project, or live-verified. All work is on `main` (fast-forwarded from the integration branch `claude/infrx-impl` at the user's instruction on 2026-09-21; both are `2c64dc2`) and on the seventeen task branches `codex/<task>-<slug>`, all pushed unchanged (the remote is public). **A fresh session starts from `main`.** Vercel auto-deploys `apps/app` on every push to `main`: the console pages `/usage`, `/billing` and `/traces` are fixture-backed until C2, so app.callbill.ai shows fixture data on those pages — known and accepted.

## 1. What was completed (wave 2)

All eleven wave-2 tasks were implemented, reviewed adversarially at HEAD with mutation testing (rounds until `pass` or a coordinator closure check), and merged onto `claude/infrx-impl` with a coordinator rerun on the merged tree before each merge commit. Final `make check` on the merged tree with all eleven: see the last STATUS.md entry for the counts (api-test, api-mutants over eight track lists, console tests/lint/typecheck, four console mutant lists, bench-test).

| Task | Merge | Status (manifest) | What is true |
|---|---|---|---|
| V1 trace list UI | `9617a2e` | implemented | fixture-backed; waits on C1 provider swap, T2, sidebar entry |
| U1 usage/balance UI | `184c5fb` | implemented | fixture-backed; coordinator seam `usage/ranges.ts` re-export |
| M1 media materialization | `4c7a29f` | implemented | in-memory object store; `stage`/`attach` not called by anything; never sets `duration_s` (S2 finding) |
| Q1 memory scheduler | `d6f26e5` | implemented | R60 two-level fairness (corrected); matched an independent reference model exactly; not constructed by any composition root |
| C1 console read services | `010b6ea` | implemented | in-memory query port; the supabase-js port is C2's first deliverable; refuses legacy/nullable rows from D1 today (S2 finding) |
| J1 judge dry-run | `b1b84a1` | implemented | shared fake only; `APPROVED_RATES` empty ⇒ live submission impossible |
| W1 vLLM engine adapter | `9a2eec2` | implemented | fakes only; `content` alias of `raw` until F2.2 item 2 |
| T1 trace capture/spool | `c13fb1a` | implemented | real disk spool, exported conformance 17/17; no gateway builds it; `SEGMENT_VERSION` 2 |
| D1 durable schema | `7a0d86d` | **integrated** | migrations 0003–0005; suite 103 passed with 82 mutants on postgres 16 + shim AND on the real supabase/postgres 17.6 image, no shim; NOT applied to any project |
| G1 ingress/auth | `49eafd2` | implemented | **not mounted** (`ROUTERS` byte-identical); cutover checklist R-1 (+ eighth inversion) proven in scratch |
| E2 integration harness | `cb04382` | implemented | real compose stack (Supabase PG 17.6, Valkey, ClickHouse, MinIO) with ownership label, 39-case RLS matrix, engine conformance 8/8 over a socket, sound mutation runner; E1's wall-clock bench bounds replaced by an injected clock |

Stage review S2 (on `49eafd2`, before E2's merge): **pass** — the tree is coherent enough to start wave 3 after F2.2. Its full report is in the coordinator's session transcript; its findings are folded into §4 and §5 below.

**Gate statement.** No pilot claim is supported: nothing is deployed, no ingress is mounted, no acceptor exists, no scheduler/engine/sink is constructed by a composition root, no console page reads a real database. D1 is the only task whose suite ran against its real service.

## 2. How the work was verified

- Per task: Opus 5 implementer in an isolated worktree → independent reviewer at HEAD (common brief `.claude/handoff/wave2-review-brief.md` + task attack list) → fix loop → coordinator closure check (for test-only rounds the coordinator applied the reviewer's surviving mutants directly and required a named failing case each) → `--no-ff` merge → rerun of the affected `make` targets on the merged tree → merge commit only on green. Review rounds per task: V1 2, U1 3, M1 2, Q1 3, C1 4, J1 3, W1 4, T1 3, D1 3, G1 4, E2 2.
- Seven of eleven first-round reviews were `fix_required` with real defects behind green suites; the recurring class was a guard whose proof used a double that could not exhibit the failure (U1's vacuous assertions, Q1's unfiltered comparison, C1's `scopedPort`, D1's eighteen vacuous checks). Merge criterion R32/R40 held throughout: a case counts only if a single-edit mutant fails it.
- Three coordinator rulings were wrong and were corrected in place (08 §10 log): R60's wait bound and tie-break; "parse large bodies off the loop via `asyncio.to_thread`" (json.loads holds the GIL — replaced by pre-parse structural counts, a two-slot semaphore and bounded number parsing); the cancel-map eviction rule (made late cancels immortal — replaced by expiry at the lease deadline and "a running key is never refused"). Lesson recorded: a fail-closed rule on a bounded map needs an expiry.
- Two API rate-limit interruptions killed running agents; nothing was lost because implementers commit after each item; reconciliation was from git.

## 3. Rulings in force

R1–R60 in 08 §10, plus the corrections logged there (R60 ×2; the withdrawn to_thread mechanism; the restated cancel lifecycle; "a generation runs once"). Rulings issued this wave: R58 (allow-listed messages, visible/raw), R59 (SQL-level masking, privileges, tenant FKs, wallet trigger, D-owned RPCs), R60 (two-level scheduler fairness). Settled but not yet written into 08 §10 (F2.2 item 1–2 do it): R-3 (`admit` derives `deadline_at = min(caller, db_now + budgets)`), the R58 usage wording, request bodies must be UTF-8, customer upload reference is `infrx-upload:upl_<id>` only.

## 4. F2.2 — the first wave-3 task (coordinator-owned; contract revisions in one change)

1. 08 §10: record R-3; `contracts/fakes/state.py` admit clamps instead of refusing; conformance case + mutant.
2. `contracts/fakes/engine.py` emits `{visible, raw}`; the two exported engine cases in `conformance/services.py` read `raw` and assert `visible`; then W deletes the alias (`worker/engine.py`), sets `PAYLOAD_COPIES = 2`, drops the alias test. Fold the settled R58 usage wording into 08 §10.
3. Trace accounting extraction: `infrx/contracts/traces_accounting.py` with `TraceCaptureBase`/`TraceSinkBase` (clock required, no `crash`); `FakeTraceSink` subclasses it; `infrx/traces/spool.py` subclasses the base; `crash` → `tests/t`; 08 §5 "spool segment 1" → 2 + `TRACE_SPOOL_SEGMENT_BYTES`; revise `trace_bounds__metadata_exhaustion_drops_with_counters` so a sink may charge `max(declared, len(payload))`, then drop `QUEUED_PAYLOAD_MAX_BYTES`.
4. `contracts/ports.py`: `MediaStore.stage` accepts only store-produced refs + `materialized(org_id, ref)` harness hook; shared builder fixture with one media content part per ref in order; `duration_s` set by preparation.
5. Move `CandidateSource`/`TraceCandidate` (judge/sampling.py) into `contracts/ports.py` with the lower-case UUIDv4 id form; `JudgeRun.sample_ids` unique + UUIDv4 in the record.
6. Console contract: `UsageRow.key_id: string | null`; `usageSummary`/`usageDaily` require `from`/`to` (and the exported cases); legacy-row nullability decision for usage/keys (`execution_mode`, `job_state`, `usage_certainty`, `trace_mode`, `api_keys.trace_mode`) or an explicit pilot-regime filter; `judgeRuns` sample shape frozen to one of the two; `AuditEntry.target_org_id: string | null`; fake's bounds in code points; the G0 leftovers (Q18/Q16 pins, `walkAll` assert.fail, self-test label).
7. `infrx/config.py` + 08 §5: `TRACE_SPOOL_SEGMENT_BYTES`, `MAX_INDEX_ITEMS` 500, `MAX_INDEX_BYTES` 268435456, `CONSOLE_CURSOR_SECRET`, `DATABASE_POOL_MIN_SIZE/MAX_SIZE/CONNECT_TIMEOUT_S/STATEMENT_TIMEOUT_MS`, G1's structure caps.
8. `contracts/money.py`: `copy_abs()` in `parse`; `CONTEXT` not shared mutable.
9. `tests/contracts/mutants.py`: parameterise the runner (target, package path, own cache/temp dir per subprocess); delete the seven copies of `run_mutant`; neither runner counts an undeclared runtime exception as a kill.
10. Makefile/README: D's list skips without Docker and `make check` still exits 0 — say so; `make integration` target (E2's harness; not in `check`).
11. Console wiring: sidebar `/traces` entry, `/billing` → "Balance"; `(console)/layout.tsx` + `lib/session.ts` balance through `Money` from `org_wallet_summary`/`balances().available` (R11); delete `components/credits-card.tsx`; `lib/credits.ts` stops `Number()`; `traces/query.ts` `now` from the context; server-side shape-failure log sink (relation/column only).
12. `apps/app/supabase/README.md`: re-running `0002` requires `select infrx.extend_model_limits();`; PostgREST `db-schemas` stays `public`; every later `public` migration uses revoke-then-grant (Supabase default table ACL).
13. `research/cross-cutting/cloud-pricing.md`: approved judge price row `(model, in/out per MTok, price_version, source, effective_at)` — ⚠️ TO BE VERIFIED, blocks J2's live path; needs a human decision.
14. G1 R-1 cutover checklist: add "retire `tests/g/mutants.py` `unset_mode_refuses`" (the eighth inversion).
15. `research/plan/evidence/q/Q1-4e48eee.md` "Limits, round 1": the withdrawn `tag − V[kind]` wording marked CORRECTED (done in this session's wrap-up commit).
16. E2 at its merge (done in this session): invert E2-RLS-01..04/44 to 42501 expectations when D1's migrations enter the harness; drop E2's private clock for `infrx_test` — carried to E3.

## 5. Next actions, in order (wave 3)

1. F2.2 (above), one coordinator-owned change with its own review.
2. D2 real admission on the D1 schema (`admit`/`claim*`/`prepared`/`load_work`/`complete`, R-3 clamp, psycopg pool with `set role service_role` per connection, schema-qualified names, never writing `wallets.ledger_total`, lock in `org_id` order).
3. G2 acceptor + cutover (R-1 with the eighth inversion, `redirect_slashes=False`, one `LargeBodies` per process on `rt.ingress`, calls M1 `stage`/`attach`, builds the T1 sink and Q1 scheduler in the composition root, `validate_runtime` unset → refuse) — **together with I2's fail-closed installer** (`O-FAILOPEN`; `INFRX_MODE=pilot`; Python ≥ 3.12.4 by digest; transport-logger assertion after logging config; no `--reasoning-parser`, no `continuous_usage_stats`).
4. M2 preparation (materialize with a duration probe, `prepare`, `{url}` → `{ref}` rewrite, never budget from ref fields, HEAD + digest check), M3 (`UploadCreated.destination_ref` reconciled with `infrx-upload:upl_<id>`).
5. W2 attempt loop (relay/journal `visible` only; TTFT at the first raw nonempty delta; `completed` needs stop|length + authoritative usage + `malformed_lines == 0`; never reuse a lease; `cancel() is False` ⇒ cancel the task; task-level deadline).
6. T2 shipper (coroutine `ack`/`read_segment`/`rotate`; `recover`/`scan_segment` via `to_thread`; scan `adopted` segments; quarantine a torn segment larger than one frame; dedupe key `(segment, position)`); T3 ClickHouse.
7. Q2 Valkey adapter reproducing the 14-point list in Q1's evidence (differential run incl. unfiltered claims with both kinds holding work, non-unit costs).
8. C2 supabase-js `QueryPort` (RPCs with non-NULL bounds; keyset compares instants; legacy-row handling per F2.2 item 6; Layer-2 tenant test with operator and service-role sessions on port 55441), then U/V provider swaps.
9. E3 cross-module tests on E2's harness (with the five RLS inversions), J2 (after item 13), then I2/I3, E4 before any pilot gate. Fleet (I4) follows verified pilot evidence.

## 6. Known risks and deliberate non-actions

- `O-FAILOPEN`: the deployed gateway's installer swallows SSM errors and the gateway then admits all requests. Recorded in `infra/README.md` and the I1 evidence; **not acted on** — production mutation was not authorized. Owner I2 + G2 cutover.
- The deployed console's `addCredit` will get a database error for a negative `grant`/`purchase` once D1's migrations are applied (sign CHECKs); operators use `adjustment` (U3).
- D1's keyset pages read every tenant row (0.7–2.3 s at 10^5 rows/tenant): a limit with owner D before E4 load testing.
- Real `supabase/postgres` image: `auth.uid()` reads only the legacy per-claim GUC; hosted Supabase sets both — I2/C2 must confirm what their PostgREST sets.
- Harness runs are serialized host-wide (E2's ownership label is the checkout path); `tests/d/pgharness.py` hard-codes one container name/port, so two concurrent D1 suites would reuse each other's container.
- The remote `callgideon/model-inference` is public; the pushed tree contains the I1 live-infrastructure inventory and the fail-open write-up (the user chose to push as is).

## 7. Environment for the next session

- Integration worktree `.claude/worktrees/infrx-impl` (branch `claude/infrx-impl`); task worktrees `.claude/worktrees/codex-<task>`; shared venv `apps/infrx-api/.venv` via `make api-env`; console `apps/app/node_modules` (pnpm 9 / Node 22). Briefs and review finding files (git-ignored) under `<repo>/.claude/handoff/wave2/`.
- Canonical checks: root `make check` (needs Docker for D's mutant list; skips visibly without it), `make integration` to be added by F2.2 (E2's `tests/integration/run.py`).
- Agents: Fable plans/reviews, Opus 5 implements; delegation through the Agent tool + SendMessage; API rate limits can kill agents mid-task — implementers commit after each item; reconcile from `git status`/`git log` per worktree and the STATUS.md board.
