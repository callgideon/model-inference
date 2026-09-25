# Evidence, operations and release briefs — S3, E2C, I8, E3C, E4C

Status: planned. [Program](../22-consumer-v1-implementation.md) and [manifest](../tasks.json) govern sequencing. Retain the original operational handoffs and E3B/E4B software as inputs. Extend existing runners instead of building a parallel certification system. Target review slices of 2–8 hours, with longer live windows explicitly bounded.

## S3 — Reconcile the actual baseline before coding

Own a new dated coordinator evidence record and proposed status corrections; coordinator merges manifest changes. Fetch main, read `RESUME-NOW`, the newest operational tail, [handoff 20](../20-platform-handoff-2026-09-24.md) §14, [review 21](../21-v1-consumer-readiness-review-2026-09-24.md), progress tracker and current release decision. Do not assume the in-flight run3 is still running or that all RV findings remain open.

1. Record source SHA, deployed SHA/image/runtime/model/processor/config hashes, migration history, enabled regime/flags, effective limits and current traffic state. Read configuration through sanitized queries; never put credentials or customer prompts in evidence.
2. Map every RV-01…RV-12 to open / fixed with evidence / superseded with reason. Preserve old task completion history while distinguishing implemented runner from accepted release. Reuse the bda1586 E1B 120-attempt cells only for the exact behavior/profile they measured; record any invalidated assumptions.
3. Inventory owned resources, test authorization, cost/runtime limits, approved workload, rate card, email/origins, alert recipient and missing release decisions. Use [pending inputs](../15-pending-inputs.md). Assign unresolved input to its gate; continue independent local work.

Acceptance: one committed reconciliation table tells every lane what to repair and what not to redo. It does not certify a deployment merely because a newer SHA exists. Any newly completed work narrows this plan through evidence, not silently dropped acceptance criteria.

## E2C — Reproducible Linux verification and failure oracles

Own local integration harness/CI/test portability; coordinator merges root Makefile/lockfile changes. Reuse E2/E2R. This package may inventory Linux resources while S3 proceeds, but uses its reconciled baseline for acceptance.

1. Publish one reproducible supported Linux environment: Python/Node/package lock versions, PostgreSQL/PostgREST/Valkey/S3-compatible service versions, required systemd/GNU utilities, Bash and container runtime. Define clean setup/teardown, isolated namespaces/ports/object prefixes and credential handling. Mac developer checks remain useful; missing Linux prerequisites are explicit NOT RUN/BLOCKED, never a green certification.
2. Fix test harness assumptions found by the audit: `git init` default `main` must not make a fetch target the checked-out branch; metrics tests inject/provide a platform abstraction rather than require `/proc/meminfo` on every host; scripts either require the documented shell/utilities or handle alternatives. Keep production Linux behavior under real tests. First confirm each failure on the supported runner; do not mask genuine defects as platform skips.
3. Ensure mutants/failure drills run on a green baseline, report surviving failures individually and restore modified state. Keep targeted seam regressions for readiness, expiry, cleanup, permissions and accounting. Quarantine requires owner/reason/expiry and prevents claiming the affected gate passed. Triage the moderate dependency alert, including reachability and fix/mitigation; preserve lockfile reproducibility.
4. Provide documented entrypoints wrapping existing runners: `make consumer-local`, `make backend-certify`, `make app-e2e` are **proposed deliverables**, not currently guaranteed commands. Preflight distinguishes missing resources, test failure, invalid benchmark and passed evidence using exit codes + JSON. Add contract/profile validation without triggering live requests.

Acceptance: a clean Linux checkout can reproduce the local gate from documented commands; a missing DB or deliberately broken readiness/expiry control yields a failing/non-pass result. Record complete counts, skips and per-suite duration, not only a favorable subset.

### Existing verification entrypoints to retain

At the planning baseline the root Makefile already provides the following commands. Run setup once with the pinned tools; execute focused lane tests during development and the required combined checks at integration. Live endpoints are not needed for these command definitions. Service-backed suites still require their declared environment, and their skips cannot certify integration.

