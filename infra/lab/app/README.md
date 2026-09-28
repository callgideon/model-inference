# Lab release runbook — `apps/lab` and the Lab control service (I2L)

**Disabled by default. Operator-run, preparation only.** The lab-operate lane wrote this runbook,
the unit and site it installs (`apps/infrx-api/deploy/lab/app/`) and the names manifest
([`lab.json`](lab.json)). It changed nothing on Vercel, hosted Supabase, DNS, AWS or the pilot
box. Nothing here is a live-state claim. **Hosted staging needs P-08** (the hosted Lab origin and
operator identity, 15-pending-inputs.md); until P-08 is supplied every step marked **[OP]** stays
unrun and `lab.json` keeps `"enabled": false`.

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

Lab control (`/etc/infrx-lab-control.env`, mode 0600, root-owned) [OP]:

| Name | Exposure | Value |
|---|---|---|
| `INFRX_LAB_IMAGE` | unit | the Lab control release, a content-addressed image id (`sha256:…`) |
| `INFRX_LAB_DATABASE_URL` | **secret** | the control service's own database login (L3-SQL names the role); never the runtime's `DATABASE_URL` |
| `INFRX_LAB_SUPABASE_URL` | server | the project whose Lab session tokens it verifies |
| `INFRX_LAB_ORIGIN` | server | the Lab web origin whose sessions it accepts |

Lab edge: `INFRX_LAB_CONTROL_SITE` (the control origin's address; default the placeholder in
`lab.json`) and `INFRX_LAB_CONTROL_UPSTREAM` (local drill only; unset on the box).

## 3. Origins and the auth allowlist (P-05 settings, per project) [OP]

- Lab web: `https://lab.callbill.ai`; control: `https://lab-control.callbill.ai`. ⚠️ TO BE VERIFIED
  (P-08 decides both; DNS A record of the control origin → the backend box).
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
sudo install -m 0600 -o root -g root /dev/stdin /etc/infrx-lab-control.env < lab-control.env
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
