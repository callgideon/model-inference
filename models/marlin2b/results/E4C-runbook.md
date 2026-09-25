# E4C window runbook: the certificate run for BACKEND-READY

This runbook orders the E4C certificate run. It runs inside a rollout window
([infra/runbooks/rollout.md](../../../infra/runbooks/rollout.md) §2, W1–W13, and
[infra/rollout/README.md](../../../infra/rollout/README.md) §1), after the candidate is installed and
W12's smoke has passed. It measures against the predeclared criteria and sets none of them.

- **Criteria.** [E4B-protocol.md](E4B-protocol.md) §5 as amended on 2026-09-25 (amendment 6, P-18), and
  `tests/integration/backend/certify.py` `CRITERIA`/`MATRIX` (ruling R133).
- **Acceptance.** The P-17 ten-check list in
  [consumer-v1/03 §E4C](../../../research/plan/consumer-v1/03-operations-and-verification.md).
- **Inputs.** [15-pending-inputs.md](../../../research/plan/15-pending-inputs.md) "Decisions 2026-09-25": P-01, P-02,
  P-05, P-06, P-17, P-18, P-24 and P-25.

**Marked steps.** A step marked **[operator-held]** is held by the operator. A step marked
**[coordinator]** is run by the coordinator. The lane that wrote this runbook ran none of them.

## Rules (carried verbatim; each one voids the run when broken)

1. The box shell exports only `INFRX_API_KEY`. bench prefers `MARLIN_API_KEY` when both are set, and the cell
   is then refused.
2. Never run bench or dataset on the box without `--profile` (E4P-V9). An unprofiled run treats
   `127.0.0.1:8001` as a free target.
3. The declared supported rate is **0.5 req/s**. The higher rungs (1.0 and 2.0) are measurement-only
   (`measured_passing_rate_per_s`) and never supported.
4. Loosening a criterion after a qualifying run's start disqualifies the run (R133).
5. The overload burst goes through the public edge, `https://marlin2b.callbill.ai/v1`, as a P4 cell. It never
   goes through `127.0.0.1:8001`.
6. CREDIT and USD are exact and separate, and are never converted. Every amount below is CREDIT.
7. Never print a secret. Name SSM parameters by name only. Keys reach processes only through 0600 env or header
   files.

## 0. Before the window

| # | Who | Step | Record |
|---|---|---|---|
| 0.1 | coordinator | Check that P-18 was committed before the run. `git log -1 --format='%H %cI' -S'"latency_p95_s": 9.0' -- tests/integration/backend/certify.py` must show a commit timestamp earlier than step 5's start. Confirm that the E4B-protocol amendment 6 is in the release tree. | SHA and timestamp |
| 0.2 | coordinator | **P-06.** Run `CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=/opt/dlami/nvme/marlin2b bash models/marlin2b/measure/inventory.sh` through SSM. Fill `processor_config_digest` and `preprocessor_config_digest` in `models/marlin2b/serving-version.json`, then pin both in `infra/runbooks/artifacts.py` `PINNED`. **Stop** if they differ from the repository copies (`d89ef49c…3b1` and `27225450…516`). | SSM command id, both digests |
| 0.3 | coordinator | **P-01.** Publish the launch card: `python -m infrx.operations.cli publish-card --model nemostation/marlin-2b --card-version rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 --approved-by "Launch price approved by the coordinator under the operator's authorization of 2026-09-25" --effective-at <RFC3339 ≤ now> --idempotency-key pub-20260925-launch --reason "launch price"`. Export `OPERATIONS_DATABASE_URL` first with `read -rs`. | card id, output |
| 0.4 | coordinator | **P-02 dry-run.** `credit-transition --dry-run --card rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200` must exit 0 with `drift == []`. Then activate CREDIT (G8). The ledger half must run in CREDIT, not in `legacy_usd`. | JSON sha256, `keys` block (prefixes only), exit code |
| 0.5 | **[operator-held]** | **Revoke the pre-cutover keys.** The two pre-cutover consumer keys must be revoked (`revoke-key`, logged), or the 0.4 `keys` block must show `revoked_at` on both. Otherwise the run is refused: any active prefix outside `test_key_ids` is an error. | prefixes, `revoked_at` |
| 0.6 | **[operator-held]** | **P-05 second tenant.** Read `auth.users.email_confirmed_at`, and confirm the second user if needed. The coordinator then runs `grant --user <uuid2> --idempotency-key grant-tenant2-20260925 --reason "E4C second test tenant"` and `issue-key --user <uuid2> --name e4c-tenant2 --secret-file <0600 new path> --idempotency-key key-tenant2-20260925 --reason "E4C second test tenant"`. | tenant-2 key **prefix** only |
| 0.7 | **[operator-held]** | **P-24 funding.** Run `python -m infrx.operations.cli adjust --user <certify tenant> --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation"`. This is test funding, not a second grant. The tenant then holds 10,000 + 40,000 = 50,000 CREDIT, which is the per-cell cap. | adjustment id, balance after |
| 0.8 | coordinator | Build the key inventory from the 0.4 `keys` block (still active after 0.5 and 0.6): `{"active_key_id_prefixes": ["142c7d81", "<tenant-2 prefix>"], "taken_at": "<RFC3339 of the dry-run>", "source": "G8 credit-transition --dry-run sha256:<json sha>"}`. Write it as `/opt/dlami/nvme/e4b/e4c/keys.json` on the box and `~/e4c/keys.json` on the host. It holds prefixes only. | file sha256 |

