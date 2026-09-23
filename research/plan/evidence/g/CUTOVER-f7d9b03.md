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
| Implementation SHA | `f7d9b03` (items `88e1cfd..f7d9b03`, including the merge `f11f1f6` of `origin/codex/m1l2-object-store` @ `e1bb54f`); this report is committed on top |
| Branch / worktree | `codex/cutover-mount` / `.claude/worktrees/codex-cutover`; nothing pushed |
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
  - No manifest key was added. The adapters' settings already have entries:
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
    - A malformed value refuses startup in every mode (`validate_deployment`).
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
`python -m tests.g.ops.mutants <names>`. The full-list runs are under "Runs".

| Item | Commit | Cases | Mutants and kill text |
|---|---|---|---|
| 1 mount + adapters | `88e1cfd` | `tests/g/test_startup.py::test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only` (the full `ROUTERS`, each upload/jobs route's module, `rt.relay.on_async`, the route table), `…the_route_table_is_asserted_after_every_router_mounted`, `…an_unset_mode_refuses_to_start`; `tests/g/test_composition.py::test_f_base__create_app_never_stages_into_process_memory` (3 modes x bucket unset/set, injected or not; M1-L2 re-pointed it at a dead local endpoint), `…create_app_builds_the_stores_it_is_not_given_on_one_pool`, `…the_startup_probe_connects_on_its_own_until_the_lifespan_opens_the_pool`, `…a_pilot_is_not_built_without_its_durable_adapters[jobs]`; `tests/g/jobs/test_jobs.py::test_f_base__the_pilot_composition_carries_the_relay_the_jobs_router_needs` (now over `create_app`); `tests/contracts/test_config_and_imports.py::test_the_router_list_is_fixed_and_uses_the_register_protocol` | G list: `composition_root_mounts_the_legacy_route` (re-anchored) `1 failed, 20 deselected`; `composition_root_drops_uploads` `1 failed, 20 deselected`; `composition_root_drops_jobs` `1 failed, 20 deselected`; `composition_root_jobs_before_ingress` `1 failed, 20 deselected`; `composition_root_route_table_unchecked` `1 failed, 20 deselected`; `composition_root_adapters_not_from_settings` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `objects_from_settings_in_memory` (re-anchored by M1-L2) `3 failed, 3 passed, 19 deselected`; `objects_unset_in_memory` `3 failed, 3 passed, 19 deselected`; `injected_objects_replaced` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `given_stores_replaced` (dies_by `RuntimeMisconfigured`) `1 failed, 24 deselected`; `stores_on_two_pools` `1 failed, 24 deselected`; `startup_probe_on_the_closed_pool` (dies_by `PoolClosed`) `1 failed, 24 deselected`; `startup_connection_unconfigured` `1 failed, 24 deselected`; `open_pool_bypassed` `1 failed, 24 deselected`; `pilot_built_without_jobs` `1 failed, 3 passed, 21 deselected`; re-anchored `pool_configure_dropped` `1 failed, 24 deselected`, `pool_statement_timeout_zero` `1 failed, 24 deselected`; G2's `composition_root_publishes_docs` `1 failed, 20 deselected`, `unset_mode_starts_legacy` `1 failed, 20 deselected` |
| 1 empty DSN | `1a3c3d9` | `…create_app_builds_the_stores_it_is_not_given_on_one_pool` (dev, `database_url=""`) | G list: `stores_on_an_unnamed_database` `1/1 killed` |
| 1 on PostgreSQL | `6e90847` | `tests/d/test_composition_pg.py::test_f_base__create_app_composes_the_pilot_from_settings_on_postgresql[legacy_usd|credit]` (INFRX_D_TASK=d3; `objects=` and `index=` injected, see Limits 3) | no committed list (see Limits 3); a one-off copy-and-edit check, quoted under "Runs" |
| 2 retire + deploy | `43fe900` | `tests/i/test_packaging.py::test_backend_deploy__the_gateway_runs_the_factory_from_what_the_image_copies` | I list: `unit_runs_the_retired_shim` `1 failed, 20 deselected`; `image_copies_the_retired_shim` `1 failed, 20 deselected`; `image_compiles_the_retired_shim` `1 failed, 20 deselected`; `context_drops_the_package` `1 failed, 20 deselected` |
| 3 edge | `6cb8ebe` | `tests/i/test_packaging.py::test_backend_deploy__the_edge_proxies_jobs_and_uploads_untouched_and_unbuffered` | I list: `edge_strips_a_contract_response_header` `1 failed, 21 deselected`; `edge_strips_last_event_id` `1 failed, 21 deselected`; `edge_buffers_events` `1 failed, 21 deselected`; `edge_hides_jobs` `1 failed, 21 deselected`; `edge_hides_uploads` `1 failed, 21 deselected` |
| 4 preflight + 08 | `eef1262`, `c6f4d0e` | `tests/i/test_prereqs.py::test_deploy_failclosed__pilot_refuses_a_regime_or_card_it_cannot_serve` | I list: `credit_without_card_installs` `1 failed, 15 deselected`; `unknown_regime_installs` `1 failed, 15 deselected` (the three `compose_*` mutants of `eef1262` were retired with the check) |
| subsets | `b4eba9d`, `c6f4d0e`, `9fd3457` | — | the default (`make api-test`) subsets gain G `composition_root_drops_jobs`, `objects_from_settings_in_memory`, `build_info_not_required_in_pilot`, and I `unit_runs_the_retired_shim`, `edge_strips_a_contract_response_header`, `edge_buffers_events`, `edge_hides_jobs`, `credit_without_card_installs`, `release_not_required_in_pilot` |
| 6 release pins | `351d084` | `tests/g/ops/test_publication.py::test_api_ops__the_published_release_pins_the_measured_image_and_engine_options` | G6B list: `release_image_placeholder_restored` `1 failed, 40 deselected`; `release_engine_options_placeholder_restored` `1 failed, 40 deselected` |
| 7 build info | `9fd3457` | `tests/g/test_startup.py::test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as` (the gauge with both labels on loopback /metrics, a public peer 404, a pilot without either setting refused naming it, a malformed one refused, dev without both: no gauge); `tests/i/test_install.py::test_deploy_failclosed__a_pilot_env_carries_the_release_install_sh_deploys` | G list: `composition_root_drops_metrics` `2 failed, 20 deselected`; `build_info_not_set_at_startup` `1 failed, 21 deselected`; `build_info_gauge_omitted` `1 failed, 21 deselected`; `build_info_revision_from_git` `1 failed, 21 deselected`; `build_info_not_required_in_pilot` `1 failed, 21 deselected`; `release_sha_shape_unchecked` `1 failed, 21 deselected`; `build_image_shape_unchecked` `1 failed, 21 deselected` (and the four ROUTERS-line mutants re-anchored, each `1 failed, 21 deselected`). I list: `release_not_required_in_pilot`, `release_shape_unchecked` `1 failed, 42 deselected`, `release_not_supplied` `1 failed, 42 deselected`, `install_passes_no_release` `1 failed, 42 deselected` (`4/4 killed`) |
| 8 build_operations | `f7d9b03` | `tests/g/ops/test_cli.py::test_api_ops__the_operator_tool_builds_the_postgres_adapters_from_the_environment`; `tests/d/test_operations_pg.py` (d3: `13 passed in 6.96s`) | G6B list: `operations_without_a_database` `1 failed, 41 deselected`; `operations_dsn_ignored` `1 failed, 41 deselected` |
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

RUNS_PLACEHOLDER

## Limits

1. **The S3 path is not exercised against a real bucket here.** The fakes' cases inject
   `objects=`. M1-L2's own conformance ran on MinIO (its evidence). Its
   `tests/m/test_s3.py` cases skip visibly in this lane: there is no
   `INFRX_M_S3_ENDPOINT`, and this lane owns no S3 container. The image refusal under
   "Runs" is HeadBucket against a dead endpoint.
2. **The in-memory upload state stays (G4U limit 1, M3 limit 1).** The upload records and
   `_resume`'s witness (`MediaUploads.by_job`) live in the process. M's stray-payload sweep
   (G2 R2-4) is still open, and the collector is not in the lifespan (G2 request 6).
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
4. **The edge is pinned by parsing, not by running Caddy.** This is I2B's pattern. The
   resolver models only `@private` path patterns, `handle /health` and the catch-all. A new
   `handle` block for a public path would need that model extended.
5. **`VALKEY_URL` is required by the composition unless an index is injected.** That is
   `build_ingress_deps`'s default, from G2. 08 §5 now says so.
6. **E3B layer 0 after the mount.** `ingress_is_mounted()` is now true, so every pending case
   that probes it stops pending and fails until its body is written. That is the design:
   "fail the day `ingress` enters `ROUTERS`". There were 12 `PENDING[G2-R1…]` skips at
   `a0711ac` and there are 0 now. Those 12 cases, plus dr17, rc00 and `test_stage`'s
   held-cutover case, are the 15 new layer-0 failures. They belong to the E3B phase-3 lane
   (see Integration requests).
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
- **I (I2B), `deploy/rehearse.sh`.** The rehearsal was not run here. Read against the cutover,
  three of its steps no longer hold:
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
- **Coordinator: ruling (next free R95), proposed text.** "R95 — The gateway composes its
  durable adapters from settings or refuses (the cutover).
  - `create_app` builds each adapter it is not given: D5's catalog, D4's journal and the
    job store on one pool from `DATABASE_URL`/`DATABASE_POOL_*`, and the object store from
    `S3_MEDIA_BUCKET`, which must answer HeadBucket (M1-L2).
  - It never stages media in process memory outside a test that injects the store.
  - An unset `S3_MEDIA_BUCKET`, or a store to build with no `DATABASE_URL`, refuses in
    every mode, naming the setting.
  - An unset `INFRX_MODE` refuses to start (R44, completed)."

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

## Verification log

- 2026-09-23: Written at implementation SHA `f7d9b03` from the runs quoted above. Every count
  is copied from command output. Only fakes, the d3 task-local PostgreSQL and locally built
  images were used. Nothing is deployed or live-verified.
