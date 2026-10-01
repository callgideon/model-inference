# 26 — Launch readiness review, 2026-10-01

Reviewed main: `252f3ea8fda0d144fe03151840bb232fb1c76633`. Last remote check: 2026-10-01, approximately 18:46 UTC; unchanged. This is an independent review and proposed launch sequence, **not a release acceptance or an instruction to change the running certification window**.

## 1. Assessment

The consumer product has moved substantially beyond the September 24 baseline. The Marlin endpoint is deployed in CREDIT mode, the consumer App is deployed, its real account/key/usage/result adapters are implemented, and the backend repairs have local integration evidence. Most remaining consumer work concerns production verification, onboarding configuration and operational readiness. There is also a specific missing onboarding implementation: the approved CAPTCHA requirement has not reached the auth forms.

We should aim first for an **invited, bounded production pilot of the consumer App and Marlin API**. Public self-service follows the hosted onboarding and abuse checks. Completing the Lab, adding arbitrary models, implementing GPU autoscaling or matching Modal's breadth is not necessary for this first pilot.

The current E4C window cannot, by itself, close the existing BACKEND-READY definition: the handoff and wrapper explicitly leave P-17 checks 1, 5 and 7 false. Complete the missing checks or record a separate, explicitly limited internal-test decision. A skipped check is not a passing check, and a narrower decision must not overwrite the existing gate.

### Evidence boundaries

- Read the latest handoff/addendum, state record, carried register, original consumer requirements and closure program, deployed configuration/launch records, and selected production composition, auth, catalog, upload/result, worker and verification code. This is a review of the critical paths and evidence, not a claim to have inspected every line of the approximately 500,000 added lines, much of which is test output.
- Ran fresh local checks and unauthenticated production HTTP probes. [Verification record](evidence/coordinator/2026-10-01-launch-review/verification.md) and [public responses](evidence/coordinator/2026-10-01-launch-review/public-probes.json) preserve the results.
- Did not submit paid inference, provision accounts/resources, change flags, migrate a database, deploy, inject a production fault or interrupt E4C. Existing operating evidence remains attributed to the implementation session.
- Docker is installed on this review Mac but its daemon is unavailable. Real PostgreSQL/PostgREST/Valkey/object-store/browser integration gates were not rerun here. Successful unit checks are not substitutes.
- Historical [state 25](25-state-2026-10-01.md) and evidence documents are snapshots. Use the dated addendum in [handoff 25](25-planning-handoff.md) for later events; do not rewrite the snapshots to imply earlier knowledge.

## 2. What is actually available

| System / capability | Implementation and deployment evidence | What remains unproved or incomplete |
|---|---|---|
| Marlin inference | Handoff reports consumer runtime `41693d5d`, one L40S. Fresh public `/health` and `/v1/models` both return 200; catalog reports available, CREDIT and the approved card. | Health/catalog do not prove a full authenticated request, settlement or recovery. E4C final result is pending. |
| Actual serving profile | Pinned vLLM image, BF16 Marlin weights, immutable processor/template identities; public catalog advertises text/video → text, sync/SSE/async, one finite video ≤82 seconds and ≤64 MiB, 2 fps, 32,768 context / 2,048 output tokens. | These are enforced capability ceilings, not a guarantee that every encoding near the ceiling succeeds, an SLA, native streaming video, robot actions or SOP accuracy. |
| Durable execution | Admission/credit holds, job ownership, idempotency, leases/fences, preparation, stream journal, result retrieval/cancel, reconciliation and media lifecycle are implemented with backend-local evidence. | Production load, fault, retention, restore and accounting acceptance on the selected deployment combination. |
| Consumer App | Fresh `/api/version` identifies production commit `252f3ea8`, built at 18:24:01Z. Signup/verification/recovery, once-per-individual grant, keys, catalog/docs, credit/usage/request detail, owned result and operator controls are implemented. | Hosted email/onboarding/browser journey; public signup remains closed per latest records. `/signup` returning HTML does not demonstrate account creation works. |
| Commercial policy | One-time 10,000 CREDIT per verified individual; free plan only. Live card: 400 CREDIT/M input tokens, 1,200 CREDIT/M output tokens. Exact unit separation and legacy USD are implemented. | Live two-user grant/ledger/retry/revocation proof and free-plan abuse configuration; no payments/top-ups are required for v1. |
| Operations | Release scripts, monitoring/maintenance, retention, backup/restore and known-good tooling exist; schema 0001–0059 is reported applied. | Delivery/canary and selected outage tests were skipped in this window; replacement-host restoration remains uncovered. Tools existing is not evidence of an exercised response. |
| Lab web / control | Lab root returns a sign-in form. Control origin refuses unauthenticated release reads with 401. Provider access, data/evaluation/judge/pipeline/release interfaces and many local suites exist. | Control unit is reported at `7ecbab0e`, other worker roles inert. Evaluations/pipeline listings, trace composition, dedicated role logins and real engine smoke remain incomplete. |
| Hosting / optimization | The current Marlin recipe already uses vLLM. There are versioning, qualification and optimization foundations. | No accepted general model-card/weights → qualified endpoint service; no fleet autoscaler/scale-to-zero, heterogeneous placement, accepted SGLang alternative or matched Modal comparison. Follow [roadmap 23](23-inference-hosting-roadmap.md). |

