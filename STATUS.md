# Current project state

**Evidence cutoff: 2026-10-01, 18:46 UTC. Reviewed implementation: `252f3ea8`.** This page consolidates the latest implementation record and independent review. Repository changes after this cutoff must be reconciled before operating production. A past report of a running process is not a fresh liveness check.

## Launch decision

**Consumer App and Marlin API: deployed, production acceptance pending. Public signup: closed. Provider Lab: deployed for a limited internal checklist, with incomplete backend workflows.** No public-launch, HA, SOP accuracy or general custom-model-hosting claim is established.

The immediate objective is an invited, bounded consumer production pilot, followed by public self-service after onboarding and operational checks pass. The [launch review](research/plan/26-launch-readiness-review-2026-10-01.md) defines the remaining work and proposed production workflows. Completing the Lab or an autoscaler is not a dependency of that pilot.

## Deployment inventory

| Component | Last known state | Evidence and limit |
|---|---|---|
| Consumer API | `https://marlin2b.callbill.ai`; runtime `41693d5d`, one L40S, CREDIT mode | Runtime identity from operator record; independent `/health` and `/v1/models` probes returned 200 at 18:43 UTC. No authenticated inference was submitted by the review. |
| Consumer App | `https://app.callbill.ai`; deployed source `252f3ea8`, built 18:24:01Z | Fresh public `/api/version` response. Public signup remains closed according to the latest operator record; a rendered signup page does not prove signup works. |
| Hosted database | Supabase, applied migrations 0001–0059 | Operator record; not independently queried in the review. Next additive migration requires the existing R151 migration/reproof workflow. |
| Lab web | `https://lab.callbill.ai`; deployed from the W6/main work | Root served a sign-in form at 18:46 UTC. Exact deployed web SHA was not independently verified. |
| Lab control | `https://lab-control.callbill.ai`; checkout `7ecbab0e`, image `sha256:870aa2ea…` | Operator record; unauthenticated release read freshly returned 401 as expected. Other Lab worker roles remain installed but inert. |
| Membership | `infrx-internal`, one developer tester provisioned | Operator's 18:23 UTC addendum; the internal checklist still needs its recorded outcome. |

The App's source outside README/migrations is unchanged between `41693d5d` and `252f3ea8`. Main includes newer backend refactors; the commit difference alone does not require interrupting the current runtime to upgrade it.

## Serving and product scope

- Model: `nemostation/marlin-2b`, pinned weights/processor/template and vLLM image, BF16. Finite text/video input → text output. Sync responses, streamed text and explicit async jobs are implemented.
- Published video ceiling: one clip, 82 seconds, 64 MiB; 2 fps, up to 240 frames and 200,704 pixels/frame. Request body ≤96 MiB; context 32,768, input 30,720 and output 2,048 tokens. These bounds do not guarantee every source at the boundary fits or establish task accuracy.
- Approved card: `rc_marlin2b_20260925_launch`, 400 CREDIT/M input tokens and 1,200 CREDIT/M output tokens. One-time 10,000 CREDIT per verified individual; no monthly refill, payments or implicit USD conversion.
- App implementation includes verification/recovery, keys, live catalog/docs, credit/usage/request views, owned results and audited operator controls. Hosted end-to-end acceptance remains unfinished.
- Result TTL 24 h; stream journal 1 h; idempotency 24 h; prepared-media cache up to 7 days. Serving retains content even when optional trace capture is off. No zero-retention or physical-deletion deadline claim is published by the current model document.
- No native live-video input, robot actuation, tool calling or structured-output guarantee. Large-dataset SOP work still needs representative data, labels and an agreed rubric.

## Certification and next work

The operator last reported E4C running at **18:23 UTC**: `LOGDIR=~/infrx-e4c/20261001T175557Z`, run `20261001T181109Z`, installed `RELEASE=41693d5d`. Preconditions/config/build checks were green. Two outage drills, O4–O6 and the canary were skipped by decision. **P-17 checks 1, 5 and 7 remain false in that record.** No completed result or BACKEND-READY/APP-PILOT acceptance is present at this cutoff.