| Working directory | Existing command | Purpose |
|---|---|---|
| Repository root | `make api-env` | Frozen Python dependency setup |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/contracts` | Shared Python contracts |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/d` | Durable state and grants; real DB prerequisites apply |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/m` | Media/upload/cache adapter checks |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/w` | Worker/engine/preparation checks |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/g` | Gateway/upload/jobs/headless operation checks |
| `apps/infrx-api` | `uv run --frozen pytest -q tests/i` | Deployment and operations checks on supported Linux |
| Repository root | `make bench-test` | Benchmark client/protocol regression suite |
| Repository root | `make integration` | Existing real-service harness; inspect supported arguments before selecting layers |
| Repository root | `make console-test console-lint console-typecheck` | Existing App tests, lint and generated-route-aware type check |
| Repository root | `make check` | Existing aggregate unit/mutation/console/benchmark checks after prerequisites are available |

Install App dependencies from its committed pnpm lock before App checks. Do not repeat the entire aggregate suite after each prose change; run it when runtime integration warrants it. New wrappers must compose these maintained suites and the existing backend certify runner, adding missing cells and verdict semantics without dropping earlier failure checks.

## I8 — Continuous operations, least privilege and restoration

Own infrastructure/deployment/observability artifacts and operational tests; D10 owns SQL roles; coordinator owns runtime composition/config. Reuse I2B/I3B. Seven separate review slices:

1. **Connection budget:** enumerate gateway replicas/processes, worker/preparer, collector, monitor, console and migration pools against actual Supabase limits, including reserved headroom. Prove concurrent startup and peak load do not exhaust the pool. If transaction pooling is selected, test prepared-statement behavior and transaction-scoped role/search-path/settings; never assume session state survives a pooled transaction. Record pool wait/timeout telemetry.
2. **Identity/config:** replace broad runtime database access with the D10 least-privilege role; exercise denied table/function/admin operations through the same deployed pooler. Version/validate config and env inputs at startup. Do not dump credentials into evidence. Grant-only/admin credentials stay outside public runtime processes.
3. **Continuous monitoring:** deploy and verify the scrape/export/retention/alert path, not just dashboard JSON. Include durable ready/unready/backlog age, prep/engine/terminal states, stuck holds/unknown usage, DB pool, disk/cache, GC lag, gateway buffering/drains and GPU readiness. Use durable queue truth where a legacy in-memory counter is misleading. No prompts, API keys, signed URLs or unbounded request-ID labels in metrics.
4. **Delivery proof:** send a clearly marked test alert to the configured authorized destination, verify delivery and recovery, and record owner/escalation/runbook. Reuse existing explicit notification authorization; otherwise leave delivery blocked pending that concrete configuration. Schedule operational sweeps/health checks in the deployment, not in this planning task. Avoid a success claim from a manually fetched metrics page.
5. **Artifact recovery:** mirror pinned weights/processor/tokenizer/templates/images/config in the approved durable stores with digest/access checks. Restore onto replacement ephemeral storage; measure fetch, model load, warmup and first usable request. Verify actual backup/PITR policy and perform the scoped database recovery drill. A file on instance NVMe is not a durable artifact strategy.
6. **Rollout/rollback:** identify the real known-good bundle, including prep worker and compatible migrations/config. The historical backup contained older code than expected; file presence/readiness alone cannot pass. Execute public finite-video inference, result retrieval and correct settlement after rollback, then roll forward and repeat. Prevent new incompatible admission during rollback; explicitly drain or retain ownership of accepted work. Additive schema compatibility must be proven with both versions. Separate cold startup time from misleading readiness-only timings.
7. **Runbook/deploy integration:** bounded maintenance/restore/cleanup commands, target allowlists, versioned alarms and evidence exporter. State single-GPU outage behavior honestly; no high-availability promise or extra fleet purchase is required here.
   S3 (2026-09-24): `gateway/pilot.py` `configure_connection` and `state/jobstore.py` `connector` set role and statement timeout as session state; under transaction pooling that state is lost. The certify runner's fresh-connection reads on 6543 are no evidence for prepared statements (S3 finding 6).
   S3 (2026-09-24): `observe.metrics.record_reconciliation` has no runtime caller at `dff31efc`: the drift/unsettleable gauges and their alerts are absent, and the certify soak's `reconciled_at_end` is always unknown (S3 finding 4).

Acceptance: a replacement process/instance restores the approved service; alerts reach the owner; pool/privilege limits hold at load; rollback serves and settles real requests. No live destructive drill on a customer-serving target without the applicable authorization/maintenance isolation.

## E3C — Corrective backend real-service integration

Own `tests/integration/backend/` and new E evidence, serialized with E2C/E4C runner edits. Start harness design from E2C/F2C/E3B; final integration requires D10/M5/M6/W5/G7/G8/I8/E1C. No Next.js process is required.

Run a composed stack with real PostgreSQL, PostgREST/auth boundary, Valkey and S3-compatible storage; the engine can be a controlled protocol server for deterministic failures. Label this clearly: it proves orchestration, not Marlin quality or GPU capacity. Include:

| Scenario | Required observation |
|---|---|
| Verified identity → grant → key → text/video sync/SSE/async → result → revoke | One entitlement, exact hold/settlement, correct modes and ownership |
| Two gateways and two tenants, repeated callback/idem/finalize | No cross-tenant access; one accepted logical request and terminal accounting |
| Crash each upload/admission/readiness/attachment/prep/outbox/claim/output/settle step | Recovery without prematurely executing, conflicting output, lost authorized media or double settlement |
| Empty manifest and late rejection | Text-only work cannot race ahead of capability/rate checks; permanent failures bounded |
| Two collectors with live jobs; expiry + policy change + replay | No deletion of live references; original expiry enforced on every read; scrub content, keep needed metadata |
| DB/object/Valkey unavailable; process replacement | Durable truth survives, waits/retries bounded, cache/wakeup rebuilds |
| CREDIT enablement while historical USD jobs exist | Original units/card preserved, no mixed settlement or implicit balance conversion |
| Runtime DB role, Supabase browser role, operator role | Real denied writes/reads, no bypass through RPC/function grants |
| Reconcile under active settlement/cancel/collector | No negative/spurious balances or recreated terminal content; typed conflicts |

Use subprocess termination for selected real-process crashes, not only monkeypatched exceptions. Isolate fault targets by resource allowlist; never kill a production process discovered by name. Compare API output **and durable DB/object/ledger state**. Each run writes a compact machine-readable verdict with reproduction commands and raw evidence references.

Acceptance: BACKEND-LOCAL passes on the combined revision, all new seam regressions fail when their relevant control is intentionally removed, and every skipped required case prevents gate acceptance. Review any increased code/test scope for actual relevance before broadening tests.

## E4C — Final repaired backend certificate

Reuse E4B's runner/protocol and valid E1B evidence. The old software task remains implemented; do not insist on certifying an obsolete binary before testing the repaired candidate. E3C is a prerequisite; this task needs actual allocated Marlin hardware/storage/DB/auth, approved CREDIT card/transition and the [load protocol](05-client-and-load-testing.md).

1. Freeze candidate source/image/weights/processor, capability and price snapshots, DB migration hashes, rollout bundle and workload manifest. Run preflight and end-to-end headless CREDIT journey from an external client. Recheck public discovery and content-retention claims against actual behavior.
2. Run required capacity/burst/soak/fault/restore/rollback cells on the final combined configuration. Explicitly declare workload/SLO/quality-parity criteria before measurement; P-18 unresolved means measured data can be reported but release acceptance remains pending. Distinguish video-only from mixed traffic and reused outputs from fresh generation.
3. Reconcile every accepted item/hold/terminal record and actual test spend; inspect memory/disk/queue/pool behavior during steady state and recovery. Validate alerts, source/result expiry and cleanup without altering other users' policy. Evidence from a prior SHA can be reused only with a written unaffected-path/profile rationale; rerun combined journey after the last runtime change.
4. Publish a candidate decision with PASS / FAIL / BLOCKED / INVALID / NOT RUN per cell, limitations, uncovered modalities, accepted operating envelope, rollback triggers and dated operator decision. Preserve failed attempts and fixes. Do not label aggregate mixed-workload p95 as video p95 or parity on two clips as SOP accuracy validation.
   S3 (2026-09-24): the certify dataset ledger half asserts CREDIT (E4B protocol §3). A `legacy_usd` target yields a regime mismatch, not a runtime verdict (S3 §2.2). Run E4C in CREDIT mode, or record the ledger half NOT RUN with the regime.

Acceptance: BACKEND-READY is accepted on a pinned target within measured limits, with no unresolved launch-path security/accounting/lifecycle defects and all required evidence present. Source status `implemented` alone cannot release App dispatch. Public cutover still follows the documented account/traffic transition and launch authorization.

### Acceptance checklist (P-17, decided 2026-09-25)

Decided by the coordinator under the operator's authorization ([15 P-17](../15-pending-inputs.md#decisions-2026-09-25-coordinator-under-the-operators-authorization); draft §P-17 in `research/plan/evidence/coordinator/2026-09-25-inputs-decisions-draft.md`). Acceptance is mechanical:

1. **Gate schema** (`research/plan/evidence/coordinator/updates/README.md`:62): E4C is `implemented`; `gates.BACKEND-READY.candidate.source` and `.deployed` are recorded; the cells cover every E4C `test_id` (BACKEND-JOURNEY, LOAD-CLOSEDLOOP, PERF-ENVELOPE, OPS-CONTINUOUS, CREDIT-CUTOVER, MARLIN-SOP; `tasks.json`:4501-4508), each PASS with evidence; reuse of a historical candidate is explained in `candidate.note`.
2. **Frozen identities** (step 1 above): source, image, weights and processor digests (P-06 filled), capability and price snapshots, migration hashes, rollout bundle, workload manifest and the run-profile sha (P-24, `models/marlin2b/profiles/E4C-box.base.json` as stamped per cell).
3. **CREDIT regime** (S3 note above): the ledger half ran in CREDIT, not `legacy_usd`; the active card is the P-01 card `rc_marlin2b_20260925_launch`; the P-02 dry-run exited 0 before activation.
4. **P-18 limits committed before the first qualifying run's start timestamp** (`models/marlin2b/results/E4B-protocol.md` §5, amendment 6; [05 §3](05-client-and-load-testing.md)); every PERF-ENVELOPE and LOAD-CLOSEDLOOP threshold row is PASS on the final combined configuration (step 2 above).
5. **Two-tenant headless journey passed** ([04](../04-verification.md) BACKEND-JOURNEY): an external CREDIT journey passed with two tenants (P-05) over sync/SSE/async, upload/URL, cancel, replay and dataset resume; public discovery and retention claims match behavior (step 1 above; R109).
6. **Exact reconciliation within the P-24 cap** (step 3 above): every accepted item, hold and terminal record reconciles exactly with `drift == []`, and actual test spend is within the profile's 50,000 CREDIT per-cell cap.
7. **Alert, expiry, restore and known-good rollback proven** ([04](../04-verification.md) OPS-CONTINUOUS; R119): `74-alert-test.sh` delivered to the P-25 destination; result/source expiry and cleanup observed; a restore and a rollback to a known-good target (`infra/rollout/known-good.py` exit 0 with `--bundles`, plus `infra/rollout/steps/85-known-good-box.sh`, P-25) executed.
8. **No open launch-path P1** security, accounting or lifecycle defect (`tasks.json`:4521); RV-04, RV-08, RV-09 and RV-10 (`tasks.json`:4719-4756) are `fixed` with evidence.
9. **Published limitations** (step 4 above): limitations, uncovered modalities (no live video, no actuation, no clips over 82 s), the accepted P-18 envelope, rollback triggers and "single-GPU recovery is not high availability"; no aggregate p95 labelled as video p95.
10. **Cutover separate** (Acceptance above): the public account/traffic cutover is a separately recorded authorized action; acceptance releases App dispatch ([22](../22-consumer-v1-implementation.md)), not public launch.

BACKEND-READY is accepted only when all ten hold. The coordinator records the decision, its date and the SHA in the E4C evidence under `research/plan/evidence/e/` and in `gates.BACKEND-READY`; a false item is `rejected` or pending with the item named, and a later FAIL on the same candidate sets `rejected`.

Runbook: the box shell that runs certify exports only `INFRX_API_KEY` (the profile's `tenant_key_env`); bench prefers `MARLIN_API_KEY` when both are set, and the cell is then refused.
Window runbook: [models/marlin2b/results/E4C-runbook.md](../../../models/marlin2b/results/E4C-runbook.md) (freeze, FILL sources, key inventory, the certify and two-tenant journey invocations with `E4C-edge.overload.base.json` and `E4C-box.two-tenant.base.json`, drill record, evidence layout, P-17 tick-off).

## Evidence format and ownership

Use append-only dated directories under the established track evidence roots. Include base/head/deployed identity, dependency versions, redacted effective configuration, input/profile hashes, authorization/resource bounds, commands/exit codes, per-cell attempt/fresh/replay/reject/error counts, telemetry windows, ledger reconciliation, operator decision and cleanup confirmation. Raw private media/prompts/keys stay in their approved protected store, linked by opaque IDs/hashes only. Re-run a failed cell after repair and retain both versions; never overwrite failure evidence with a final green summary.
