# Lab internal-testing rollout — the pilot box and a Vercel project for `apps/lab` (LAB-DEPLOY-PREP)

**Operator-run. Preparation only.** The LAB-DEPLOY-PREP lane wrote this runbook, the box steps
it calls (`infra/lab/rollout/`) and the E4-ON gate (`make lab-local`). It ran **nothing** against
the pilot box (`i-0e8449a4ffca29bab`), AWS, SSM, S3, Vercel or hosted Supabase; nothing here is a
live-state claim. Every value lives in the operator's stores: this page names **parameters and
settings by NAME only**. Every step names its proof; a step without its proof recorded did not
happen.

Scope: stand the Lab (provider product) up **beside** the launched App on the pilot box for
**internal testing** by named operators, with the App untouched. The App's own rollout is
[`infra/rollout/README.md`](../../../infra/rollout/README.md); the Lab's release pieces are
I2L's [`infra/lab/app/README.md`](../../../infra/lab/app/README.md) (names:
[`lab.json`](../../../infra/lab/app/lab.json)), I5/I6/I7's worker runbooks
(`infra/lab/workers/*/RUNBOOK.md`) and I2L-OBS (`infra/lab/observe/`).

```bash
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
export RELEASE=<the merged commit the App runs on the box>   # 40 hex; the Lab ships from the same checkout
export INSTANCE=i-0e8449a4ffca29bab
```

## 0. What internal testing can cover on this base (read first)

`make lab-local` (the E4-ON regression, §1 G3) composed everything with every switch ON and found
what works. On this base, **with no gateway switch turned on** (§5 rule 1):

| Lab surface | Served on the box by | Internal testing today |
|---|---|---|
| Sign-in, workspace selection, `/settings` | the Lab web + the 0030 session door (hosted Supabase) | **yes** |
| `/judge` (configure, budget, calibration, dry-run request) | the Lab web + 0036/0037 RPC doors | **yes** (dry_run only, §5 rule 4) |
| `/lab/v1/control` API (register, listings, smoke, proposals) | the control service (`infrx-lab-control`, :8003) | **yes, API only**; the Lab web's control pages (`/overview`, `/models`, `/deployments`) say "unavailable" until WR-E3L-J (lab-app-control lane) gives apps/lab an HTTP control adapter |
| `/requests` (traces) | the control service with `CLICKHOUSE_URL` + `S3_TRACE_BUCKET` | **no** until the trace projection is deployed (T2I/T3; not on the box) |
| `/datasets`, `/annotations`, `/training`, `/evaluations`, `/releases`, `/optimizations` | the gateway's Lab routes only (`LAB_DATASETS`, `LAB_PIPELINES`, `LAB_EVALS`, `LAB_RELEASES`) | **no**: those switches are NOT_SETTABLE on the hosted gateway (`deploy/preflight.py`). Needs WR-LDP-2 (mount them in the control factory, so the App gateway keeps every Lab switch OFF); then evals/pipelines/releases still answer "unavailable" until WR-B4-2 / WR-LAB2-4 / WR-R4-1/2 / WR-P4B-1 merge (E4-ON o05/o07) |
| Worker role `eval` | `infrx-lab-eval` | starts ready locally (E4-ON o03) — **on the owner login only**; on the box it needs its own login (WR-LDP-6, §3) |
| Worker roles `judge`, `datasets` | `infrx-lab-{judge,datasets}` | **no on this box**: each requires `CLICKHOUSE_URL` and `S3_TRACE_BUCKET` (`infrx.lab.workers` NEEDS) and the trace projection is not deployed; without them the entry point refuses (exit 2), so 50-lab-role.sh refuses the SPEC first. They start locally (o03) only because the composition gives every role a ClickHouse |
| Worker role `annotation` | `infrx-lab-annotation` | has its teacher-collect pass on this base (needs `LAB_S3_BUCKET` + `LAB_TEACHER_URL` = the local teacher fake only, P-10); not for internal testing (§5 rule 3) |
| Worker roles `checkpoints`, `training`, `rollout` | their units | **refuse by name** (exit 2, R198/R211) until WR-B3-3 / P-11 / WR-LSQ-9 |

