# Lab release runbook — `apps/lab` and the Lab control service (I2L)

**State 2026-10-01: enabled on the pilot box.** The operator ran this release through
`infra/lab/rollout/launch-v1.sh box` at 7ecbab0e (2026-10-01T01:26Z): the control image
`sha256:870aa2ea…`, `infrx-lab-control` ON (readyz 200), the `lab-control.callbill.ai` site on the
App edge; the Lab web is live at `https://lab.callbill.ai` (Vercel `infrx-lab`, 2026-10-01T02:21Z).
Sources: session-03 record lines 594–595, [09](../../../research/plan/consumer-v1/09-path-to-internal-testing.md)'s log;
the procedure of record is runbook [08](../../../research/plan/consumer-v1/08-lab-internal-testing-rollout.md),
whose box steps (`infra/lab/rollout/steps/`) script the manual blocks below. `lab.json`'s
`"enabled": false` is the repository default that `tests/i/lab/test_lab_packaging.py` pins; no
deploy step reads it — the box's switch is the marker `/etc/infrx-lab/enabled` (`enable_marker`),
which step 40 (`STATE=on`) creates. Every value stays in the operator's stores (names below).

Proven locally (evidence `research/plan/evidence/i/I2L-*.md`): the packaging cases
`apps/infrx-api/tests/i/lab/` (the unit's isolation and drain, names only, origins, the pinned
Caddy refusing a broken or hijacking Lab site, a live Lab edge refusing consumer keys) and E3L's
`l11` drill (`tests/integration/lab_operate/`: App inference keeps serving and every accepted job
finishes once while the Lab is down, crash-looping from a bad release and rolled back).

## 1. What the Lab is, and what it never shares with the App

| Part | Where | Shares with the App |
|---|---|---|
| Lab web (`apps/lab`) | its own Vercel project, root `apps/lab`, its own lockfile | the Supabase auth/DB project only (the 0030 session door); no service-role key |
| Lab control service | `infrx-lab-control.service` on the backend box, `127.0.0.1:8003` | the host and PostgreSQL; no unit link, no env file, no image variable, no uid/group, no volume |
| Lab control site | `lab-control.caddy`, imported by the App edge through one glob line | the Caddy process; installed only after the composed config validates |

A Lab outage, crash loop or rollback therefore never stops, restarts or reconfigures the gateway,
worker, engine or index. The App rollback (`deploy/rollback.sh`) never touches the Lab either:
it restores only `UNIT_FILES`, the App env file and `$CADDY_DIR/infrx/`.

## 2. Names (values live in the operator's stores, never in the repository)

Lab web (Vercel project `infrx-lab`, per environment; `apps/lab/.env.example`) [OP]:

