# Executable revision handoffs after wave 2

**Current priority (2026-09-22):** [Marlin backend first](18-marlin-backend-first.md). E3B/I2B/I3B/E1B/M4/W4/E4B separate endpoint readiness and measured optimization from the later App browser/deployment gates; G6B provides protected headless operations. Use the updated [fresh-session handoff](16-fresh-session-handoff.md) and manifest for dispatch.

**Current scheduling overlay:** [complete plan](12-complete-build-plan.md), manifest v4 and [fresh-session handoff](16-fresh-session-handoff.md). Marlin App launch is the immediate scope; compatible brief details below remain binding. S2M adds the Marlin profile; Lab M2–M4 are now decomposed separately.

Use the [audit](10-wave2-platform-audit.md), [manifest v4](tasks.json), [product architecture](../platforms/README.md) and original module briefs together. These revision tasks preserve completed v1 behavior and establish the common base for the remaining implementation. They supersede conflicting start/order/ownership instructions in the old F2.2 list. No implementation-model-specific orchestration is required.

## Assignment protocol

Record task ID, owner, reviewed base SHA, branch/worktree, owned paths, test-resource namespace and reviewers in the coordinator log. Use `codex/<task>-<description>` branches from a **committed** integration base. Each handback includes code SHA, exact commands/exits/skips, named failure oracles and intentional defect detection, schema/contract changes, unresolved integration dependencies and rollback effects. Review at the actual handback HEAD, fix findings, rerun on the merged tree. Fakes mean implemented; actual adapters and integration dependencies mean integrated; neither means deployed.

Only the coordinator edits composition roots, shared contracts/config/locks, task manifest, Makefile and both-app build wiring. A delegated F task may own a clearly recorded shared-contract revision until handback; other owners submit integration requests against it. D owns every SQL migration and migration-number allocation. Do not duplicate the Supabase migration directory under Lab. Retain 0001–0005 unchanged.

## Concurrent worktree allocation

The coordinator can use the following lanes after recording each base and path claim. These are independent worktrees, not instructions to spawn agents automatically. Allocate only ready tasks; one lane may cover several sequential tasks. The manifest remains the dependency authority.

| Lane | Earliest useful task | Exclusive files / collision rule | Integration proof |
|---|---|---|---|
| Contract Python | F2R Python records/ports/fakes | `infrx/contracts`, shared config and Python conformance; temporarily coordinator-delegated | Shared and concrete W/T adapters agree; no production fake base |
| Contract TypeScript | F2R TS DTOs/fakes | App contracts/fixtures/tests; cross-language names frozen before merge | Legacy/nullability/bounds fixtures and TS conformance |
| Installer | I0 | Deploy scripts + I tests; mode/composition via coordinator | Failed install leaves env/service intact |
| Harness | E2R | Root integration tests; D harness changes with D owner | Foreign resource untouched, corrected real-role evidence |
| Database | D1R → D2 → D3 → D4 → D5; A1 as a coordinated grant slice | All SQL + state paths; one writer/number allocator | Real PG transactions, races, upgrade and replay |
| Media | M2 → M3, or split preparation/upload directories explicitly | Media implementation/tests; no shared type edits | Real storage/probe bounds, owned upload lifecycle |
| Queue | Q2 → Q3 | Scheduling implementation/tests | Memory/Valkey differential fairness and PG rebuild |
| Worker | W2 → W3 | Worker implementation/tests | Fenced completion/cancel/restart and measured engine |
| Gateway | G1R → G2/G3/G4U | Route/auth tests; coordinator mounts | Full durable HTTP/SSE loop, not fake acceptor |
| Consumer services | C0 → C3A | App server/query/actions; shared extraction by coordinator | Actual tenant DB context and exact balances |
| Consumer views | A2/A3, U1R → U2/U3 on separately claimed routes | Auth/catalog versus keys/admin views; no duplicated server logic | Verified signup-to-spend browser journey |
| Lab shell | L1 then L4 | Lab app/layout/UI; workspace/package edits coordinated | Separate build and guarded provider routes |
| Lab policy | L2 then L3 | Provider service/policy modules; D owns SQL | Cross-provider/source-purpose denial and publication |
| Provider explorer | V1M then V2/V3 | Lab trace/review UI; App removal only coordinator | Authorized trace list/content and both app builds |
| Capture | T2I → T2F → T3 | Trace shipping/analytics/retention paths | Crash/dedupe/deletion and bounded capture |
| Evaluation | J2 then J3 | Judge workflow/adapters/tests; fake source until permissions ready | Consent/budget/idempotency; live calls separately gated |
| Integration/release | E3A/E3L/E5L scenarios, I2A/I2L packaging | E owns cross-module tests; I deploy templates; one environment lock | Independent App/Lab release evidence |