E4-ON FAILs that block turning the gateway's Lab switches on (never do it on this base):
**LDP-F1** — with `ROLLOUT_ROUTING` ON the gateway must run on `infrx_runtime`, which holds no
grant on L2's `infrx.lab_*` RPCs, so every Lab route refuses (503) and **LDP-F3** — the datasets
route lets that store error escape as a 500 instead of the typed refusal. Evidence:
`research/plan/evidence/i/LAB-DEPLOY-PREP-<head7>.md`.

## 1. Go / no-go (all local, at `RELEASE`, before any AWS call)

| Gate | Command (repo root, checkout at `RELEASE`) | Pass = proof to record |
|---|---|---|
| G1 the tree is the release | `git rev-parse HEAD; git status --porcelain` | `$RELEASE`, empty |
| G2 the suite | `infra/rollout/README.md` G2 (`make check` on e2c + `tests/i`), then `cd apps/infrx-api && uv run --frozen pytest -q tests/i/lab` | exit 0 both |
| G3 E4-ON | `make lab-local` (≈45 min; holds E3L's e3l block under its lock) | `research/plan/evidence/e/E4ON-raw-<head7>/verdict.json`: o01, o02, o04, o06 PASS; every FAIL/NOT RUN is one of §0's named findings/lanes, nothing new |
| G4 the Lab web builds | `cd apps/lab && pnpm install --frozen-lockfile && pnpm build` | exit 0, `.next/BUILD_ID` |
| G5 P-08 inputs | 15-pending-inputs.md P-08 row: the Lab origin, the control origin, the Vercel project, the operator identities and roles, the internal-testing window | a dated P-08 record |
| G6 the App is healthy | `infra/rollout/steps/60-verify-local.sh` via `ssm.sh` (read-only) | its output green |

No go on any red row. G3's FAILs are acceptable **only** as the named findings of §0 (they are
all on switches this runbook keeps OFF).

## 2. Hosted Lab migrations — R151/R201's three conditions (coordinator host)

The Lab's tables and doors are migrations **0027–0051** (local-only until now, R151). The Lab on
the box reads them on hosted Supabase, so this is the one hosted write of the rollout. It needs
**all three** conditions; `infra/lab/rollout/lab-migrate.sh` refuses (exit 2, nothing dialled)
unless each holds.

1. **The known-good re-proof** (rollback targets stay KNOWN-GOOD past 0026). For each recorded
   target with a `schema_proof` (`4226315…`, `bda1586…` in `infra/rollout/known-good.json`),
   task-locally (never hosted):

   ```bash
   apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <target 40-hex sha> --candidate "$RELEASE" --task d10
   ```

   every line `PASS`, exit 0. Then **append** (never rewrite) a new record entry per target
   with `schema_proof.through: "0051"` and `files` = the sha256 of 0019–0051 at `RELEASE`
   (the KNOWN-GOOD-PROOF-3 shape), update `tests/i/test_known_good_proof.py`'s
   "not beyond" case to the new level (a reviewed change, I/D lanes), and check:

   ```bash
   apps/infrx-api/.venv/bin/python infra/rollout/known-good.py --list --applied 0051   # exit 0
   ```

   Proof: the proof run's output, the record diff, the `known-good.py` output.
