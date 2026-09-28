# Lab annotation and training workers (I6) - runbook, OFF by default

Scope: the two Lab worker roles of LAB-M3. Each is its own process and systemd unit, and
each follows I5's shape (`infra/lab/workers/eval/RUNBOOK.md`: bounds, OFF until
`/etc/infrx-lab/<role>.env` exists, no consumer coupling, drain on SIGTERM) and I2L's switch
(`infra/lab/app/README.md`): a unit also needs the Lab-wide marker `/etc/infrx-lab/enabled`,
so the Lab's Disable (removing the marker) keeps every Lab role down.

| role | work | unit | health port | adapter (default) | approval |
|---|---|---|---|---|---|
| `annotation` | P1 imports, reviews, exports; P2 teacher labels | `apps/infrx-api/deploy/lab/pipelines/infrx-lab-annotation.service` | 8014 | `LAB_ANNOTATION_TEACHER` (`dry-run`) | P-10 |
| `training` | P3 bundles, connector submit / reconcile / poll / cancel, checkpoint imports | `.../infrx-lab-training.service` | 8015 | `LAB_TRAINING_CONNECTOR` (`manual-bundle`) | P-11 |

**Default: OFF, local and manual only.** `egress.json` is empty: P-10 (a live teacher,
its USD rates and egress) and P-11 (an automatic training connector and its paid terms) are
not approved (`research/plan/15-pending-inputs.md:20-21`). Dry-run labels and the manual
export -> external run -> checkpoint import workflow need no external secret. Nothing here is
executed against staging or the pilot box before the operator allocates a Lab window (P-08).

## 1. Entry point (the composition batch, as WR-B-5 for I5)

`python -m infrx.lab.workers annotation|training`: reads only its env file; answers `/livez`,
`/readyz`, `/metrics` on `127.0.0.1:$LAB_WORKER_HEALTH_PORT`; exits 2 naming a missing
setting. The training role builds its connector from `LAB_TRAINING_CONNECTOR` alone and passes
`advertised = {manual-bundle}` plus that one name to P3 (`infrx.pipelines.training.submit`), so
no other adapter is ever reachable from a run. Every connector call is a plain
`httpx.AsyncClient(base_url=LAB_TRAINING_CONNECTOR_URL)` (environment proxies trusted), so
the unit's egress deny applies.

## 2. Security: settings, secrets and egress

Threat model: the env file's author (ubuntu or root; ubuntu is in the docker group, so
root-equivalent) is trusted but fallible. The checks stop a mistake or a one-line edit from
enabling paid work, widening egress or leaking a secret; they are not a boundary against
that account.

Before each start `python3 -I preflight.py --role <role>` (the unit's `ExecStartPre`, as
ubuntu, from the deployed checkout; `-I` because `EnvironmentFile=` reaches every Exec line,
so no `PYTHON*` setting steers the checker) refuses the start, naming the setting and never
its value, when:

* the env file is readable by another account (not 0600/0400);
* systemd and docker could read a line differently: docker's `--env-file` fills the container
  while systemd's `EnvironmentFile=` fills `${INFRX_IMAGE}` and `${LAB_EGRESS_ALLOW}` in
  argv, and systemd joins a line ending in `\` to the next and unquotes a value (one stray
  backslash hid `INFRX_IMAGE=--privileged` and `LAB_EGRESS_ALLOW=*` from the old check). So a
  line with a backslash (comments too), a value with a quote, a control character or
  surrounding space, and a name set twice are refused by line number; and every setting must
  equal the value `EnvironmentFile=` gave the preflight itself (`ExecStartPre` runs in the
  unit's environment; `-I` keeps it), so the values checked are the values run. A DSN whose
  password holds a quote or backslash is refused: rotate it;
* a setting is not the role's own (a consumer secret, another purpose's token, `AWS_*`, any
  `*_proxy` in any letter case, `DOCKER_HOST` and other settings that steer the unit's docker
  CLI, `PYTHONPATH`, `LD_PRELOAD`, `SSL_CERT_FILE`), a line is a bare name, or a line's name
  is not an identifier (a pasted DSN: refused by line number, never printed);
* `INFRX_IMAGE` is not the release's local image id `sha256:<64 hex>` (a docker flag, an
  empty value - docker would run the public `python` - or a registry reference; the unit
  also runs `--pull never`, so the daemon never pulls: registry egress outside the deny);
* the default adapter carries an endpoint, token, budget or payer (no one-line enable);
* a non-default adapter has no `egress.json` entry for this role, an endpoint other than
  `https://<approved host>`, an absent or empty token (**a failed secret lookup refuses; the
  worker never falls back to another adapter**), a budget that is not a finite USD amount
  above 0 and within the approval's, or a payer other than the approval's named one;
