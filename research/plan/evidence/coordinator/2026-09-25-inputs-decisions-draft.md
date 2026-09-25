# Draft decisions for the open pending inputs (INPUTS-DECIDE lane, 2026-09-25)

This is a draft for the coordinator to ratify. Base: `claude/consumer-v1` at `00ce68c6`. The operator delegated every open input to the coordinator. Each decision below is derived only from repository evidence, and each number cites file:line (paths are repo-relative).

Nothing was run against the box, AWS, SSM or the hosted DB. `15-pending-inputs.md` and `tasks.json` are unchanged. Labels follow METHODOLOGY:13-14: `est.` means derived, `meas.` means measured with a source.

**Open set.**
- P-01, P-02 (re-opened, 15:111), P-05, P-06 (digests, 15:113), P-17, P-18, P-19, P-22, P-24, P-25, P-26.
- P-22 is already decided (15:117-147): close it.

**Ordering the coordinator must respect.**
1. P-01 card publication, then the P-02 dry-run (exit 0 or only `in_flight`), then CREDIT activation.
2. P-06 digests, then the E4C candidate freeze.
3. P-05 second tenant, then the E4C two-tenant cells.
4. P-18 and P-24 must be ratified **before** the first qualifying E4C run (05-client-and-load-testing.md:39).
5. P-17 acceptance is mechanical after that.

---

## P-01 — CREDIT rate card

### Decision
Approve a new, immutable launch card with the **same rates as the provisional seed**.

| Field | Value |
|---|---|
| Card | `rc_marlin2b_20260925_launch` (naming from G8-6a075c5.md:118) |
| Model | `nemostation/marlin-2b` |
| Unit | `CREDIT` |
| Meter | `tokens-v1`: input and output tokens, with preprocessing tokens counted as input (records.py:438-478; research/platforms/02-credits.md:26) |
| Rates | **400.00000000 CREDIT per 1M input tokens; 1200.00000000 CREDIT per 1M output tokens** |
| Rounding | Maximum hold rounds up to 8 places (`ceiling_8`). The final debit rounds half-up once to 8 places (`half_up_8`). Binary floats are never used (records.py:458-459, 473-478; 08:77-79; 02-credits.md:34). |
| Minimum charge | None. A zero-token request costs 0 (08:79). |
| Maximum hold per request | At the pinned limits of 30,720 input and 2,048 output tokens (seed_marlin_provisional.sql:87,90): 30720×400/1e6 + 2048×1200/1e6 = **14.74560000 CREDIT** (est., arithmetic). |
| Typical charges (est., arithmetic) | 72 s clip, 14,773 prompt tokens (15:84) and 512 output tokens: **6.52360000 CREDIT**, about 1,532 such clips per 10,000 grant. 5 s clip measured in W12, 1,211 in and 96 out (2026-09-22-session-02.md:895): **0.59960000 CREDIT**. |

**Failed-execution disclosure** (App docs and API docs, owners A3/U2). This reconciles 02-credits.md:36 with the binding rulings R21 (08:187) and R106 (08:272). R21 and R106 win.

> Charges are in CREDIT, a product unit with no cash value. CREDIT has no USD exchange rate. A request is charged 400 CREDIT per million input tokens (video frames and text after preprocessing) and 1,200 CREDIT per million output tokens, rounded half-up to 8 decimal places. We place a hold for the maximum a request can cost and release the unused part.
>
> A request that completes is charged for its tokens. If you cancel a request or disconnect, it is charged only for tokens the model reports as consumed. If no usage is reported, nothing is charged.
>
> The following are never charged:
> - requests we reject or refuse;
> - requests that never ran;
> - requests that time out on our side;
> - requests that fail for platform reasons.
>
> Usage we cannot determine is released after 24 hours and never debited later.

### Why these rates
- **CREDIT is not derived from USD.** R65 (08:231) says no conversion exists. 02-credits.md:24 says "No exchange rate is implied". R117 (08:283) says no balance is converted.
- **400/1200 is the only card the pilot, fixtures and tests already exercise.** Sources: fixtures.py:111,138-140; rate_card_marlin.json:1-16; published_marlin_credit.json:78-87; seed_marlin_provisional.sql:93-112.
- **Keeping it changes no fixture, projection or published catalog shape.** Its sizing rationale (fixtures.py:114-137) gives about 998 two-minute requests per grant, and 02-credits.md:30 forbids calling the grant "N requests".
- **Measured infrastructure cost per successful unit (est.).** Price: $2.24208/h on-demand (P-19 below). Run3 figures are from E4B-box-bda1586/run3-20260924T202924Z.
  - 0.5/s rung: 9.391 video-s/s (`work/envelope-r0.5.jsonl:1`). Cost ≈ **$0.239 per successful video-hour** and ≈ **$0.0013 per successful request** (126/135 answered, report.json:280-304).
  - 14,400 s soak: 4.999 video-s/s, 3,371 answered in 14,223.12 s (`work/soak.jsonl:1`). Cost ≈ **$0.449 per video-hour** and ≈ **$0.0026 per successful request**.
  - These are est. figures, because the AWS bill is not available and the box is shared by other work. They are recorded as the cost basis; they are **not** used to compute CREDIT.

### Assumptions
- The operator's "decide yourself" delegation covers product pricing.
- Launch meters tokens only. A per-second meter needs its own contract (02-credits.md:26).
- The disclosure's R21/R106 reading is the one DUR-SETTLE enforces (`dur_settle__cancel_records_its_cause_and_settles_by_r21`, 08:272).