**Release identity matters, but different SHAs are not automatically a defect.** Comparing `41693d5d..252f3ea8` shows no consumer App source changes outside its README and migrations. Main does contain backend composition, shared SQL adapter and engine-client refactors not deployed in the consumer runtime. Do not interrupt certification to deploy main solely to align labels. Qualify the intended combination, then use a separate runtime upgrade window if needed.

The Lab does not currently provide the entire model-improvement loop. Its internal checklist is deliberately limited to login/workspace/settings, a judge dry-run and access boundaries ([08 §8](consumer-v1/08-lab-internal-testing-rollout.md)). Completing that checklist does not prove dataset → real model evaluation → training → deployment → observed improvement.

## 3. Review findings and original-plan reconciliation

The references below refine the existing tasks/register; they do not create a second task graph. “Blocks” is scoped to the named release. The carried register's “blocks testing” column describes the narrow **Lab checklist**, not consumer or public launch readiness.

### LR-01 — P1 for accepting the consumer release: certification has explicit missing checks

Evidence: [handoff 25, status addendum](25-planning-handoff.md), [certify-window.sh](../../infra/rollout/certify-window.sh) lines 22–23, 131, 346 and 418; [P-17](15-pending-inputs.md), register row 3. The window records skipped alert/canary work and outage drills; the SSE/two-tenant work also has recorded prerequisites. The wrapper ends by retaining BACKEND-READY PENDING.

Action: when the active run ends, ingest its actual report and enumerate every PASS/FAIL/SKIP/PENDING against P-17's ten checks. Close missing cells in a declared follow-up window without discarding valid measured results. Preserve the runtime/image/profile/card/schema pins. An internal-test exception, if chosen, must name its limits and outstanding checks; it is not APP-PILOT acceptance.

### LR-02 — P1 before public signup: CAPTCHA is missing from the implemented auth flow

Evidence: P-05 explicitly requires CAPTCHA for signup and recovery. `apps/app/app/(auth)/flow.ts:302–329` accepts/sends only redirect options; the signup form collects email/password only; `apps/app/lib/deploy/env.ts:55` and its corresponding configuration test omit CAPTCHA from the P-05 checklist. No CAPTCHA/token integration was found in App source.

Action: extend A2/I2A, rather than opening signup as a configuration-only change. Implement the chosen challenge and token handling for the applicable hosted auth calls, failure/expiry/retry states, and missing-config behavior; reflect it in the deployment checklist. Verify the hosted auth settings, production redirect allowlist, SMTP/confirmation/recovery delivery and rate limits. Test rejection without a valid challenge as well as successful verification and exactly one grant. Audit any additional auth calls covered by the chosen hosted challenge policy.

This does not require changing the once-per-individual policy or block a deliberately invited test using existing verified accounts. Do not reset an existing signup entitlement to manufacture a passing onboarding test.

