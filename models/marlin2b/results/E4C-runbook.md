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

## 1. The window (inside rollout.md §2)

1. **[coordinator]** Run rollout.md W1–W7 for the candidate `RELEASE`. Hold the deployment lock. The window id
   that is logged is the `maintenance_window` value in step 3. W7 takes hosted from 0001–0018 to 0001–0025
   (its second `plan` prints `nothing pending`).
2. **[coordinator]** §1a below (H1–H6, on hosted), which is rollout.md W7f plus the key steps, then rollout.md
   W8–W13 with §1's `INSTALL_ARGS` (`ACCOUNTING_REGIME=credit`, `ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch`)
   and W10b (`55-runtime-login.sh`, the dedicated logins). W12's `verify-external.sh` must print `failures: 0`.
3. The **edge must be open**, not in maintenance, for the P4 burst and the journey. The window's safety comes from
   the key inventory (only test keys are active), not from the edge.
4. From the box, `curl -s -o /dev/null -w '%{http_code}' https://marlin2b.callbill.ai/health` must print `200`.
   The burst leaves the box and comes back in through Caddy.
5. **[coordinator]** Run the observe and ops steps O3–O6 from rollout.md §4. Step O6 (`74-alert-test.sh`) is
   **[operator-held]**: the operator confirms the nonce at the P-25 destination.
   - **The canary stays off until §5 ends.** Run O4 **without** `P24_APPROVED`, so the canary timer is not enabled
     (`BLOCKED (P-24)`) and O5 shows no `infrx_canary_up`. The canary tenant's traffic would otherwise be foreign
     traffic in the envelope and soak cells. Its key, unless it is `142c7d81`, must not be active at H6 (see H6).
   - After §5: issue or restore the canary key (logged), re-run O4 with `P24_APPROVED=<ref>`, then O5. Record both
     in `ops.md`.

### 1a. After the hosted apply (before W8)