F2P may also split Python and TS implementation after the **same** field-level schema is agreed, but it has one atomic acceptance gate. Shared fixture versions, lockfiles, package manifests and composition are never concurrently edited. Database and C service lanes remain serialized on shared files. This gives broad parallel development after the contract base without pretending partially integrated modules are ready to serve.

### F2R — complete the repaired v1 contract baseline

**Start:** S1 reviewed audit base. **Paths:** Python contracts/config and tests/contracts, TS contracts/tests, Makefile and encoding docs. Specific cross-module edits to engine/spool/media/judge and mutation runners are coordinated as one revision; pause competing edits to those files. Existing local fixes for deadline/money/UI/test portability remain in place.

1. Use the audit's 16-item disposition table as a checklist, not the old unchecked list. Close items 2–7 and 9; item 11's real context/logging belongs to C0. Item 13 is J2 live-only; item 14 is G2 cutover; item 16 is E2R. Record each closure/transfer explicitly.
2. Update shared FakeEngine to raw/visible, exported conformance and W adapter together; remove content alias and correct memory copy accounting. Public relay/journal use visible only; raw is internal observability/usage evidence under policy.
3. Extract production trace capture/accounting from FakeTraceSink. Inject clock, move crash simulation to tests, use max(declared metadata bytes, actual serialized bytes), preserve bounded overhead/content/segment accounting and spool v2. No production subclass or import may acquire fake test hooks.
4. Make media staging accept only materialized refs produced by the store. Expose a harness hook to construct authentic refs. Canonical requests contain one matching media part per ref in order. Define who writes duration (M2) and prove forged/foreign refs cannot attach.
5. Move candidate source/sample protocol into shared contracts. Require unique lower-case UUIDv4 judge sample IDs. Freeze the judge-run sample DTO and nullable audit target.
6. Freeze **legacy nullable** TS usage/key fields rather than rejecting old D1 history: missing deleted key is null, accounting regime explicit, unknown old usage certainty not invented. Summary/daily bounds required. Fix code-point lengths and exported conformance/self-test leftovers named in the old handoff.
7. Add exact config names/defaults from original item 7, including spool segment limit, index caps, database pool/timeouts, cursor secret and intake structure caps. Invalid config fails before mounting pilot ingress.
8. Consolidate the eight Python mutation runners. Every subprocess owns its cache/temp path; a syntactically invalid mutant or unexpected undeclared runtime error must not count as the intended assertion kill. Preserve canary, survivor and timeout sensitivity tests.

**Verification:** full affected conformance on shared fakes and concrete W/T adapters; cross-language fixtures; `make check` with skip inventory; mutation runner self-tests and all affected mutation lists. Independently remove each new critical guard and require its named assertion to fail. No paid judge submission required.

**Done:** no unassigned F2.2 item, consistent v1 ports/config/fakes/production adapter semantics, reviewed repair commit. F2P starts from this exact commit. Preserve R1–R60 history and append corrections rather than silently rewriting their evidence.

### F2P — encode the product-v2 contracts

**Start:** F2R accepted. **Paths:** shared Python/TS contracts, fixtures/conformance, contract/database map docs; dependency/package extraction only through coordinator. No speculative training schema.

1. Publish a field-by-field v1→v2 map covering AuthContext, NormalizedRequest, Admission/Work, PriceSnapshot, usage/balance DTOs, credential audience, wallet kind, provider membership, serving/deployment revision and data access policy reference. Keep public OpenAI-style fields compatible; explicitly version internal schemas and proprietary extensions.
2. Money arithmetic is shared; denominations are not. Introduce exact CREDIT decimal types and separate legacy USD/external-provider USD types. Reject mixed-unit arithmetic, wrong-unit rate cards, negative/nonfinite/out-of-domain inputs. Python runtime validation must enforce the unit, not just a TS type alias.
3. A consumer credential resolves a user-owned wallet and protected personal-org binding from trusted data. A provider dev credential resolves a zero-initialized, operator-funded provider wallet and private endpoint audience. Neither can choose another wallet in request fields. Provider dev wallets have no signup entitlement and cannot transfer into consumer wallets.
4. Pin model, serving revision (weights/adapter/prompt/harness/preprocessor/runtime), deployment revision and approved CREDIT rate in admission. Caller-supplied prices/identities are refused. Test changing rates/aliases after acceptance and retrying idempotency.
5. Freeze the initial-grant operation by individual identity, independent of campaign/org/provider membership. It records amount, wallet, verification evidence and unique operation. Keep legacy USD replayable and separate; no conversion rate is assumed.
6. Freeze provider roles and source-purpose grants for capture, sharing, external judging and training. New permissions default deny. Provider ownership alone yields no customer payload. Plan current-grant checks for revocation and short-lived content access, not snapshot-only authorization.
7. Update fake records, ports and existing module fixture consumers atomically. Keep an explicit v1 read projection/upgrade test instead of pretending all old rows contain new fields. Independent runtime adapters may remain fake-backed, but the common tree must typecheck/test with v2.

