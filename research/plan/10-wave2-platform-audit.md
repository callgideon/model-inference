# Wave-2 audit and two-platform continuation

> Follow-on scheduling: [complete build plan](12-complete-build-plan.md) and [fresh-session handoff](16-fresh-session-handoff.md). Historical counts/status evidence below describe the audit boundary; current manifest v4 includes later planned work and App-first Marlin priority.

2026-09-21. Implementation baseline: `271add946771ddc4efc3cbc2044758443080759b`, pulled from `origin/main` with `--ff-only`. Audit branch: `codex/wave2-platform-audit`. This document supersedes the next-step ordering and single-product assumptions in the [wave-2 handoff](evidence/coordinator/2026-09-21-wave2-handoff.md). Preserve that handoff and its evidence as history. The implementation model or agent name does not affect task ownership or acceptance.

## Conclusion

Keep the wave-2 modules. They provide substantial tested behavior, but they are **not an integrated inference product**, and their USD/organization contracts do not yet implement the agreed consumer product. A shared contract and additive database revision must precede new feature integration. App reporting needs its own database adapter task; provider content and judge work must not block it. Lab needs its own authenticated application before the existing provider UI moves.

The product remains:

- `apps/app`: free consumer inference, public verified signup, **10,000 CREDIT once per individual user**, catalog, keys, own usage, exact available balance and reliable metered requests.
- `apps/lab`: provider membership, model and serving versions, private dev and controlled prod endpoints, authorized operational analysis; review/evaluation and data/training workflows in later milestones.
- Shared backend: identity and authorization, registry/rates, durable jobs and accounting, media and engines. Customer data does not become provider-owned when a provider serves a request.

[Product documents](../platforms/README.md) define intended behavior. [Implemented v1 encoding](08-contracts-v1-encoding.md) describes the existing types and rulings; [target contracts](01-contracts.md) are not evidence of encoded v2 types. F2R and F2P bridge that difference. No SQL migration or hosted service was changed in this audit.

## Reconciliation and evidence limits

The incoming implementation matches the supplied SHA. Original F1/F2/E1/I1 completions and all eleven wave-2 task states are retained in [manifest v3](tasks.json). Task S1 here means product reconciliation, distinct from the old coordinator stage-review label S1. F2.1 corrections are part of the imported history; F2.2 was a proposed pass, not a completed task. Main contains only the old implementation plan; the pre-pull product amendment was local work. It was stashed, main was pulled, and conflicts were resolved by retaining upstream completion evidence and the new product requirements.

Only this checkout and an older detached worktree are visible locally. The seventeen task worktrees described by the previous session are on another system; their presence/cleanliness is not newly verified. Hosted migration versions, live behavior and production balances have not been re-inspected. Historical statements about live resources retain their original dates.

This is a repository audit and local corrective pass by the current session, not a new independent reviewer signoff. See [local verification](evidence/wave2-platform-audit.md) for commands, actual results and skipped service coverage. No deployment, production migration, paid model call or push is part of this audit.

## Implemented versus integrated

