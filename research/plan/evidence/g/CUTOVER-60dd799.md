# CUTOVER — mount the pilot ingress, uploads and jobs; build the adapters from settings; retire the shim; the edge

## Task and status

| Field | Value |
|---|---|
| Task | the held cutover (coordinator-owned, implemented by the CUTOVER lane): G2 integration request 1 and R2-1 (`G2-e5e7d3a.md`), G3 request (a) and (e) (`G3-9e3bc71.md`), G4U request (a) (`G4U-a3cb5c2.md`); the coordinator's mid-lane requests (merge M1-L2's object store; item 6, E4B's release pins; item 7, `infrx_build_info`; item 8, `build_operations`) |
| Owner / session | implementation agent (Claude Opus 5.5, 1M context), CUTOVER lane |
| Status | **implemented** and run against the fakes, the task-local PostgreSQL (d3) and a locally built runtime image. Nothing is deployed, nothing ran on the pilot box, AWS or a hosted project. Since the M1-L2 merge, `create_app` builds everything from settings. It refuses to start unless `S3_MEDIA_BUCKET` answers HeadBucket; that was not exercised against a real bucket here |

## Source

| Field | Value |
|---|---|
| Base SHA | `a0711ac` = integration head `7c52627` + D5's branch `c67e4f5` (terminalize, `PgCatalogDirectory`, G6B adapters, `PgJobStore.lookup`) |
| Review fix round | `c5c5395`, `fa881cf`, `e0b97f8` on `1bed457`: tests, docs and comments only (see "Review fix round"). The fix-round evidence is committed on top |
| Implementation SHA | `60dd799` (items `88e1cfd..60dd799`, including the merge `f11f1f6` of `origin/codex/m1l2-object-store` @ `e1bb54f`). `92269a6` on top is the coordinator's save of this report's draft at the Opus session limit (evidence only). This report is committed on top of that as the lane's final head |
| Branch / worktree | `codex/cutover-mount` / `.claude/worktrees/codex-cutover`. This lane pushed nothing; the coordinator pushed its save `92269a6` |
| G2's cutover diff | the inline text of `G2-e5e7d3a.md` "integration_requests" 1, sha256 `bae3449513c283b09f6ad90c9164334f08405801462a979b352d2b7fc36c46b4` (the round-2 hash of that text), `git apply` clean at `a0711ac`; its hunks are in `88e1cfd` (code and tests) and `43fe900` (deploy files) |