## 1. The window (inside rollout.md §2)

1. **[coordinator]** Run rollout.md W1–W13 for the candidate `RELEASE`. Hold the deployment lock. The window id
   that is logged is the `maintenance_window` value in step 3.
2. W12's `verify-external.sh` must print `failures: 0`.
3. The **edge must be open**, not in maintenance, for the P4 burst and the journey. The window's safety comes from
   the key inventory (only test keys are active), not from the edge.
4. From the box, `curl -s -o /dev/null -w '%{http_code}' https://marlin2b.callbill.ai/health` must print `200`.
   The burst leaves the box and comes back in through Caddy.
5. **[coordinator]** Run the observe and ops steps O3–O6 from rollout.md §4. Step O6 (`74-alert-test.sh`) is
   **[operator-held]**: the operator confirms the nonce at the P-25 destination.

## 2. Freeze the candidate (P-06, P-17 check 2)

Every value is read from the served build, never typed from memory. Record them all in `freeze.json` (§6).

| Identity | Source |
|---|---|
| `source_sha` | `git -C /opt/dlami/nvme/w3-checkout rev-parse HEAD` must equal `RELEASE` (the 40-hex SHA) and must be clean (`git status --porcelain` empty) |
| `deployed_sha` | `curl -s 127.0.0.1:8001/metrics \| grep '^infrx_build_info'`, label `revision`. The worker's `127.0.0.1:8002/metrics` must agree (certify's `e4b.b.served-build` checks both) |
| `image_digest` | `docker image inspect --format '{{.Id}}' infrx-runtime:$RELEASE`. It must equal `docker inspect --format '{{.Image}}' infrx-gateway` and `infrx_build_info{image}` |
| `weights_sha256` | "shard-set digest": `sha256:` plus the sha256 of the compact JSON list `model.weight_shard_digests` in `serving-version.json`, in file order: `python3 -c 'import json,hashlib;d=json.load(open("models/marlin2b/serving-version.json"))["model"]["weight_shard_digests"];print("sha256:"+hashlib.sha256(json.dumps(d,separators=(",",":")).encode()).hexdigest())'`. Each shard is served-bytes verified by `inventory.sh` (0.2) |
| `processor_sha256` | `serving-version.json` `model.processor_config_digest`, filled by 0.2 (P-06, served bytes) |
| `tokenizer_sha256`, `template_sha256` | Already in the base. Check them against `serving-version.json` `tokenizer_digest` and `chat_template_digest` |
| `migration_version` | The newest applied version on the `applied:` line of W7's `migrate.py plan`. It must be 0025 or newer |
| `config_version` | `sha256:` plus `sudo sha256sum /etc/marlin2b-gateway.env`. Hash only; the file itself is never printed |
| `allowed_fault_targets[1..2]` (box base only) | The worker and Valkey unit names installed by W10: `infrx-worker` and `infrx-valkey` (`apps/infrx-api/deploy/*.service`). Check with `systemctl list-units 'infrx-*'` |
| `maintenance_window` | The window id logged in the lock record (step 1) |
| `test_key_ids[1]` (two-tenant only) | The tenant-2 prefix from 0.6 |
| certify hashes | `python tests/integration/backend/certify.py --hashes` from the checkout (serving version, engine options, migrations, deploy and rollout trees, alerts, lock) |
| rollout bundle | `release-bundle.sh` `SHA256SUMS` from W1 |
| workload manifest | `models/marlin2b/corpus/manifest.json` sha256 `386a2d89…095e18` (`e1-2026-09-20`) |