Before another production action, inspect the latest [operational log](research/plan/consumer-v1/09-path-to-internal-testing.md) and the current operator window. Do not infer that a long-running process is finished or replay an obsolete wrapper instruction using `origin/main` in place of the installed release.

| Priority / scope | Remaining work | Existing owner / reference |
|---|---|---|
| Consumer acceptance | Ingest E4C results; close missing/failing cells, exact reconciliation and operating proofs; record separate backend and App decisions | E4C/I3/E4; carried row 3; review LR-01/LR-03 |
| Public onboarding | Implement CAPTCHA token handling for signup/recovery; verify SMTP, confirmation/recovery delivery, allowlist, auth rate limits and exactly one grant | A2/I2A; P-05; review LR-02 |
| Consumer production workflow | Two users through real auth, App-created keys, Marlin sync/SSE/async, uploads/results, retry/isolation, usage/balance and revocation | E3A/I2A/I3/E4; review LR-04 and §4 |
| Verification | Final combined supported-host checks; recovery test/mutant gaps; benchmark test override restoration | Register rows 11, 79, 86; review LR-06 (row 78 documentation regression is closed) |
| Lab enablement only | Missing evaluation/pipeline listings, trace composition, dedicated role logins and real engine smoke | Register rows 14–19, 30; review LR-05 |
| Lab accounting only | Root-cause killed-evaluation-attempt double debit before enabling eval worker; real worker lost-ack test before enabling trace pumps | Register rows 26–27; affected workers remain off |

Alert delivery/canary evidence is incomplete. Known-good schema proofs through 0059 and same-host recovery work exist; replacement-host restoration remains uncovered. The older Lab checkout blocks the newer observe installer by design. Use the actual runbooks and an owned window to resolve these, not repeated blind restarts.

## What verification establishes

Independent review at `252f3ea8`: 432 focused backend tests passed (54 skipped), 591 App tests passed (59 skipped), App build and 22 build checks passed, 262 Lab tests passed (14 skipped). App/Lab lint and typing passed; API lint passed with existing exemptions; API typing met its **458-error baseline**, not zero errors. Certification-rule tests passed 49/49 in isolation.

Combined benchmark tests exposed a shared-process corpus-loader test leak; a separate test needs Linux `flock`. Docker was unavailable on the review Mac, so real-service integration, GPU and production load/fault tests were not rerun. [Commands and evidence](research/plan/evidence/coordinator/2026-10-01-launch-review/verification.md).

All 16 W6 cleanup lanes are merged according to the implementation record. The task manifest has 109 implemented, 5 integrated, 13 planned and 6 superseded records; these counts do not measure production readiness. The progress overlay is reconciled at revision 395: merged W6 lanes are complete, stale estimates are cleared, and 16 malformed historical updates are explicitly rejected with their original files retained. Tracker validation has zero warnings. E4C is awaiting its recorded outcome; ETA remains unknown. Use evidence and gate scope, not task counts, to decide launch status.

## Source of truth and maintenance

1. This page: dated current state and unresolved release decisions.
2. [Launch review](research/plan/26-launch-readiness-review-2026-10-01.md): findings and closure sequence.
3. [Task manifest](research/plan/tasks.json), [carried register](research/plan/consumer-v1/10-carried-work-register.md), [implementation index](research/plan/README.md): task routing and requirements.
4. [Product architecture](research/platforms/README.md), contracts and [verification gates](research/plan/04-verification.md): intended behavior and acceptance.
5. [Session record](research/plan/evidence/coordinator/2026-09-24-session-03.md), [operational log](research/plan/consumer-v1/09-path-to-internal-testing.md), immutable reports and raw evidence: what was actually observed.

Update this page when new evidence changes state. Include UTC, component/release, environment and evidence location. Do not mark implemented, deployed, tested and accepted as interchangeable; do not copy a dated inventory into an undated claim. Removed handoffs remain retrievable from [Git history](research/plan/DOCUMENTATION.md).