| Commit | What |
|---|---|
| `88e1cfd` | item 1: the mount, `adapters_from_env`, the unset-mode refusal, G2's test hunks, the legacy top-level suite retired |
| `43fe900` | item 2: `gateway.py` retired; Dockerfile, `.dockerignore`, the unit; docs |
| `6cb8ebe` | item 3: the edge |
| `eef1262` | item 4: preflight (regime/card case; a compose check, removed again in `c6f4d0e`); 08 §5/§5.1 rows |
| `6e90847` | item 1 on PostgreSQL: `tests/d/test_composition_pg.py` |
| `b4eba9d` | the default mutant subsets carry the deciding cutover mutants |
| `1a3c3d9` | item 1: a store is built from settings only on a named `DATABASE_URL` |
| `f11f1f6` | merge of M1-L2 (`S3ObjectStore`; `pilot.object_store` probes HeadBucket; `bucket_problems` on preflight's pilot `apply`; Dockerfile `--extra traces`); one conflict, the 08 log, resolved by keeping both lines |
| `c6f4d0e` | item 4 after the merge: the probe's compose check removed (see below) |
| `351d084` | item 6: the published release pins W3's measured image and engine options |
| `9fd3457` | item 7: `infrx_build_info{revision, image} 1` on /metrics from the installed release; /metrics mounted |
| `f7d9b03` | item 8: the operator tool's `build_operations()` on D5's PostgreSQL adapters |
| `60dd799` | item 7 fix: I2B's `preflight_refusal_ignored` re-anchored on install.sh's preflight line, which `9fd3457` extended with `--release "$sha"` (the full I list at `f7d9b03` reported it misdeclared, anchor 0 times) |

## What changed

- **The mount (`infrx/gateway/app.py`).**
  `ROUTERS = (health, models, ingress, uploads, jobs, metrics)`. `metrics` is I3B's
  loopback-only `infrx.observe.route`, added by item 7.
  - `create_app` validates the mode, then builds `rt.ingress` before the router loop. That
    puts `rt.relay`, `rt.media_store`, `rt.large_bodies` and `rt.lifetime` on the runtime.
  - It then sets the build gauge (item 7), mounts the routers in that order and calls
    `ingress.assert_route_table(app)`.
  - It serves no docs, schema or slash redirects (G2's hunk).
- **The adapters from settings (`infrx/gateway/pilot.py`, G2 R2-1).**
  `create_app(settings, **adapters)` calls
  `pilot.build_ingress_deps(rt, **pilot.adapters_from_env(rt.settings, **adapters))`.
  `adapters_from_env` keeps each adapter it is given and builds each one it is not:
  - **The object store comes first**, from `S3_MEDIA_BUCKET`. An unset bucket refuses in
    every mode, naming the setting. It never falls back to the in-memory store.
    - Before the merge, a set bucket also refused, because there was no S3 adapter.
    - Since the merge, it is M1-L2's `S3ObjectStore` (`S3_MEDIA_PREFIX`,
      `S3_ENDPOINT_URL`), and it must answer HeadBucket or `create_app` refuses.
    - Because it runs first, a refusal builds nothing.
  - **The stores need a named database.** With a store to build and no `DATABASE_URL`, it
    refuses, naming `DATABASE_URL` (`1a3c3d9`). Pilot already required that setting. In
    dev/test an empty DSN would mean libpq's defaults, which is some other database.
  - **One pool for the three stores:** D5's `PgCatalogDirectory`, D4's `PgStreamStore` and
    D2's `PgJobStore`. The job store is one class for both regimes; the relay dispatches on
    `ACCOUNTING_REGIME` (R86). `lifespan` opens and closes the pool.

  `build_ingress_deps` no longer builds a job store of its own. It now refuses without
  `jobs`, as it already did without `catalog`, `stream` or `objects`, and it takes the
  `pool` to hand to the lifetime. `index` still defaults to the Valkey index (`VALKEY_URL`).
- **The pool's connect before the pool is open.** `Probe` asks its first answer inside
  `create_app`, on a thread and loop of its own. That happens before `lifespan` opens the
  pool, and a pool belongs to the loop that opens it.
  - While `pool.closed`, the stores' connect opens a connection of its own. It runs the
    pool's `configure` hook (`set role service_role`, the statement timeout).
  - Once the pool is open, the connect lends a pooled connection.

  This is G2's request 4 ("each probe path usable from a fresh loop") met on the one pool.
- **Unset mode refuses (`config.py`, G2's hunk).** With the legacy composition gone, an
  unset `INFRX_MODE` is `RuntimeMisconfigured(("INFRX_MODE",))`.
- **Retirements (R48 / S2M D10).**
  - `88e1cfd` retired the legacy top-level suite: `tests/conftest.py`, `test_app_factory.py`,
    `test_gateway_auth.py`, `test_inflight.py`, `test_media.py`. It pinned the composition
    that commit removes, and after that commit it would not collect (`create_app()` with no
    mode refuses).
  - `43fe900` retired `apps/infrx-api/gateway.py`.
- **Deploy files.** The Dockerfile no longer copies or compiles `gateway.py`, and the
  `.dockerignore` no longer admits it. `marlin2b-gateway.service` runs
  `uvicorn --factory infrx.gateway.app:create_app`. These no longer present the shim as live:
  README.md, apps/README.md, models/marlin2b/README.md, CLAUDE.md (the entry-point sentence
  and a verification-log line), and two docstrings.
- **The edge (`deploy/Caddyfile`).** No behaviour change. Its catch-all `handle` already
  proxied every path that is not `@private` or `/health` to `127.0.0.1:8001`, with
  `flush_interval -1` and no header directive. A comment now says that this block serves
  chat, the jobs routes and the upload routes, and why it has no header directive. A test
  pins it (item 3).
- **Preflight (`deploy/preflight.py`).**
  - `ACCOUNTING_REGIME` and `ACTIVE_RATE_CARD_VERSION` were already `TUNABLE`. The probe's
    `validate_runtime(from_env(staged))` refuses a bad value, and a pilot case now pins it.
  - `eef1262` also made the probe run `adapters_from_env` over the staged bytes. `c6f4d0e`
    removed that again after the merge: `pilot.object_store` now asks HeadBucket over the
    network, and the probe runs with `--network none`, so the check would refuse every
    pilot install.
  - M1-L2's `bucket_problems` asks the bucket from the host on `apply`'s pilot path. It is
    kept, and was not touched here. `validate_runtime` already requires `DATABASE_URL` in
    pilot.
  - Item 4 added no manifest key (item 7 adds `INFRX_RELEASE_SHA`). The adapters' settings
    already have entries:
    `DATABASE_URL` (MANIFEST, pilot, SSM), `DATABASE_POOL_*`, `S3_MEDIA_BUCKET`,
    `S3_MEDIA_PREFIX` and `S3_ENDPOINT_URL` (TUNABLE).
- **08 §5/§5.1.** Rows updated or added:
  - `INFRX_MODE`: unset refuses.
  - `DATABASE_URL`: the three stores come from it.
  - `VALKEY_URL`: the gateway needs it, or an injected index.
  - `S3_MEDIA_BUCKET` gets a row of its own: unset, or a bucket that does not answer
    HeadBucket, refuses; never process memory.
  - `DATABASE_POOL_*`: the one shared pool.

  M1-L2 added the `S3_MEDIA_PREFIX`/`S3_ENDPOINT_URL` rows. No ruling was numbered.
- **Item 6 (coordinator, from E4B's certification run).** `marlin_release`, which every
  admission pins, carried the moving tag `vllm/vllm-openai:nightly` and the placeholder
  `engine_options_digest` `sha256:44…44`.
  - `infrx/contracts/v2/fixtures.py` `RUNTIME_IMAGE_REF` / `ENGINE_OPTIONS_DIGEST` are now
    W3's pins: `models/marlin2b/serving-version.json` `runtime_image.ref` =
    `vllm/vllm-openai@sha256:4cbfd34a…20b42`, and `engine_options_digest` =
    `sha256:3c4bbfac…36147`.
  - `fixtures/v2/serving_revision.json` was regenerated with `--write` (R77).
  - The operator seed `seed_marlin_provisional.sql` carries the same pins, so D's
    `check_seed_is_the_fixtures` still holds.
  - The exported serving conformance case now asserts a digest-named runtime.
  - A new G6B case reads `serving-version.json`, never a literal, and ties the release, the
    generated fixture and the seed to it.
  - `runtime_image_digest` stays unset on this revision, **by the coordinator's decision**
    (2026-09-23). Under R76 the digest belongs to a new serving version, and D's registry
    checks refuse pinning a digest onto an existing one. W4 phase B cut no new version
    tonight (decision B at 82 s), and `serve.sh` and `serving-version.json` are unchanged.
    `image_is_pinned` therefore stays false on the `2026-09-01` revision.
- **Item 7 (coordinator, from E4B's served-build check).** The gateway publishes
  `infrx_build_info{process="gateway", revision=<git sha>, image=<image id>} 1` on /metrics
  from startup.
  - The two values are settings the installer writes. They are never read from git or
    docker at runtime.
    - `install.sh` passes its checked-out HEAD (`--release "$sha"`).
    - preflight writes it as `INFRX_RELEASE_SHA`: a new MANIFEST key, required in pilot,
      shaped as 40 hex digits.
    - `INFRX_IMAGE`, the image id preflight already wrote for systemd, is now also read by
      the runtime (`config.DeploymentSettings.infrx_release_sha` / `infrx_image`).
    - A malformed value refuses startup in every mode (`validate_deployment` runs before
      the mode is dispatched on). Since `c5c5395` the case checks dev, test and pilot,
      with abbreviated and 39-hex commits and short or bare digests (review COMP-B1,
      H-B1).
  - `pilot.build_info(rt)` runs in `create_app` after the composition. A pilot without
    either value refuses to start, naming it. Dev/test set the gauge only when both values
    are given.
  - The metrics family gains the `image` label (`sha256:<64 hex>`).
  - /metrics is now mounted (`infrx.observe.route`, last in `ROUTERS`; I3B request 1). It
    answers a direct loopback peer only, and Caddy already 404s it.
  - I3B's `METRICS_DISKS` is not added: the route's default (`root=/`) applies.
  - 08 §5.1 has a row.
- **Item 8 (coordinator: D5 request 4 / E4B request 4).** `infrx.operations.cli.build_operations(settings=None)`
  builds G6B's `Operations` on D5/A1's PostgreSQL adapters: `PgSignup`, `PgTenantStore`,
  `PgLedger`, `PgAuditLog`, `PgRegistry`, `PgWalletDirectory`, `PgAccountView`,
  `PgCatalogDirectory` and `PgJobStore`.
  - It reads the deployment's `DATABASE_URL` through `config.from_env`, the one environment
    reader, and refuses (`SystemExit`) without one.
  - This is D5's proposed diff, with the environment read moved to that reader.
  - It lives in the CLI's own composition root, not in the gateway's `adapters_from_env`:
    the gateway composes no `Operations`.
  - D5's end-to-end PostgreSQL operations case now builds its service through it.

## Per item

Each kill below is quoted from a by-name run of the list's own runner, at the item's commit
or later: `python -m tests.g.mutants <names>`, `python tests/i/mutants.py <names>` or
`python -m tests.g.ops.mutants <names>`. The rows for items 6, 7 and 8 quote the by-name
reruns on the final code (`92269a6`, code identical to `60dd799`). The full-list runs are
under "Runs".

| Item | Commit | Cases | Mutants and kill text |
|---|---|---|---|
| 1 mount + adapters | `88e1cfd` | `tests/g/test_startup.py::test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only` (the full `ROUTERS`, each upload/jobs route's module, `rt.relay.on_async`, the route table), `…the_route_table_is_asserted_after_every_router_mounted`, `…an_unset_mode_refuses_to_start`; `tests/g/test_composition.py::test_f_base__create_app_never_stages_into_process_memory` (3 modes x bucket unset/set, injected or not; M1-L2 re-pointed it at a dead local endpoint), `…create_app_builds_the_stores_it_is_not_given_on_one_pool`, `…the_startup_probe_connects_on_its_own_until_the_lifespan_opens_the_pool`, `…a_pilot_is_not_built_without_its_durable_adapters[jobs]`; `tests/g/jobs/test_jobs.py::test_f_base__the_pilot_composition_carries_the_relay_the_jobs_router_needs` (now over `create_app`); `tests/contracts/test_config_and_imports.py::test_the_router_list_is_fixed_and_uses_the_register_protocol` | G list: `composition_root_mounts_the_legacy_route` (re-anchored) `1 failed, 20 deselected`; `composition_root_drops_uploads` `1 failed, 20 deselected`; `composition_root_drops_jobs` `1 failed, 20 deselected`; `composition_root_jobs_before_ingress` `1 failed, 20 deselected`; `composition_root_route_table_unchecked` `1 failed, 20 deselected`; `composition_root_adapters_not_from_settings` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `objects_from_settings_in_memory` (re-anchored by M1-L2) `3 failed, 3 passed, 19 deselected`; `objects_unset_in_memory` `3 failed, 3 passed, 19 deselected`; `injected_objects_replaced` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `given_stores_replaced` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `stores_on_two_pools` `1 failed, 24 deselected`; `startup_probe_on_the_closed_pool` (dies_by `PoolClosed`) `1 failed, 24 deselected`; `startup_connection_unconfigured` `1 failed, 24 deselected`; `open_pool_bypassed` `1 failed, 24 deselected`; `pilot_built_without_jobs` `1 failed, 3 passed, 21 deselected`; re-anchored `pool_configure_dropped` `1 failed, 24 deselected`, `pool_statement_timeout_zero` `1 failed, 24 deselected`; G2's `composition_root_publishes_docs` `1 failed, 20 deselected`, `unset_mode_starts_legacy` `1 failed, 20 deselected` |
| 1 empty DSN | `1a3c3d9` | `…create_app_builds_the_stores_it_is_not_given_on_one_pool` (dev, `database_url=""`) | G list: `stores_on_an_unnamed_database` `1/1 killed` |
| 1 on PostgreSQL | `6e90847` | `tests/d/test_composition_pg.py::test_f_base__create_app_composes_the_pilot_from_settings_on_postgresql[legacy_usd|credit]` (INFRX_D_TASK=d3; `objects=` and `index=` injected, see Limits 3) | no committed list (see Limits 3); a one-off copy-and-edit check, quoted under "Runs" |
| 2 retire + deploy | `43fe900` | `tests/i/test_packaging.py::test_backend_deploy__the_gateway_runs_the_factory_from_what_the_image_copies` | I list: `unit_runs_the_retired_shim` `1 failed, 20 deselected`; `image_copies_the_retired_shim` `1 failed, 20 deselected`; `image_compiles_the_retired_shim` `1 failed, 20 deselected`; `context_drops_the_package` `1 failed, 20 deselected` |
| 3 edge | `6cb8ebe` | `tests/i/test_packaging.py::test_backend_deploy__the_edge_proxies_jobs_and_uploads_untouched_and_unbuffered` | I list: `edge_strips_a_contract_response_header` `1 failed, 21 deselected`; `edge_strips_last_event_id` `1 failed, 21 deselected`; `edge_buffers_events` `1 failed, 21 deselected`; `edge_hides_jobs` `1 failed, 21 deselected`; `edge_hides_uploads` `1 failed, 21 deselected` |
| 4 preflight + 08 | `eef1262`, `c6f4d0e` | `tests/i/test_prereqs.py::test_deploy_failclosed__pilot_refuses_a_regime_or_card_it_cannot_serve` | I list: `credit_without_card_installs` `1 failed, 15 deselected`; `unknown_regime_installs` `1 failed, 15 deselected` (the three `compose_*` mutants of `eef1262` were retired with the check) |
| subsets | `b4eba9d`, `c6f4d0e`, `9fd3457` | — | the default (`make api-test`) subsets gain G `composition_root_drops_jobs`, `objects_from_settings_in_memory`, `build_info_not_required_in_pilot`, and I `unit_runs_the_retired_shim`, `edge_strips_a_contract_response_header`, `edge_buffers_events`, `edge_hides_jobs`, `credit_without_card_installs`, `release_not_required_in_pilot` |
| 6 release pins | `351d084` | `tests/g/ops/test_publication.py::test_api_ops__the_published_release_pins_the_measured_image_and_engine_options` | G6B list: `release_image_placeholder_restored` `1 failed, 41 deselected`; `release_engine_options_placeholder_restored` `1 failed, 41 deselected` (40 deselected at `351d084`, before item 8's case) |
| 7 build info | `9fd3457` | `tests/g/test_startup.py::test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as` (the gauge with both labels on loopback /metrics, a public peer 404, a pilot without either setting refused naming it, a malformed one refused, dev without both: no gauge); `tests/i/test_install.py::test_deploy_failclosed__a_pilot_env_carries_the_release_install_sh_deploys` | G list: `composition_root_drops_metrics` `2 failed, 20 deselected`; `build_info_not_set_at_startup` `1 failed, 21 deselected`; `build_info_gauge_omitted` `1 failed, 21 deselected`; `build_info_revision_from_git` `1 failed, 21 deselected`; `build_info_not_required_in_pilot` `1 failed, 21 deselected`; `release_sha_shape_unchecked` `1 failed, 21 deselected`; `build_image_shape_unchecked` `1 failed, 21 deselected` (and the four ROUTERS-line mutants re-anchored, each `1 failed, 21 deselected`). I list: `release_not_required_in_pilot`, `release_shape_unchecked` `1 failed, 42 deselected`, `release_not_supplied` `1 failed, 42 deselected`, `install_passes_no_release` `1 failed, 42 deselected` (`4/4 killed`) |
| 8 build_operations | `f7d9b03` | `tests/g/ops/test_cli.py::test_api_ops__the_operator_tool_builds_the_postgres_adapters_from_the_environment`; `tests/d/test_operations_pg.py` (d3: `13 passed in 6.96s`) | G6B list: `operations_without_a_database` `1 failed, 41 deselected`; `operations_dsn_ignored` `1 failed, 41 deselected` (with item 6's two: `4/4 killed`) |
| 5 evidence | this file | — | — |

Test changes that carry G2's diff, adjusted to the tree at `a0711ac`:
- `tests/g/support.py` lost `legacy_app`, and also `as_cutover`/`CUTOVER`, a patch of
  `ROUTERS` that is now the real list.
- `tests/contracts/test_config_and_imports.py::_validated_as_cutover` is the plain hook.
- `tests/i/test_mutants.py` SUBSET names `unset_mode_starts_legacy`. G2's diff renamed the
  mutant but missed this SUBSET entry, so the list did not validate.
- The I mutant tree now carries `openrouter/` and `uv.lock`. The Dockerfile case checks
  that they exist.

## Runs

Logs are in the session scratchpad (`$S` = `/tmp/claude-1000/…/scratchpad`). Environment
for every run: `INFRX_D_TASK=d3` (PostgreSQL 55434), `INFRX_D2_VALKEY_PORT=55464`,
`INFRX_D2_VALKEY_CONTAINER=infrx-cutover-valkey`, `INFRX_Q_VALKEY_PORT=55492`, AWS
credentials disabled.

**Which code each run saw.** `f7d9b03..92269a6` changes two files:
`apps/infrx-api/tests/i/mutants.py` (`60dd799`, one I mutant's anchor) and this report.

**On the final code (`92269a6`/`a1e88dc`, same code as `60dd799`), `$S/cut4/` (`streamA.out`, `streamB.out`, one log per run):**

| Run | Tail | Exit |
|---|---|---|
| layer 0, repo root: `INFRX_E2_NAMESPACE=e3b2 apps/infrx-api/.venv/bin/python -m pytest -q -rfEs tests/integration` (no stack) | `17 failed, 149 passed, 86 skipped, 2 warnings in 24.28s`. The 17 are the 15 of Limits 6 plus rc05b and the harness migration set (Limits 6). The anchor guard `test_run.py::test_every_mutant_anchor_occurs_as_declared_on_the_checkout` passes (`1 passed, 50 deselected` by name) | 1 |
| G by-name, items 1/7 (`python -m tests.g.mutants` with 11 names) | `11/11 killed` | 0 |
| G6B by-name, items 6/8 (`python -m tests.g.ops.mutants` with 4 names) | `4/4 killed` | 0 |
| tests/g whole | `574 passed, 2 warnings in 210.19s (0:03:30)` | 0 |
| tests/i | `153 passed in 175.31s (0:02:55)` | 0 |
| tests/contracts, quick (no `INFRX_MUTANTS`) | `23 failed, 1045 passed in 45.24s`. The 23 are the pre-existing `test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` plus its pristine-baseline fallout in `tests/contracts/test_mutants.py`: 19 subset mutants and 3 runner self-tests (Integration requests, D5 / F). None is the cutover's | 1 |
| tests/d focused on d3: `test_jobstore_conformance.py`, `test_streamstore_conformance.py`, `test_credit_jobstore_conformance.py`, `test_catalog_pg.py`, `test_operations_pg.py`, `test_composition_pg.py` | `151 passed, 5 xfailed, 2 warnings in 226.50s (0:03:46)` | 0 |
| I by-name, items 2/7 (`python tests/i/mutants.py` with 5 names) | `5/5 killed`: `release_not_required_in_pilot`, `release_shape_unchecked`, `release_not_supplied`, `install_passes_no_release` each `1 failed, 42 deselected`; `preflight_refusal_ignored` `1 failed, 10 deselected` | 0 |
| `INFRX_MUTANTS=all` G list `tests/g/test_mutants.py` | `329 passed in 1339.94s (0:22:19)` | 0 |
| `INFRX_MUTANTS=all` G6B list `tests/g/ops/test_mutants.py` | `72 passed in 218.46s (0:03:38)` | 0 |
| `INFRX_MUTANTS=all` G3 list `tests/g/jobs/test_jobs_mutants.py` | `85 passed in 323.80s (0:05:23)` | 0 |
| `INFRX_MUTANTS=all` G4U list `tests/g/uploads/test_uploads_mutants.py` | `42 passed in 147.06s (0:02:27)` | 0 |
| `INFRX_MUTANTS=all` I list `tests/i/test_mutants.py` | `204 passed in 750.03s (0:12:30)`. The `f7d9b03` failure is gone | 0 |
| `INFRX_MUTANTS=all` M1-L2 list `tests/m/test_s3_mutants.py` | `14 passed, 14 skipped in 61.88s` (skip: "no S3-compatible endpoint", Limits 1) | 0 |
| `make api-test` (`PYTEST_ADDOPTS="-rfEs -p no:cacheprovider"`), 23:40:23Z–00:16:08Z | `23 failed, 3497 passed, 14 skipped, 5 xfailed, 2 warnings in 2134.05s (0:35:34)`. The 23 are the same pre-existing case and fallout as contracts quick. The 14 skips are M1-L2's S3 cases with no endpoint (12 in `tests/m/test_s3.py`, 2 in its list) | 2 |

The earlier results below are superseded by these runs, and are kept for the record.

**At `f7d9b03`, from the lane's first session (`$S/final.log`, `$S/mut3-*.log`), all with `INFRX_MUTANTS=all`:**

| Run | Tail | Exit |
|---|---|---|
| G list `tests/g/test_mutants.py` | `329 passed in 1101.26s (0:18:21)` | 0 |
| G6B list `tests/g/ops/test_mutants.py` | `72 passed in 158.76s (0:02:38)` | 0 |
| G3 list `tests/g/jobs/test_jobs_mutants.py` | `85 passed in 239.23s (0:03:59)` | 0 |
| G4U list `tests/g/uploads/test_uploads_mutants.py` | `42 passed in 98.16s (0:01:38)` | 0 |
| I list `tests/i/test_mutants.py` | `1 failed, 203 passed in 417.23s (0:06:57)`. The one is `preflight_refusal_ignored` misdeclared, "anchor appears 0 times in deploy/install.sh". `60dd799` fixes it, and the by-name rerun then killed it (`1 failed, 10 deselected`) | 1 |
| M1-L2 list `tests/m/test_s3_mutants.py` | `14 passed, 14 skipped in 51.81s` (skip: "no S3-compatible endpoint", Limits 1) | 0 |
| `make api-test` (`PYTEST_ADDOPTS="-rfEs -p no:cacheprovider"`), 22:45:25–23:14:33Z | `23 failed, 3497 passed, 14 skipped, 5 xfailed, 2 warnings in 1743.41s (0:29:03)`. The 23 are the pre-existing `test_cancel_cause.py::test_dur_settle__before_0018…` and its pristine-baseline fallout in `tests/contracts/test_mutants.py`: 19 subset mutants and 3 runner self-tests. That is the same 23 that M1-L2 reports at `eae07a9`. `60dd799`'s file was edited 22 s after this run started. It changes only the anchor of `preflight_refusal_ignored`, which is not in I's default `SUBSET` | 2 |

**Earlier, quoted by the lane's first session:** tests/d focused on d3 at `351d084` (21:56Z,
`$S/tests-d-d3-final.log`): `159 passed, 5 xfailed, 2 warnings in 117.31s`, exit 0. Image
checks at `351d084` (`$S/image-checks-final.log`): the image has no `/app/gateway.py`, and
ROUTERS is `['health', 'models', 'ingress', 'uploads', 'jobs']` (before item 7 added
metrics). The factory with a pilot env and no bucket refuses: "requires S3_MEDIA_BUCKET",
exit 1. With a bucket and no network it refuses: "S3_MEDIA_BUCKET did not answer HeadBucket
(EndpointConnectionError)", exit 1. With no mode: "requires INFRX_MODE", exit 1. The
in-image pilot probe reports only `PENDING(W3)`, exit 2.

## Limits

1. **The S3 path is not exercised against a real bucket here.** The fakes' cases inject
   `objects=`. M1-L2's own conformance ran on MinIO (its evidence). Its
   `tests/m/test_s3.py` cases skip visibly in this lane: there is no
   `INFRX_M_S3_ENDPOINT`, and this lane owns no S3 container. The image refusal under
   "Runs" is HeadBucket against a dead endpoint.
2. **The in-memory upload state stays (G4U limit 1, M3 limit 1; owners G4U/M).** The
   upload records and `_resume`'s witness (`MediaUploads.by_job`) live in the process. M's
   stray-payload sweep (G2 R2-4) is still open, and the collector is not in the lifespan
   (G2 request 6).
   - Since the cutover mounts the upload routes, one uvicorn worker is a correctness
     invariant. If worker A created an upload and worker B took the PUT, the PUT would 404.
   - The unit runs `--workers 1`. Since `fa881cf` the factory case pins that, and pins the
     110 s drain inside docker's 120 s and systemd's 150 s (`unit_two_workers`,
     `unit_drain_shortened`).
   - 08 §5.1 has no `WORKERS`/uvicorn row, so none was edited (review COMP-N1).
3. **The PostgreSQL case injects `objects=` and `index=` and has no committed mutant list.**
   The coordinator asked for `objects=` to be dropped where the composition is built from
   settings. Here it stays, because this lane has no S3-compatible store (M's is 59100, not
   this lane's). The case is a test, and every store it exercises is built from
   `DATABASE_URL`. D's committed lists run unit files without Docker, so the case was
   checked once by copying the tree and editing it (quoted under "Runs"). It killed 5
   edits. One edit is equivalent on this harness: `service_role_not_set` survives because
   the harness DSN is the superuser. The fakes' case kills it
   (`test_f_base__the_pool_sets_the_service_role_on_every_connection`,
   `…the_startup_probe_connects_on_its_own…`).
4. **The edge is pinned by parsing, not by running Caddy.** This is I2B's pattern.
   - The resolver models only `@private` path patterns, `handle /health` and the catch-all.
     A new `handle` block for a public path without `flush_interval -1` still passes (the
     review's H10d, H-N6 (a)); the resolver would need extending to catch it.
   - Since `fa881cf`, the scan for directives that rewrite, compress or buffer (`header*`,
     `encode`, `*_buffers`) covers the whole site, not only the proxy block (review COMP-B2).
   - Dropping `--extra traces` from the Dockerfile's `uv sync` (botocore) passes tests/i
     and tests/m. Only the in-image pilot probe at install catches it (the review's H8,
     H-N6 (b)). This stays disclosed.
5. **`VALKEY_URL` is required by the composition unless an index is injected.** That is
   `build_ingress_deps`'s default, from G2. 08 §5 now says so.
   - A `VALKEY_URL` pointing at a closed port does not fail startup or readiness. The
     scheduling index is not in `REQUIRED_CHECKS` (price_source, journal).
   - That is G2/Q's existing design, not the cutover's. The cutover makes it the production
     composition (review COMP-N5). G2/Q decide whether an index probe joins
     `REQUIRED_CHECKS`.
6. **E3B layer 0 after the mount.** `ingress_is_mounted()` is now true, so every pending case
   that probes it stops pending and fails until its body is written. That is the design:
   "fail the day `ingress` enters `ROUTERS`". There were 12 `PENDING[G2-R1…]` skips at
   `a0711ac` and there are 0 now. Those 12 cases, plus dr17, rc00 and `test_stage`'s
   held-cutover case, are the 15 new layer-0 failures. They belong to the E3B phase-3 lane
   (see Integration requests). Layer 0 fails 17 in all. The other 2 are not the cutover's,
   and nothing under `tests/integration` was edited on this branch:
   - `test_recovery.py::test_i3b_rc05b_…_pending_on_the_s3_adapter` fails with "an S3 object
     store exists (['infrx.media.s3.S3ObjectStore']): pause MinIO under it now". Its
     `PENDING[M1-L2]` lifted when M1-L2's adapter (`7760bcf`) came in with the merge `f11f1f6`.
     rc00 pins rc05b as `PENDING[M1-L2]` as well as rc03 as `PENDING[G2-R1]`, so rc00 fails
     until both drill bodies are written.
   - `test_harness.py::test_the_migration_set_is_the_console_one_and_is_read_in_filename_order`
     fails with "Left contains one more item: '0018_terminal_settlement.sql'". D5 added 0018
     (`e96449e`, in the base `a0711ac`). The harness's migration list gains it at the merge,
     as it gained 0017 at D4's (`e2a52b2`).

   On the five-way merge the review simulated, the lane's 17 are resolved, and layer 0 has
   2 other failures: the `e3bm62` anchor, and `stack.OWNERS['M3-U1']` (review H-N2). Both
   belong to the E3B phase-3 lane. The coordinator reports that its head `b9529d1` now
   closes them.
7. **Dev installs.** Preflight no longer checks the composition, and M1-L2's bucket check
   runs only on the pilot path. So a dev env with no reachable bucket installs, and its unit
   then refuses to start.
8. **Item 6 leaves `runtime_image_digest` unset, by the coordinator's decision (R76).**
   `image_is_pinned` is still false, although the ref names the digest. A later serving
   version records the digest when one is cut.
9. **Item 7 was not exercised on a box.** On a box, `INFRX_RELEASE_SHA` is only as true as
   the commit install.sh checked out, and it refuses a dirty tree. The worker and reaper
   processes do not publish the gauge: they have no HTTP listener, and I3B's textfile path
   is not wired.

## Integration requests

- **E3B phase-3 lane (tests/integration).** This lane merges this head and writes the
  bodies. When it retires `G2-R1` from `stack.OWNERS` / `recoverykit.OWNERS`, the cases are:
  the 9 journey cells, the dataset resume, dr11, I3B's rc03 (and rc00, which checks that
  rc03 pends), and **dr17**.
  - dr17 calls `create_app(_pilot_settings(tmp_path))` with no adapters and now gets
    `RuntimeMisconfigured: INFRX_MODE='pilot': requires S3_MEDIA_BUCKET`, so its
    `assert "legacy route" in str(refused)` fails.
  - Proposed body: inject the fakes' adapters (`objects=`, `index=`, and the stores or a
    DSN), then assert that the one `/v1/chat/completions` endpoint's module is
    `infrx.gateway.routes.ingress`.
  - Nothing under `tests/integration` was edited here. The `G2-R1` owner text and pendings
    are untouched.
- **I3B rc03.** The gateway-restart drill needs a process `create_app` can start. On the
  stack that means the E2 `s3` service's bucket (`S3_MEDIA_BUCKET`, `S3_ENDPOINT_URL`), or
  an injected object store.
- **I3B rc05b (observed, not the cutover's; nothing new for the E3B phase-3 lane beyond
  it).** M1-L2's `S3ObjectStore` lifts rc05b's `PENDING[M1-L2]` at layer 0 (Limits 6). Its
  MinIO-outage body is due from the owner of I3B's drills. rc00 pins both rc03 and rc05b,
  so it passes only when both bodies are written.
- **Coordinator: the harness migration list += `0018_terminal_settlement.sql`**
  (`tests/integration/test_harness.py`, D5's migration; Limits 6). This is the merge-time
  step that `e2a52b2` did for 0017.
- **W: none.**
- **I (I2B), `deploy/rehearse.sh`.** The rehearsal was not run here. The review confirms
  the disclosure (COMP-N4, H-N4). The coordinator has routed it: the worker lane owns item
  2. Until it is rewritten, the deploy has no end-to-end rehearsal. Read against the
  cutover, three of its steps no longer hold:
  - Step 1 deploys dev and expects the gateway to start. A dev env now needs a bucket that
    answers HeadBucket.
  - Step 2 expects a legacy-key chat to answer 200. The ingress has no legacy-key path.
  - Step 3's pilot-probe check matches `*ROUTERS*`. The ROUTERS gate passes now: the in-image
    probe at `351d084` reports only `PENDING(W3)`.
  - install.sh now passes `--release "$sha"`. Its `INFRX_SET` path is unchanged.
- **D5 / F (pre-existing, not the cutover's).**
  `tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record`
  fails at `a0711ac`, because 0018 records the cause now. It sits in the contracts mutant
  list's pristine baseline, so every contracts mutant reports `broken_runner` until the
  case is retired or inverted ("after 0018 the cause is recorded").
- **Coordinator: Makefile.** The new mutants joined existing lists (G, G6B ops, I), and
  M1-L2 added `tests/m/test_s3_mutants.py`, so the `api-mutants` line needs no change for
  them. Not mine, but noticed: `tests/d/test_code_mutants_d5.py` (D5's list, merged at
  `a0711ac`) is **not** on the `api-mutants` line.
- **Coordinator: ruling, proposed text.** The coordinator numbers it at the merge (R98,
  review H-N5). "The gateway composes its durable adapters from settings or refuses (the
  cutover).
  - `create_app` builds each adapter it is not given: D5's catalog, D4's journal and the
    job store on one pool from `DATABASE_URL`/`DATABASE_POOL_*`, and the object store from
    `S3_MEDIA_BUCKET`, which must answer HeadBucket (M1-L2).
  - It never stages media in process memory outside a test that injects the store.
  - An unset `S3_MEDIA_BUCKET`, or a store to build with no `DATABASE_URL`, refuses in
    every mode, naming the setting.
  - An unset `INFRX_MODE` refuses to start (R44, completed).
  - A pilot refuses to start without `INFRX_RELEASE_SHA` and `INFRX_IMAGE`, and exposes
    them once as `infrx_build_info{revision, image}` on its loopback /metrics. A value not
    of the full shape (a 40-hex commit, `sha256:<64 hex>`) refuses in every mode.
  - The scheduling index comes from `VALKEY_URL`, or the composition refuses unless an
    index is injected."

  M1-L2 proposes its own object-store ruling. The two can be one.
- **Item 6 follow-up: decided.** The coordinator decided on 2026-09-23 to leave
  `runtime_image_digest` unset: R76 gives the digest to a new serving version, and W4 phase
  B cut none. Whoever cuts the next Marlin serving version records the digest there.
  `tests/g/ops/test_publication.py`'s pin case reads `serving-version.json`, so it follows
  a changed `engine_options_digest` without an edit.
- **G1R/G6B owners (G4U request (d)).** The "specified, not served" wording for uploads and
  `--respond-async` should say **"mounted; served once a deployment with its bucket runs"**:
  - `tests/g/test_client_smoke.py:8`
  - `client_example.py:16`
  - README.md's client section
  - `research/workloads/marlin-sop.md:561`, and D12 at `:699`

  Not edited here.

## Review fix round (1bed457 -> e0b97f8)

This round answers the review at `a1e88dc` (`CUTOVER-review-a1e88dc.json`, verdict
fix_required). There were three blocking findings, all of them test gaps, and every refuter
confirmed that the shipped code is strict. `git diff --stat 1bed457 e0b97f8 -- apps/infrx-api/infrx apps/infrx-api/deploy`
touches only a comment in `infrx/config.py`. There is no behaviour change.

| Finding | Commit | What changed | Mutants killed (by name at `e0b97f8`) |
|---|---|---|---|
| COMP-B1 / H-N1 (a weaker shape passed) | `c5c5395`, `fa881cf` | `test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as` now also refuses the release `RELEASE[:7]`, `RELEASE[:-1]` and upper case, and the image `sha256:c0ffee`, `sha256:` + 63 hex and the bare 64 hex. `test_deploy_failclosed__a_pilot_env_carries_the_release_install_sh_deploys` adds `RELEASE[:7]` and `RELEASE[:-1]` | G `release_sha_accepts_a_short_id` (O4, `[0-9a-f]{7,40}`) `1 failed, 21 deselected`; `image_id_accepts_a_short_digest` (`{6,64}`) `1 failed, 21 deselected`; `image_id_accepts_any_sha256` (O6, `sha256:\S+`) `1 failed, 21 deselected`. I `release_shape_accepts_a_short_id` (O5, preflight `git_sha` `{7,40}`) `1 failed, 42 deselected` |
| H-B1 ("every mode" tested in pilot only) | `c5c5395` | The code does refuse in every mode: `validate_runtime` calls `validate_deployment` before it dispatches on the mode. So the case now asserts the refusal for each malformed value in `dev`, `test` and `pilot` (`limits.MODES`). The 08 §5.1 row and item 7 keep "in every mode" | G `release_shape_refused_only_in_pilot` (H16) `1 failed, 21 deselected`; `image_shape_refused_only_in_pilot` (H6) `1 failed, 21 deselected` |
| COMP-B2 (a site-level directive passed) | `fa881cf` | The edge case adds `Server-Timing` to `PASSTHROUGH` (08 §3). The `header*`/`encode`/`*_buffers` scan now covers the whole site with comments stripped. The only header it allows is `header Content-Type application/json` directly followed by `respond` (the @private, @health, handle_errors 413 and /health handle_response blocks) | I `edge_site_encodes_streams` (O1, site-level `encode gzip zstd`) `1 failed, 21 deselected`; `edge_site_strips_server_timing` (O2, site-level `header -Server-Timing`) `1 failed, 21 deselected` |
| COMP-N2 (gauge asserted with `in`) | `c5c5395` | Exactly one `infrx_build_info{` line, equal to the expected one (the value renders `1.0`) | G `build_info_exposed_twice` (O3) `1 failed, 21 deselected` |
| COMP-N1 (one worker is a correctness invariant) | `fa881cf` | The factory case pins `--workers 1` and `--timeout-graceful-shutdown 110`, and 110 < docker stop -t < TimeoutStopSec. Limits 2 states it. 08 §5.1 has no WORKERS/uvicorn row to edit | I `unit_two_workers` (O9) `1 failed, 21 deselected`; `unit_drain_shortened` (O10) `1 failed, 21 deselected` |
| COMP-N3 / H-N3 (stale docs) | `e0b97f8` | Reworded: the README environment paragraph (M1-L2's `S3ObjectStore`, HeadBucket), the `test_composition_pg.py` docstring, the apps/README.md §7 heading, the pyproject `[tool.uv]` comment and a `config.py` comment | none (docs and comments) |
| COMP-N4 / H-N4 (rehearse.sh) | evidence | Already disclosed to I2B. The coordinator routes it to the worker lane's item 2 (Integration requests) | — |
| COMP-N5 (closed VALKEY_URL still ready) | evidence | Limits 5: G2/Q's existing design | — |
| H-N2 (layer 0 on the five-way merge) | evidence | Limits 6: the coordinator reports that E3B phase 3 `b9529d1` closes `e3bm62` and `M3-U1` | — |
| H-N5 (ruling number) | evidence | The literal "R95" is dropped; the coordinator numbers it (R98). The proposed text gains the build-info and `VALKEY_URL` clauses | — |
| H-N6 (partly killable deploy pins) | evidence | Kept disclosed: Limits 4 covers the unmodelled `handle` block (H10d) and the `--extra traces` line (H8) | — |

New mutants: six in the G list (322 -> 328) and five in the I list (197 -> 202).
`release_sha_accepts_a_short_id` (G) and `edge_site_encodes_streams` (I) join the default
subsets. That is why tests/g and tests/i each gain one case.

Runs at `e0b97f8`: logs in `$S/cut6/`, clean tree under `apps/`. No PostgreSQL was used:
`INFRX_D_TASK=cutover-nopg`, so any PostgreSQL harness would refuse. `INFRX_D2_VALKEY_PORT=55464`,
`INFRX_D2_VALKEY_CONTAINER=infrx-cutover-valkey`, `INFRX_Q_VALKEY_PORT=55492`.

| Run | Tail | Exit |
|---|---|---|
| `pytest -q tests/g/test_startup.py` | `22 passed, 2 warnings in 0.57s` | 0 |
| G by-name, every mutant on the build case (13) | `13/13 killed` (the 7 earlier ones as before; the 6 new ones above) | 0 |
| I by-name, every mutant on the edge, factory and release cases (18) | `18/18 killed` (the 13 earlier ones as before; the 5 new ones above) | 0 |
| `pytest -q tests/i` | `154 passed in 158.10s (0:02:38)` | 0 |
| `pytest -q tests/g` | `575 passed, 2 warnings in 173.75s (0:02:53)` | 0 |
| `pytest -q tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py --ignore=tests/contracts/test_mutants.py` | `1 failed, 1025 passed in 17.49s`. The failure is the pre-existing `test_cancel_cause.py::test_dur_settle__before_0018…` (D5/F) | 1 |

Not rerun in this round: the full G and I lists (`INFRX_MUTANTS=all`) and `make api-test`.
Every mutant that names one of the four edited cases ran by name above. No other case, and
no code, changed.

## Verification log

- 2026-09-23: Written at implementation SHA `f7d9b03` from the runs quoted above. Every count
  is copied from command output. Only fakes, the d3 task-local PostgreSQL and locally built
  images were used. Nothing is deployed or live-verified.
- 2026-09-23 (resumed after the Opus session limit): The report was renamed to the
  implementation SHA `60dd799`. `60dd799` and the coordinator's save `92269a6` were
  recorded. Items 6–8 were updated with by-name reruns on the final code. Runs were filled
  in from logs, and each is marked with the code it saw. Layer 0's two failures that are not
  the cutover's were attributed (rc05b, harness migration set). Integration requests were
  added for rc05b, the 0018 harness entry and W (none). The final-code rerun of the focused
  suites, the full mutant lists and `make api-test` was still running at handback. Nothing
  here quotes it.
- 2026-09-24: Quoted every final-code run from `$S/cut4/` after both streams ended
  (`STREAM_A_DONE`, `STREAM_B_DONE`). The only red runs are layer 0 (Limits 6) and the 23
  pre-existing contracts failures in contracts quick and `make api-test`. None is the
  cutover's.
- 2026-09-24: Review fix round (`1bed457` -> `e0b97f8`). The three blocking findings
  (COMP-B1, COMP-B2, H-B1) are closed with tests and 11 new mutants. COMP-N1, COMP-N2 and
  COMP-N3 are folded in. COMP-N4, COMP-N5, H-N2, H-N5 and H-N6 are stated in Limits and
  Integration requests. Every count in the new section is copied from `$S/cut6/`.