**Verification:** SPLIT-CONTRACT, CREDIT-UNITS/IDENTITY/RATE and LAB-ACCESS fixtures in both languages; unit-confusion and forged wallet/provider/endpoint mutations fail; mixed legacy/CREDIT histories remain intelligible. Entire changed consumer/provider contract surface has one reviewed version identifier.

**Done:** committed v2 fixture base and schema/RPC map usable without owner-local edits. This is the broad parallel-work gate. It does not mark A1 or D1R implemented.

### D1R — additive CREDIT and minimal provider schema

**Start:** F2P accepted; original D1 schema/evidence already in baseline. **Integrate:** E2R owned service harness. **Paths:** `apps/app/supabase/migrations/`, D state/schema tests. Reserve new filenames centrally after 0005. Keep individual migration slices reviewable; no production apply.

1. Inventory 0001–0005 constraints, wallet trigger and narrow financial RPC privileges; test upgrade from that exact schema with existing users, positive/negative USD history and accepted old-regime jobs.
2. Add CREDIT wallets/ledger/holds and user entitlement uniqueness without rewriting USD totals. Protect wallet kind, owner binding and denomination with DB constraints and narrow server operations. Existing USD jobs settle in their original regime; new jobs resolve a supported explicit regime.
3. Add the minimum provider/model/serving/deployment/listing/rate ownership needed by A3/L2/L3. Operator seed path must work without Lab. Immutable revisions and rate snapshots cannot be edited retroactively. Provider display text is not an ownership key.
4. Add real RPC/view projections with exact text amounts and explicit legacy fields. Revoke default table/function privileges before granting intended access. Keep `infrx` hidden from PostgREST; grant only public narrow RPCs. No direct browser mint/hold/settle/provider-role writes.
5. Prove upgrade re-run/idempotency, compatibility with the legacy read path, and fail-closed behavior when new writes cannot be honored. Separate migration application from feature enablement. Document legacy-account transition as a rollout input; do not choose an exchange rate.
6. Hand A1 the schema/RPC seam for the initial grant; D2 the atomic admission schema; C0 exact result shapes. Coordinate D-owned SQL merges serially.

**Verification:** real PostgreSQL plus Supabase role/default-ACL behavior; explicit anon/owner/foreign/provider/operator/service cases. Migration fixture preserves historical values/IDs, old jobs, no cross-unit totals. Inspect query plans at realistic tenant size. Use repaired E2R ownership before real-service runs.

**Done:** additive migration evidence and prior-regime compatibility; no legacy schema rewrite and no hosted apply. A1 still separately proves concurrent verified grant issuance/backfill; D2–D5 supply real transactional runtime.

### C0 — real consumer database adapter and account context

**Start:** F2P accepted; original C1 code available in baseline. **Integrate:** D1R. **Paths:** App server-only services/query adapter, consumer context and targeted tests; coordinator owns page/layout composition. C2 remains Lab content access.

1. Implement C1 QueryPort using the existing Supabase/PostgREST client, bounded named queries/RPCs, mandatory summary bounds, keyset instants and exact amounts. Never expose a generic SQL executor to the client or route parameters.
2. Resolve actual auth identity and personal consumer wallet from trusted server state. Wire App usage/balance to that identity. No fixture fallback on timeout, denied access, missing schema/row or shape failure. An error must not become a zero balance or an empty successful report.
3. Preserve legacy USD history with a separate projection and label; nullable legacy fields and deleted key identities are first-class. Do not synthesize charged CREDIT from cost_usd. Account balances reflect all holds, not a first-page ledger sum.
4. Replace preview contexts for consumer routes after the real port is proven. Keep development fixtures explicit. Trace/provider contexts remain Lab-owned; C0 does not make App consume ClickHouse/content/judge services.
5. Add server-side shape-failure diagnostics containing bounded relation/column/error category only. Never log row content, credentials, cursor secret or signed object references.
6. Audit client import graph and trusted session boundary. Use actual PostgREST/JWT role behavior as well as repository query tests; verify operator reads still obey intentional scope and financial actions remain separately authorized.