### Enactment (the coordinator runs this from the merged tree with `INFRX_OPERATOR_KEY` and `OPERATIONS_DATABASE_URL` on the session pooler, G8-6a075c5.md:115)
```
python -m infrx.operations.cli publish-card --model nemostation/marlin-2b \
  --card-version rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 \
  --approved-by "operator-delegated coordinator decision 2026-09-25 (inputs-decisions-draft §P-01)" \
  --effective-at <RFC3339 <= now> --idempotency-key pub-20260925-launch --reason "P-01 launch price"
```
- **The `--approved-by` text must not contain `P-01`, `provisional` or `pending`.** Otherwise `transition.unapproved` blocks activation (transition.py:51-55; R117 08:283).
- Publication moves the listing to this card atomically (15:146). The seed card and the minted `_provisional_p01` cards stay as history (R78).
- Next, run the P-02 dry-run with `--card rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200`.
- A3/U2 copy the disclosure verbatim.

### Reversibility
- Cards are immutable, so a changed price is a **new** card and listing version (seed_marlin_provisional.sql:18-22).
- Jobs keep the card they pinned (R68/R78).
- Before CREDIT activation, nothing user-visible changes. After activation, a rate change applies only to new admissions (02-credits.md:28).

---

## P-02 — USD 5.00 test grant and legacy_usd usage (re-opened)

### Decision
**No conversion. No write off. No transition runbook beyond the G8 activation.**
- The pilot org's legacy USD balance, recorded as 4.99984270 after W12 (session-02.md:895) and lower after E1B/E4B, stays on its separate labelled USD statement with `rollout_hold: true`. This is R72: "neither converted nor discarded" (08:238).
- At activation this balance is a **note, not a blocker** (transition.py:332-335). The only blockers are `card_*`, `drift` and `in_flight` (transition.py:262-300).
- USD jobs in flight drain and settle in USD (`in_flight` blocker, transition.py:276-278).
- The USD grant row is never relabelled (R117) or copied into CREDIT (02-credits.md:44).

What "no conversion" means in practice:
- The org's CREDIT wallet receives only the one-time 10,000 CREDIT individual grant (records.py:201; 0006_credit_accounting.sql:156,196), whatever its USD balance is.
- USD history stays readable through `console_legacy_usd_statement` (transition.py:108-110).
- No combined total is ever shown (R73, 08:239).

### Enactment: the read-only inventory
G8's `credit-transition --dry-run` needs no operator key. It runs one `repeatable read, read only` transaction (transition.py:160-171; cli.py:155-157,209-210). It exits 0 when activation would succeed now and 1 when there are blockers (cli.py:223-224).

- **On the box.** This needs the merged tree, because the deployed `bda1586` predates the command (G8-6a075c5.md:92). It is the verbatim form from G8-6a075c5.md:94-107, with the P-01 card:
  ```
  SRC=<merged checkout>/apps/infrx-api
  IMG=$(sudo sed -n 's/^INFRX_IMAGE=//p' /etc/marlin2b-gateway.env)
  sudo docker run --rm --network host --env-file /etc/marlin2b-gateway.env \
    -v "$SRC:/src:ro" -w /src -e PYTHONPATH=/src --entrypoint python "$IMG" \
    -m infrx.operations.cli credit-transition --dry-run \
    --card rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 > /tmp/g8-dry-run.json; echo "exit=$?"
  jq '{blockers, would_change, notes, usd: .inventory.usd, in_flight: .inventory.in_flight,
       holds: .inventory.holds, drift: .inventory.drift, flags: .inventory.flags,
       keys: [.inventory.keys[] | {prefix, audience, created_at, revoked_at, last_used_at}]}' /tmp/g8-dry-run.json
  ```
- **Alternative on the coordinator host.** `read -rs OPERATIONS_DATABASE_URL; export OPERATIONS_DATABASE_URL` (infra/rollout/README.md:61-67). Use the session-pooler URL, not `infrx_runtime`/`infrx_monitor` (cli.py:51-58). Then run `apps/infrx-api/.venv/bin/python -m infrx.operations.cli credit-transition --dry-run --card … --input-rate 400 --output-rate 1200` from `apps/infrx-api`.
- **Pass condition.**
  - `drift == []`.
  - `usd.statements` shows the grant org with `rollout_hold: true`.
  - `usd.usage_events` counts the W12/E1B/E4B jobs (G8-6a075c5.md:109-113).
  - The only blockers are `in_flight` (drain with `--freeze-only`, then `--drain-timeout-s`) or `card_*` (fix P-01).
- Record the JSON's sha256 and the `keys` block. The keys block also discharges the P-24 key inventory.
- Session 03 (:173) records that the box upload was denied by the classifier. Running on the coordinator host avoids the upload.

### Reversibility
The dry-run is read-only. Activation reverses with `credit-transition --to legacy_usd` under the same drain rules (cli.py:105-113). Balances are never converted in either direction.

---

## P-05 — signup email/callback/recovery and a second hosted test tenant

### Decision
Split P-05 into two parts.

**(a) Backend gate (E4C).** Public signup stays **closed**: `disable_signup true`, `mailer_autoconfirm false` (I1B-inventory-2026-09-22.md:34). The coordinator provisions **one second verified individual** as a logged operation. That unblocks the E4C two-tenant and fairness cells (S3 :193, :345) without any public onboarding.