## 3. Fill every FILL and validate (no request leaves)

Copy the committed bases to `/opt/dlami/nvme/e4b/e4c/`, and to `~/e4c/` for the journey:

- `E4C-box.json` from `E4C-box.base.json`
- `E4C-edge.json` from `E4C-edge.overload.base.json`
- `E4C-two-tenant.json` from `E4C-box.two-tenant.base.json`

Replace every `FILL` from §2, and change nothing else. **Changing a bound or a rate is a new profile version,
not a fill** (R133). Record the sha256 of each committed base and of each filled copy. The committed base
`E4C-box.base.json` is `c0d4aa1b…d2ff`.

| Profile | FILL fields |
|---|---|
| box | `source_sha`, `deployed_sha`, `image_digest`, `weights_sha256`, `processor_sha256`, `migration_version`, `config_version`, `allowed_fault_targets[1]`, `allowed_fault_targets[2]`, `maintenance_window` |
| edge | The same identity fields and `maintenance_window`. It has no fault targets |
| two-tenant | The same identity fields, `maintenance_window` and `test_key_ids[1]` |

Then validate every filled profile with bench `--validate-only`. Each must exit 0 with `runnable: true` and no
errors, blocks or warnings, and none of them may create `--out`. Run all three with `INFRX_API_KEY` unset or
set; the key values are never read here.

```bash
M=models/marlin2b; C="--corpus $M/corpus/manifest.json --subset full --target gateway --model nemostation/marlin-2b --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --key-inventory /opt/dlami/nvme/e4b/e4c/keys.json"
python $M/bench.py $C --base-url http://127.0.0.1:8001/v1 --rate 0.25 --requests 3600 --dataset-version e4c-1 --profile /opt/dlami/nvme/e4b/e4c/E4C-box.json --validate-only        # soak shape; projected 48660.4800
python $M/bench.py $C --base-url https://marlin2b.callbill.ai/v1 --rate 1000.0 --requests 32 --burst 32 --dataset-version e4c-1-overload --profile /opt/dlami/nvme/e4b/e4c/E4C-edge.json --validate-only   # measurement.profile_class P4 (certify stamps it); projected 432.5376
```

The two-tenant check runs on the coordinator host with the command in §5 plus `--validate-only`. It projects
99.5328.

## 4. The certificate run (box, one certify invocation for every cell)

**[coordinator]**

The launcher is `research/plan/evidence/coordinator/session-03-tools/rollout/e4b-certify3.sh`, with the
E4C additions below:

- the certify image `infrx-certify:$RELEASE`
- `--env-file /etc/marlin2b-gateway.env`, never `-e MAX_VIDEO_SECONDS`
- the transaction-port DSN for the ledger half
- `E4B_WINDOW_OK=1`

Pre-checks (they print names only):

- `cut -d= -f1 /opt/dlami/nvme/e4b/key.env` must print exactly `INFRX_API_KEY` (rule 1).
- `grep -c '^MARLIN_API_KEY=' /etc/marlin2b-gateway.env` must print `0`.

```bash
python tests/integration/backend/certify.py --no-stack --box \
  --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
  --metrics-url http://127.0.0.1:8001/metrics --worker-metrics-url http://127.0.0.1:8002/metrics \
  --inventory /e4b/inventory.txt --release-sha "$RELEASE" --parity-baseline /e4b/parity-e0.jsonl \
  --run-profile /e4b/e4c/E4C-box.json --key-inventory /e4b/e4c/keys.json \
  --overload-profile /e4b/e4c/E4C-edge.json \
  --workdir /out/work --report /out/report.json
```