**Verification:** two users, provider-only member, operator, revoked session, expired/forged cursor, missing/deleted key, mixed legacy/current history, DB outage, missing RPC and held-balance edge. One user must never see another's rows with a crafted query. Compare displayed available to durable ledger minus holds; prove no numeric precision loss at eight places. Test full bounded query plans before E4.

**Done:** signed-in consumer pages read real tenant data, not fixtures; test failure is observable; C1 read services can be marked integrated with C0 evidence. C2/V/J are not dependencies. U1 retains separate D5 end-to-end settlement evidence.

### I0 — installer and runtime startup prerequisite

**Start:** reviewed S1/I1 baseline. **Paths:** installer/startup packaging, infrastructure tests/runbooks; shared mode/route composition by coordinator. This task modifies local code only.

1. Read every required SSM/config value successfully and validate it before touching an existing environment file. Fail on missing/empty/denied values. Stage a private file, atomically replace only after validation; keep secret values out of stdout/logs.
2. Prove failed fetch/validation leaves old file and running service intact, and performs no restart. Use local stubs for AWS/systemctl/filesystem failure injection; do not execute the installer against the pilot host.
3. Specify supported explicit legacy/pilot mode transition. Do not set pilot mode while only legacy routers are composed. Missing auth/metering/runtime dependencies prevent the new pilot process from serving. Rollback after credit enforcement is compatible runtime or maintenance 503, never unmetered serving.
4. Pin Python/runtime prerequisites and transport logging requirements per accepted handoff. Coordinate G2's eight cutover inversions; keep the old unmet-mode mutant until the corresponding real behavior changes.

**Verification:** denied/missing SSM, half-written env, crash before rename, bad mode/config, restart failure and valid install with mocked system calls. Assert filesystem bytes/restart calls, not merely an exit code. Runtime composition remains disabled until G2's full gate.

**Done:** reviewed installer and startup failure evidence; safe prerequisite for G2/I2A. This does not fix the deployed host until an explicitly scoped later deployment applies it.

### E2R — trustworthy local service evidence

**Start:** S1 baseline; E2/D1 code already merged. **Paths:** `tests/integration/`, D harness isolation changes coordinated with D, evidence and coordinator-owned Makefile only if needed. No production credentials.

1. Keep the audit fixes for the five-file migration inventory and portable orphan scan. Repair D harness fixed resource adoption: identify owner before using/stopping/deleting any container. Use distinct namespace/port per checkout or a lock that refuses concurrent runs without altering the first. Track only resources created by this run.
2. Update the five RLS denial expectations carried from E2 to SQLSTATE 42501 with actual D1 migrations. Use shared `infrx_test` clock conventions. Confirm claims/GUC behavior on real Supabase/PostgREST; do not assume a shim proves it.
3. Run baseline service layers after fixes, then leave fixtures extensible for D1R/E3A. Report exact migration list and container digests. Include deliberate missing grant/broken ownership/cross-tenant mutations and prove named assertions fail.
4. Preserve exit 3/PENDING for unavailable services. `make check` skips and Layer 1 success never count as Layer 2. Do not bury service failures in a shell pipeline's success status.

**Verification:** simultaneous second-run refusal or proven unique resource isolation; foreign resource untouched; cleanup after crash; real PG/Supabase permissions and socket engine behavior; canary/survivor runner tests. Record Docker absence as pending, not failure of the application.

**Done:** repeatable owned test environment and corrected service evidence; E3A/E3L use its helpers rather than private clocks/RLS assumptions. Full product integration remains pending.

### G1R — revised endpoint audience and admission context

**Start:** F2P accepted; original G1 validation/ingress is in baseline. **Integrate:** D1R/D2. **Paths:** gateway auth/validation/catalog resolution and G tests; coordinator owns `gateway/app.py` mounting.

1. Resolve public consumer versus provider dev credential audiences; wallet, provider, endpoint visibility and rate are server-derived. Consumer keys cannot invoke private dev endpoints; provider dev credentials cannot debit consumer wallets.
2. Public model aliases resolve only active approved published deployments and supported capabilities. Operator-seeded catalog must work before Lab. Validate request types/shapes and retain stable standard error envelopes.
3. Hand D2 trusted context without allowing body fields to set wallet/rate/policy. Admission rereads current authorization/publication and snapshots revisions atomically. Alias/rate changes cannot alter accepted jobs or idempotent replays.
4. Retain secure intake, encoding, media ownership and deadline tests. Do not mount the pilot router or instantiate partial runtime here. Submit a minimal composition request for G2 once its dependencies are real.