**(b) Public onboarding (App phase, A2/I2A).** Stays open, with these required values:
- Site URL `https://app.callbill.ai`.
- Redirect allowlist exactly `https://app.callbill.ai/auth/callback` plus `http://localhost:3000/auth/callback` for development only. **Drop the wildcard `https://*-humanbit.vercel.app/auth/callback` in production**: auth codes must not be redirectable to any preview deployment (listed at apps/app/supabase/README.md:29-38).
- Custom SMTP from the existing SSM names `/INFRX-SUPABASE-PROD/{smtp_user,smtp_pass}` (20-platform-handoff:395-396).
- Email confirmation on.
- Captcha on the signup and recovery forms.
- Supabase auth rate limits ⚠️ TO BE VERIFIED. The repo holds no value, so read the dashboard's defaults and record them. Do not invent numbers.
- Raise the minimum password length from 6 (update-password/page.tsx:12) to at least 8.
- The missing signup page and confirmation UI (RV-06; S3 :77) stay A2/U work after BACKEND-READY.

### Why
- G6B's operated clients need no public signup (15:32).
- A tenant is a confirmed auth user. Unconfirmed users are refused (operations/service.py:229-235; 0009_operator_seams.sql:168-179).
- The only missing fact for the second tenant is `email_confirmed_at` on the second dev account (20-platform-handoff:1019). HANDOFF-20260924T1010Z.md:8 claims "all four A1-verified", which contradicts I1B:31 ("3 confirmed") and S02:885. Resolve this by the read below, not by either document.

### Enactment (second tenant; each step is a logged coordinator op, no secrets in the log)
1. **Read-only check.** `select id, email_confirmed_at from auth.users` (hosted, read-only session).
2. **If unconfirmed,** confirm this one user: Supabase dashboard → Authentication → Users → the user → Confirm, or the GoTrue admin API `PUT /auth/v1/admin/users/<id>` with `{"email_confirm": true}` using the service-role key. No repo tool does this (the only setter is the test fixture pgworld.py:67).
3. `python -m infrx.operations.cli grant --user <uuid2> --idempotency-key grant-tenant2-20260925 --reason "E4C second test tenant"` provisions the wallet and the one 10,000 CREDIT grant (cli.py:90-117; order from 20-platform-handoff:407-409). In `legacy_usd` mode it also needs a USD grant row (S02:885-892). Prefer running after CREDIT activation, so no new USD is created.
4. `python -m infrx.operations.cli issue-key --user <uuid2> --name e4c-tenant2 --secret-file <0600 new path> --idempotency-key key-tenant2-20260925 --reason "E4C second test tenant"`. It refuses if the file exists and writes with O_EXCL (cli.py:121-124,169-170).
5. Export the key under a new env var name, `INFRX_API_KEY_B`. List it in `--tenant-keys INFRX_API_KEY,INFRX_API_KEY_B` (bench.py:437-439,1767-1794). Add its prefix to the profile's `test_key_ids` and the key inventory, and set `tenants: 2` in the fairness cell's profile (runprofile.py:128-133).

### Reversibility
- `revoke-key --org <org2> --key-id <id> --idempotency-key … --reason …`.
- The grant is one-time per individual and is not reversed. It is test entitlement, not cash.
- Public signup stays off, so nothing public changes.

---

## P-06 — served-bytes digests of the processor files

### Decision
Fill `model.processor_config_digest` and `model.preprocessor_config_digest` in `models/marlin2b/serving-version.json:16-17` from **one box measurement**. Then pin both in `infra/runbooks/artifacts.py` `PINNED` (:36-37). Do this before the E4C freeze. This is WR-I8-5 (f8c70b55; I8-wiring-ed650e1.md:12,34).

### The measurement
`models/marlin2b/measure/inventory.sh` has hashed both files on the host since f8c70b55 (:69-72), in `WEIGHTS=/opt/dlami/nvme/marlin2b`. That directory is the vLLM `/model` mount (inventory-20260923T0319Z.txt:16), so the digests are served bytes. Run it verbatim (:15-17), base64-wrapped via `aws ssm send-command` as for the 2026-09-23 inventories:
```
CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=/opt/dlami/nvme/marlin2b bash inventory.sh
```
Then:
- Copy the two lines from the "served bytes (sha256)" section as `sha256:<hex>`.
- Set `processor_digests_todo` to null and cite the new SSM command id and timestamp in `served_bytes_measured`.
- Add the two names to `PINNED`.

### Expected values and the stop rule
The repository copies hash to `sha256:d89ef49c…3b1` (processor) and `sha256:27225450…516` (preprocessor) (serving-version.json:18; est., not served bytes).
- **If the served bytes differ, stop.** The served processor is not the reviewed one, and `shortest_edge` (marlin-sop.md:219) must be re-read before any freeze.
- Rollout step 80 also lists these files as `unpinned_processor_files` (artifacts.py:86,115). After pinning, step 80 refuses on a mismatch (:75-100).

### Reversibility
This is a record edit only; revert the commit. Changing serving-version.json changes its sha, so the E4C identity must be recomputed with `certify.py --hashes` (E4B-release-decision.md §2).

---

## P-17 — operator acceptance decision for E4C / BACKEND-READY

### Decision
Acceptance is mechanical. The coordinator records `decision: accepted` for BACKEND-READY **only if every item below is true**. Any false item means `rejected` or pending, with the item named. This is the delegated operator decision. The date and SHA go in `E4C` evidence under `research/plan/evidence/e/`.

