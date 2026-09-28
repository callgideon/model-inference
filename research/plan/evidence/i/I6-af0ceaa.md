# I6 - Package annotation and training integration workers (LAB-WORKERS, PIPELINE-BUDGET)

Lane lab-workers (LW5), branch `codex/w5-lab-workers`, worktree
`.claude/worktrees/codex-w5-lab-workers`. Base `9a48300c`; code head `af0ceaab`.
Commits: `2694b69a` (I6 units, preflight, runbook, drills), `b4f48422` (port-collision
check), `ef7f2c3a` (I7), `859b27b4` (first security-lens case), `af0ceaab` (the security lens
extended across I6 and I7 by the continuation implementer; this file). The previous
implementer's drafts `I6-859b27b.md` / `I7-859b27b.md` and their update JSONs were never
committed (they carried an unfilled `make api-test` row) and are superseded by this file; their
raw logs `I6-raw/mutants-all.log`, `I6-raw/protocol-drills-on-p3-merge.log` and
`I7-raw/mutants-all.log` are committed as the `859b27b4` record.

Task-local key `i6` (PG 57525): used only by `make api-test` (INFRX_D_TASK=i6). I6 touches no
SQL/RPC; D8 (the run ledger) is not on the base, so the protocol drills use P3's fake ledger
over loopback ephemeral ports.

## Changed paths (all owned)