| Task / imported merge | Actual implementation at the pulled SHA | Disposition / next gate |
|---|---|---|
| F1 / foundation history | Factory extraction with legacy behavior preserved | Keep. Runtime composition still needs G2/I0 |
| F2 / foundation history | Executable Python/TS contracts, fakes, conformance and pinned dependencies | Preserve v1 history; F2R carryovers, then F2P product-v2 encoding |
| D1 / `7a0d86d` | 0003–0005 durable schema, roles, RPCs and exact USD money; prior evidence uses real PG and Supabase image | Baseline **integrated**. D1R adds CREDIT/individual/provider schema; never edit applied-history files |
| M1 / `4c7a29f` | Secure materialization behind memory object store; stage/attach uncomposed; duration not yet probed | Keep security tests; F2R fixes port/fixtures, M2/M3 supply preparation/storage/upload lifecycle |
| Q1 / `d6f26e5` | Corrected two-level memory fairness reference; no composition | Keep; Q2 real Valkey differential evidence, Q3 PG outbox/rebuild |
| W1 / `9a2eec2` | vLLM adapter with raw/visible plus temporary content alias; fake/controlled engine evidence | F2R removes alias consistently, W2 adds fenced execution, W3 measures real engine |
| G1 / `49eafd2` | Auth/validation/ingress implemented; `gateway/app.py:ROUTERS` still mounts legacy routes | G1R audience/wallet/revision checks. G2 mounts only at the complete runtime gate |
| T1 / `c13fb1a` | Real disk spool, bounded capture; subclasses a contract fake; no runtime constructs it | F2R extracts production base. Optional for App launch; T2I/T2F/T3 add shipping/retention |
| J1 / `b1b84a1` | Dry-run sampling/validation; approved live rates empty | Reuse in Lab. J2 requires access grants, USD budget, approved rate and explicit live scope |
| C1 / `010b6ea` | Repositories and tenant query boundary over in-memory QueryPort; legacy/null rows incompatible | F2R/F2P fix projections; **C0** adds real consumer database port independently of C2 |
| U1 / `184c5fb` | Usage/balance views over fixture identity/data | Reuse view models; local audit prevents production fixture account reports; C0 supplies real context; U1R adapts unit-aware views |
| V1 / `9617a2e` | Fixture trace explorer inside App | Reuse via **V1M** after L1/L2; no consumer trace navigation; provider detail/review stays Lab |
| E2 / `cb04382` | Pinned four-service harness and socket engine conformance; old role/clock carryovers | E2R repairs harness and proves service layer; E3A/E3L/E5L use revised contracts |
| E1/I1 / foundation history | Benchmark corpus/client and infrastructure inventory/design | Preserve tests/inventory; runtime measures and live state still need fresh allocated evidence |

D1 alone among the eleven wave-2 modules has imported real-service integration status. A module merged to main is not necessarily connected to the running product. `JobStore` is still a fake implementation, ingress has no production acceptor, and there is no composed durable scheduler/engine/sink runtime.

## Findings and disposition