1. **Gate schema** (coordinator/updates/README.md:62):
   - E4C is `implemented` in the manifest.
   - `gates.BACKEND-READY.candidate.source` and `.deployed` are recorded.
   - The cells cover **every** E4C `test_id`: BACKEND-JOURNEY, LOAD-CLOSEDLOOP, PERF-ENVELOPE, OPS-CONTINUOUS, CREDIT-CUTOVER, MARLIN-SOP (tasks.json:4501-4508).
   - Each cell is `PASS` with evidence.
   - Reuse of a historical candidate (for example `bda1586`) is explained in `candidate.note`.
2. **Freeze** (03-operations-and-verification.md §E4C step 1, :88):
   - Recorded identities: source/image/weights/processor digests (P-06 filled), capability and price snapshots, migration hashes, rollout bundle, workload manifest, and run-profile sha (P-24).
3. **CREDIT mode:**
   - The ledger half ran in CREDIT, not `legacy_usd` (03:92; S3 §2.2).
   - The active card is the P-01 card.
   - The P-02 dry-run exited 0 before activation.
4. **Predeclared limits:**
   - P-18's table below was committed **before** the first qualifying run's start timestamp.
   - Each PERF-ENVELOPE/LOAD-CLOSEDLOOP threshold row is PASS on the final combined configuration (03:89).
5. **Journey:**
   - A headless external CREDIT journey passed: two tenants (P-05), sync/SSE/async, upload/URL, cancel, replay, dataset resume (04-verification.md:135).
   - Public discovery and retention claims match behavior (03:88; R109).
6. **Reconciliation:**
   - Every accepted item/hold/terminal record reconciles exactly, `drift == []`.
   - Actual test spend is ≤ the P-24 cap (03:90).
7. **Operations:**
   - Alert delivery was proven by `74-alert-test.sh` against the P-25 destination.
   - Result/source expiry and cleanup were observed.
   - A restore and a rollback to a **known-good** target (`known-good.py` exit 0 with bundle, P-25) were executed (04-verification.md:157; R119 08:285).
8. **No open launch-path P1** security, accounting or lifecycle defect (tasks.json:4521).
   - The RV findings mapped to E4C (RV-04, RV-08, RV-09, RV-10; tasks.json:4719-4756) are `fixed` with evidence.
9. **Published limitations:**
   - The decision file lists limitations, uncovered modalities (no live video, no actuation, no clips over 82 s), the accepted envelope (P-18), rollback triggers and "single-GPU recovery is not high availability" (tasks.json robustness text).
   - No aggregate p95 is labelled as video p95 (03:91).
10. **Cutover is separate.** The public account/traffic cutover is a separately recorded authorized action (03:94). Acceptance releases App dispatch (22:33); it does not release public launch.

### Reversibility
A later FAIL on the same candidate reopens the gate by setting `decision: rejected` with the cell. Accepting releases only App task dispatch.

---

## P-18 — predeclared workload, SLO, error and recovery limits for E4C

### Decision
These are the E4C acceptance limits. They are single-GPU pilot limits, not a public SLA. They are derived from the measured run3 envelope (bda1586, box, direct gateway, `video_b64`, 82 s cap). Where a provisional E4B §5 threshold already holds, it is kept. Where it failed only against an unagreed target, it is replaced by the measured value plus stated headroom.

| Limit | E4C value | Measured basis |
|---|---|---|
| Workload | Full corpus `e1-2026-09-20` (64 clips, 4 over-cap expected refusals), `video_b64`, `max_tokens` mix 128/512/1024, seed 20260922, open loop | certify.py:81,636; manifest.json |
| Declared supported rate | **0.5 req/s** (≈ 9.4 video-s/s). 1.0/s and 2.0/s are measured but **not** supported. | report.json:258; rung 1.0 had 1 refusal within cap and rung 2.0 had 23 (:329-402) |
| Platform-caused error rate | **≤ 1 %** per cell, with 4xx for over-cap clips excluded as expected-invalid | E4B-protocol §5 (certify.py:95-105); meas. 0/126 at 0.5 and 3/3374 in the soak (report.json:280-304,431) |
| Refusals within cap at the declared rate | **0** | meas. 0 at 0.5 (report.json:280-304) |
| TTFT p95, short clips (≤30 s, ≤1280 px) | **≤ 6.0 s** (unchanged) | meas. 2.4461 s (p50 1.245), 60 samples (report.json:280-304) |
| Whole-request latency p95 at 0.5/s | **≤ 9.0 s** | meas. 7.6688 s (`work/envelope-r0.5.jsonl:1`), +17 % headroom |
| e2e p95 per clip-minute at 0.5/s | **≤ 90 s** (replaces the provisional 45, which run3 failed) | meas. 77.3142 s, 126 samples (report.json:280-304), +16 % headroom |
| Sample floors | p50 ≥ 6, p95 ≥ 60; no p99 claim below 300 | E1B-protocol.md:67-69; 05:38 |
| Burst | 32 simultaneous: only honest refusals (429 `capacity_exhausted`, with `Retry-After`); 0 5xx, 0 timeouts; every accepted request completes | meas. 8 accepted, 23×429, 1×400 (report.json:481-493). **Must run via the public edge (P4) for E4C** (E1B-protocol rule 11, :102-124; S3 finding 5) |
| Soak | **14,400 s at 0.25/s** (half the supported rate); error ≤ 1 %; host RSS growth ≤ 512 MiB; GPU memory growth ≤ 256 MiB; p50 latency drift ≤ 1.5×; ledger `reconciled_at_end` exact | E4B-protocol.md:84 (certify.py:122-125); meas. +13.2 MiB host, drift 2.795→2.880 s, **GPU growth and reconcile unknown** (report.json:443-455): these must be measured in E4C |
| Recovery: engine restart | /readyz 200 **≤ 300 s** | meas. 172 s (session-02.md:940) |
| Recovery: worker SIGKILL | **≤ 30 s** | meas. 8 s (session-02.md:941) |
| Recovery: Valkey index loss | **≤ 30 s**, no gateway 5xx other than 503 `dependency_unavailable` | meas. ~6 s (session-02.md:942) |
| Recovery: DB/object store stall | retryable 503/504 **within 45 s** | E3C-8406c79.md:249 (the s08 failure at :94 must be fixed) |
| Recovery correctness (all drills) | every accepted job reaches exactly one terminal state and settles once; post-recovery inference and settlement succeed (P7) | S3-reconciliation.md:170 |
| Availability | **no claim**; a restart window is 503 for seconds to ~3 min | E1B-protocol.md:92; session-02.md:933 |
| Quality | two-clip parity is smoke only; **no SOP accuracy claim** | 03:91; P-07 open |