2. **A new `EXPECTED_PENDING`** in `infra/rollout/hosted-migrate.sh`, as a reviewed patch
   (never edited in the window). Hosted is at 0026 (read it: `migrate.py plan`, §2 step b), so:

   ```diff
   -EXPECTED_PENDING="0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026"   # rollout.md W7: 0001-0018 -> 0001-0026
   +EXPECTED_PENDING="0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051"   # Lab window: 0001-0026 -> 0001-0051 (R151)
   -case "$HOSTED_APPLIED" in *"0018 terminal_settlement") ;; *) say "stop: hosted applied list does not end at 0018 terminal_settlement (unrecorded hosted change)"; exit 10;; esac
   +case "$HOSTED_APPLIED" in *"0026 fenced_result") ;; *) say "stop: hosted applied list does not end at 0026 fenced_result (unrecorded hosted change)"; exit 10;; esac
   -case "$POST" in *"0026 fenced_result"$'\n'"nothing pending") ;; *) say "W7: hosted is not 0001-0026 with nothing pending. restore.md A8"; exit 20;; esac
   +case "$POST" in *"0051 lab_import_jobs"$'\n'"nothing pending") ;; *) say "W7: hosted is not 0001-0051 with nothing pending. restore.md A8"; exit 20;; esac
   ```

   plus the two "0019–0026"/"0001-0026" `say` texts. `EXPECTED_FLAGS` is unchanged (it reads
   three named flags; the Lab migrations add no value to them — confirm on the W6b copy).
   Owner: the I/rollout lane (`infra/rollout/**` is not this lane's). Proof: the merged commit.
3. **An operator window tied to I2L/P-08**: a dated entry in the coordinator log naming the
   window, the operator, the P-08 record and "hosted 0027–0051 (Lab), never reverted". Proof:
   that entry; its reference is `--window`.

Then, in the window (the App keeps serving: these migrations are additive and the App's code
reads none of the Lab tables; still hold the deployment lock of `infra/README.md` §1):

```bash
# a. W6 + W6b on a fresh hosted dump, no hosted write
infra/lab/rollout/lab-migrate.sh --release "$RELEASE" --hosted-at 0026 --window "<P-08 window ref>" --through w6b
# b. the COPY_DIGEST it printed, then the hosted apply
infra/lab/rollout/lab-migrate.sh --release "$RELEASE" --hosted-at 0026 --window "<P-08 window ref>" --through w7 --expect <COPY_DIGEST>
```

`hosted-migrate.sh` runs unchanged underneath (its exits: 10 stopped before any hosted write,
20 after one — `restore.md` A8). **Caveat**: its W7 precondition checks the public `/health` is
503 (maintenance), i.e. it is built for a window with the App in maintenance. Either run it in
an App maintenance window (`infra/rollout/steps/95-maintenance.sh`, then `56-resume.sh`), or
the I lane amends that check for additive Lab-only windows (WR-LDP-4). Proof: the w6b log
(`COPY_DIGEST`, `W6b PASS`), the w7 log (`W7 PASS: hosted 0001-0051, nothing pending`).

After it: `infra/runbooks/schema_proof.py <target> --candidate "$RELEASE"` with
`SCHEMA_PROOF_DSN` (read-only) against hosted, before relying on the proof
(`infra/rollout/README.md` §2).

## 3. SSM parameters (names; values put by the operator, never typed on a command line)

Create each as the App's are (`read -rs V; umask 077; printf '%s' "$V" > ~/.p; aws ssm put-parameter --name <NAME> --type SecureString --value file://$HOME/.p; shred -u ~/.p`),
then check with `aws ssm describe-parameters --parameter-filters Key=Name,Values=<NAME> --query 'Parameters[].[Name,Type,Version]'` (never the value).

| Parameter (proposed name) | Holds | Read by |
|---|---|---|
| `/model-inference/lab/control_database_url` | the control service's own login DSN (`infrx_lab_control`, 0044; transaction pooler :6543) | 40-lab-control.sh → `INFRX_LAB_DATABASE_URL` |
| `/model-inference/lab/supabase_anon_key` | the project's publishable anon key (not a secret, kept with the rest) | 40-lab-control.sh → `INFRX_LAB_SUPABASE_ANON_KEY` |
| `/model-inference/lab/<role>_database_url` (eval; judge, datasets once the traces exist) | each role's own Lab login DSN (:6543; never the runtime's `DATABASE_URL`, never the owner) | 50-lab-role.sh → `LAB_DATABASE_URL` |
| `/model-inference/lab/eval_endpoint_key` | the provider_dev key the eval role meters its dev endpoint with (a provider_dev wallet: starts at 0 CREDIT, funded only through the audited operator path) | 50-lab-role.sh → `LAB_EVAL_ENDPOINT_KEY` |
| `/model-inference/lab/clickhouse_url` | only when the trace projection exists (judge, datasets roles; both **require** it with `S3_TRACE_BUCKET`, a literal bucket name) | 50-lab-role.sh → `CLICKHOUSE_URL` |