### LR-03 — P1 for public operation: operating proof is incomplete

Evidence: register rows 3, 9, 77–79; P-25; skipped O4–O6/canary; handoff risk list. Same-host known-good/schema proofs through 0059 are valuable. Replacement-instance image restoration is explicitly not covered. The old Lab checkout blocks `72-observe-install.sh` by design; merely executing that script again on the same checkout will not close monitoring.

Action: prove an alert reaches its intended recipient, the operator can diagnose/recover an owned test request, and backup/restore/known-good rollback work under the declared release scope. Record alert/canary state, backup freshness, on-call owner, rollback target, recovery bounds and cleanup. Plan checkout advancement with the running certification owner. Keep fresh-host recovery as an explicit service limitation until an approved rehearsal proves it; do not claim HA for one GPU/host.

### LR-04 — P1 before declaring the whole customer journey complete: production evidence is narrower than local evidence

Evidence: the App-local [E3A record](evidence/e/E3A-run-555355d.md) uses a real browser and real backing services **with an auth stand-in and controlled engine**. Its delegated backend cells are tied to prior E3C evidence. The deployed App being Ready and 17/17 local cells passing do not demonstrate real email → key → Marlin → exact wallet → owned result on production.

Action: run the bounded customer workflow in §4 after the ongoing measurement window drains. Use real hosted auth, the public edge, actual Marlin and the App's own key/result/usage interfaces. Bind results to the deployment combination, not only a repository SHA. This is E3A/I2A/I3/E4 closure, not another feature wave.

### LR-05 — P1 only before enabling the affected Lab workers: unresolved accounting risk and absent compositions

Evidence: register row 27 reports a repeatable/intermittent killed-evaluation-attempt double debit; row 26 lacks the real worker lost-ack retry test required before trace pumps. Rows 14–19 and 30 name missing listings, wiring/logins and the engine stand-in. Source confirms `RunLedger.run_rows/checkpoint_rows` deliberately raise `DependencyUnavailable` (`infrx/lab/compose.py:178`) and control smoke uses `NoEngine` (`infrx/lab/control/app.py:39`).

Action: keep those paths disabled until their specific tests pass. Make unavailable provider functions clear in the enabled navigation. A future Lab completion slice must solve role grants, the debit race, actual model smoke, compositions and one complete real workflow before enabling broad worker functionality. These are not reasons to delay a separately qualified consumer pilot.

### LR-06 — P2 verification debt: final combined proof and test isolation need closure

Evidence: register rows 11, 78–79; W6 remainder; fresh check results below. API lint passes under per-file exemptions; API typecheck succeeds at the allowed **458-error baseline**, not at zero type errors. The full final `make check` plus required real-service checks is not recorded by this review.

A newly reproduced isolation issue: `models/marlin2b/tests/test_bench.py:146` replaces `bench.load_corpus` without restoring it. Run `test_schedule_is_deterministic_and_independent_of_latency` followed by `tests/integration/backend/test_certify.py::test_e4b_a_box_rung_is_sized_to_hold_enough_short_clips_for_its_ttft_p95`: 1 pass / 1 fail. The certification file alone passes 49/49. This is a shared-process test leak, **not evidence that the running E4C process loaded a fake corpus**; the normal separate-process targets reduce its immediate impact.

Action: scope/restore test overrides and add the minimal order regression; run supported Linux checks at the final integration tip, including affected real-service compositions and recovery tests. Resolve known baseline-red/surviving recovery controls before citing those lists as proof. Do not expand the launch work into eliminating all historical lint/type debt.

### Disposition of the September 24 review