### Assumptions
- The soak and envelope reuse run3's harness (E4B protocol, certify.py:1109-1122 sizing).
- Headroom of about 15-20 % over the measured p95 is a coordinator choice, labelled est.
- The limits are valid only for the pinned engine (`max_num_seqs` 8, 82 s cap, report.json:93-98). Any engine or serving change re-measures.

### Enactment
- Commit this table as the E4C predeclared criteria, for example as an amendment to `models/marlin2b/results/E4B-protocol.md` §5, with the thresholds in `tests/integration/backend/certify.py:95-105`.
- Change `e2e_p95_s_per_clip_minute` 45.0 → 90.0 and add `latency_p95_s` 9.0.
- Commit before the first qualifying run's timestamp (05:39).

### Reversibility
Tighten in a later version. Loosening after a run invalidates that run as a qualifying run.

---

## P-19 — infrastructure price row

### Decision
Fold the sourced AWS row into `research/cross-cutting/cloud-pricing.md` §3.1. That file has no g6e row (production-api/08:17-20 says so and asks for it). The row:

| Instance | GPU | vCPU | RAM | On-demand $/inst-hr (= $/GPU-hr) | 1y NU Compute SP | 1y NU Instance SP | 3y AU Instance SP |
|---|---|---|---|---|---|---|---|
| `g6e.2xlarge` (pilot box `i-0e8449a4ffca29bab`) | 1× L40S 48 GB | 8 | 64 GiB | **$2.24208** | $1.69501 | $1.41251 | $0.84302 |

- **Source.** AWS's own price sheet, us-east-1, Linux, `publicationDate` 2026-09-18T20:33:44Z, fetched 2026-09-20 (production-api/08:168-181). It uses the same sheet URL as cloud-pricing.md:34.
- **Label.** The price is a primary-source list price. Every cost figure derived from it for the pilot is **`est.`**, because the AWS bill is not available and the attribution assumes the box's full hour is charged to the measured workload.
  - The P-01 est. figures above: $0.239/video-h at 0.5/s, $0.449/video-h in the soak.
  - The E1B sketch: $0.06-0.14/video-h (notes.md:56-64).
- **Spot.** Record the measured spot price ($2.0354, 9 % off, production-api/08:194) with its caveat. Spot quota is 0 vCPU (:199), so it is not a pilot price.
- **Separation.** State in the row's note that CREDIT consumption is not USD cost (15:102).

### Enactment
- Edit cloud-pricing.md §3.1: add the row, the source line and an audit-log entry, per research convention.
- Then E1B-protocol.md:89/:133, E4B-release-decision.md:90 and marlin-sop.md:739 may cite the row. Their figures stay `est.` with the date.

### Reversibility
Doc edit; a later bill replaces it with `meas.`

---

## P-22 — alias/legacy price identity

**Already decided; close.** The decision is "resolve, then price". It is recorded at 15:117-147, ruled as R109 (08:275) and applied at the F2C-C merge:
- One USD row per revision (`pv_marlin2b_usd_2026_09_r1`).
- CREDIT is priced by the listing's card.
- `requested_model` is recorded verbatim, and nothing is rewritten.

Owners D10/G7/G8 carry the implementation (15:143-146).

The coordinator's only remaining action is optional: set `effective_to` on the W7c row `pv_marlin2b_usd_2026_09` (15:132-133). The status table should say "P-22 closed 2026-09-24 (R109)".

---

## P-24 — versioned test profile with numeric caps

### Decision
Commit the base profile below as `models/marlin2b/profiles/E4C-box.base.json`. certify stamps it per cell: run_id, dataset/seed/manifest/forms/mix, arrival and rate (certify.py:640-655).

The bounds cover the largest cell: the soak at 0.25/s × 14,400 s = 3,600 requests (certify.py:1336-1337). They also cover the 135-request rungs, the 32-burst (certify.py:1353-1355) and the 24-item dataset.

The draft passes `runprofile.schema_errors` except for the FILL identity fields, which is expected (checked in this lane).