**Verification:** forged wallet/provider IDs, private endpoint, revoked publication/key, missing rate, wrong unit, rate/alias race, denied capacity and retry. At DB integration, refusals leave no hold/job/journal side effects. OpenAI-style client smoke tests use only declared supported features.

**Done:** v2 ingress can be developed against revised fakes and integrated with real D2 independently of Lab UI. The public runtime remains behind the G2/I0 composition gate.

### V1M — migrate provider explorer into Lab

**Start:** F2P plus reviewed L1 shell code. **Integrate:** L2/C0/T2I. **Paths:** existing trace view models/components/tests and new Lab equivalents; coordinator owns workspace/shared package and App route removal.

1. Inventory V1 files and shared helpers. Move provider-specific code/tests into Lab; extract only genuinely shared formatting/contract components into a neutral package if necessary. Neither app imports the other's server actions or private app routes.
2. Replace fixture consumer owner with trusted provider session. Enforce provider membership plus current purpose/resource grants through L2/C2; aggregate health must not leak source customer identities or content without authorization.
3. Migrate trace navigation to Lab and remove the App provider route after both builds pass. An App own-request history is still Usage; do not sneak provider control features back into the consumer shell.
4. Preserve pagination, missing-content/loss states, safe error rendering, frozen-window cursors and deterministic fixture clocks. Keep previews clearly labeled and impossible in production.

**Verification:** both apps build/test independently; App bundle and routes contain no provider explorer/action surface; provider A versus B, consumer-only membership, revoked source grant, expired content, capture off and lost capture behave correctly. Direct API/action access must be denied even when navigation is hidden.

**Done:** V1 behavior reused in the right app with real authorization evidence; V2/V3 extend Lab only. No provider content is enabled by a UI move alone.

### U1R — CREDIT usage and balance views

**Start:** F2P accepted; U1 code is already in the baseline. **Integrate:** C0/D5. **Paths:** consumer usage/balance pages, view models and U tests; shared formatter/type definitions via F/coordinator. C0 owns query execution/context, not these business views.

1. Replace USD-only assumptions with explicit accounting-regime DTOs. Format current wallet balances, holds, debits and published rates as **credits**, with exact eight-place arithmetic and no dollar symbol. Legacy USD history keeps its denomination and is never summed into CREDIT tiles/charts.
2. Preserve U1 pagination/filtering, uncertainty/hold states, error handling and accessibility. Bind page data to C0's trusted real account context at coordinated integration. No fixture fallback and no synthetic 10,000 balance before A1 persists the grant.
3. State the free-plan grant accurately: once per individual, no monthly refill or available payment/top-up action. Empty, exhausted, pending/reconciling and unavailable balances remain distinct. No provider analytics or judge dependency.
4. Exercise a fractional-credit charge, active hold, unknown usage, deleted key, legacy-only account and mixed USD/CREDIT history. Show current available as current-unit ledger minus holds; label historical reports explicitly rather than discarding them.

**Verification:** CREDIT-UNITS and U view-model/render cases using noninteger and near-domain-limit values, followed by C0/D5 real-account integration. Intentional dollar-format/unit-merging/wallet-context mutations fail named assertions. Consumer route build and keyboard/error/empty flow checks pass.

**Done:** the original U1 implementation is adapted to the agreed consumer product with precise units and preserved history. U2/U3 continue from this reviewed UI base. A2 still owns the signup journey; U1R cannot mark the grant implemented.

## Remaining original and amendment tasks

D2–D5, M2/M3, Q2/Q3, W2/W3, G2/G3/G4U and C3A/U2/U3 remain the App execution lane; their original detailed algorithm briefs still apply under F2P/D1R. Read [09](09-amendment-workstreams.md) for A1–A3, L1–L4 and split D6/G4/T2/C3/I2/E3 tasks. The manifest carries updated dependencies; the [audit](10-wave2-platform-audit.md) explains why. No remaining owner should infer that old USD structs, App trace placement or old F2.2 order are approved.

For G2 specifically, real composition requires D5, W2, M2, Q3 and I0. A router that calls a fake acceptor is not integrated. Test durable acceptance, committed-before-relay output, exact terminal settlement, cancellation, DB/index/storage outages and process restart before requesting deployment.

For all release owners, do not require Lab judge or trace UI to release App with capture off. Do not skip trace/privacy tests if capture is enabled. Paid provider pricing/budget decisions and cloud changes stay with their explicit release scope.