* `infra/lab/workers/training/preflight.py` - stdlib `ExecStartPre` for annotation, training
  and rollout: role-scoped setting names; bare-name lines refused; non-identifier names (a
  pasted DSN) refused by line number, never printed; `INFRX_IMAGE` must be `sha256:<64 hex>`
  (the consumer's `image_id` rule); the default adapter (`dry-run` / `manual-bundle`) carries
  no endpoint, token, budget or payer; a non-default adapter needs its P-10/P-11 entry in
  `egress.json`, an `https://<approved host>` endpoint, a non-empty token, a USD budget in
  (0, approval cap] and exactly the approval's named payer; `LAB_EGRESS_ALLOW` only the object
  store's host and the enabled adapter's host; the env file owner-only (0600/0400); refusals
  never echo a value (the unapproved-adapter message no longer prints the adapter value).
* `infra/lab/workers/training/egress.json` - approvals, **empty** (P-10/P-11 pending).
* `infra/lab/workers/training/RUNBOOK.md` - roles, entry-point contract, threat model,
  security, hardening, residuals, enable/rollback (I2L marker), operator actions, drills
  ledger local vs owed, verification log.
* `apps/infrx-api/deploy/lab/pipelines/infrx-lab-{annotation,training}.service` - I5's shape
  plus: I2L's `ConditionPathExists=/etc/infrx-lab/enabled` ANDed with the role's env file;
  `ExecStartPre=/usr/bin/python3 -I .../preflight.py` (no `-`, no `+`, isolated);
  `docker run --pull never`; `--user 10003:10003` (I2L's own group, not the runtime's 10000);
  egress deny (`HTTP(S)_PROXY` both cases -> `127.0.0.1:9`, `NO_PROXY`/`no_proxy` =
  `${LAB_EGRESS_ALLOW}`); health ports 8014/8015.
* `apps/infrx-api/tests/i/lab_pipeline/` - `test_units.py` (3 cases), `test_preflight.py`
  (10 cases, 31 items), `test_egress.py` (2 cases, 3 items), `test_protocol_drills.py` (2,
  skip until P3 is on the base), `mutants.py` + `test_mutants.py` (own process, I5's
  layout/runner reused).

## Commands (from `apps/infrx-api` unless noted)

| # | command | exit | result |
|---|---|---|---|
| 1 | fail-first (2694b69a) `pytest -q tests/i/lab_pipeline` with preflight/units absent | 2 / 1 | collection error; 5 failed (`I6-raw/seam-red.log`) |
| 2 | fail-first (security lens, before af0ceaab's implementation) `pytest -q tests/i/lab_pipeline/test_preflight.py tests/i/lab_pipeline/test_units.py tests/i/lab_rollout/test_units.py --tb=line` | 1 | 10 failed, 20 passed (`I6-raw/seam-red-lens.log`: `--pull` missing, `--user 10003:10003`, the image/echo/mode cases) |
| 3 | `pytest -q tests/i/lab_pipeline tests/i/lab_rollout` | 0 | 47 passed, 1 skipped (P3 drills) |
| 4 | `INFRX_MUTANTS=all pytest -q tests/i/lab_pipeline/test_mutants.py tests/i/lab_rollout/test_mutants.py` | 0 | 77 passed: **I6 58 mutants killed**, I7 15 killed, 0 survivors, both lists well formed, every case covered (`I6-raw/mutants-all-af0ceaab.log`) |
| 5 | scratch tree `git merge-tree --write-tree af0ceaab codex/w5-pipelines(5e0d6f8d)` = tree `b9bc6031` (no commit, no worktree; `git archive` into the scratchpad): `pytest -q -rA tests/i/lab_pipeline/test_protocol_drills.py tests/i/lab_pipeline/test_egress.py` | 0 | 5 passed (`I6-raw/protocol-drills-on-p3-tree-af0ceaab.log`) |
| 6 | `pytest -q tests/i/lab_eval/test_units.py tests/i/lab_eval/test_pool_budget.py tests/i/lab_eval/test_mutants.py tests/r/control` | 0 | 44 passed (I5 and R2 neighbours unchanged) |
| 7 | `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 0 | 2 passed |
| 8 | `uv run --frozen ruff check tests/i/lab_pipeline tests/i/lab_rollout ../../infra/lab/workers/training` | 0 | clean |
| 9 | root: `INFRX_D_TASK=i6 make api-test` at af0ceaab | 2 | 7 failed, 5320 passed, 92 skipped, 9 xfailed (58 min); the 7 are `tests/d/test_outbox_relay.py::*[valkey]`; rerun alone: the same 7 failed, 13 passed - `ForeignContainer: infrx-d2-valkey exists and is not this checkout's` (shared d2 Valkey held by another checkout; not this lane's key, untouched). `tests/i` all green in the full run (`I6-raw/api-test-af0ceaab.log`) |

Failed-then-passed: rows 1 -> 3 and 2 -> 3. No real-service PG/RLS suite: no SQL/RPC touched.

## Drill results (I6.c)

* Blocked egress: P3's `HttpConnector` under the training unit's container env, protocol
  server on an unlisted host -> `submit` = `ambiguous`, reservation `held`, 0 POSTs; a restarted
  worker only looks the key up (blocked -> error, run unchanged); once allowed, the lookup is
  404 -> still `ambiguous`, 0 POSTs (visible for reconciliation, never resent).
* Accept-then-down: 503 after accept -> `ambiguous`; restart while down -> error, unchanged;
  back up -> `submitted` `job-1`; poll during an outage -> error, reservation held; completed ->
  settled once at the provider-reported `12.50000000`; exactly 1 POST, 1 job.
* Budget and data-use limits across restart live in P3's ledger (D8/D6J, faked): rerun on real
  D8 owed at its merge.

## Security lens (adversarial cases, all named by mutants)

| vector | case | what fails closed |
|---|---|---|
| egress via env (proxy override any case, `ALL_PROXY`, `SSL_CERT_FILE`, `AWS_ENDPOINT_URL`) | `test_i6_every_setting_is_named_for_its_role_and_purpose`, `test_i6_only_the_allowlisted_host_is_reachable...` | refused by name; unit `-e` flags win over the env file (both cases) |
| env steering the unit's other Exec lines (`DOCKER_HOST`, `PYTHONPATH`, `LD_PRELOAD`) | same + `test_i6_each_role_is_bounded...` (`python3 -I`) | refused by name; the preflight runs isolated |
| egress via argv (image = flag, empty, registry ref) | `test_i6_the_image_is_a_local_content_addressed_id_never_a_flag_or_a_pull` | only `sha256:<64 hex>`; `--pull never` in the unit |
| mounts / privilege / namespaces / seccomp / tmpfs exec / consumer group | `test_i6_argv_carries_no_mount_privilege_namespace_pull_or_secret` | exact `docker run` flag set and fixed values |
| secret in argv (`ps`) | same | only `INFRX_IMAGE`, `LAB_EGRESS_ALLOW` expanded |
| secret in the journal (pasted DSN line, token in the adapter setting) | `test_i6_a_refusal_never_echoes_a_value_pasted_as_a_name_or_an_adapter`, `test_i6_the_cli_exits_1...` | refused by line number / generic text |
| secret readable on disk | `test_i6_the_cli_refuses_an_env_file_another_account_can_read` | 0640/0604 refused, 0600/0400 pass |
| silent paid enablement, wildcard/suffix allowlist, lookalike endpoint, empty token | the earlier preflight cases | as `2694b69a` |

Residuals (runbook §2, owed from P-08): `--network host` means raw sockets are not stopped
(network-level rule needed); httpx 0.28.1 reads a `NO_PROXY` host as host **and subdomains**
(verified: `attacker-bucket.s3.us-east-1.amazonaws.com` bypasses the proxy when the regional
endpoint is allowed; path-style URLs reach any bucket anyway, so only an S3 VPC endpoint
policy pins the bucket); `docker inspect` shows the token to the docker group; the env file's
author (ubuntu, docker group) is root-equivalent, so the preflight stops mistakes, not that
account; `LAB_S3_ENDPOINT` itself is operator-trusted (not in `egress.json`).

## Local vs owed from staging (P-08)

Local (this file): unit shape and hardening as shipped, preflight refusals, egress deny
under httpx, protocol drills on a P3 scratch tree. Owed: `systemctl show` of each installed
unit, a refused start in the journal, a request to an unlisted host from the running
container, the drills against the P-11 sandbox, real D8 on PostgreSQL, the network-level
egress rule.

## Wiring requests

* **WR-I6-1** `Makefile:21` api-mutants: append ` tests/i/lab_pipeline/test_mutants.py
  tests/i/lab_rollout/test_mutants.py` after ` tests/i/lab_eval/test_mutants.py`. Proof:
  row 4 (77 passed).
* **WR-I6-2** `infra/lab/workers/eval/pool_budget.py` (I5's) `ROLES` gains
  `"annotation": 1, "training": 1, "rollout": 1`. Proof: `pool_budget.py --runtime-port 6543
  --enable annotation --enable training --enable rollout` -> `PASS lab: peak 6 <= limit 20`.
* **WR-I6-3** composition: `python -m infrx.lab.workers annotation|training` per RUNBOOK §1
  (reads only its env file; `p3.HttpConnector(httpx.AsyncClient(base_url=LAB_TRAINING_CONNECTOR_URL), name)`
  with env proxies trusted; `advertised = {manual-bundle, LAB_TRAINING_CONNECTOR}`; health on
  `LAB_WORKER_HEALTH_PORT`). Proof: `test_protocol_drills.py` rerun through the entry point.
* **WR-I6-4** (I2L's file) `infra/lab/app/lab.json` `env` gains a `lab-workers` list naming
  the preflight's settings (`preflight.allowed_names(role)` for annotation/training/rollout,
  exposure `secret` for `LAB_DATABASE_URL` and `*_TOKEN`). Proof: I2L's names test plus
  `set(json names) == set().union(*(allowed_names(r) for r in ROLES))`.

## Open issues / proposed rulings (unnumbered)

* I5's runbook §2 says `root:root 0600` for the env file, but `docker run --env-file` runs as
  `User=ubuntu` and reads it itself (permission denied). Proposed: `ubuntu:ubuntu 0600` (this
  lane's runbooks). Owner: eval-ops / coordinator.
* I5's eval units still use group 10000 and no `--pull never` / enable marker; proposed as an
  I5 follow-up for consistency (not this lane's path).
* Proposed ruling: *an `ambiguous` external run is never resubmitted or released by the
  platform; only a D8 operator transition backed by the provider's written confirmation that
  the key was never received fails it and releases its reservation.*
* Proposed ruling: *Lab worker units run only a local content-addressed image (`--pull
  never`) and require both I2L's `/etc/infrx-lab/enabled` and the role's own env file.*
* Deviation: the units keep I5's `INFRX_IMAGE` name (inside each role's own env file) rather
  than I2L's `INFRX_LAB_IMAGE`; the App's `INFRX_IMAGE` is never read.

## Estimate (remaining to merge)

optimistic 1 h / likely 2 h / pessimistic 4 h, confidence medium. Basis: one review round on
a small diff (I5 analogue 2/4/8 minus the drills already run) plus the P3-base drill rerun;
staging (P-08) excluded.

## Fix round (2026-09-28, findings 0-LW-1, 1-LW5-LW-1, 1-LW5-LW-2)

Head before: `9c876cde`. Fix commit `1ed86c80` (+ a lint-only follow-up). Local only; nothing
touched staging, the pilot box or any hosted service.

* **0-LW-1 / 1-LW5-LW-1 (fixed, at the root).** `preflight.py` `main` now compares every
  parsed setting, and always `INFRX_IMAGE` and `LAB_EGRESS_ALLOW`, with `os.environ` - which
  in `ExecStartPre` is exactly systemd's `EnvironmentFile=` view (`-I` keeps it) - and
  refuses by name on any difference, never printing a value. `parse` splits on `\n` only (as
  both readers do; `splitlines` split a form feed neither does) and refuses, by line number,
  any line with a backslash (comments too: systemd continues them), a name set twice, and a
  value with a quote, a control character or surrounding space. Consequence: a plain
  `python3 preflight.py` outside the unit refuses; both runbooks now run the manual check
  under `sudo systemd-run --wait --pipe -q --uid ubuntu -p EnvironmentFile=...` (systemd's
  reading, as the unit gets it). A DSN password with a quote or backslash is refused (rotate).
* **1-LW5-LW-2 (fixed: instance-role path chosen).** `LAB_EGRESS_ALLOW` may name exactly
  `169.254.169.254` (no range, port, neighbour or name); `AWS_*` stays refused, so botocore's
  only credential source is the instance role. Host networking makes the container the
  instance's own hop (IMDSv2 hop limit 1 suffices); the role must be scoped to the Lab bucket.
  The unit is unchanged (the entry is opt-in per env file; without it every S3 call fails
  closed). Staging proof owed and recorded in the training runbook's P-08 ledger and §2
  residuals (an S3 `HeadBucket` from the running container, and a denied one without it).

New cases (tests first; red against the handback preflight: `I6-raw/seam-red-fix-round.log`,
6 failed / 31 passed):
`test_i6_a_file_systemd_reads_otherwise_is_refused_as_the_unit_runs_it` (the three repros
from the findings, parametrized; each asserts the host's user systemd 255.4 reads the file as
recorded, then runs the preflight both with that view and inside `systemd-run` with
`EnvironmentFile=` - exit 1, no `PASS`, nothing echoed),
`test_i6_a_line_systemd_and_docker_could_read_differently_is_refused_unprinted`,
`test_i6_the_values_checked_are_the_values_systemd_passes_the_unit`,
`test_i6_the_object_store_credentials_come_from_the_instance_role_via_imds`,
`test_egress.py::test_i6_botocore_reaches_the_instance_role_only_when_imds_is_allowlisted`
(botocore's `get_environ_proxies` under each unit's container env; no network).

New mutants (14, all killed): `i6_pf_environ_unchecked`, `i6_pf_environ_file_names_only`,
`i6_pf_environ_image_only`, `i6_pf_backslash_allowed`, `i6_pf_comment_backslash_allowed`,
`i6_pf_duplicate_last_wins`, `i6_pf_quote_allowed`, `i6_pf_parse_refusals_dropped`,
`i6_pf_control_allowed`, `i6_pf_space_allowed`, `i6_pf_splitlines`, `i6_pf_imds_refused`,
`i6_pf_imds_link_local`, `i6_annotation_no_proxy_imds_always`; `i6_training_http_proxy_empty`
now also names the botocore case.

| command | exit | result |
|---|---|---|
| `pytest -q tests/i/lab_pipeline tests/i/lab_rollout tests/i/lab_eval` | 0 | 69 passed, 5 skipped (P3 drills skip until P3 on base) |
| `INFRX_D_TASK=i6 INFRX_MUTANTS=all pytest -q tests/i/lab_pipeline/test_mutants.py` | 0 | 74 passed: 72 mutants killed, 0 survivors, every case named (`I6-raw/mutants-all-1ed86c80.log`) |
| `INFRX_D_TASK=i7 INFRX_MUTANTS=all pytest -q tests/i/lab_rollout/test_mutants.py` | 0 | 17 passed: 15 killed (`I7-raw/mutants-all-1ed86c80.log`) |
| `ruff check tests/i/lab_pipeline tests/i/lab_rollout infra/lab/workers/training` | 0 | clean (after dropping an unused `import os`, lint-only) |
| `INFRX_D_TASK=i6 make api-test` | 2 | 7 failed, 5329 passed, 92 skipped, 9 xfailed: the 7 are `tests/d/test_outbox_relay[valkey]`, rerun alone the same 7 (ForeignContainer `infrx-d2-valkey` held by another checkout, not touched); `tests/i` green (`I6-raw/api-test-fix-round.log`) |

Local vs owed (P-08): the systemd/docker agreement is proven locally under the host's user
systemd 255.4; the box's systemd reading a refused file (a failed start in the journal) and
the instance-role S3 call from the running container are owed from staging.

Rulings: the two proposed rulings above are numbered R184 (an ambiguous external run is never resubmitted or released by the platform) and R185 (Lab worker units: local content-addressed image, `--pull never`, enable marker and the role's own 0600 env file) in `08-contracts-v1-encoding.md` §10 at the lab-workers merge (2026-09-28, `codex/w5-merge-12`).