```json
{
 "schema": "infrx.run-profile/1",
 "identity": {
  "run_id": "e4c-box-1",
  "source_sha": "FILL 40-hex: frozen candidate",
  "deployed_sha": "FILL 40-hex: gateway infrx_build_info",
  "image_digest": "FILL sha256: infrx-runtime:<release>",
  "weights_sha256": "FILL sha256: shard-set digest (serving-version.json:8-11)",
  "tokenizer_sha256": "sha256:06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523",
  "processor_sha256": "FILL sha256: P-06 served bytes",
  "template_sha256": "sha256:273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
  "model_revision": "nemostation/marlin-2b@2026-09-01",
  "preparation_profile": "v1",
  "engine_options": {"max_num_seqs": 8, "max_video_seconds": 82},
  "migration_version": "FILL: newest applied (0025 per I8-20260925T0002Z.json:27)",
  "config_version": "FILL: sha256 of /etc/marlin2b-gateway.env"
 },
 "target": {
  "path": "direct-gateway",
  "allowlist": ["127.0.0.1:8001"],
  "environment_owner": "coordinator (pilot box i-0e8449a4ffca29bab)",
  "allowed_fault_targets": ["marlin2b-vllm", "FILL worker unit", "FILL valkey unit"],
  "tenant_key_env": ["INFRX_API_KEY"],
  "test_key_ids": ["142c7d81"],
  "customer_traffic": false,
  "maintenance_window": "FILL: coordinator-logged window id"
 },
 "bounds": {
  "max_duration_s": 14700,
  "max_requests": 3600,
  "max_input_bytes": 14000000000,
  "max_input_tokens_per_request": 30720,
  "max_output_tokens_per_request": 1024,
  "max_output_tokens": 3686400,
  "max_concurrency": 64,
  "max_drain_s": 600,
  "spend": {"currency": "USD", "max_usd": 12.5, "outstanding_holds_usd": 0.0,
   "rates": {"input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.30,
    "source": "pv_marlin2b_usd_2026_09_r1 (P-22, 15-pending-inputs.md:133-137)", "as_of": "2026-09-24"}},
  "stop_thresholds": {"platform_error_rate": 0.01, "host_growth_mib": 512, "gpu_growth_mib": 256}
 },
 "workload": {
  "manifest_sha256": "386a2d89c26c1bbff4df03004239b3153651901ffcda76f36c3ce5fc23095e18",
  "data_source": "E1 licensed corpus (CC-BY / public domain), models/marlin2b/corpus/manifest.json e1-2026-09-20",
  "item_ids": "FILL: jq '[.clips[].id]' models/marlin2b/corpus/manifest.json (64 ids c000-…c063-…)",
  "dataset_version": "e4c-1",
  "seed": 20260922,
  "forms": ["video_b64"],
  "max_tokens_mix": [128, 512, 1024],
  "max_clip_duration_s": 82,
  "transport": "sse",
  "tenants": 1,
  "expected_invalid": ["c012-bbb1080p30-1024x768-4x3", "c025-tos720p-2560x1080-ultrawide-rot180",
                       "c038-sintel1080p-480x854-portrait", "c051-tos720p-360p-16x9"]
 },
 "measurement": {
  "profile_class": "P1",
  "client": {"location": "pilot box, same host as the gateway", "resources": "one process", "clock": "time.perf_counter, one process"},
  "arrival": "open-loop", "rate_per_s": 0.5, "concurrency": null,
  "warmup_excluded": true, "cache_regime": "unknown", "max_driver_lag_s": 1.0,
  "min_samples": 60, "repeats": 1,
  "thresholds": {"ttft_s": 6.0, "e2e_s": null, "max_error_rate": 0.01},
  "fault_schedule": [], "telemetry": [],
  "price": {"usd_per_hour": 2.24208, "instances": 1,
   "source": "AWS on-demand price sheet us-east-1 Linux g6e.2xlarge (research/production-api/08-cost-model-and-unit-economics.md:181; P-19)", "as_of": "2026-09-20"}
 },
 "cleanup": {
  "policy": "drain-then-cancel",
  "owned_prefix": "infrx-e4c-",
  "restore": "no target state changes except test-tenant jobs/ledger rows; uploads and results expire with their TTLs",
  "max_cleanup_s": 900,
  "evidence_destination": "models/marlin2b/results/E4C-box-<release7>/"
 }
}
```

### How the numbers were set (all est., arithmetic)
- **`max_input_tokens_per_request` 30,720** is the deployment's pinned ceiling (seed_marlin_provisional.sql:87), so the spend projection is a true upper bound.
- **Spend projection.** Per request: (30720×0.10 + 1024×0.30)/1e6 = $0.0033792. Soak projection = $12.165, so `max_usd` is 12.5 per cell.
- **Expected actual USD spend** is ≈ $1.8 for the soak (≈ 4,330 prompt tokens per request, from the 21.1 s mean clip at 14,773 tokens/72 s).
- **`max_input_bytes`.** 3,600 × 3,220,403 B (mean over 64 clips; 206,105,764 B total) ≈ 11.6 GB, so the cap is 14 GB. If validation reports a larger scheduled sum, raise it to the printed `derived.media_bytes` and record why.
- **`max_output_tokens`** = 3,600 × 1,024.
- **`max_duration_s`** = 14,400 + 300 s driver slack. `max_drain_s` 600.
- **`expected_invalid`** is the four 112 s clips (manifest.json:887, 1550, 2207, 2857).

### Key-inventory file
Record the shape. It holds id prefixes only (runprofile.py:99-109) and is built from the P-02 dry-run's `keys` block:
```json
{"active_key_id_prefixes": ["142c7d81"], "taken_at": "<RFC3339 of the dry-run>", "source": "G8 credit-transition --dry-run sha256:<json sha>"}
```
Every active consumer prefix must appear in `test_key_ids`, or the run is blocked. The two pre-cutover consumer keys (S3 :24, :61) therefore must either be revoked (`revoke-key`, logged) or be proven revoked by the dry-run before the run.