| Earlier findings | Current disposition |
|---|---|
| RV-01 public capability/retention claims | G7/F2C repair implemented; fresh production catalog now reports 82 s, supported parameters, CREDIT and nonzero serving retention. Do not repeat the old 120 s/tools/ZDR finding as current. |
| RV-02 upload reconstruction; RV-03 lifecycle; RV-05 admission/worker readiness; RV-11 persisted expiry | Corrective implementations and backend-local evidence exist. Focused current tests pass. Production drills/retention proof remain part of acceptance; this review did not rerun their real-service stack. |
| RV-06 App not wired | The original missing product implementation is repaired. Real adapters, live deployment, local browser evidence and fresh build checks exist; hosted journey acceptance remains LR-02/LR-04. |
| RV-07 upload client protocol; RV-08 replay-contaminated performance | Client/summary repairs implemented. Preserve raw fresh-generation versus replay denominators in the current E4C report; acceptance/claims remain pending. |
| RV-04 release certificate; RV-09 operations; RV-10 recovery inference | Still release-evidence work, now LR-01/LR-03. Existing tooling and partial proofs should be reused, not rebuilt. |
| RV-12 verification portability | Supported Linux/service workflow exists. This Mac cannot prove its Docker gates, and one Linux shell test needs `flock`. Record the environment honestly; use the supported runner for final proof. |

## 4. Shortest verifiable path to usable production

### Step A — Preserve and finish the current evidence window

Owner: current certification/operator session. Dependency: none; already running.

- Leave the running release/configuration, GPU and test identities undisturbed.
- Commit the completed report, raw-run references, profile/card/image/schema identities, reconciliation and cleanup state.
- Record each missing acceptance item and its closure owner. Do not convert the window's skipped operations into a green gate.
- Reuse successful cells that remain valid for the same release; rerun failures and invalidated dependencies only. Publish the separate BACKEND-READY and APP-PILOT decisions with their actual status.

Exit: a trustworthy release decision or a finite list of failed/missing cells, rather than another broad implementation program.

### Step B — Close onboarding and verification gaps in parallel, away from production

Two independently owned slices are useful while E4C runs:

1. **App auth slice (A2/I2A):** CAPTCHA + hosted configuration checklist + challenge/verification/recovery tests. Own auth forms/flow/deploy config and tests. No database migration or runtime change is inherently needed.
2. **Verification slice (E1C/E2C/E3A/I3):** restore test isolation, resolve recovery harness debt relevant to the release, inventory all required final commands and assemble the production journey evidence form. Own test/runbook paths; do not tune the running engine or change thresholds.

The operator owns any hosted config, runtime upgrade and fault window. Keep a single migration owner if a new schema change is actually necessary; applied migrations remain immutable and 0060 requires the existing reproof procedure.

Exit: tested changes ready for deployment and the precise remaining operating tests. Full Lab work and W7 cosmetic cleanup stay outside this launch closure.

### Step C — Run a small real consumer workflow

Dependency: E4C has drained; intended release combination recorded; test accounts/media and operating window identified. Use the existing authorized test resources. The following is a **proposed smoke profile**, not a new spend grant or replacement for E4C:

- Two owned test individuals, owned non-sensitive clips within the advertised limits, public App + public API; one active generation at a time.
- At most 20 fresh generations, output ≤2,048 tokens/request, 20 minutes, and a conservative maximum of 300 CREDIT total (20 × current maximum hold 14.7456 = 294.912). Every transport attempt, retry and accepted job must be counted separately. The final runner must enforce request/time/spend bounds rather than relying on this prose.
- Stop on cross-account data visibility, duplicate debit, incorrect grant, lost accepted work, reconciliation drift, unexpected external spend or exhausted bounds. Preserve failing evidence; do not clear state and relabel a retry as the original pass.