H1–H2 call the migrated schema: `publish-card` writes the 0018+ card tables, and the activation writes the
flags through 0022's `infrx.set_feature_flag`. Hosted is at 0018 until W7 applies 0019–0025, so none of these
runs before W7. They all run before W8, because W10 installs `ACCOUNTING_REGIME=credit` with this card, and
without both the gateway does not serve CREDIT ([rollout.md §1](../../../infra/runbooks/rollout.md#1-settings-for-this-release),
the `ACCOUNTING_REGIME` row). The operator CLI runs from the coordinator host with `OPERATIONS_DATABASE_URL`.

| # | Who | Step | Record |
|---|---|---|---|
| H1 | coordinator | **P-01.** Publish the launch card: `python -m infrx.operations.cli publish-card --model nemostation/marlin-2b --card-version rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 --approved-by "Launch price approved by the coordinator under the operator's authorization of 2026-09-25" --effective-at <RFC3339 ≤ now> --idempotency-key pub-20260925-launch --reason "launch price"`. Export `OPERATIONS_DATABASE_URL` first with `read -rs` (the owner login; the CLI refuses the dedicated logins). It writes the 0018+ card tables of the migrated schema. | card id, output |
| H2 | coordinator | **P-02 dry-run.** `credit-transition --dry-run --card rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200` must exit 0 with `drift == []`. Then activate CREDIT (G8 step 3): `credit-transition --card rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 --drain-timeout-s 900 --idempotency-key cutover-20260925-launch --reason "E4C CREDIT activation (P-01, P-02)"` with `INFRX_OPERATOR_KEY` exported from the secure store. It writes the flags through 0022's `infrx.set_feature_flag` (`infrx/operations/transition.py:206`), which is why H1-H2 follow W7. The ledger half must run in CREDIT, not in `legacy_usd`. | JSON sha256, `keys` block (prefixes only), exit code |
| H3 | **[operator-held]** | **Revoke the pre-cutover keys.** The two pre-cutover consumer keys must be revoked (`revoke-key`, logged), or the H2 `keys` block must show `revoked_at` on both. Otherwise the run is refused: any active prefix outside `test_key_ids` is an error. | prefixes, `revoked_at` |
| H4 | **[operator-held]** | **P-05 second tenant: the account only.** Read `auth.users.email_confirmed_at`, and confirm the second user if needed. The coordinator then runs `grant --user <uuid2> --idempotency-key grant-tenant2-20260925 --reason "E4C second test tenant"`. **Do not issue its key here.** It is issued in §5.0, after certify. An active tenant-2 key is outside the box and edge profiles' `test_key_ids` (`["142c7d81"]`), so every certify cell would be refused. | tenant-2 user id (opaque), grant id |
| H5 | **[operator-held]** | **P-24 funding.** Run `python -m infrx.operations.cli adjust --user <certify tenant> --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation"`. This is test funding, not a second grant. The tenant then holds 10,000 + 40,000 = 50,000 CREDIT, which is the per-cell cap. | adjustment id, balance after |
| H6 | coordinator | **The certify key inventory**, taken after H3 and H4. Run a fresh read-only `credit-transition --dry-run` (same flags as H2's dry-run; it writes nothing and needs no operator key). List every prefix in its `keys` block whose `revoked_at` is null. The list must be exactly `142c7d81`. For any other active prefix (a pre-cutover key, a tenant-2 key issued early, a canary key), **stop**: the operator revokes it, then retake H6. Write `keys-certify.json`: `{"active_key_id_prefixes": ["142c7d81"], "taken_at": "<as_of of this dry-run>", "source": "G8 credit-transition --dry-run sha256:<this dry-run's JSON sha256>"}` as `/opt/dlami/nvme/e4b/e4c/keys-certify.json` on the box. It holds prefixes only. No key may be issued or restored between H6 and the end of §4; if one is, retake H6 before the next cell. | dry-run JSON sha256, `as_of`, file sha256 |

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
| `test_key_ids[1]` (two-tenant only) | The tenant-2 prefix, issued in §5.0 after certify. It is filled there, not in §3 |
| certify hashes | `python tests/integration/backend/certify.py --hashes` from the checkout (serving version, engine options, migrations, deploy and rollout trees, alerts, lock) |
| rollout bundle | `release-bundle.sh` `SHA256SUMS` from W1 |
| workload manifest | `models/marlin2b/corpus/manifest.json` sha256 `386a2d89…095e18` (`e1-2026-09-20`) |

## 3. Fill every FILL and validate (no request leaves)

Copy the committed bases to `/opt/dlami/nvme/e4b/e4c/` on the box:

- `E4C-box.json` from `E4C-box.base.json`
- `E4C-edge.json` from `E4C-edge.overload.base.json`

The journey profile, `E4C-two-tenant.json` from `E4C-box.two-tenant.base.json`, is filled in §5.0, after the
tenant-2 key exists.

Replace every `FILL` from §2, and change nothing else. **Changing a bound or a rate is a new profile version,
not a fill** (R133). Record the sha256 of each committed base and of each filled copy. The committed base
`E4C-box.base.json` is `c0d4aa1b…d2ff`.

| Profile | FILL fields |
|---|---|
| box | `source_sha`, `deployed_sha`, `image_digest`, `weights_sha256`, `processor_sha256`, `migration_version`, `config_version`, `allowed_fault_targets[1]`, `allowed_fault_targets[2]`, `maintenance_window` |
| edge | The same identity fields and `maintenance_window`. It has no fault targets |
| two-tenant (§5.0) | The same identity fields, `maintenance_window` and `test_key_ids[1]` |

Then validate both filled profiles with bench `--validate-only` against the certify inventory. Each must exit 0
with `runnable: true` and no errors, blocks or warnings, and neither may create `--out`. Run them with
`INFRX_API_KEY` unset or set; the key values are never read here.

```bash
M=models/marlin2b; C="--corpus $M/corpus/manifest.json --subset full --target gateway --model nemostation/marlin-2b --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --key-inventory /opt/dlami/nvme/e4b/e4c/keys-certify.json"
python $M/bench.py $C --base-url http://127.0.0.1:8001/v1 --rate 0.5 --requests 3600 --dataset-version e4c-1 --profile /opt/dlami/nvme/e4b/e4c/E4C-box.json --validate-only        # the soak's request count at the filled rate; projected 48660.4800
python $M/bench.py $C --base-url https://marlin2b.callbill.ai/v1 --rate 1000.0 --requests 32 --burst 32 --dataset-version e4c-1-overload --profile /opt/dlami/nvme/e4b/e4c/E4C-edge.json --validate-only   # measurement.profile_class P4 (certify stamps it); projected 432.5376
```

- The box line runs at **0.5**, the base's `measurement.rate_per_s`, which the filled copy keeps. Certify stamps
  each cell's own rate into its own copy (`cell_profile`): 0.5, 1.0 and 2.0 for the envelope, 0.25 for the soak.
  A `--rate 0.25` check of the unstamped copy is refused with "measurement.rate_per_s is 0.5, the run uses
  0.25". Never edit the filled copy's rate to make it pass.
- The projection does not depend on the rate: 3,600 × 13.5168 = 48,660.48 CREDIT, the soak's ceiling.

## 4. The certificate run (box, one certify invocation for every cell)

**[coordinator]**

The launcher is `infra/rollout/e4c-certify.sh` (`infra/rollout/ssm.sh infra/rollout/e4c-certify.sh
RELEASE=$RELEASE`): session-03's `e4b-certify3.sh` (kept unchanged there as run3's record) with the E4C
additions below. `apps/infrx-api/tests/i/test_rollout.py` pins its certify flags to the command below and to
certify.py's parser.

- the certify image `infrx-certify:$RELEASE`
- `--env-file /etc/marlin2b-gateway.env`, never `-e MAX_VIDEO_SECONDS`
- the ledger half on the transaction port: the env file's `DATABASE_URL` rewritten to :6543 as in run3, and
  `OPERATIONS_DATABASE_URL` = the owner login (SSM `pg_journal_url`) on :6543. After W10b `DATABASE_URL` is
  `infrx_runtime`, which the operator tool that runs the ledger half refuses. Both reach docker in a 0600 file.
- `E4B_WINDOW_OK=1`
- the three E4C inputs: `--run-profile`, `--key-inventory` and `--overload-profile` (§3, H6)

Pre-checks. The launcher runs them and refuses with exit 2 (they print names only):

- `cut -d= -f1 /opt/dlami/nvme/e4b/key.env` must print exactly `INFRX_API_KEY` (rule 1).
- `grep -c '^MARLIN_API_KEY=' /etc/marlin2b-gateway.env` must print `0`.
- `E4C-box.json`, `E4C-edge.json` and `keys-certify.json` exist under `/opt/dlami/nvme/e4b/e4c/`.

```bash
python tests/integration/backend/certify.py --no-stack --box \
  --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
  --metrics-url http://127.0.0.1:8001/metrics --worker-metrics-url http://127.0.0.1:8002/metrics \
  --inventory /e4b/inventory.txt --release-sha "$RELEASE" --parity-baseline /e4b/parity-e0.jsonl \
  --run-profile /e4b/e4c/E4C-box.json --key-inventory /e4b/e4c/keys-certify.json \
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

Certify passes the same `keys-certify.json` to every cell. The tenant-2 key does not exist yet (H4), so
no cell is refused for it.

A failed cell follows the automatic fix loop in
[05 §7](../../../research/plan/consumer-v1/05-client-and-load-testing.md). A rerun is a new qualifying run with
new start timestamps, and the criteria are unchanged. A certify rerun after §5.0 first revokes the tenant-2 key
(and turns the canary off again), then retakes H6.

## 5. The two-tenant headless journey (P-17 check 5)

**[coordinator]**, on the coordinator host, from outside the box, through the public edge. It starts only after
§4's report is fetched. The corpus cache must be built there (`CORPUS_CACHE`). Both keys are read with `read -rs`
and exported under their own names. `MARLIN_API_KEY` must be unset.

### 5.0 The second tenant's key, the journey inventory and the media prefix

1. **[operator-held]** The coordinator runs `issue-key --user <uuid2> --name e4c-tenant2 --secret-file <0600 new
   path> --idempotency-key key-tenant2-20260925 --reason "E4C second test tenant"`. Record the **prefix** only.
2. Fill `~/e4c/E4C-two-tenant.json` from `E4C-box.two-tenant.base.json` with the §2 values, `maintenance_window`,
   and `test_key_ids[1]` = the new prefix. Record both sha256 values.
3. **The journey inventory.** Run a fresh read-only `credit-transition --dry-run`, as in H6. The active prefixes
   must be exactly `142c7d81` and the new prefix. Otherwise stop, as in H6. Then write the inventory:
   `~/e4c/keys-journey.json` = `{"active_key_id_prefixes": ["142c7d81", "<tenant-2 prefix>"], "taken_at": "<as_of of this dry-run>", "source": "G8 credit-transition --dry-run sha256:<this dry-run's JSON sha256>"}`
4. **[operator-held] `MEDIA_BASE_URL`.** This is an https prefix, approved by the operator, that serves every corpus
   clip file under its manifest basename (`<prefix>/<basename of the clip's file>`). The gateway fetches it
   itself, so it must be publicly reachable. No such prefix is named in the repository or in the 2026-09-25
   decisions. **Until one is named, the journey leg is BLOCKED on this input, and P-17 check 5 stays false.**
   Export it (`export MEDIA_BASE_URL=…`).
5. Validate: the journey command below plus `--validate-only` must exit 0, runnable, with no errors, blocks or
   warnings, and it must create no `--out`. It projects 298.5984 CREDIT.

### 5.1 The legs

The SSE journey (upload, inline video, video URL and text; two tenants; cancel):

```bash
python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --base-url https://marlin2b.callbill.ai/v1 --target gateway --model nemostation/marlin-2b -c 2 --requests 24 --seed 20260922 --dataset-version e4c-journey-1 --forms upload,upload,video_b64,video_b64,video_url,text,text,video_url --media-base-url "$MEDIA_BASE_URL" --max-tokens 128 --retries 0 --tenant-keys INFRX_API_KEY,INFRX_API_KEY_B --cancel-after 2 --cancel-fraction 0.4 --profile ~/e4c/E4C-two-tenant.json --key-inventory ~/e4c/keys-journey.json --out ~/e4c/journey.jsonl --raw ~/e4c/journey-raw.jsonl   # projected 298.5984 <= 299 CREDIT
```

The frozen schedule (seed 20260922; `tenant = seq % 2`, so tenant A is `INFRX_API_KEY` and tenant B is
`INFRX_API_KEY_B`):

- **Forms.** Each tenant sends 12 requests, 3 of each form: upload, `video_b64`, `video_url` and text.
- **Over-cap clips.** There is one per tenant: seq 3 (B, `video_b64`, c025, 112 s) and seq 12 (A, `video_url`,
  c051, 112 s). Each gets the typed 400.
- **Cancels.** The expected cancelled seqs: `4, 7, 8, 18`. They are seq 4 (A, `video_url`, c016), 7 (B,
  `video_url`, c037, 72 s), 8 (A, upload, c019) and 18 (A, `video_b64`, c015). All are in-cap media; no text item
  is cancelled. Before the run, print the schedule's cancel list with the command's flags. It must print
  `[4, 7, 8, 18]`; otherwise stop:
  `python3 -c 'import sys; sys.path[:0]=["models/marlin2b"]; import bench; c=bench.load_corpus("models/marlin2b/corpus/manifest.json","full")[0]; s=bench.build_schedule(24,c,"upload,upload,video_b64,video_b64,video_url,text,text,video_url".split(","),seed=20260922,max_tokens_mix=(128,),tenants=2,dataset_version="e4c-journey-1",cancel_fraction=0.4,cancel_after=2); print([i["seq"] for i in s if i["cancel_at_s"] is not None])'`
- **Spend.** The ceiling is 24 × 12.4416 = 298.5984 ≤ 299 CREDIT. Tenant B's worst case is 12 × 12.4416 =
  149.2992 CREDIT of its one 10,000 grant.

| Leg | Command | Pass |
|---|---|---|
| SSE journey | the block above | Every non-cancelled in-cap item is accepted, and each tenant has at least one accepted item of each of the four forms. Both over-cap clips get the typed 400. Each tenant has at least one `cancelled` row (a scheduled cancel that finished first is recorded, not failed). No cross-tenant output |
| replay | The same command with `--resume ~/e4c/journey-raw.jsonl` and a new `--out` and `--raw` | No terminal item is re-sent. Each cancelled item is re-sent with its original Idempotency-Key, payload and upload handle, and answers the replay (`Idempotency-Replayed: true`; R106 `cancelled_by_interruption`) with no new job and no new charge. Re-derive the result with `bench.py --validate-raw <raw> --intentional-resume` |
| async jobs, per tenant | `INFRX_TEST_KEY` set to each tenant's key in turn (`read -rs`), then `VIDEO_FILE=<in-cap clip> infra/rollout/verify-journey.sh` | `failures: 0` for both tenants; `drift.py --request-id <printed id>` shows SETTLED |
| sync (non-stream), per tenant | the sync block below | 200 with content and usage > 0; the same key again answers 200 with the same `Inference-Id` and `Idempotency-Replayed: true` |
| foreign calls, per tenant | the foreign-call block below | Each tenant's GET, result read and DELETE on the other tenant's job answers 404 `not_found`. The owner's job still ends `succeeded`, not cancelled |
| dataset resume | certify's `e4b.a.dataset-resume` (§4) | PASS |
| discovery and retention | `curl -s https://marlin2b.callbill.ai/v1/models`, compared with the published catalog and the P-25 TTLs (R109) | match |

Sync (non-stream). No bench mode runs it, and `tests/integration/backend/test_journey.py` (mode `sync`) is local
proof only. The key reaches curl only through a 0600 header file, and only status, ids and token counts are
printed:

```bash
E=https://marlin2b.callbill.ai/v1
for K in INFRX_API_KEY INFRX_API_KEY_B; do
  h=$(mktemp); printf 'Authorization: Bearer %s\nContent-Type: application/json\nIdempotency-Key: e4c-sync-%s-1\n' "${!K}" "$K" > "$h"
  for try in first replay; do
    curl -s -D "$h.hd" -o "$h.body" -w "$K sync $try %{http_code} " -H "@$h" "$E/chat/completions" --data-binary \
      '{"model":"nemostation/marlin-2b","stream":false,"max_tokens":32,"messages":[{"role":"user","content":"Name three primary colors."}]}'
    grep -i '^\(inference-id\|idempotency-replayed\):' "$h.hd" | tr -d '\r' | tr '\n' ' '
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(bool(d["choices"][0]["message"]["content"]), d["usage"]["prompt_tokens"], d["usage"]["completion_tokens"])' "$h.body"
  done
  rm -f "$h" "$h.hd" "$h.body"
done
```

Foreign calls. Each tenant submits one small async job and the other tenant tries to read and cancel it:

```bash
E=https://marlin2b.callbill.ai/v1
hdr() { f=$(mktemp); printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "${!1}" > "$f"; echo "$f"; }
for pair in "INFRX_API_KEY INFRX_API_KEY_B" "INFRX_API_KEY_B INFRX_API_KEY"; do
  set -- $pair; own=$(hdr "$1"); other=$(hdr "$2")
  job=$(curl -s -H "@$own" -H "Idempotency-Key: e4c-foreign-$1-1" "$E/jobs" --data-binary \
    '{"model":"nemostation/marlin-2b","max_tokens":32,"messages":[{"role":"user","content":"Say hello."}]}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_handle"])')
  for call in "GET $E/jobs/$job" "GET $E/jobs/$job/result" "DELETE $E/jobs/$job"; do
    set -- $call; curl -s -o "$other.body" -w "foreign $1 %{http_code} " -X "$1" -H "@$other" "$2"
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["error"]["code"])' "$other.body"
  done
  for i in $(seq 60); do
    state=$(curl -s -H "@$own" "$E/jobs/$job" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    case "$state" in succeeded|failed|cancelled|expired) break ;; esac; sleep 2
  done
  echo "owner state $state"; rm -f "$own" "$other" "$other.body"
done
```

**Check 5 holds only when every leg above passes.** A leg recorded NOT RUN or BLOCKED leaves P-17 check 5 false,
and BACKEND-READY stays pending with the leg named. That includes the SSE journey without an approved
`MEDIA_BASE_URL`, and a sync leg that was not run.

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
  journey/  journey.jsonl  journey-raw.jsonl  replay.jsonl  verify-journey-<tenant>.txt  sync.txt  foreign.txt
research/plan/evidence/e/E4C-<release7>/
  freeze.json          # §2 identities, certify --hashes, bundle SHA256SUMS, command ids
  profiles.sha256      # the 3 committed bases + the 3 filled copies (filled copies committed too)
  keys-certify.json    # H6's inventory (142c7d81 only) + its dry-run JSON sha256
  keys-journey.json    # §5.0's inventory (142c7d81 + tenant 2) + its dry-run JSON sha256
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
| 3 | CREDIT regime, P-01 card, P-02 dry-run exit 0 | step H1/H2 records; the install's `ACCOUNTING_REGIME=credit` (W10); the report's ledger half in CREDIT |
| 4 | P-18 committed before the first qualifying run; every envelope and soak row PASS | step 0.1 timestamp compared with the `report.json` start; `e4b.b.envelope` and `e4b.b.soak` |
| 5 | Two-tenant headless journey passed: both tenants over sync, SSE and async; upload, inline video, video URL and text; at least one cancel per tenant and the replay of each; dataset resume; foreign read and cancel 404; discovery and retention match. A leg NOT RUN or BLOCKED leaves this false | `results/…/journey/`; the §5.1 legs |
| 6 | Exact reconciliation within the P-24 cap | `reconcile.txt`; the report's ledger rows; the spend figure |
| 7 | Alert, expiry, restore and known-good rollback | `ops.md`, `drills.md` |
| 8 | No open launch-path P1; RV-04, RV-08, RV-09 and RV-10 fixed | `tasks.json` RV rows with evidence links |
| 9 | Published limitations: no live video, no actuation, nothing over 82 s, the P-18 envelope, rollback triggers, "single-GPU recovery is not high availability", and no aggregate p95 labelled as video p95 | `E4C-<release7>.md` limitations section |
| 10 | Cutover recorded separately | the coordinator's separate authorized cutover record |

## Verification log

- 2026-09-25 (E4C-RUNBOOK): written with `E4C-edge.overload.base.json`, `E4C-box.two-tenant.base.json` and
  certify E4P-V7/V8. Nothing here has run against the box, AWS or hosted; every command above is for the
  coordinator or the operator. `E4C-box.base.json` is unchanged (sha256 `c0d4aa1b…d2ff`).
- 2026-09-25 (E4C-RUNBOOK fix round): the key phases are split. Certify runs on `keys-certify.json`, which holds
  only `142c7d81` and is taken after revocation. The tenant-2 key is issued after certify, and the journey has its
  own `keys-journey.json`. The canary stays off until §5 ends. The box validate line runs at 0.5. The journey
  adds video URL and text, and its cancels are fixed at seqs 4, 7, 8 and 18. Sync and foreign-call legs are added;
  a NOT RUN leg leaves check 5 false. `models/marlin2b/tests/test_profile.py` validates every bench command here as
  written.
- 2026-09-26 (E4C-RUNBOOK-2): the P-01 card and the P-02 dry-run and activation (old 0.3/0.4) need 0022's
  `infrx.set_feature_flag` and the 0018+ card tables, so they move with the key steps (old 0.5–0.8) into §1a,
  H1–H6, after rollout.md W7 and before W8; every reference is renumbered. §1 installs the CREDIT regime and
  runs W10b (`55-runtime-login.sh`). §4's launcher is `infra/rollout/e4c-certify.sh`, with the three E4C flags
  and the ledger half's owner login. Tests: `tests/integration/backend/recovery/test_runbooks.py` rb11,
  `apps/infrx-api/tests/i/test_rollout.py`. Nothing here has run against the box, AWS or hosted.