| Cell | Profile, as certify stamps it | Expected shape |
|---|---|---|
| `e4b.a.dataset-resume` | the box base (direct gateway) | 24 items, interrupt after 8, 1.0 req/s |
| `e4b.b.envelope` | the box base at 0.5, 1.0 and 2.0 req/s, 135 requests per rung | supported **0.5** only; 1.0 and 2.0 are measured |
| `e4b.b.soak` | the box base at 0.25 req/s × 14,400 s = 3,600 requests | runs only if the 0.5 rung passes; projected 48,660.48 ≤ 50,000 CREDIT |
| `e4b.b.overload` | the edge base, stamped P4, at `https://marlin2b.callbill.ai/v1`, 32-burst | honest 429 + `Retry-After` only. Without `--overload-profile`, or with a profile that has no `target.allowlist` host, the cell is PENDING on PROFILE (BLOCKED) |

Two more checks on the report:

- Check `target.max_video_seconds == 82.0` in the report.
- Poll the run with `infra/rollout/steps/78-e4b-report.sh`.

A failed cell follows the automatic fix loop in
[05 §7](../../../research/plan/consumer-v1/05-client-and-load-testing.md). A rerun is a new qualifying run with
new start timestamps, and the criteria are unchanged.

## 5. The two-tenant headless journey (P-17 check 5)

**[coordinator]**, on the coordinator host, from outside the box, through the public edge. The corpus cache
must be built there (`CORPUS_CACHE`). Both keys are read with `read -rs` and exported under their own names.
`MARLIN_API_KEY` must be unset.

| Leg | Command | Pass |
|---|---|---|
| SSE, upload and inline video; two tenants; cancel | `python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --base-url https://marlin2b.callbill.ai/v1 --target gateway --model nemostation/marlin-2b -c 2 --requests 8 --seed 20260922 --dataset-version e4c-journey-1 --forms upload,upload,video_b64,video_b64 --max-tokens 128 --retries 0 --tenant-keys INFRX_API_KEY,INFRX_API_KEY_B --cancel-after 2 --cancel-fraction 0.25 --profile ~/e4c/E4C-two-tenant.json --key-inventory ~/e4c/keys.json --out ~/e4c/journey.jsonl --raw ~/e4c/journey-raw.jsonl` | Each tenant has 4 requests (2 upload and 2 `video_b64`). Every non-cancelled in-cap item is accepted. Over-cap clips get the typed 400. No cross-tenant output. The spend ceiling is 8 × 12.4416 = 99.5328 ≤ 100 CREDIT |
| replay | The same command with `--resume ~/e4c/journey-raw.jsonl` and a new `--out` and `--raw` | No terminal item is re-sent. Each cancelled item is re-sent with its original Idempotency-Key, payload and upload handle, and answers the replay (`Idempotency-Replayed: true`; R106 `cancelled_by_interruption`) with no new job and no new charge. Re-derive the result with `bench.py --validate-raw <raw> --intentional-resume` |
| async jobs, per tenant | `INFRX_TEST_KEY` set to each tenant's key in turn (`read -rs`), then `VIDEO_FILE=<in-cap clip> infra/rollout/verify-journey.sh` | `failures: 0` for both tenants; `drift.py --request-id <printed id>` shows SETTLED |
| sync (non-stream) | no external client in the repo runs this leg; local proof is `tests/integration/backend/test_journey.py` (mode `sync`) | record a one-off header-file curl per tenant, or record NOT RUN with this reason |
| dataset resume | certify's `e4b.a.dataset-resume` (§4) | PASS |
| discovery and retention | `curl -s https://marlin2b.callbill.ai/v1/models`, compared with the published catalog and the P-25 TTLs (R109) | match |

## 6. Drills, reconciliation, operations (P-17 checks 6 and 7)

**Drill record format.** Write one line per drill into `drills.md`. Each measured recovery time is printed next to
its §5 bound with PASS or FAIL:

```
drill=<name> runbook=<file#section> ssm=<command id> t0=<UTC> recovered=<UTC> measured_s=<n> bound_s=<n> verdict=<PASS|FAIL> correctness=<PASS|FAIL: detail>
```