| ID / priority | Evidence and consequence | Action and verification owner |
|---|---|---|
| A01 / release blocker | `contracts/money.py`, `records.py`, TS Money brand, 0003 `price_versions` and org-keyed wallets all encode USD. Relabeling balances would silently change denomination and grant ownership | F2P separates CREDIT/USD and immutable wallet identity. D1R adds migrations; A1 grants once per user. Upgrade nonzero/negative USD histories, in-flight old jobs and concurrent signup retries |
| A02 / release blocker | C1 delegates its real QueryPort to C2, whose content dependencies include media, retention and Lab. U1 cannot show real consumer accounts through the proposed independent App graph | New C0 owns real Supabase/PostgREST query execution and trusted session context. Real two-tenant/operator tests and legacy/null fixtures; no C2/T/J dependency |
| A03 / high | `/usage`, `/billing`, `/traces` use fake services and a fixture owner in production-renderable pages | Fixed locally: explicit non-production opt-in, demo notice, unavailable state otherwise, fixture clock shared by traces. C0 replaces App context; V1M removes provider route from App |
| A04 / high | Sidebar uses legacy `org_balance` ignoring holds; `lib/credits.ts` converts exact amounts to Number; missing summary row falls back to hold-blind balance | Fixed locally: available from wallet summary, exact Money through display, no missing-row fallback, explicit errors; pre-D1 missing-function fallback retained. Legacy USD display remains USD |
| A05 / release blocker | Old handoff orders G2 cutover before M2/W2/Q2 while no durable runtime exists; installer can replace env after SSM errors | New I0 repairs installer first; G2 cutover requires D5/W2/M2/Q3/I0. E3A proves failure/recovery before deployment. No local audit change to live startup |
| A06 / release blocker | Consumer org roles and model provider label are not provider authorization; no Lab app exists | F2P/L2 explicit audience and purpose grants; D1R minimal ownership; L1 shell. V1M migration cannot bypass access checks. Provider membership never grants customer content automatically |
| A07 / high | C1 rejects D1's legacy nullable usage fields and represents missing key identity inconsistently | F2R freezes nullable legacy shape, F2P tags accounting regime; C0 preserves old history instead of silently filtering it. Deleted-key and mixed-history tests |
| A08 / high | Decimal `abs()` can consult ambient precision; shared Context can be mutated by another caller | Fixed locally: `copy_abs`, fresh explicit contexts, judge uses factory. Tests use low precision, strict rounding traps and poisoned returned context |
| A09 / medium | Fake admission rejects oversized deadline although accepted R-3 calls for DB-clock clamp; G tests encode old refusal/margin heuristic | Fixed locally: clamp caller future bound, reject elapsed deadlines, replay pinned bound; remove gateway skew heuristic. Contract mutant still proves clamp; obsolete heuristic mutants retired |
| A10 / medium | T1 leak oracle uses Linux `/proc/self/fd` and fails on this macOS checkout | Fixed locally: check actual opened descriptor is EBADF after every failed write, plus orphan/charge invariants. Test still fails if cleanup is removed |
| A11 / high | E2 carries old RLS expectations/private clock; D harness uses fixed container/port and can adopt another checkout's resource | E2R: ownership-before-adoption/cleanup, concurrency refusal or unique namespace, real role/clock checks. Do not run destructive parallel harnesses until repaired |
| A12 / high | Green local tests may skip all database cases; spies/fakes do not prove tenant SQL/RLS or runtime composition | Added `make integration`; document skips. Layer 2 and Layer 3 remain independent release requirements, not inferred from unit totals |
| A14 / medium | Integration migration-list oracle stopped at 0002; process scan assumed `/proc`; media unwrap mutant survived on Python 3.12.13 | Fixed locally: inventory includes 0003–0005, macOS uses untruncated process listing, orphan mutant covers both OS paths, media tests include explicitly denied mapped IPv4 destinations |
| A13 / measured risk | Prior D1 evidence records 0.7–2.3s keyset reads at 100k tenant rows | C0/D owner inspect actual query plans and bounded scans with realistic sizes before E4; a UI LIMIT alone is not sufficient |

## Every original F2.2 carryover

F2R is the successor name; do not create a competing F2.2 branch from the older handoff. Local fixes do not mark the entire pass complete.

| Old item | Current disposition | Remaining owner / closed-loop evidence |
|---|---|---|
| 1 DB-clock deadline | Fixed locally; ruling appended; fake/conformance/ingress skew cases consistent | D2 must prove identical SQL admission and replay behavior with an injected DB clock |
| 2 engine visible/raw | Pending | F2R changes shared fake + exported cases, W1 alias/copy count and usage wording atomically; both adapters pass conformance |
| 3 trace production base | Pending | F2R extracts base with required clock/no production crash, serialized payload charge, spool v2/config; fake and disk sink same adversarial bounds |
| 4 media stage/provenance/duration | Pending | F2R port + produced-ref hook + ordered media fixtures; M2 probes real duration and enforces immutable materialization |
| 5 judge candidate port/sample IDs | Pending, Lab contract work | F2R moves port and validates unique UUIDv4 samples; J2 real access-aware source later |
| 6 console DTO/nullability/bounds | Pending | F2R then F2P cross-language fixtures; C0 real legacy/new projection, explicit accounting regimes |
| 7 config names/caps | Pending | F2R adds exact upstream names/defaults and invalid/missing config tests; I0 consumes fail-closed deployment settings |
| 8 decimal context/abs | Fixed locally | Focused money/judge tests and relevant mutants; both currencies reuse arithmetic only, never denomination |
| 9 shared mutant runner | Pending | F2R consolidates eight runners with unique temp/cache, declared exception rules and sensitivity self-tests |
| 10 Makefile/docs | Fixed locally | `make integration` separate from `check`; Docker skips explicitly do not prove integration |
| 11 console wiring | Partially fixed and partly superseded | Exact balance/nav/unused card/trace clock fixed. **Do not add App Traces nav.** C0 still supplies real context + redacted relation/column-only failure logging; V1M owns Lab move |
| 12 Supabase README | Fixed locally | Seed extension, public-only PostgREST, revoke-then-grant policy documented; D1R/E2R prove SQL permissions |
| 13 approved live judge rate | Deferred from App release | J2 live gate: recorded model/input/output/version/source/effective time and budget approval; no invented rate, not an F2R merge blocker |
| 14 unset-mode mutant inversion | Pending at cutover | G2/I0 retire `unset_mode_refuses` only when installing the new fail-closed runtime; test all eight inversions |
| 15 Q1 lag wording | Already corrected upstream | Preserve corrected evidence; no new implementation |
| 16 E2 RLS/clock | Pending | E2R updates five RLS denial expectations to SQLSTATE 42501 and shared `infrx_test` clock; run both real role contexts |