| Workflow | Required observation |
|---|---|
| Hosted onboarding | Real verification/recovery email and callback; unverified access refused; grant exactly once across callback/relogin/retry. Admin-created accounts can prove invited use, but do not count as public signup proof. |
| Key and catalog | Key created/revealed once through App; live price/limits agree with API; no secret in URL, browser storage, screenshots or logs. |
| Finite-video inference | Short owned clip through URL, inline and uploaded-handle forms; sync answer, complete SSE answer and explicit async job/result. Unsupported live-video/tools/structured-output requests refuse honestly. |
| Retry and ownership | One repeated idempotency key preserves its job/result/charge; mismatched payload refuses; second account cannot read/cancel the first account's job, upload or result. |
| Cancel/revoke | Cancel a test-owned request and inspect its terminal/accounting outcome; revoke the test key and verify new admissions stop, with documented bounded read/cancel behavior. |
| App reconciliation | Request status, token usage, current card, charge, reserved/available wallet and ledger agree exactly; refresh/relogin does not create another grant/debit. |
| Account exhaustion/error UX | Use an approved, isolated low-balance test identity for 402 and bounded capacity refusal cases. Do not deplete a real user's wallet or inject load into the measured window. |
| Follow-up retention | Revisit the owned result after the real TTL and confirm 410 plus retained metadata/charge. Use existing controlled-time local tests for fast feedback; do not shorten production retention to speed up this check. |

Evidence per case: UTC, anonymous test label, App/runtime/schema/profile/card versions, request/job IDs, expected vs observed state, exact ledger delta, result code and artifact location. Do not store bearer keys, email tokens or customer clips in the repository. An App screenshot alone is insufficient accounting evidence.

Exit: an invited user can complete the actual customer journey using the published instructions; all enabled-path P1 findings are closed or the decision explicitly limits testing to the remaining proven scope.

### Step D — Open public self-service only after its extra checks

Requires Step C plus LR-02, complete operational acceptance, verified abuse/rate limits and email delivery, honest model/retention/revocation copy, known credit-exhaustion behavior and a named operator. Enable signup as a recorded reversible change, then rerun one fresh public signup journey. Watch the first bounded cohort and compare actual job/accounting/error outcomes with the qualified profile.

No date/ETA is inferred from the task count. The four-hour soak, real email delivery, any operating windows and real retention follow-up have elapsed-time requirements that additional agents cannot remove.

## 5. What follows the first pilot

1. **Real SOP-use-case loop:** agree on a small owned robotics dataset, episode/segment IDs, time alignment, event schema, SOP rubric and human labels. Run a resumable bounded batch, inspect failures and measure event/step accuracy and useful video-hours processed. Synthetic/parity clips establish serving behavior; they do not establish SOP verification accuracy. P-07 remains the quality-claim boundary.
2. **Measured inference improvements:** profile video fetch/decode/preparation, queue, prefill and decode independently. Qualify each engine/precision/batching/cache change as a new serving version against the same workload, task quality and accounting invariants. Report latency percentiles, fresh successful video-hours/second, refusals and full cost basis; CREDIT price is not infrastructure cost.
3. **Hosting milestones:** real model qualification and engine smoke → second compatible worker and drain/recovery proof → measured scaling policy and cold-start/scale-down → broader hardware/engine matrix → matched Modal comparison. Preserve [roadmap 23](23-inference-hosting-roadmap.md); one pinned model on vLLM is already serving, while a general hosting platform is still future work.
4. **Lab completion:** choose one complete observe/evaluate/improve workflow, close the carried compositions and accounting blockers, then enable only its qualified roles. Do not dispatch all 84 carried rows as equal launch blockers.

## 6. Planning and tracker hygiene

- Keep `tasks.json` and the carried register as the existing task sources. Map LR-01–LR-06 to their named tasks/rows when dispatching; these labels are review references, not a replacement backlog.
- Reconcile the progress overlay after evidence lands: the validator currently reports 14 warnings, including stale estimates and writer-overlap entries for already-merged W6 lanes, plus 16 unapplied update files. These do not demonstrate actual concurrent writers; they do make the displayed ETA/status unreliable.
- Separate statuses in the published tracker: implemented, locally integrated, deployed, production-tested and accepted. Show consumer/public/Lab gate scope explicitly.
- Update handoff 25's operational instructions when the current window finishes: its dated addendum correctly pins E4C to installed `41693d5d`; an older instruction in §3 still says the wrapper uses `origin/main`. New sessions must not pick the wrong release from that stale sentence.
- Leave the original dirty development checkout untouched. This review was prepared in `/Users/rey/Documents/GitHub/model-inference-v1-review` on `codex/v1-launch-review-20261001` after fast-forwarding its main to the latest remote.