| Name | Exposure | Value the code expects |
|---|---|---|
| `NEXT_PUBLIC_LAB_URL` | public | the Lab's own https origin, no path (production refuses http) |
| `NEXT_PUBLIC_SUPABASE_URL` | public | staging: the staging project; production: the App's project |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | that project's publishable key. **Never** `SUPABASE_SERVICE_ROLE_KEY` |
| `LAB_DATASETS_API_URL` | server | server-only base URL of the datasets backend (WR-N4-5, `apps/lab/lib/services/datasets/server.ts`); unset = the datasets pages answer "unavailable" |
| `LAB_TRACES_API_URL` | server | server-only lab-api base URL for the provider trace read (V1M/WR-LAB-API-5, `apps/lab/lib/services/traces/`, `GET /lab/v1/traces` behind `LAB_TRACES`); unset = the request pages read nothing and say so |
| `LAB_EVALS_API_URL` | server | server-only lab-api base URL for the evaluation pages (WR-B4-1, `apps/lab/lib/services/evaluation/server.ts`, `/lab/v1/evaluations` behind `LAB_EVALS`); unset = those pages answer "unavailable" |
| `LAB_PIPELINES_API_URL` | server | server-only lab-api base URL for the annotation and training pages (WR-P4-1, `apps/lab/lib/services/pipelines/server.ts`, `/lab/v1/pipelines` behind `LAB_PIPELINES`); unset = those pages answer "unavailable" |
| `LAB_RELEASES_API_URL` | server | server-only lab-api base URL for the release and optimization pages (WR-R4-1, `apps/lab/lib/services/rollouts/server.ts`, `/lab/v1/releases` + `/lab/v1/optimizations` behind `LAB_RELEASES`); unset = those pages answer "unavailable" |
| `LAB_CONTROL_URL` | server | server-only base URL of the Lab control service (the control origin, R186's factory) for the overview, models and deployments pages (WR-E3L-J, `apps/lab/lib/services/control/server.ts`, `/lab/v1/control`); unset = those pages answer "unavailable" |

Lab control (`/etc/infrx-lab-control.env`, mode 0600, owned by `ubuntu` — the unit runs docker as `User=ubuntu`, which reads `--env-file`; root-owned it fails "permission denied", the 2026-10-01 box defect) [OP]:

| Name | Exposure | Value |
|---|---|---|
| `INFRX_LAB_IMAGE` | unit | the Lab control release, a content-addressed image id (`sha256:…`) |
| `INFRX_LAB_DATABASE_URL` | **secret** | the control service's own database login (L3-SQL names the role); never the runtime's `DATABASE_URL` |
| `INFRX_LAB_SUPABASE_URL` | server | the project whose Lab session tokens it verifies |
| `INFRX_LAB_SUPABASE_ANON_KEY` | server | the publishable anon key the control service presents as `apikey` when it verifies a Lab session token (`infrx.lab.control.app`); never the service-role key |
| `INFRX_LAB_ORIGIN` | server | the Lab web origin whose sessions it accepts |
| `LAB_S3_BUCKET` | server | the Lab objects (the Lab workers' bucket, instance-role credentials) the datasets, pipelines and releases families read and write; unset = those uses answer 503 (`NoObjects`) |
| `LAB_S3_ENDPOINT` | server | that bucket's endpoint (optional) |
| `LAB_CHECKPOINT_KEYS` | **secret** | the checkpoint receiver's key directory (WR-B3-2); set = `POST /lab/v1/checkpoints` is served by this unit, unset = not mounted |

Lab edge: `INFRX_LAB_CONTROL_SITE` (the control origin's address; default the placeholder in
`lab.json`) and `INFRX_LAB_CONTROL_UPSTREAM` (local drill only; unset on the box).

Lab workers (annotation, training, rollout: `/etc/infrx-lab/<role>.env`, ubuntu, mode 0600, one
file per role holding only that role's names; checked by `infra/lab/workers/training/preflight.py`,
runbooks `infra/lab/workers/{training,rollout}/RUNBOOK.md`) [OP]:

| Name | Exposure | Value |
|---|---|---|
| `INFRX_IMAGE` | unit | the role's release in its own env file: a local content-addressed image id (sha256:<64 hex>, --pull never); never the App's INFRX_IMAGE |
| `LAB_DATABASE_URL` | **secret** | the worker role's own Lab database login; never the runtime's DATABASE_URL |
| `LAB_S3_BUCKET` | server | the Lab object store bucket (instance-role credentials only; AWS_* refused) |
| `LAB_S3_ENDPOINT` | server | the Lab object store endpoint; its host may be in LAB_EGRESS_ALLOW |
| `LAB_S3_PREFIX` | server | the Lab objects' key prefix in LAB_S3_BUCKET (`lab/<provider>/` under it, R182); unset = `infrx/`, the media store's default (infrx.lab.workers LAB_PREFIX) |
| `LAB_EVAL_ENDPOINT_URL` | server | the eval role's dev endpoint: the gateway that meters provider_dev (HttpDevEndpoint); unset = the eval role exits 2 |
| `LAB_EVAL_ENDPOINT_KEY` | **secret** | the eval role's credential for LAB_EVAL_ENDPOINT_URL; unset = the eval role exits 2 |
| `LAB_EVAL_CONCURRENCY` | server | eval worker concurrency (pool_budget.py role default 4) |
| `LAB_CHECKPOINTS_CONCURRENCY` | server | checkpoints worker concurrency (pool_budget.py role default 1) |
| `LAB_DATASETS_CONCURRENCY` | server | datasets worker concurrency (pool_budget.py role default 2) |
| `LAB_EGRESS_ALLOW` | unit | NO_PROXY allowlist: exactly the object store host, 169.254.169.254 and the enabled adapter's approved host |
| `LAB_ANNOTATION_CONCURRENCY` | server | annotation worker concurrency (pool_budget.py role default 1) |
| `LAB_ANNOTATION_TEACHER` | server | the annotation teacher adapter; default dry-run, any other needs its P-10 approval in egress.json |
| `LAB_TEACHER_URL` | server | the annotation role's teacher endpoint for collecting approved teacher batches (WR-P4B-2); the local teacher fake (`http://127.0.0.1:<port>`) only until P-10, its host in LAB_EGRESS_ALLOW; unset = the annotation role exits 2 |
| `LAB_ANNOTATION_TEACHER_URL` | server | the approved teacher's https endpoint (non-default adapter only) |
| `LAB_ANNOTATION_TEACHER_TOKEN` | **secret** | the teacher's purpose-specific token (non-default adapter only) |
| `LAB_ANNOTATION_BUDGET_USD` | server | the annotation USD budget, above 0 and within the P-10 approval's cap |
| `LAB_ANNOTATION_PAYER_REF` | server | exactly the P-10 approval's named payer |
| `LAB_TRAINING_CONCURRENCY` | server | training worker concurrency (pool_budget.py role default 1) |
| `LAB_TRAINING_CONNECTOR` | server | the training connector adapter; default manual-bundle, any other needs its P-11 approval in egress.json |
| `LAB_TRAINING_CONNECTOR_URL` | server | the approved connector's https endpoint (non-default adapter only) |
| `LAB_TRAINING_CONNECTOR_TOKEN` | **secret** | the connector's purpose-specific token (non-default adapter only) |
| `LAB_TRAINING_BUDGET_USD` | server | the training USD budget, above 0 and within the P-11 approval's cap |
| `LAB_TRAINING_PAYER_REF` | server | exactly the P-11 approval's named payer |
| `LAB_ROLLOUT_CONCURRENCY` | server | rollout controller concurrency (pool_budget.py role default 1) |
| `LAB_OPERATOR_ID` | server | the rollout role's principal id (a UUID) that R2's pass and `emergency-rollback` record their decisions under (WR-C5-PREFLIGHT); unset = the rollout role exits 2 |
| `S3_MEDIA_BUCKET` | server | the rollout role (and the launcher's shell) only: the gateway's media bucket, copied from the App env, where the page reads a release's stored plan (R249, C7-RV-5); the rollout unit's env file names it equal to the gateway's (its preflight allows it, WR-C7G-PREFLIGHT); set and not `LAB_S3_BUCKET` = every Lab worker role exits 2 naming both, unset = not compared |
| `S3_MEDIA_PREFIX` | server | with `S3_MEDIA_BUCKET`: the gateway's media prefix, copied from the App env (unset = `infrx/`); not `LAB_S3_PREFIX` (unset = `infrx/`) = the role exits 2 (R249) |

## 3. Origins and the auth allowlist (P-05 settings, per project) [OP]

- Lab web: `https://lab.callbill.ai`; control: `https://lab-control.callbill.ai` (the P-08 record of
  2026-09-30; both answer since 2026-10-01, session-03 lines 594–595; DNS A record of the control
  origin → the backend box).
- **Site URL stays the App's.** The Lab only adds its own callbacks to **Redirect URLs**:
  staging project `https://infrx-lab-*-humanbit.vercel.app/auth/callback**` and
  `http://localhost:3100/auth/callback**`; production `https://lab.callbill.ai/auth/callback**`.
- The production entry is an App-side change first: `REDIRECT_ALLOWLIST.production` in
  `apps/app/lib/deploy/env.ts` says "only" the App's callback today (I2A-AUTH-03/04). Add the Lab
  entry there, with its test, in the same change that enables the production Lab.
- Sessions stay apart: the Lab cookie is `sb-infrx-lab-auth`, host-only (L1).

## 4. Enable (staging first) [OP, needs P-08]

```bash
# 1. the Lab's env file (values from the operator's store; names above)
sudo install -m 0600 -o ubuntu -g ubuntu /dev/stdin /etc/infrx-lab-control.env < lab-control.env
# 2. the unit, then the switch it is conditioned on
sudo install -m 0644 apps/infrx-api/deploy/lab/app/infrx-lab-control.service /etc/systemd/system/
sudo install -d -m 0755 /etc/infrx-lab && sudo touch /etc/infrx-lab/enabled
sudo systemctl daemon-reload && sudo systemctl enable --now infrx-lab-control
curl -fsS http://127.0.0.1:8003/readyz          # the L3 factory's readiness (WR-I2L-2)
```

## 5. Install the site [OP]

Needs WR-I2L-1 in the running release: the App edge's `import /etc/caddy/lab/*.caddy` line, and
`lib.sh edge_install` validating every later App release with `/etc/caddy/lab` mounted, so an App
release or a Caddy bump that conflicts with an installed Lab site stops at exit 4 before the edge
is reloaded or replaced (remove the Lab site, section Disable, to ship such a release).
Validate the App's site **with** the Lab's, then rename into place and reload; a file the pinned
Caddy refuses never reaches the edge (the App keeps its current configuration).

```bash
. apps/infrx-api/deploy/lib.sh
sudo install -d /etc/caddy/lab
stage=$(mktemp -d) && sudo cp -r /etc/caddy/. "$stage" && sudo install -d "$stage/lab"
sudo cp apps/infrx-api/deploy/lab/app/lab-control.caddy "$stage/lab/lab-control.caddy"
sudo docker run --rm --network none -v "$stage:/etc/caddy:ro" "$CADDY_IMAGE" \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo cp "$stage/lab/lab-control.caddy" /etc/caddy/lab/lab-control.caddy.tmp
sudo mv -f /etc/caddy/lab/lab-control.caddy.tmp /etc/caddy/lab/lab-control.caddy
caddy_reload
```

## Rollback

A Lab rollback is the previous `INFRX_LAB_IMAGE` and one draining restart of the Lab unit:
in-flight control operations finish within uvicorn's 25 s grace before docker (35 s) and systemd
(45 s) stop it. Accepted App jobs are the gateway's and worker's, in PostgreSQL; nothing here
stops, restarts or reconfigures them, so none is lost or re-run (E3L `l11`).

```bash
prev=sha256:<the previous Lab release, from the release log>
sudo sed -i "s|^INFRX_LAB_IMAGE=.*|INFRX_LAB_IMAGE=$prev|" /etc/infrx-lab-control.env
sudo systemctl restart infrx-lab-control
curl -fsS http://127.0.0.1:8003/readyz
```

A changed site file rolls back through §5 with the previous file.

## Disable

```bash
sudo rm -f /etc/caddy/lab/lab-control.caddy && . apps/infrx-api/deploy/lib.sh && caddy_reload
sudo systemctl disable --now infrx-lab-control
sudo rm -f /etc/infrx-lab/enabled
```

The Lab web is taken down in its own Vercel project (pause or remove the production domain)
[OP]; the App's project is not touched.

## Verification log

- 2026-09-27: written by the lab-operate lane (I2L); preparation only, nothing hosted run.
- 2026-09-29 (lab-c7-gaps, C7-RV-5): `S3_MEDIA_BUCKET` / `S3_MEDIA_PREFIX` declared for the rollout role (R249: the plan location the page reads); nothing hosted run.
- 2026-09-29 (lab-c7-gaps merge, 1-C7G-RV-B): WR-C7G-PREFLIGHT applied: the rollout unit's env file names `S3_MEDIA_BUCKET` / `S3_MEDIA_PREFIX` equal to the gateway's (R249, R256); nothing hosted run.
- 2026-10-01 (W6 docs-state): head replaced by the dated enabled state (session-03 lines 594–595, 09 log); `lab.json` `enabled` documented as the repository default (the box switch is the marker); the control env file's owner is `ubuntu` in §2 and §4 (40-lab-control.sh, 7ecbab0e); §3 origins cite the P-08 record.
