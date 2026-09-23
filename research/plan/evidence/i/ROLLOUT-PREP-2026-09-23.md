# ROLLOUT-PREP — phase-2 rollout preparation (evidence)

Lane `codex/rollout-prep` (worktree `.claude/worktrees/codex-rollout`), base `6ac2bec`.
Items 1-4 ran in the first session (killed by the Opus session limit at 22:4xZ while writing
item 4's step test); 4b and 5 in the resumed session. **Nothing was applied to hosted, nothing
ran on the pilot box, no cutover.** Secrets went from SSM into shell variables or preflight's
process memory only; no value is in this file, in a log, in a commit or on a command line.
The operator page this evidence backs is [infra/runbooks/rollout.md](../../../../infra/runbooks/rollout.md).

## Commits

| Commit | Item |
|---|---|
| `a670ec6` | 3 - `rehearse.sh` step 8 derives the migration versions from the directory |
| `fda306a` | 4 - `deploy/release-bundle.sh` + `tests/i/test_release_bundle.py` (2 cases, 5 mutants) |
| `0e9f950` | 4 - `infra/runbooks/rollout.md`, steps `25-save-edge.sh`, `45-s3-check.sh`, `93-restore-edge.sh` |
| `d2da297` | 4b - the three steps against stubs (`tests/i/test_rollout.py`, 2 cases, 8 mutants) |
| (this change) | 5 - preflight dry-run, rollout.md (edge swap, W9 host command, P3/P4), this file |

The branch touches code (`deploy/rehearse.sh`, `deploy/release-bundle.sh`, `tests/i`,
`infra/rollout/steps`, `infra/runbooks`): the merge plan's `8-rollout-prep.sh` guard ("evidence
only", fails when the merge touches anything outside `research/`) must be relaxed for it.

## 1. Hosted inventory (read-only, 2026-09-23T21:56Z)

`psycopg` session with `default_transaction_read_only = on` (asserted), session pooler
`aws-0-us-east-2.pooler.supabase.com:5432`, user `postgres.fcbnscgsymzdykendbrc`, password
from `/INFRX-SUPABASE-PROD/db_password` in `PGPASSWORD` only.

| Item | Hosted |
|---|---|
| server | PostgreSQL 17.6 |
| migration history | `supabase_migrations.schema_migrations(version, statements, name)`: **0001 init, 0002 seed_models** |
| schemas | `auth`, `public`, `storage`, `supabase_migrations` (no `infrx`) |
| rows | `public.api_keys` 2, `credit_ledger` 0, `models` 4, `org_members` 4, `organizations` 4, `profiles` 4, `usage_events` 1; `auth.users` 4, `auth.identities` 4 |
| money | `sum(usage_events.cost_usd)` = 0.00019660 USD; ledger sum 0 |
| other | no `infrx*` roles, no views in public/infrx, 0 storage buckets |
| auth | GoTrue schema: 27 tables, 82 GoTrue migrations, head `20260831180000` |

### The hosted migration gap

Hosted is at **0002**; the release adds **0003-0018, sixteen files**. `migrate.py plan`
(read-only) against hosted, on the tree carrying D5's 0018 at `8554b47`:

```
applied: 0001 init, 0002 seed_models
pending: 0003_pilot_durable_schema.sql  sha256=235d2915fa668cf13f17d0f2bce9371979cce775671c0797ae2341c92e8351f8
pending: 0004_pilot_roles_and_rpcs.sql  sha256=a83a205c48fae75bc4a6ba06da3a3fc127eb3cdba8b37491c6bb73a607a9bfa8
pending: 0005_console_read_surface.sql  sha256=8d19c7ee9fd90d4f2b2a485029b52d9365d2147f99d4527b67e06851e5a3007c
pending: 0006_credit_accounting.sql     sha256=3c37385599c72eb13a1ee0a3be02e3cafb955a6f2194ee18cd80eda906d70b38
pending: 0007_provider_registry.sql     sha256=b7320a1d61d3d7970c6f7b05833be1774b2e9a6f97cb44a0c694ca1c8d12e495
pending: 0008_credit_read_surface.sql   sha256=b367197f7d25a1a4d598604c4061d16e9eed17a6979a9245b2396c4fd0718253
pending: 0009_operator_seams.sql        sha256=d973041229f006b36aa1b6287cbb1776d59f69a4791aa77c5cfc7f1596ee30e4
pending: 0010_media_uploads.sql         sha256=02030e1149f0369ebdec132e8da06bd45c5f133d868668df7cceab9f5872196b
pending: 0011_admission.sql             sha256=1ff73c7750350b7ce9e4f2a2ace38cd4ce884b8ef02d3a719043a70c20b4a83b
pending: 0012_dispatch_outbox.sql       sha256=f6c53676dc882dc2cd08e64734db63f94e0b3567d0be7523d5a86e5d3a93936b
pending: 0013_outbox_gc.sql             sha256=26234254c979127fac59fa44fd2683efd1b072170d365ae3cca2731da8301d20
pending: 0014_job_results.sql           sha256=a1d1ea1baf7b11f739dac24d8509a829904eac04d948fceff48c72698d1b09fa
pending: 0015_signup_eligibility.sql    sha256=7599a9c8db53d10938e2bc6b96a5a931d114979b0f92019a9577b7f41c3545e0
pending: 0016_fenced_leases.sql         sha256=e01476545600a676e7878b31402cf3f1b581fcc9db5baef8881222f9e3e4086f
pending: 0017_stream_journal.sql        sha256=09fc83e01fda499bfac6c666d9e980c2d724bc675e833b9ec99c341d6f527d2c
pending: 0018_terminal_settlement.sql   sha256=9f55935a226e55c4dc89b40d72c5338b8f59eb42d0162dc8b3b7246d08ae5e15
plan digest: 4524cbc02ef70e080f65999d8c797f4c25831985107e9750afb4d261069db181
```

**The digest is tree-specific.** The merge tree `0645e65` (cutover + M1-L2 + this lane)
carries a *different* 0018 (sha256 `1d1dc296…`, an earlier D5 revision via the cutover
branch) than D5's head `8554b47` (`9f55935a…`, above). Whichever 0018 `RELEASE` carries, W6
computes the copy's digest on the day and W7 requires hosted's plan to print the same one.

## 2. Hosted backup and restore rehearsal

`infra/runbooks/pgrestore.py dump` (read-only on hosted: `public` schema + `auth.users`,
`auth.identities`, the `on_auth_user_created` trigger).

| Backup | File | Bytes | sha256 |
|---|---|---|---|
| `/home/rey/infrx-backups/hosted-20260923T215800Z` (item 2) | `project.dump` | 44796 | `13ae27751350351ad72261b416ce09b1d14e285756a0e8d34675ce3b715891df` |
| | `auth.dump` | 3239 | `e70636470bf9bb478dc2a35f0f2167a760a3f168c856be29cd4323d3331d3add` |
| | `meta.json` | 613 | `d06293ae2e40598e961d0a0ac4571fc595e4420aecae7478332ea3378825666b` |
| `/home/rey/infrx-backups/hosted-20260923T223903Z` (the W6 block run verbatim, per `0e9f950`) | `project.dump` | 44796 | `21e6fbeaf6680ad0d5e604436f1ed61338dfb17669ab6d9bb0950838ed94d706` |
| | `auth.dump` | 3239 | `55c90ee7e0ae3a5994bf0c687e6bf1149c1025a00fb0271f79c1ad4510ed4b1a` |
| | `meta.json` | 613 | `16e6f7a02ec5e7db8641e61a554279fb252a3d22665987dd6a41d6512f74ec68` |

Both directories are 0700, files 0600, each with its `SHA256SUMS`. Retention: restore.md A9.

**Restore comparison** (into `supabase/postgres@sha256:7768d0d1…` on 127.0.0.1:55697):

- **Finding:** the pinned image's template has only its own `auth.users`; hosted's auth schema
  is GoTrue's, so a restore into the bare template fails at `auth.identities`. GoTrue
  `v2.197.0` (`supabase/gotrue@sha256:1736a630…`, migration head `20260831180000` = hosted's)
  migrates the template first (75 migrations, 0.6 s). This is now in rollout.md W6.
- A5 restore: exit 0 in 1.0 s. **A6 `check` hosted vs restored: exit 0, `"equal": true`,
  `problems: []`.**
- Schema parity of hosted's applied 0001/0002 against the repository's 0001/0002 on a fresh
  copy: equal.
- Seed rows: `public.models` 4 rows on both, but a **different content fingerprint** (hosted
  `3b948fde…` vs fresh seed `e37bb50a…`): hosted's model rows differ from the repository's
  0002 seed. Not a blocker by itself - 0003 updates `public.models` and 0007 alters it, and
  the copy apply below ran on hosted's actual rows - but W6 must keep applying to a restore of
  hosted, never to a fresh seed; recorded so no one "fixes" it by reseeding.
- The rollout's apply on the restored copy (hosted's history rows 0001, 0002 inserted):
  `plan` digest `4524cbc0…` (= hosted's); `apply --expect <wrong>` refused, exit 2, nothing
  changed; `apply --expect 4524cbc0…` exit 0 in 0.4 s, applied 0003-0018; `plan` after:
  nothing pending. A7: flags `credit_admission` f, `legacy_usd_admission` t, `signup_grant` f;
  `drift_rows` 0; pgrestore's drift detectors `[]`. Rows after apply: `api_keys` 2,
  `usage_events` 1, `credit_ledger` 0, `auth.users` 4, `infrx.wallets` 4, `infrx.jobs` 0.

## 3. Install rehearsal (`deploy/rehearse.sh`, local Docker only)

| Tree | Result |
|---|---|
| this branch, `a670ec6` | **REHEARSAL PASSED**, 44 PASS / 0 FAIL, teardown clean. Before the item-3 fix step 8 failed: the injected `0010_broken.sql` collided with `0010_media_uploads.sql` (two files, one version: exit 2 not 3) and the expected history was the nine-file list |
| merge tree `f5602d4` / `0645e65` (cutover-mount + m1l2-object-store + this lane) | **FAIL at step 1**: `install.sh` dev exits 4, "the runtime did not become ready"; nothing after step 1 ran. Cause (gateway log): `create_app` -> `pilot.adapters_from_env` -> `object_store`: `RuntimeMisconfigured: INFRX_MODE='dev': requires S3_MEDIA_BUCKET`; past that, the same function requires `DATABASE_URL` in every mode once a store is built from it, and `build_ingress_deps` connects the Valkey index (`valkey_index`) |

The diff is therefore not in the steps but in the runtime's requirements: since the cutover,
**every** mode builds the object store, the Postgres stores and the Valkey index, so the
rehearsal's dev deploy needs an S3 endpoint (MinIO) + `S3_MEDIA_BUCKET`, a `DATABASE_URL` (its
own Postgres) and a reachable Valkey, and
the G5 gate cannot pass on the release tree until `rehearse.sh` supplies them. Owner: I /
cutover lane, on the merged tree (this branch cannot test it: its `create_app` predates the
cutover).

## 4. Release route, runbook, steps (items 4 and 4b)

- `release-bundle.sh <40-hex>`: a git bundle of the one commit (built in a throwaway
  repository that borrows the objects - no ref added here), its sha256 manifest, a round trip
  into an empty repository before upload, upload with the stale shell keys unset, and the box
  step `<sha>.fetch.sh` (download, `sha256sum -c`, `bundle verify`, fetch into
  `refs/infrx/releases/<sha>`; the working tree untouched).
- **S3 smoke (real bucket, cheap):** `NAME=test-20260923T231945Z` bundle of `0e9f950`
  (12,508,392 bytes, sha256 `ef7a21dc…`) uploaded to
  `s3://llm-bootcamp-641134885443/releases/test-20260923T231945Z.{bundle,sha256}`; the
  generated box step run on the coordinator host (aws/sudo shims; `RELEASES_DIR`/`BOX_REPO` in
  the scratchpad) fetched it into an **empty** repository: `…bundle: OK`, `release 0e9f950… is
  in …`, one ref `refs/infrx/releases/0e9f950…`; both objects deleted; `releases/` empty
  after; the bucket is **unversioned** (no noncurrent copies left). Bundles are not
  byte-reproducible (a rebuild gave 12,480,127 bytes): the manifest is per build, as designed.
- 4b, `tests/i/test_rollout.py`:
  `test_ops_recover__the_saved_edge_comes_back_byte_for_byte` (25 then 93 against a stub
  docker: the saved copy and printed sha256; a wrong sha256 or a missing name restores and
  reloads nothing; the restore rewrites in place - a hard link standing in for Caddy's
  single-file bind mount sees the original bytes - and reloads through
  `unix//config/admin.sock`) and
  `test_backend_deploy__the_real_bucket_check_runs_the_release_image_before_the_install` (45:
  refuses without RELEASE and on a checkout that is not the release, builds
  `infrx-runtime:$RELEASE`, passes only the names `INFRX_M_S3_ENDPOINT`/`INFRX_M_S3_BUCKET`
  with AWS S3 and the media bucket, never `INFRX_M_S3_LOCAL_CREDS`, the container script ends
  with the pytest run, a red run fails the step).
- Mutants added (tests/i/mutants.py): `save_edge_without_sha`, `restore_edge_unchecked`,
  `restore_edge_new_inode`, `restore_edge_reload_on_loopback`, `s3_check_any_checkout`,
  `s3_check_local_creds`, `s3_check_red_ignored`, `s3_check_red_ignored_inside` - **8/8
  killed** (two needed test changes on the way: a regex miss was a crash, not a kill; the
  first inode check survived because ext4 reuses a freed inode number at once - the hard link
  closes it). The five `bundle_*` mutants of item 4 re-run: killed.
- `45-s3-check.sh`'s in-image script (five hash-pinned wheels, `--network host`) run locally
  on the merge tree's image `3ec9fc9f…` without an endpoint: install OK, 25 passed, 15
  skipped (M1-L2's own count).

### M1-L2 on the real bucket (from the coordinator host)

The M1-L2 evidence says "proven against MinIO, not AWS S3". From `0645e65/apps/infrx-api`,
default profile (stale keys unset), `INFRX_M_S3_LOCAL_CREDS` unset,
`INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com INFRX_M_S3_BUCKET=llm-bootcamp-641134885443`:

- `uv run --frozen pytest -q tests/m/test_s3.py` on a fresh venv: 12 failed / 8 errors, all
  `ModuleNotFoundError: botocore` in `S3ObjectStore.connect` before any S3 call - botocore is
  the `traces` extra. The command needs the venv of `make api-env` (`--all-extras`).
- `uv run --frozen --all-extras pytest -q -rs tests/m/test_s3.py`: **40 passed in 90.97 s**
  on AWS S3; `test/m1l2/` empty afterwards. W9 on the box then proves only the instance role.

## 5. Preflight dry-run against the hosted settings (2026-09-23T23:21Z)

`preflight.collect()` of the merge tree `0645e65` in check mode (env file
`/nonexistent/never-written.env`, nothing written, nothing restarted), with the real SSM
reads (`/model-inference`, us-east-1, stale keys unset), `release=0645e65…`,
`image=sha256:3ec9fc9f…` (that tree's runtime image) and the planned
`--set S3_MEDIA_BUCKET=llm-bootcamp-641134885443 --set MAX_VIDEO_SECONDS=82 --set
WORKER_CONCURRENCY=8 --set ENGINE_MAX_NUM_SEQS=8`; `engine_problems` on the tree's
`serve.sh`; `bucket_problems` (HeadBucket from this host); then the in-image probe fed the
collected values on stdin, `--network none`. The whole output is the names and problems
quoted here (`rp-smoke/preflight-dry3.log` in the coordinator scratchpad).

| Key | Source | Exists? |
|---|---|---|
| `INFRX_MODE`=pilot, `MODEL_ID`, `MAX_INFLIGHT`, `USAGE_LOG`, `UPSTREAM`, `VALKEY_URL` (`valkey://127.0.0.1:6379/0`), `PROCESSING_CACHE_DIR` | installer (`local_values`) | yes (collected) |
| `INFRX_IMAGE` | installer: the built image id | yes |
| `INFRX_RELEASE_SHA` (new, cutover) | installer: `--release "$sha"` (install.sh's HEAD) | yes when supplied - the earlier dry-run that did not pass `release` refused `INFRX_RELEASE_SHA: no value supplied` |
| `SUPABASE_URL` | SSM `/model-inference/supabase_url` (String, v1) | yes |
| `SUPABASE_SERVICE_ROLE_KEY` | SSM `/model-inference/supabase_service_role_key` (SecureString, v1) | yes |
| **`DATABASE_URL`** | SSM **`/model-inference/pg_journal_url`** | **NO - `not_found`** (describe-parameters confirms: not in SSM) |
| `GATEWAY_API_KEY` | SSM `marlin2b_api_key` exists (v1) | forbidden in pilot: not read, not written |
| `S3_MEDIA_BUCKET` | `INFRX_SET` | HeadBucket answered (no problem) |
| `S3_MEDIA_PREFIX` (default `infrx/`), `S3_ENDPOINT_URL` (unset) | defaults | not set, as decided |
| `MAX_VIDEO_SECONDS`=82, `WORKER_CONCURRENCY`=8 | `INFRX_SET` | accepted |
| `ENGINE_MAX_NUM_SEQS`=8 | 50-install's own variable (it adds it to `INFRX_SET`) | accepted |
| `ACCOUNTING_REGIME` | default `legacy_usd` | not set, as decided |
| migration (W6/W7) | SSM `/INFRX-SUPABASE-PROD/db_password` (SecureString, v1) | yes |

Results: host-side problems = **only** `/model-inference/pg_journal_url: not_found
(DATABASE_URL …)`; `engine_problems` none; the in-image probe `ok: false` with
`INFRX_MODE='pilot': requires DATABASE_URL` and `PENDING(W3): infrx.worker.__main__ is not in
the runtime`. With a placeholder DSN (G3's shape, no SSM) the probe's **only** refusal is the
worker root; `infrx/worker/__main__.py` is on none of `claude/backend-impl`,
`codex/cutover-mount`, `codex/e3b-phase3-bodies`, `codex/m-pilot-media`.

The box's env file today (I1B, 2026-09-22) holds the monolith's names (`MODEL_ID`,
`MAX_INFLIGHT`, `GATEWAY_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`); install.sh
replaces it whole from the manifest, so nothing there has to pre-exist.

## Requests to the coordinator (names only)

1. **`/model-inference/pg_journal_url`** (SecureString): the session-pooler DSN of the login
   role D's 0004 note names. Install refuses (exit 2) without it.
2. **`infrx.worker.__main__`** (the worker composition root, W3/I2B-R4): install refuses until
   it is in the runtime image.
3. **`rehearse.sh` on the release tree** (I / cutover lane): its dev deploy needs
   `S3_MEDIA_BUCKET` + an S3 endpoint, `DATABASE_URL` and Valkey since the cutover (G5).
4. Relax `merge-plan/8-rollout-prep.sh`'s "research only" guard for this branch.
5. Decide which 0018 `RELEASE` carries (merge tree `1d1dc296…` vs D5 head `9f55935a…`);
   the W6/W7 digest follows it.

## Could not do / not done

- Nothing on the box (by rule): 25/45/93 are tested against stubs only; the instance role's
  permissions on `test/m1l2/*` are asserted by the coordinator's note, not tested.
- No hosted write (by rule); the hosted apply is W7 of the checklist.
- The first session's per-command AWS/hosted log died with it; its hosted commands, from its
  artefacts: the read-only inventory, two `pgrestore.py dump`s, `pgrestore.py check` (read on
  hosted), `migrate.py plan` against hosted (read-only), and SSM reads of
  `/INFRX-SUPABASE-PROD/db_password` into `PGPASSWORD`.
- G5 on the merged tree (request 3); the full `make check` was not run by this lane.

## AWS commands of the resumed session (purpose first; values never on a command line)

```
23:19:59Z list releases/ (read-only)                        -> empty
23:20:01Z release-bundle.sh upload smoke, 2 objects        -> exit 0
23:20:17Z the box step on this host (2 downloads)          -> exit 0
23:20:32Z s3 rm releases/test-20260923T231945Z.bundle      -> exit 0
23:20:33Z s3 rm releases/test-20260923T231945Z.sha256      -> exit 0
23:20:34Z list releases/ (read-only)                        -> empty
23:20:44Z get-bucket-versioning (read-only)                  -> unversioned
23:21:37Z preflight dry-run: 3 SSM get-parameter (in-process), HeadBucket
23:22:07Z ssm describe-parameters (names/types/versions only)
23:26:23Z M1-L2 test_s3.py on the real bucket               -> import error, no S3 call
23:26:42Z the same with --all-extras                         -> 40 passed
23:28:22Z list test/m1l2/ (read-only, twice)                 -> empty
```

## Verification log

- 2026-09-23: written by the resumed ROLLOUT-PREP session from the first session's
  artefacts (scratchpad inventory/restore/rehearsal/dry-run logs, the backup directories) and
  this session's runs. `tests/i/test_rollout.py` + `test_release_bundle.py` 10 passed; the
  13 ROLLOUT-PREP mutants (8 step + 5 bundle) re-run on the final tree: 13/13 killed;
  `uv run --frozen pytest -q tests/i` (default mutant subset included): **147 passed** in 202 s.