### Required amendment for a CREDIT run (gap found)
The schema fixes `bounds.spend.currency` to `"USD"` and names the rates `*_usd_per_mtok` (run-profile.v1.schema.json:80-90; runprofile.py:214-228). E4C must run in CREDIT (03:92), so a CREDIT cap cannot be expressed today. That is a new blocker for E4C, owned by E1C/E4C.
- **Minimal change.** Add `"currency": {"enum": ["USD", "CREDIT"]}`, rename the rate and cap keys to `input_per_mtok`/`output_per_mtok`/`max_spend`/`outstanding_holds` (unit carried by `currency`), and keep the same projection. That is the shortest schema diff; do not convert between units.
- **CREDIT values for the same base.** Per request 30720×400/1e6 + 1024×1200/1e6 = 13.5168 CREDIT. Soak projection = 48,660.48 CREDIT, so the cap is **50,000 CREDIT per cell**. Expected actual ≈ 4,330×400/1e6 + ≤ 0.67 ≈ ≤ 2.4 CREDIT per request, so ≤ ≈ 8,100 CREDIT for the soak and ≤ ≈ 9,200 CREDIT per full certify run.
- **Funding consequence.** The expected actual spend is close to the 10,000 CREDIT grant.
  - The certify tenant needs an audited `adjust --user <uuid> --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation"` (cli.py:90-117), recorded as test funding.
  - This is an operator adjustment, not a second grant or a refill product (15:30).
  - ⚠️ The coordinator must ratify this. The alternative is a per-cell rerun after exhaustion, which would contaminate the soak.

### Reversibility
The profile is a versioned file; a new version is used for a new run. The adjust is reversible by a negative `adjust` with its own idempotency key.

---

## P-25 — operations and retention ownership

### Decision
Owner: the coordinator (I8 wiring, D10/M6 code).

**1. TTLs.** Approve the existing published values; none is extended. Set the unset ones.

| Knob | Value | Source / change |
|---|---|---|
| Result TTL | 86,400 s | limits.py:102; published serving_profile_marlin.json:74-77 (unchanged) |
| Stream journal chunk TTL | 3,600 s | limits.py:99 (unchanged) |
| Processing cache TTL = source retention | 604,800 s (7 d) | limits.py:103; lifecycle.py:36-41 (`RETENTION_S`, "fixture value" → now approved, unchanged) |
| Idempotency TTL | 86,400 s | limits.py:106 (unchanged) |
| Claim TTL | 300 s (≥ 300 per M6, > 75 s delete timeout) | lifecycle.py:39-40; retention.py:54-56 (unchanged) |
| Retention grace | **3,600 s** (was 604,800 by default) | lifecycle.py:38. A 7-day grace on top of a 7-day window can hold unadmitted sources up to 14 days, which no published figure states. The composition root passes `grace_s=3600` (lifecycle.py:98). M6 fixtures used 600 (M6-phase2:235). |
| Upload window | 604,800 s (unchanged, = published cache TTL) | lifecycle.py:37 |
| Cache high water `PROCESSING_CACHE_MAX_BYTES` | **53,687,091,200 (50 GiB)**, low water 0.8 (prepare.py:117) | New setting (M6 wiring 1, M6-phase2:165-184). The g6e.2xlarge has 450 GB local NVMe (production-api/08:181), and weights/images share it; ⚠️ confirm free space with `df -B1 /opt/dlami/nvme` in the next box inventory. |
| Retention collector interval | **300 s** | matches the alert `RetentionPendingDeleteOld` (900 = 300 + 2×300, infra/alerts/operations.json:298-307) |
| Cache sweep interval | **300 s** | M6 wiring 1 |

- **Required change.** Apply M6 wiring request 1: one collector in the worker, `content=lifecycle`, `max_bytes`, and two intervals. Until it is applied, sources are never collected (M6-phase2:180-182). This means the public retention figure is untrue until then, and E4C step 3 (03:90) cannot pass.
- **Setting names.** Add `processing_cache_max_bytes`, `retention_interval_s`, `cache_sweep_interval_s` and `retention_grace_s` as `PilotSettings` fields. They then become env vars via `env_name` (limits.py:169-170).

**2. Alert destination.**
- **Topic:** SNS `infrx-pilot-alerts`, account 641134885443, us-east-1, ARN `arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts`.
- **Subscription:** one email subscription to the operator's address, confirmed by the operator.
- **Wiring gap.** `infra/observe/deliver.py` only POSTs `{"text": …}` to an https `ALERT_WEBHOOK_URL` (:8-11, 58-70). SNS does not accept an unsigned webhook POST. Choose one:
  - **(a) Minimal.** Add an `ALERT_SNS_TOPIC_ARN` branch to `deliver.post()` calling `aws sns publish --topic-arn … --subject … --message …`. Grant the box's instance role `sns:Publish` on that ARN only.
  - **(b) No code.** Use a Slack incoming webhook URL in SSM (`ALERT_WEBHOOK_PARAM`, 72-observe-install.sh:12-13).
  - Recommendation: (a), because the operator asked for email.
- Set `ALERT_OWNER=coordinator` and `ALERT_ESCALATION=operator email via SNS` (deliver.py:97-98).
- **Proof.** `infra/rollout/steps/74-alert-test.sh` must exit 0 (test + resolve) and the email must arrive. `undelivered.jsonl` (observe.md:123-127) must be empty afterwards.
- Do not create the topic in this lane.

