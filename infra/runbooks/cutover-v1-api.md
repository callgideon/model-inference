# Cutover to the API-first v1 — the App and the Lab as thin clients (register row 99)

The order and the names only; every value lives in the operator's stores (SSM parameters, Vercel
project env), never here. Each step is reversible and is recorded in the window log
(`~/infrx-e4c/window-<utc>/` on the coordinator host). Written 2026-10-02 at the batch-3
integration (`39065ef0`); the release that ships is the one the window installs, named in
the log. Operator-run where marked [OP]: the auto-mode classifier refuses host and deploy
actions to the coordinator session.

## 0. Preconditions (all four, in this order)

1. **Hosted schema at 0068.** The R151 window applied 0060–0068 (`infra/runbooks/r151-0060-0068.md`,
   prepared by the r151-prep lane; its branch merges only inside the window). Hosted migrate's
   post-check PASS is in the window log. Until then every `/console/v1/*`, `/auth/v1/*` and new
   `/lab/v1/*` family answers 503 `dependency_unavailable` by design.
2. **Hosted compute on Medium** (operator, 2026-10-02) — Large after the v1 launch is tested.
3. **The box serves a release carrying R273's probe** (`gateway/readiness.py`), installed by a
   consumer window (`infra/rollout/README.md`; `MIGRATION_DIGEST=<the R151 digest>` once step 1
   is done, `nothing-pending` before it).
4. **BACKEND-READY** from a new qualifying E4C run on that release (05 §7); the run needs the
   `pg_stat_activity` sampler step (register row 102) if the soak is to be diagnosable.

## 1. Console and identity on the gateway [OP, the consumer window]

Names in `/etc/marlin2b-gateway.env` on the box (values from SSM by `preflight.py`; every one is
pinned NOT_SETTABLE until this step, `deploy/preflight.py`):

| Name | Value shape | Why |
|---|---|---|
| `CONSOLE_READS=1`, `CONSOLE_ACTIONS_API=1`, `CONSOLE_DATA_USE=1` | switches | the App's reads, actions and data-use doors |
| `CONSOLE_DATABASE_URL` | the authenticator-like login's DSN (SSM `/model-inference/console_database_url`) | the console API's own login, never the service role |
| `CONSOLE_CURSOR_SECRET` | ≥ 32 bytes (SSM `/model-inference/console_cursor_secret`) | opaque list cursors (R270) |
| `IDENTITY_API=1`, `AUTH_FACADE=1` | switches | sessions and the auth facade (AP-01) |
| `SUPABASE_ANON_KEY` | the project's anon key (public value) | the facade's GoTrue calls |
| `WEB_ORIGINS` | `https://app.callbill.ai` (and the Lab origin once §3 runs) | redirect_to and Origin checks |
| `AUTH_CAPTCHA_REQUIRED=1`, `AUTH_CAPTCHA_PROVIDER`, `AUTH_CAPTCHA_SITE_KEY` | P-05's choice (public values; the secret by SSM name) | LR-02 at every password door |

Gate: `GET /auth/v1/availability` answers `configured` with the captcha block; `GET /console/v1/me`
with a verified session answers 200; a consumer API key on `/console/v1/data-use` answers 401 (R272).
Stop rule: any 503 on these after the window = the switches go back off (same step, values removed)
before the App ships.

## 2. The App [OP, Vercel project app.callbill.ai, team callgideon]

1. `vercel env rm` the service-role key and every `NEXT_PUBLIC_SUPABASE_*` the App still carries
   (the App holds no product DB access since merge #97; `apps/app/tests/boundary` proves it).
2. Set `INFRX_API_BASE_URL=https://marlin2b.callbill.ai` (the gateway with §1 on).
3. Deploy the main release. Every user signs in once after the cutover (old `sb-*` cookies are
   ignored). The two-user real consumer journey (review 26 §4 Step C) is the acceptance.
4. Rollback = redeploy the previous Vercel deployment and put the removed env back; §1 stays.

## 3. The Lab [OP, the Lab unit then Vercel lab.callbill.ai]

1. The Lab unit's env (`infra/lab/app/README.md` §2 names): `AUTH_FACADE=1`, `IDENTITY_API=1`,
   `LAB_JUDGE_API=1`, `WEB_ORIGINS=https://lab.callbill.ai`, its own publishable key; the
   dedicated role logins (0068's `infrx_lab_datasets` on the :6543 pooler DSN, register row 103)
   only when their workers are switched on. Restart the unit (`infra/lab/rollout/lab-release.sh box`).
2. Vercel: `vercel env rm` the six per-family `LAB_*` URL names and `NEXT_PUBLIC_SUPABASE_URL`/
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`; set `LAB_API_URL=https://lab-control.callbill.ai`.
3. Deploy the Lab (`lab-release.sh web`), then `members` for the testers. Gate: sign-in through
   `/auth/v1/*`, `/lab/v1/workspaces` lists the tester's workspace, the request page shows judge runs.
4. Rollback = previous deployment + the env put back; the unit's switches off.

## 4. After both cutovers

- `LAB_PUBLICATION`, `LAB_ARTIFACTS`, `LAB_HOSTING`, `LAB_TRACE_DATASETS` stay off until their own
  gates (register rows 97, 103, 105; P-10 for live judging; a GPU window for AP-05's real smoke).
- Public signup (LR-02 closure) is its own recorded, reversible change after the two-user journey.
- Record the window in the register and `STATUS.md`; the tracker's deployed row moves.

## Verification log

- 2026-10-02: written by the coordinator from the batch-2/3 handbacks (api-frontends-app WR-AP09-DEPLOY, api-frontends-lab's Vercel list, api-identity-2's settings, api-schema-3's WR-AS3-3); nothing run.