| Drill | Runbook | Bound (§5, P-18) |
|---|---|---|
| engine restart | [restart.md § Engine](../../../infra/runbooks/restart.md) | `/readyz` 200 ≤ 300 s |
| worker SIGKILL (`docker kill --signal=KILL infrx-worker`) | [restart.md § Worker](../../../infra/runbooks/restart.md) | ≤ 30 s |
| Valkey index loss | [index-loss.md](../../../infra/runbooks/index-loss.md) | ≤ 30 s; the only gateway 5xx allowed is 503 `dependency_unavailable` |
| DB or object-store stall | E3C s08 | retryable 503/504 within 45 s |
| restore | [restore.md](../../../infra/runbooks/restore.md) (hosted A-steps; box B1) | restored and checked |
| known-good rollback | [rollback.md](../../../infra/runbooks/rollback.md#known-good-rollback-drill), steps 1–7 | `known-good.py --bundles` exit 0 and `85-known-good-box.sh`; both journeys `failures: 0`, both SETTLED |

**Correctness for every drill (P7).** Every accepted job reaches exactly one terminal state and settles once, and
inference and settlement work after the recovery.

**Reconciliation.**

- **[coordinator]** Run `infra/runbooks/drift.py --hours <window>` after each drill and after the run. It must show
  zero wallet drift rows and zero credit-wallet drift rows.
- The dataset and soak ledger halves in the report must be exact.
- Actual spend per cell is the certify tenant's CREDIT debits, and it must be ≤ 50,000. Expected ≤ ~9,200 per
  full run (est.).
- Expiry and cleanup: `86-cleanup.sh` as a dry run, then the real run once logged. The result and source TTLs are
  the P-25 values, observed and not shortened.

## 7. Evidence layout

```
models/marlin2b/results/E4C-box-<release7>/run<N>-<UTC>/   # the profiles' evidence_destination
  report.json  certify.log  work/                          # as fetched by e4b-fetch3.sh
  journey/  journey.jsonl  journey-raw.jsonl  replay.jsonl  verify-journey-<tenant>.txt
research/plan/evidence/e/E4C-<release7>/
  freeze.json          # §2 identities, certify --hashes, bundle SHA256SUMS, command ids
  profiles.sha256      # the 3 committed bases + the 3 filled copies (filled copies committed too)
  keys.json            # the key inventory (prefixes only) + the dry-run JSON sha256
  drills.md            # §6 records, one line per drill
  reconcile.txt        # drift.py outputs (counts only)
  ops.md               # 74-alert-test, expiry/cleanup, restore, rollback, known-good
research/plan/evidence/e/E4C-<release7>.md   # the decision: per-cell PASS/FAIL/BLOCKED/INVALID/NOT RUN,
                                             # the limitations and the P-17 table below, dated
```

Evidence directories are append-only. A failed attempt keeps its own `run<N>`.

## 8. P-17 tick-off (BACKEND-READY is accepted only when all ten hold)

| # | Check | Where the proof lives |
|---|---|---|
| 1 | Gate schema: E4C is implemented; candidate source and deployed identity recorded; every E4C test_id cell PASS | `E4C-<release7>.md` cell table; `gates.BACKEND-READY` in the coordinator updates |
| 2 | Frozen identities, including P-06 and the profile sha | `E4C-<release7>/freeze.json`, `profiles.sha256` |
| 3 | CREDIT regime, P-01 card, P-02 dry-run exit 0 | step 0.3/0.4 records; the report's ledger half in CREDIT |
| 4 | P-18 committed before the first qualifying run; every envelope and soak row PASS | step 0.1 timestamp compared with the `report.json` start; `e4b.b.envelope` and `e4b.b.soak` |
| 5 | Two-tenant headless journey passed | `results/…/journey/`; §5 legs |
| 6 | Exact reconciliation within the P-24 cap | `reconcile.txt`; the report's ledger rows; the spend figure |
| 7 | Alert, expiry, restore and known-good rollback | `ops.md`, `drills.md` |
| 8 | No open launch-path P1; RV-04, RV-08, RV-09 and RV-10 fixed | `tasks.json` RV rows with evidence links |
| 9 | Published limitations: no live video, no actuation, nothing over 82 s, the P-18 envelope, rollback triggers, "single-GPU recovery is not high availability", and no aggregate p95 labelled as video p95 | `E4C-<release7>.md` limitations section |
| 10 | Cutover recorded separately | the coordinator's separate authorized cutover record |

## Verification log

- 2026-09-25 (E4C-RUNBOOK): written with `E4C-edge.overload.base.json`, `E4C-box.two-tenant.base.json` and
  certify E4P-V7/V8. Nothing here has run against the box, AWS or hosted; every command above is for the
  coordinator or the operator. `E4C-box.base.json` is unchanged (sha256 `c0d4aa1b…d2ff`).