* `LAB_EGRESS_ALLOW` holds anything but the object store's host (`LAB_S3_ENDPOINT`, named
  explicitly, the regional endpoint on AWS), `169.254.169.254` or the enabled adapter's
  approved host.

Object-store credentials: the instance role, through botocore's metadata lookup, and nothing
else (`AWS_*` is refused, so no long-lived key sits in an env file). botocore sends that
lookup through the environment's proxy, so it reaches the metadata address only when
`LAB_EGRESS_ALLOW` names `169.254.169.254` exactly (no range, port or name). The units use
`--network host`, so the container is the instance's own hop and IMDSv2's default hop limit
of 1 suffices; any process in the container with the entry can read the role's credentials,
so the role's policy must name the Lab bucket only. Without the entry every object-store call
fails closed.

Egress deny: the unit points `HTTP(S)_PROXY` (both cases) at `127.0.0.1:9` (privileged, so no
unprivileged process can become the proxy) with `NO_PROXY` = `LAB_EGRESS_ALLOW`. An
unlisted host is a connection error, and P3 turns an unknown submit outcome into
`ambiguous` with the reservation held, never a retry. **Residuals** (owed from staging, the
operator's P-08 decision):

* the deny guards HTTP clients that trust the environment; the units use `--network host`
  (I5's), so a raw socket is not stopped - a network-level rule (an egress proxy or an
  nftables owner match on uid 10003) closes it;
* httpx reads a `NO_PROXY` host as that host **and its subdomains** (0.28.1:
  `all://*<host>`), so allowing the regional S3 endpoint also allows virtual-hosted URLs of
  any bucket (`<bucket>.s3.<region>.amazonaws.com`), as path-style URLs are anyway: a host
  allowlist cannot pin the bucket; an S3 VPC endpoint policy naming the Lab bucket does;
* the instance-role path (IMDSv2 token and credentials through `NO_PROXY`, hop limit 1 under
  host networking, a role scoped to the Lab bucket) is proven locally only as botocore's
  proxy decision; a real object-store call from the running container is owed;
* `docker inspect` shows the container's environment (the token) to the docker group, as
  for every consumer unit.

Hardening of every unit (checked as shipped, `tests/i/lab_pipeline/test_units.py`): exactly
the listed `docker run` flags - no mount, device, socket, added capability, `--privileged`,
host namespace, seccomp/AppArmor opt-out or DNS/hosts override; `--cap-drop ALL`,
`no-new-privileges`, read-only root, a noexec/nosuid/nodev tmpfs, uid 10003 in its own group
10003 (I2L's; not the runtime's 10000), `--pull never`, and only `INFRX_IMAGE` and
`LAB_EGRESS_ALLOW` expanded into argv (never a secret: argv is readable in `ps`).

Secrets: purpose-specific (`LAB_ANNOTATION_TEACHER_TOKEN`, `LAB_TRAINING_CONNECTOR_TOKEN`),
only in that role's env file (ubuntu, 0600 - the preflight refuses a readable one; docker
reads `--env-file` as the unit's user),
never in argv, logs or this repository. Rotation = rewrite and restart; revocation = empty the
token and restart (the preflight refuses the start, so nothing runs on a stale credential).

## 3. Enable one role (staging window only)

1. Record the approval in `egress.json` as a reviewed commit (adapter, host, approval record,
   `payer_ref`, `budget_usd`) and deploy that commit; never edit the box's checkout.
2. Budget the pooler: `infra/lab/workers/eval/pool_budget.py` (wiring WR-I6-2 adds these
   roles to its `ROLES`), then write `/etc/infrx-lab/<role>.env`.
3. `sudo systemd-run --wait --pipe -q --uid ubuntu -p EnvironmentFile=/etc/infrx-lab/<role>.env /usr/bin/python3 -I /home/ubuntu/model-inference/infra/lab/workers/training/preflight.py --role <role> --env-file /etc/infrx-lab/<role>.env`
   prints `PASS <role>` (systemd's reading of the file, as `ExecStartPre` gets it; a plain
   `python3 preflight.py` refuses because its environment is not the unit's); then install the unit (I5 runbook §2 step
   3), make sure I2L's marker exists (`sudo install -d -m 0755 /etc/infrx-lab && sudo touch
   /etc/infrx-lab/enabled`, I2L's Enable), `enable --now` it and
   `curl -fsS 127.0.0.1:<port>/readyz`.

Roll back one role: `sudo systemctl disable --now infrx-lab-<role> && sudo rm
/etc/infrx-lab/<role>.env`. Every Lab role at once: I2L's Disable (`sudo rm -f
/etc/infrx-lab/enabled`, then stop the units; none restarts without the marker).
Nothing is lost: runs stay in their D8 state, reservations stay held, and they are reconciled
when the role is enabled again.

## 4. Operator actions

* **Unknown submission** (`ambiguous`, or `submitting` after a crash): visible in the run list
  and `infrx_lab_external_runs{state="ambiguous"}`. The only action is P3's `reconcile`
  (lookup by `submit_key`): found -> `submitted`; not found stays `ambiguous` with the
  reservation held. **Never resubmit and never release by hand**: the provider may still hold
  the job. If the provider confirms in writing it never received the key, the run is failed
  and released through a D8 operator transition (lab-sql's; a ruling is proposed).
* **Dead letter**: a checkpoint `rejected` (its reason recorded once; a redelivery returns the
  same outcome) and a P1 import that failed stay as records; the fix is a new delivery or a
  new import, never an edit of the rejected one.
* **Revocation**: a source whose training grant is revoked while a run is `prepared` makes
  P3's `submit` refuse (the bundle's ids are re-read); a `submitted` run is cancelled with
  P3's `cancel` (the connector's cost is settled as reported, or unknown). A revoked P-10/P-11
  approval: remove the entry, deploy, restart - the preflight refuses the adapter and the unit
  stays down until the env file is back to the default.
* **Budget alerts** (Lab only, never paging as a consumer outage):
  `infrx_lab_external_run_reserved_usd{payer}` at 80 % of its approval's `budget_usd`;
  any `ambiguous` run older than 1 h; `infrx_lab_teacher_spent_usd{payer}` at 80 % of its
  budget. CREDIT and USD are never summed: these are PROVIDER_USD.

## 5. Drills

Local (task-local key `i6`, no container needed): from `apps/infrx-api`,
`uv run --frozen pytest -q tests/i/lab_pipeline`.

| drill (LAB-WORKERS, PIPELINE-BUDGET) | local evidence | owed from staging (P-08) |
|---|---|---|
| unit shape, OFF by default, preflight gate | `test_units.py` | `systemctl show` of each unit |
| setting / secret / adapter / budget refusals, no value printed | `test_preflight.py` | a refused start in the journal |
| a file systemd reads otherwise (continuation, quotes, duplicates) refused; values checked = values run | `test_preflight.py` (the repros under the host's user systemd 255 when reachable) | a refused start in the journal under the box's systemd |
| object-store credentials from the instance role only when `169.254.169.254` is allowlisted | `test_preflight.py`, `test_egress.py` (botocore's proxy decision, no network) | an S3 `HeadBucket` from the running container; a denied one without the entry |
| blocked egress fails closed, env file cannot widen it | `test_egress.py` (httpx under the unit's env) | a request to an unlisted host from the running container |
| submit with egress blocked -> `ambiguous`, nothing sent; restart reconciles, never resubmits; a protocol server that accepted then went down converges on lookup | `test_protocol_drills.py` (P3's `HttpConnector` and protocol server over TCP, D8 faked; skips until P3 is on the base) | the same against the P-11 provider's sandbox |
| restart keeps budget and data-use limits | P3's ledger (D8/D6J) holds them; the drill restarts with a fresh connector and the same ledger | real D8 on PostgreSQL (lab-sql) |

Remaining adapter and staging inputs: P-10 (teacher model, USD rates, egress host), P-11
(connector, paid terms, sandbox), P-08 (window, operator identity, network-level egress
rule), D8/D6J merged (the ledger), and the entry point (composition).

## Verification log

- 2026-09-28: I6 packaging, preflight, egress deny and drills written (lab-workers lane, LW5);
  nothing run on staging or the pilot box.
- 2026-09-28: security lens extended - I2L's enable marker, `python3 -I`, `--pull never`, own
  group 10003, local image id only, owner-only env file, no value echoed (adapter or pasted
  line), exact `docker run` flag set; residuals (host network, httpx subdomain match, docker
  inspect) recorded. Local only.
- 2026-09-28 (fix round): the preflight refuses any line systemd's `EnvironmentFile=` and
  docker's `--env-file` could read differently and compares every setting with the unit's
  own environment (0-LW-1, 1-LW5-LW-1); `169.254.169.254` may be allowlisted for the
  instance-role credentials (1-LW5-LW-2); manual preflight under `systemd-run`. Local only.