## Revised sequence and parallel boundaries

1. Commit/review this S1 audit as a common base. Keep original completion evidence; never create worktrees from an uncommitted tree. Confirm actual current main before applying if another session has advanced it.
2. **Parallel preparation:** F2R contract repairs, I0 installer failure tests, E2R isolated real-service harness repairs. They own different files except test/config composition, which the coordinator serializes. No cloud mutations.
3. F2P encodes the product-v2 types/fakes/fixtures against the repaired v1 base. Freeze the specific field/enum/RPC migration map and publish one reviewed contract commit. Do not make every feature owner invent a wallet or provider schema.
4. **Maximum useful module parallelism after F2P:** D1R; M2/M3 (same owner serializes overlapping paths); Q2; W2 against revised fakes; G1R/G2 drafts; C0; A2/A3 fixtures; L1 shell and L2 policy in distinct owned subdirectories; optional T2I and J2 dry-run work. I/E continue independently. Real integration waits on each manifest edge.
5. D1R opens D2 → D3 → D4 → D5. D owns migrations and shared state paths serially; A1 can be developed as a separate grant slice against frozen RPCs but its D-owned merge cannot collide. C0 can integrate real consumer reads once D1R is proven. U1R/C3A/U2/U3 and A2/A3 then complete the consumer UI loop.
6. Q3/M2/W2 plus D5 enable G2 composition; G3/G4U complete explicit jobs/uploads. I0 must already be proven. E3A validates signup → grant → key → priced inference → replay/cancel/settle → usage/exhaustion with real PG and a controlled engine. I2A/I3/E4 require separate allocated deployment/GPU evidence.
7. Lab L2/L3/L4 and E3L/I2L deliver provider operations independently. V1M moves V1 after L1/L2; C2 and trace/review/judge slices feed E5L. App E4 has no dependency on L/V/J/C2 or trace shipping. If App capture is enabled, its relevant trace privacy/resource gates become mandatory.

The manifest has **77 records: 71 active, six retired original mixed tasks**. The App E4 dependency closure has 41 tasks and excludes Lab/provider content/judge tasks. Start edges require reviewed code/fixtures; integration edges require real adapter evidence. Baseline code already merged may enable development without being falsely marked live-integrated. New F2R/F2P/D1R gates require their acceptance evidence before dependent implementation starts. This explicitly avoids the old C1/C2 integration deadlock.

## Release decisions still required

Production model CREDIT rates, transition of existing spendable USD balances, concrete Lab origin/auth configuration and allocated GPU deployment evidence are not known from this checkout. They do not block fixture-based implementation. They do block affected publication/cutover. Live judge pricing/consent/budget is a Lab-only gate. Default signup verification and personal-wallet mapping are specified; no further credit-unit or grant-scope question remains open.

Do not deploy the existing broad free-pilot plan and rename the UI afterward. Deliver the revised consumer loop, then independently enable the provider workflows that have their own acceptance evidence.

## Verification log

- 2026-09-21: Reconciled imported main, preserved original evidence, audited composition/schema/UI boundaries, applied bounded local corrections, and revised task graph. Detailed command outcomes are in the linked audit evidence; pending real-service/live gates are not counted as passes.