**3. Hosted backup and PITR verification (read-only).**
1. `apps/infrx-api/.venv/bin/python infra/runbooks/supabase_policy.py` with `SUPABASE_ACCESS_TOKEN` (restore.md:264-284). Record the plan tier, whether PITR is enabled, and the daily backup list with its newest timestamp. The equivalent Management API read is `GET /v1/projects/fcbnscgsymzdykendbrc/database/backups`.
2. If PITR is off (⚠️ today, runbooks/README.md:10), the policy is:
   - A **logical dump** with `pgrestore.py` (restore.md:30-40) before every migration/rollout and at least daily during E4C.
   - The same `"equal": true` restore check as `hosted-20260924T050746Z` (session-02.md:850).
   - Retain the 7 newest dumps.
   - Enabling PITR is a separate paid change the operator must authorize; this draft does not decide it.
3. Record RPO = time since the newest verified dump (a logical dump gives no PITR). Record the RTO measured from the last restore check.

**4. Known-good rollback bundle.**
- **Criterion.** A target is known-good only if `infra/rollout/known-good.py <sha> --applied <newest applied migration> --bundles s3://llm-bootcamp-641134885443/releases/` exits 0 on all six checks: commit, preparation, migrations or `schema_proof`, config, record, bundle (known-good.py:2-25). `85-known-good-box.sh` must also pass on the box (:1-38).
- **Today.** `bda15866` is NOT-KNOWN-GOOD against applied 0025 because it has no `schema_proof` (I8-20260925T0002Z.json:27-28), and `27af05a` is false.
- **Action.** Add a `schema_proof {through: 0025, evidence}` for `bda15866` to `known-good.json`, or designate the E4C candidate once its own bundle is uploaded.
- **Scope.** Durable image store (ECR copy by digest) and a fresh-instance restore rehearsal are required for a replacement-instance claim (15:151; est. 2-4 h, I8-103d20a.md:288-291). Without them, E4C states "rollback proven same-host only".

### Reversibility
- TTLs are configuration; shortening the grace only deletes sooner what was never published as retained.
- SNS and the dump cadence are additive.
- Nothing here extends retention.

---

## P-26 — revocation during an identity-source outage

### Decision
**(a) Keep bounded staleness. Do not fail closed.** State the bound publicly. The U2/A3 copy is:

> "Revoking a key stops new requests immediately. Reads and cancels by that key stop within 60 seconds while our account service is reachable. During an account-service outage, a revoked key may continue to read or cancel its own existing jobs until the service recovers. It can never start new work."

### Reasoning
- **New admission is unaffected by the cache.** Admission re-reads the key in SQL (`k.revoked_at is null`, 0011_admission.sql:272; R131 08:297). A revoked key can never start or be charged for new work.
- **The exposure is small.** Stale serving (keys.py:49-60) exposes only the key holder's **own** existing jobs, read and cancel. There is no cross-tenant data and no spend.
- **Revoking needs the same source.** Revocation itself is a write to Supabase (operations.py:121-126). A revocation "during the outage" is only possible in a partial outage, where PostgREST is unreachable from the gateway but the DB is writable elsewhere.
- **Fail closed costs a lot.** It would 503 **every** tenant's reads and cancels for the whole outage (G7-2bbfe0e.md:109; G7-a0e0928.md:117-120). That also breaks the SOP client's resume and poll path.
- **Keys never seen already fail closed.** Unseen keys during an outage already get 503 (keys.py:58-60).

### If the coordinator chooses (b) fail-closed instead
The exact G7 change is in `apps/infrx-api/infrx/auth/keys.py:57-60`. Replace
```python
            except Exception as e:
                if hit is None:  # never seen this key and Supabase is down: fail closed, but retryable
```
with
```python
            except Exception as e:
                if hit is None or hit[0] < now():  # fail closed once the cached entry has expired (P-26 b)
```
- Expected result: every key 503s once its `KEY_TTL` (60 s, config.py:59) lapses during an outage.
- Add a test in `tests/g/jobs/test_revocation.py`: with PostgREST failing, a cached key returns 503 at KEY_TTL+1.
- Keep mutant `revocation_outlives_key_ttl`.
- Change the copy to "all API access is unavailable during an account-service outage".

### Reversibility
(a) is copy only. (b) is a two-line revertible change.

---

## Summary of new blockers found while drafting
1. **The run-profile cannot express a CREDIT spend cap.** The schema fixes the currency to `"USD"` (P-24). E4C in CREDIT needs the schema amendment.
2. **The CREDIT test spend is close to the 10,000 grant.** The certify tenant needs an audited `adjust`, which must be ratified (P-24).
3. **Source retention is not enforced.** No collector or cache keeper is wired, so the published source retention is untrue until M6 wiring 1 is applied (P-25).
4. **The alert sink cannot deliver to SNS.** The sink is webhook-only (P-25). SNS needs a small `deliver.py` branch, or a webhook is used instead.
5. **Approval text must avoid the blocker markers.** P-01 approval text must avoid "P-01", "provisional" and "pending" (transition.py:51-55).
6. **The second tenant needs a manual confirmation.** Its email confirmation is a dashboard or admin-API step with no repo tool (P-05). HANDOFF-20260924T1010Z.md:8 contradicts I1B:31 about how many users are verified.

## Audit log
- 2026-09-25: Drafted by the INPUTS-DECIDE lane from repository evidence at `00ce68c6` (scratch clone).
  - No box, AWS, SSM or hosted-DB access.
  - The E4C base profile was schema-checked with `runprofile.schema_errors`: only the FILL identity fields fail, as expected.
  - Cost figures are est. (arithmetic on meas. run3 figures × the sourced list price).