The role logins: one per role, created by the lab-sql lane's role migration or the operator
(`grant` shape as 0021's dedicated logins); until a role has its own login, **do not** switch it
on with the owner DSN. **No such login exists yet** (WR-LDP-6): E4-ON proves the roles only on the
owner login (o03), and the control factory on `infrx_lab_control` (0043/0044, o04 since the fix
round). So L5's proof is E4-ON o04 on `infrx_lab_control`; L7 waits for WR-LDP-6 and an E4-ON run
whose o03 uses those logins. Proof: each `describe-parameters` line.

## 4. The box, in order (each step through `infra/rollout/ssm.sh`, as root)

| # | Step | Command | Proof (record the output) |
|---|---|---|---|
| L1 | read-only inventory | `infra/rollout/ssm.sh infra/lab/rollout/steps/10-lab-preflight.sh RELEASE=$RELEASE` | `control switch: off`, no Lab env file, App :8001/:8002 200 |
| L2 | hosted Lab migrations | §2 (coordinator host) | `W7 PASS: hosted 0001-0051` |
| L3 | the Lab image | `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/lab/rollout/steps/20-lab-image.sh RELEASE=$RELEASE` | `Lab image sha256:… (infrx-lab:$RELEASE)` |
| L4 | the units (inert) | `infra/rollout/ssm.sh infra/lab/rollout/steps/30-lab-units.sh RELEASE=$RELEASE` | `installed infrx-lab-*.service` lines, `none enabled` |
| L5 | the control service ON | `infra/rollout/ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=on RELEASE=$RELEASE CONTROL_DSN_PARAM=/model-inference/lab/control_database_url ANON_KEY_PARAM=/model-inference/lab/supabase_anon_key SUPABASE_URL=https://<project-ref>.supabase.co LAB_ORIGIN=https://lab.callbill.ai` | `wrote /etc/infrx-lab-control.env: INFRX_LAB_IMAGE INFRX_LAB_DATABASE_URL …`, `control ON: 127.0.0.1:8003/readyz 200` |
| L5s | smoke | `infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh` | `PASS App gateway`, `PASS App worker`, `PASS Lab control` |
| L6 | the control origin on the edge | `infra/rollout/ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=on RELEASE=$RELEASE` (DNS A record `lab-control.callbill.ai` → the box first, P-08) | `Lab site installed and the edge reloaded`; then from the host: `curl -s -o /dev/null -w '%{http_code}' https://lab-control.callbill.ai/lab/v1/control/models` = 401 (no session) and with `-H 'Authorization: Bearer sk-x'` = 401 `invalid_audience` |
| L6s | smoke | 60-lab-smoke.sh again + `infra/rollout/verify-external.sh` (the App's external checks) | all PASS; App external `failures: 0` |
| L7 | **`eval` only on this box** (after WR-LDP-6 gives it its own login); `judge`, then `datasets` only once `CLICKHOUSE_URL` and `S3_TRACE_BUCKET` exist (the trace projection deployed) | `infra/rollout/ssm.sh infra/lab/rollout/steps/50-lab-role.sh STATE=on ROLE=eval RELEASE=$RELEASE SPEC="LAB_DATABASE_URL=/model-inference/lab/eval_database_url LAB_S3_BUCKET:=<Lab bucket> LAB_EVAL_ENDPOINT_URL:=https://marlin2b.callbill.ai/v1 LAB_EVAL_ENDPOINT_KEY=/model-inference/lab/eval_endpoint_key"` (later, judge: `JUDGE_PROVIDER_URL:=<P-10 approved host> CLICKHOUSE_URL=/model-inference/lab/clickhouse_url S3_TRACE_BUCKET:=<trace bucket> JUDGE_MODE:=dry_run`; datasets: `LAB_S3_BUCKET:=… CLICKHOUSE_URL=/model-inference/lab/clickhouse_url S3_TRACE_BUCKET:=…`). The step refuses (exit 2, before any change) a SPEC without a name the role needs | `wrote /etc/infrx-lab/eval.env: INFRX_IMAGE LAB_DATABASE_URL …`, `eval ON: 127.0.0.1:8012/readyz 200`; the pooler budget printed no FAIL |
| L7s | smoke after **each** role | 60-lab-smoke.sh | the new role PASS, App PASS |
| L8 | record | release, Lab image id, parameter names + versions, which switches are ON, the smoke outputs | coordinator log entry |

Exit codes the steps share: 2 refused before any change; 3 a preflight/budget refusal (nothing
replaced); 4 started but not ready, or a served role (`eval`, `judge`, `datasets`, `annotation`)
exited 2 refusing its settings (the unit's journal names them; the step's `STATE=off` turns it
back off); 5 a pending role refused by name (R198: its work source is not on this release —
`checkpoints`, `training`, `rollout` today; R211: it is not restarted). Every step
is idempotent and logs to `/var/log/infrx-lab-rollout.log`.

## 5. Switch-on order and rules

1. **The App gateway's switches stay OFF** (`FEEDBACK_API`, `TRACE_EXPORT_API`, `LAB_CONTROL`,
   `LAB_TRACES`, `ROLLOUT_ROUTING`, `LAB_EVALS`, `LAB_PIPELINES`, `LAB_RELEASES`, `LAB_DATASETS`,
   `LAB_CHECKPOINTS`; and `TRACE_PUMPS`, `LAB_EVAL_WORKER` on the consumer worker). They are
   NOT_SETTABLE by `install.sh --set` on purpose, E4-ON found LDP-F1/F3 with them ON, and the Lab
   is served by its own units. `LAB_TEACHERS` (composition-4, now on the base) stays OFF too.
2. Order: L5 control → L6 site → L7 `eval` (then `judge` → `datasets` only once the trace
   projection gives them `CLICKHOUSE_URL` + `S3_TRACE_BUCKET`). The smoke (60) after each;
   any FAIL = that switch OFF again (its `STATE=off`) before anything else.
3. Never switch on `checkpoints`, `annotation`, `training`, `rollout` for testing:
   `checkpoints`/`training`/`rollout` refuse by name (exit 5 here) until their lanes land;
   `annotation` only collects approved teacher batches from the local fake (P-10).
4. Paid work stays off: `JUDGE_MODE` is `dry_run` (50-lab-role refuses anything else), the
   annotation teacher is `dry-run`, the training connector `manual-bundle` (P-10/P-11 approvals
   are separate). Lab paid work is USD with a named payer; CREDIT is never converted.

## 6. The Lab web: its own Vercel project (P-08)

| Setting | Value |
|---|---|
| Project | `infrx-lab` (new; the App's project is not touched) |
| Root directory | `apps/lab`; "Include files outside the root directory" ON (it imports `packages/shared` by `file:`) |
| Framework / install / build | Next.js; `pnpm install --frozen-lockfile`; `pnpm build`; Node ≥ 22.18 |
| Production domain | `lab.callbill.ai` (⚠️ TO BE VERIFIED by P-08) |
| Env (Production) | `NEXT_PUBLIC_LAB_URL=https://lab.callbill.ai`, `NEXT_PUBLIC_SUPABASE_URL=<the App's project URL>`, `NEXT_PUBLIC_SUPABASE_ANON_KEY=<its publishable key>`; server-only: `LAB_TRACES_API_URL=https://lab-control.callbill.ai` (once traces are served), `LAB_DATASETS_API_URL`, `LAB_EVALS_API_URL`, `LAB_PIPELINES_API_URL`, `LAB_RELEASES_API_URL` = the same control origin **after WR-LDP-2** (unset until then: those pages say "unavailable") |
| Never | `SUPABASE_SERVICE_ROLE_KEY` or any other secret; `LAB_CONTROL_PREVIEW` / `LAB_PIPELINES_PREVIEW` (dev only, ignored in production) |

`NEXT_PUBLIC_*` are inlined at build: set them before the first production build, and rebuild
after a change. Supabase Auth (the App's project, P-05 settings): **Site URL unchanged**; add to
Redirect URLs `https://lab.callbill.ai/auth/callback**` (and the preview pattern
`https://infrx-lab-*-humanbit.vercel.app/auth/callback**` only for a staging project). The App's
`REDIRECT_ALLOWLIST.production` (`apps/app/lib/deploy/env.ts`, I2A-AUTH-03/04) gains the Lab
entry in the same change (App lane). The Lab's session cookie is `sb-infrx-lab-auth`, host-only.
Proof: the Vercel deployment id, the domain's TLS answer, `https://lab.callbill.ai/` rendering
the sign-in form, the Redirect URLs screenshot/export.

## 7. Operators and memberships (P-08)

1. Each internal tester signs up **in the App** (signup and recovery are the App's; the Lab
   verifies sign-in links only) and verifies the email. A provider membership is not a consumer
   grant: the tester's consumer account keeps its one-time 10,000 CREDIT as any user's.
2. The internal-testing provider: the seed's NemoStation (`b0000001-0000-4000-8000-000000000001`)
   or a new provider org (P-08 decides). New org, as the owner login in the window
   (`read -rs OPERATIONS_DATABASE_URL`, never typed):

   ```sql
   insert into infrx.provider_orgs (slug, display_name, created_by)
   values ('infrx-internal', 'infrx internal testing', 'operator:<name> P-08 <window ref>');
   ```
3. One membership per tester (roles: `viewer`, `developer`, `administrator`; no browser role
   can write this table):

   ```sql
   insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
   values ('<provider_org_id>', (select id from auth.users where email = '<tester email>'),
           'developer', 'operator:<name> P-08 <window ref>');
   ```
   Revocation is the one-way update: `update infrx.provider_memberships set revoked_at = now()
   where membership_id = '<id>'` — the next Lab call is refused (E3L l01).
4. Proof: `select provider_org_id, user_id, role, granted_at from infrx.provider_memberships
   where revoked_at is null` (ids only), and each tester seeing exactly their workspace on
   `https://lab.callbill.ai/` (the 0030 door).

## 8. Internal testing checklist

What a tester does (signed in on `https://lab.callbill.ai` with their own account):

1. Sign in by email link; the workspace list shows exactly the provider(s) granted in §7.
2. `/settings`: role and capabilities match the membership.
3. `/judge`: configure a judge, set a USD budget with a named payer, request a **dry-run** run;
   the plan shows the ceiling against the budget; nothing is sent.
4. Control API (developer/administrator, with the session token the Lab web holds — a `curl`
   by the operator on the tester's behalf): `GET https://lab-control.callbill.ai/lab/v1/control/models?provider_org_id=<id>`
   answers 200 with the provider's rows; another provider's id is 404; a consumer key is 401.
5. Negative checks: signed out, every Lab page shows the sign-in form; a consumer API key on
   the control origin is 401 `invalid_audience`; the App (`app.callbill.ai`, the public API)
   behaves exactly as before.

What to record per session: UTC time, tester (role, not email), page/route, expected vs seen,
the `request_id` of any error, a screenshot of any "unavailable" page. Feedback path: one issue
per finding in the repository tracker labelled `lab-internal-testing` (title `[lab] <page>:
<summary>`), linked from the coordinator log; blocking findings (data from another provider,
a consumer key accepted, any App change) stop testing and trigger §9 at once.

## 9. Rollback (switches first)

```bash
infra/rollout/ssm.sh infra/lab/rollout/steps/90-lab-revert.sh
```

In order: every role OFF (unit disabled, its env file — the switch — removed), the control
service OFF (disabled, marker and env file removed), the Lab site removed from the edge and the
edge reloaded, then the App's :8001/:8002 `/readyz` (exit 1 if the App does not answer — it never
depended on the Lab). Units and the Lab image stay installed, inert. One switch only:
`50-lab-role.sh STATE=off ROLE=<r>`, `40-lab-control.sh STATE=off`, `45-lab-site.sh STATE=off`.
The Lab web: pause the `infrx-lab` Vercel project or remove its production domain (the App's
project is not touched). Memberships: revoke (§7.3).

**Migrations are not rolled back.** 0027–0051 are additive (new tables, functions, doors); the
App never reads them. A rollback of the App after the Lab window goes to a `known-good.json`
target whose `schema_proof` reaches 0051 (§2 condition 1) — that is why condition 1 exists; if
none qualifies, `infra/rollout/steps/95-maintenance.sh` (R3) is the fallback. A destructive
reversal of Lab tables is never part of this runbook.

## 10. Wiring requests and findings this runbook depends on

- **WR-LDP-1** (Lab app lanes): `apps/lab/tests/{r,p,b,v/list}/backend.py` accept `INFRX_D_TASK=lab-on`
  so `make lab-local` runs their real-route journeys on its own key (today NOT RUN, key-pinned).
- **WR-LDP-2** (composition lane): mount `lab_datasets`, `lab_evaluations`, `lab_pipelines`,
  `lab_releases` in `infrx.lab.control.app` (the gateway's `pilot._lab`/`_lab_2` composition on
  `INFRX_LAB_DATABASE_URL`), so the box serves every Lab family from the control unit and the
  App gateway keeps every Lab switch OFF (WR-I2L-2b's rule).
- **WR-LDP-3** (I2L owner): declare in `infra/lab/app/lab.json` `lab-workers` the names the
  entry point reads that are not listed there (`LAB_S3_PREFIX`, `LAB_EVAL_ENDPOINT_URL`,
  `LAB_EVAL_ENDPOINT_KEY`, `LAB_EVAL_CONCURRENCY`, `LAB_CHECKPOINTS_CONCURRENCY`,
  `LAB_DATASETS_CONCURRENCY`; the judge's are in `infra/lab/observe/observe.json`).
- **WR-LDP-4** (I/rollout lane): the reviewed `hosted-migrate.sh` patch of §2 condition 2 —
  **landed on the tip** (bcb73cc1; its post-check is `*"0051 lab_import_jobs"$'\n'"nothing
  pending"`, the form `lab-migrate.sh` checks) — and its W7 maintenance precondition for an
  additive Lab-only window (open).
- **WR-LDP-6** (lab-sql lane): one dedicated login per Lab worker role (`infrx_lab_eval`, then
  `infrx_lab_judge`, `infrx_lab_datasets`), noinherit, a connection limit, each granted exactly
  what its `infrx.lab.workers` composition calls (as 0043 does for `infrx_lab_control`), and
  `lab_world.role_env` switched to them so E4-ON o03 proves the roles on the box's logins. Until
  then o03 is proven on the owner login only and L7 does not run.
- **LDP-F1** (lab-sql + composition): with `ROLLOUT_ROUTING` ON the gateway runs on
  `infrx_runtime`, which holds no EXECUTE on L2's `infrx.lab_*` RPCs → every gateway Lab route
  503. Either grant the membership/read RPCs to `infrx_runtime` or serve the Lab only from the
  control unit (WR-LDP-2).
- **LDP-F2 = WR-E3L-J** (lab-app-control lane, running): apps/lab has no HTTP control adapter.
- **LDP-F3** (G/datasets owner): `lab_datasets` lets a store `InsufficientPrivilege` escape as a
  500; the other Lab families answer the typed 503.

## 11. Proof table

| Step | Proof | Where it is recorded |
|---|---|---|
| G1–G6 | commands + exit codes; `E4ON-raw-<head7>/verdict.json` | coordinator log |
| §2 (1) | `schema_proof.py` output, `known-good.json` diff, `known-good.py --list --applied 0051` exit 0 | evidence/i/KNOWN-GOOD-PROOF-4-* |
| §2 (2) | the merged `hosted-migrate.sh` patch (bcb73cc1); `lab-migrate.sh` stops at condition 1, not 2, on the tree (`test_ldp__todays_hosted_migrate_carries_the_reviewed_patch`) | its commit |
| §2 (3) | the window entry | coordinator log |
| §2 a/b | `W6b PASS: COPY_DIGEST=…`, `W7 PASS: hosted 0001-0051` | `~/infrx-backups/migrate-*.log` + coordinator log |
| §3 | `describe-parameters` name/type/version | coordinator log |
| L5 | E4-ON o04 PASS with the control factory on `infrx_lab_control` (fix round) + L5's printed lines | verdict.json + coordinator log |
| L7 | WR-LDP-6 merged + E4-ON o03 PASS on the per-role logins (NOT RUN today) | verdict.json |
| L1–L7 | each step's printed lines (names only) + `60-lab-smoke.sh` after each | `/var/log/infrx-lab-rollout.log` + coordinator log |
| §6 | Vercel deployment id, domain, Redirect URLs | P-08 record |
| §7 | the membership select (ids), each tester's workspace list | P-08 record |
| §8 | the per-session records and issues | `lab-internal-testing` issues |
| §9 | `90-lab-revert.sh` output ending `Lab reverted; the App answers ready` | coordinator log |

## Verification log

- 2026-09-29: written by LAB-DEPLOY-PREP at the commit that carries it. The steps are tested
  against a box stand-in (`apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py`, their mutants
  in `tests/i/lab/mutants.py`); the E4-ON composition is `make lab-local`
  (`tests/integration/lab_local/`). Nothing was run against the box, AWS, SSM, Vercel or hosted
  Supabase.
- 2026-09-29 (fix round, LDP-R1/R3/R4/RSI-1): §2 condition 2 is on the tip (bcb73cc1) and
  condition 1's re-proof too (aafd387e); `lab-migrate.sh` checks the post-check in migrate.py
  plan's `NNNN name` form. §0/§4 L7/§5: judge and datasets require `CLICKHOUSE_URL` and
  `S3_TRACE_BUCKET`, so this box runs `eval` only; step 50 refuses a SPEC missing a needed name
  and keeps exit 5 for the pending roles. §3/§10/§11: the control factory is proven on
  `infrx_lab_control`, the roles only on the owner login until WR-LDP-6. `LAB_TEACHERS` is on
  the base (OFF on the box). Nothing was run against the box, AWS, SSM, Vercel or hosted Supabase.
