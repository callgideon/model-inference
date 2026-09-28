# Lab annotation and training workers (I6) - runbook, OFF by default

Scope: the two Lab worker roles of LAB-M3. Each is its own process and systemd unit, and
each follows I5's shape (`infra/lab/workers/eval/RUNBOOK.md`: bounds, OFF until
`/etc/infrx-lab/<role>.env` exists, no consumer coupling, drain on SIGTERM).

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

Before each start `preflight.py --role <role>` (the unit's `ExecStartPre`, as ubuntu, from
the deployed checkout) refuses the start, naming the setting and never its value, when:

* a setting is not the role's own (a consumer secret, another purpose's token, `AWS_*`, any
  `*_proxy` in any letter case) or a line is a bare name;
* the default adapter carries an endpoint, token, budget or payer (no one-line enable);
* a non-default adapter has no `egress.json` entry for this role, an endpoint other than
  `https://<approved host>`, an absent or empty token (**a failed secret lookup refuses; the
  worker never falls back to another adapter**), a budget that is not a finite USD amount
  above 0 and within the approval's, or a payer other than the approval's named one;
* `LAB_EGRESS_ALLOW` holds anything but the object store's host (`LAB_S3_ENDPOINT`, named
  explicitly, the regional endpoint on AWS) or the enabled adapter's approved host.

Egress deny: the unit points `HTTP(S)_PROXY` (both cases) at `127.0.0.1:9` (privileged, so no
unprivileged process can become the proxy) with `NO_PROXY` = `LAB_EGRESS_ALLOW`. An
unlisted host is a connection error, and P3 turns an unknown submit outcome into
`ambiguous` with the reservation held, never a retry. **Residual** (owed from staging): this
guards HTTP clients that trust the environment; a network-level rule (an egress proxy or an
nftables owner match on uid 10003) is the operator's P-08 decision.

Secrets: purpose-specific (`LAB_ANNOTATION_TEACHER_TOKEN`, `LAB_TRAINING_CONNECTOR_TOKEN`),
only in that role's env file (ubuntu, 0600: docker reads `--env-file` as the unit's user),
never in argv, logs or this repository. Rotation = rewrite and restart; revocation = empty the
token and restart (the preflight refuses the start, so nothing runs on a stale credential).

## 3. Enable one role (staging window only)

1. Record the approval in `egress.json` as a reviewed commit (adapter, host, approval record,
   `payer_ref`, `budget_usd`) and deploy that commit; never edit the box's checkout.
2. Budget the pooler: `infra/lab/workers/eval/pool_budget.py` (wiring WR-I6-2 adds these
   roles to its `ROLES`), then write `/etc/infrx-lab/<role>.env`.
3. `python3 infra/lab/workers/training/preflight.py --role <role> --env-file
   /etc/infrx-lab/<role>.env` prints `PASS <role>`; then install and `enable --now` the unit
   (I5 runbook §2 step 3) and `curl -fsS 127.0.0.1:<port>/readyz`.

Roll back: `sudo systemctl disable --now infrx-lab-<role> && sudo rm /etc/infrx-lab/<role>.env`.
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
| blocked egress fails closed, env file cannot widen it | `test_egress.py` (httpx under the unit's env) | a request to an unlisted host from the running container |
| submit with egress blocked -> `ambiguous`, nothing sent; restart reconciles, never resubmits; a protocol server that accepted then went down converges on lookup | `test_protocol_drills.py` (P3's `HttpConnector` and protocol server over TCP, D8 faked; skips until P3 is on the base) | the same against the P-11 provider's sandbox |
| restart keeps budget and data-use limits | P3's ledger (D8/D6J) holds them; the drill restarts with a fresh connector and the same ledger | real D8 on PostgreSQL (lab-sql) |

Remaining adapter and staging inputs: P-10 (teacher model, USD rates, egress host), P-11
(connector, paid terms, sandbox), P-08 (window, operator identity, network-level egress
rule), D8/D6J merged (the ledger), and the entry point (composition).
