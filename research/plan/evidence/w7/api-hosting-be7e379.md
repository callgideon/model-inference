# api-hosting (AP-05a-e), code head be7e379: evidence

Lane api-hosting, wave 7 (LW7) batch 2. Branch `codex/w7-api-hosting`, base `b05eb6f4`, code head `be7e3795`. Key `ap5`: PostgreSQL 57556 and engine port 57557. Written 2026-10-02.

## Changed paths (all owned)

- `apps/app/supabase/migrations/0062_deployments_hosting.sql` is new and carries the LOCAL-ONLY header. It adds three tables:
  - `hosting_deployments`: the request (profile, limits, expiry), immutable.
  - `hosting_allocations`: reserved → launched → released, forward only. The capacity rule allows one unreleased allocation per slot and one per deployment.
  - `hosting_receipts`: identity, smoke or health observations, immutable, expiring. A receipt passes only if it names no reason, and only a `health` receipt may be written outside an operation.

  It also adds three SECURITY DEFINER functions:
  - `hosting_request`: one transaction that writes the 0060 `deployment.create` operation, the 0007 private dev `draft` and the hosting row. A replay writes nothing.
  - `hosting_fence`: checks the live lease of an operation on this deployment and locks its 0060 row until commit.
  - `hosting_active`.

  RLS is on and only the control login `infrx_lab_control` has a policy. Browser roles reach nothing. The ROLLBACK lines are in the header.
- `apps/infrx-api/infrx/lab/hosting/`:
  - `__init__.py`: the profile, `LabHosting` (the route port, plus `status()`, the server-side readiness read for AP-06), and the wire models.
  - `store.py`: Pg and Fake stores, fenced writes.
  - `engine.py`: verified install, `LocalLauncher`, `BoxLauncher`, identity `mismatches`, the smoke, the in-flight gauge, and the port check.
  - `controller.py`: the out-of-process controller and the `hosting` worker role's `tasks`.
- `apps/infrx-api/infrx/gateway/routes/lab_deployments.py` (R270; nothing mounts while `rt.lab_hosting` is None) serves:
  - `GET /lab/v1/hosting-profiles`
  - `POST /lab/v1/control/deployments` → 202 OperationDoc
  - `GET .../{id}` and `GET .../{id}/readiness`
  - `POST .../{id}/smoke` and `POST .../{id}/retire` → 202
  - `POST /lab/v1/operations/{id}/cancel`
- `infra/lab/hosting/`: `infrx-candidate@.service` (serve.sh on a candidate port; 8000 refused), `window.sh` (the coordinator-window box step, open/close), `README.md` (the box procedure).
- `apps/infrx-api/tests/ap05/`: conftest, test_hosting, controller_proc, mutants, test_mutants.
- `apps/infrx-api/tests/d/test_upgrade_0062_mutants.py`.
- `tests/integration/lab_hosting/`: candidate_engine (fake_vllm's app plus the `/metrics` in-flight gauge), scenarios, and runner (verdict.json).

## Slices (the seam tests were red before each implementation; the logs were kept in the lane scratchpad)

| Slice | State | Proof |
|---|---|---|
| 05a operations | done | Red: ImportError at conftest. The request is one transaction (operation + draft + hosting row). A replay returns the same operation and deployment, with one draft. Another body under the same key → 409. Doors: a viewer reads and cannot mutate; another provider or an outsider gets 404; no session gets 401. Unsupported profile, limits, pins or a missing verified artifact → 422 naming each reason. No target → 503 and the profile reads `unavailable`. Cancel: a queued create is cancelled and its draft retired; a running one is reconciled (engine stopped, slot released, retired). Cancel is scoped to its workspace. A stale fence writes nothing. |
| 05b allocator + launcher | done | Red: `BoxLauncher` missing. A taken slot gives `capacity_unavailable` (retryable), and the holder is untouched. Real-process SIGKILL of the controller at every boundary of every operation (create: allocated, installed, launched, identity; smoke: identity, smoke, promoted; retire: retired, stopped, released) is resumed once. The engine is found by its tag and never started twice, and the dead holder's fence is refused. A forged or reused pid without the tag in its argv is never stopped or adopted. BoxLauncher acts only on its own tag's unit, never on port 8000, and treats a stopped container as no engine. The window step refuses without maintenance or with port 8000, stops the serving engine before installing, never touches 8000 on close, and exits 4 if the serving engine does not come back. Added after the gate found an orphaned engine: a port held by another process fails the create with `port_in_use` before any start. |
| 05c install + identity | done | Red: 5 failed. Install copies exactly the AP-04 manifest, checks each digest and removes strays. Identity compares the installed bytes (measured now) with the manifest and the revision pins, plus the image digest, the options digest (computed from the running process's own argv), the model dir, the served name and the harness/preprocessor. Each mismatch (image, options, served name, model dir, tampered shard, extra file) fails the create and retires it with its resources removed. The source missing or holding other bytes → `artifact_unavailable`, nothing launched. The profile is pinned to serving-version.json (flags, digest, image). |
| 05d smoke + receipt | done (fake engine) | Red: 11 failed. The smoke re-observes identity, then sends one bounded finite-video request (data: URL mp4 with EOS ids). It checks: 200; the served model; a non-empty answer; prompt tokens ≥ 256 (a text-only answer fails); completion usage; a normal finish; and that the engine is reachable. The receipt records wall time, tokens and the clip/text digests. Promotion to `ready_private` happens only on this operation's own receipts while they are still the newest, passed and unexpired: a newer failed check supersedes an interrupted smoke (`stale_receipt`). A resumed smoke never sends a second request. **The real GPU smoke is a coordinator window** (infra/lab/hosting/README.md). |
| 05e retire/drain/expiry/restart | done | Red: 3 failed. Health receipts are written every minute: a lost engine → `health_failed`, a stale check → `health_expired`, so it is not ready while its domain state stays `ready_private`. A deployment that is expired, past its validation window or an abandoned draft is retired by the controller. Engine silent past the launch window → `engine_timeout`; vLLM died while loading → `engine_exited`; an operation older than 2 h → `deadline_exceeded`. Retire cancels and waits for the deployment's running work, then drains the in-flight requests the engine reports. The drain is bounded (300 s). A real 15 s streamed request finishes before its engine stops, then the process group is gone. Worker role composition: `tasks(env, connect, owner)`; `HOSTING_PORT` must be 8100-8199. |

## Commands (exit codes and counts)

- `uv run --frozen pytest -q tests/ap05` (no key): exit 0. 45 fake-world cases plus the mutant guards; the PostgreSQL cases skip.
- `INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/ap05`: exit 0, 82 passed, 1 skipped (the PostgreSQL mutant list outside `INFRX_MUTANTS=all`).
- `INFRX_D_TASK=ap5 INFRX_D1_IMAGE=supabase ... tests/ap05/test_hosting.py -m pg`: exit 0, 30 passed.
- `INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/d -k 0062`: exit 0, 33 passed. That covers 8 checks; the upgrade over consumer history with re-run, the file's ROLLBACK lines and forward again; and 22 SQL mutants killed. With `INFRX_D1_IMAGE=supabase`: exit 0, 33 passed.
- `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap05/test_mutants.py -k 'not pg_mutant'`: exit 0, 79 passed. That is 73 fake-world mutants killed (including 5 on the bash box step, run through a Python-only-compile runner as track I does), 0 survivors, the every-case guard over 32 cases and the runner self-tests.
- `INFRX_MUTANTS=all INFRX_D_TASK=ap5 ... -k pg_mutant`: 5 PostgreSQL and real-process mutants (unfenced writes, receipts oldest-first, pid adoption, held port, real in-flight) are all killed. The first four were killed in the full run. `real_in_flight_ignored` survived it, because uvicorn's SIGTERM grace let the stream finish. The case now streams for 15 s, and the rerun kills it (1 passed).
- `apps/infrx-api/.venv/bin/python tests/integration/lab_hosting/runner.py --out <dir>`: exit 0, gate PASS (g01-g04). `api-hosting-be7e379-gate-verdict.json`.
- `make api-lint`: clean. `make api-typecheck` (pyright JSON count): 458, which is the baseline; no new errors.
- With the WR patch applied (`api-hosting-be7e379-WR.patch`, then reverted):
  - `tests/integration/test_makefile_mutant_lists.py` and `test_harness.py`: 54 passed, 1 failed. The failure is `test_nothing_in_this_directory_points_at_production`, pre-existing at base: `api-lifecycle.sh` names `AWS_SECRET_ACCESS_KEY`. It is AP-11's.
  - `INFRX_D_TASK=ap5 tests/d/test_upgrade_lab.py tests/i/test_migrate.py tests/d/test_control_ops*.py`: 24 passed, 1 failed. The failure is `test_control_ops_upgrade`'s "0060 is the only file past 0059", pre-existing at base since 0061/0064 merged. It belongs to api-schema-2, which owns the 0060..0066 range.
- `tests/contracts/v2/test_v1_projection_pg.py` refused another checkout's d1 container (ForeignContainer). Nothing was touched; it is unrelated to this lane.

## Found by the gate

An orphaned fake engine was left on 57557 by a leaky PostgreSQL mutant (`os.kill(pid, 0)`), and it made g01/g03/g04 fail. The cause was real: a candidate that could not bind its port was answered for by a stranger, so the create's identity read the stranger's `/v1/models`. The smoke's re-check failed safe. Fixes:
- `free(port)` before any start, with `port_in_use` (retryable), a case and two mutants.
- No mutant may leave an engine running (the mutant was replaced).

## Wiring requests (exact patch for 4, 5, 6: `api-hosting-be7e379-WR.patch`)

- **WR-AP05-1 `infrx/gateway/app.py` ROUTERS.** Add `lab_deployments` after `lab_artifacts`. It mounts nothing while `rt.lab_hosting` is None. Composed test: `tests/ap05 ...::test_ap05__without_a_target_nothing_is_queued_and_unwired_nothing_mounts`. Then regenerate the OpenAPI export (`uv run --frozen python -m infrx.contracts.openapi.export`) and `packages/api-client` in the enabled composition.
- **WR-AP05-2 switch `LAB_HOSTING`** (`DeploymentSettings.lab_hosting: bool = False`). In `lab/control/app.py:_compose`, when it is on:

  ```python
  from infrx.lab.hosting import LabHosting
  from infrx.lab.hosting.controller import target_from

  rt.lab_hosting = LabHosting(access, lab_control(connect, access), PgArtifactStore(connect),
                              PgControlOps(connect), PgHostingStore(connect),
                              target_from(os.environ))
  ```

  It runs on the control login (0060–0062 grant it everything). Also, `routes/lab_control.py` (AP-06's file this batch) must not mount its synchronous stand-in `POST /lab/v1/control/deployments/{id}/smoke` when `rt.lab_hosting` is set, because the path collides. Patch: guard that one route with `if getattr(rt, "lab_hosting", None) is None:`.

  Composed test: the tests/ap05 pg world plus `test_ap05__membership_and_role_decide_every_door`.
- **WR-AP05-3 worker role `hosting`.** In `infrx/lab/workers/__main__.py`:

  ```python
  ROLES += ("hosting",)
  NEEDS["hosting"] = controller.NEEDS
  BUILD["hosting"] = lambda mode, env, connect, objects, worker_id, **_: (
      hosting_tasks(env, connect, owner=worker_id), None)
  ```

  Here `hosting_tasks` is `infrx.lab.hosting.controller.tasks`. The box side (infra/lab/rollout lib.sh ROLES, 50-lab-role names `HOSTING_*`, a health port such as 8018) is the coordinator's.

  Composed test: `test_ap05__the_hosting_role_hosts_only_its_configured_slot_on_the_box_launcher`.
- **WR-AP05-4 `Makefile`.**
  - api-mutants gains `INFRX_D_TASK=ap5 ... tests/d/test_upgrade_0062_mutants.py` and `INFRX_D_TASK=ap5 ... tests/ap05/test_mutants.py`.
  - A new target `lab-hosting` (runner → `research/plan/evidence/w7/AP05-hosting-raw-<sha>`), added to `.PHONY`.
  - Proven: `test_makefile_mutant_lists.py` green with the patch.
- **WR-AP05-5 `tests/integration/test_harness.py`:** add `0062_deployments_hosting.sql` to the migration list. **WR-AP05-6 `tests/d/test_upgrade_lab.py`:** add the three 0062 tables to NEW_TABLES. Both are proven with the patch. api-schema-2's range edits (tests/i pins, `test_control_ops_upgrade`) must include 0062.
- **WR-AP05-7 (AP-04 / api-artifacts-2).** AP-04's `GET /lab/v1/operations/{id}` reads AP-04's `MemoryControlOps`. Deployment operations live in 0060 (`PgControlOps`), so the 202 `Location` resolves only once WR-AP04-2 swaps that read to 0060's `get(operation_id, actor)`. Until then a client reads the deployment through `GET .../deployments/{id}` (its `operations`).
- **WR-AP05-8 (api-lifecycle-2, stage 04/05).** Set the AP-05 owners to None in `stages/__init__.py` once mounted, and add a `s04`:
  1. Read `GET /lab/v1/hosting-profiles` and require `availability.state == configured`.
  2. `ctx.mutate("deployment.create", "POST", "/lab/v1/control/deployments", origin="lab", json={serving_version_id: <stage 03>, endpoint_name, max_input_tokens: 16384, max_output_tokens: 1024, expire_after_s: 3600}, extract=accepted_operation)`.
  3. Poll `GET /lab/v1/control/deployments/{id}` until `operations` is empty, with a bounded wait.
  4. Require `state == validating` and readiness `identity.passed`.
  5. Call `ctx.own("deployment", id, {"method": "POST", "route": ".../{id}/retire"})`.

  Stage 05's smoke is the same pattern: `POST .../{id}/smoke` → `ready_private`.

## Schema requests

None (0062 is this lane's). Note: the gateway login `infrx_runtime` holds no 0062 grant. The routes are meant for the Lab control unit; mounting them on the gateway composition would need a grant request first.

## Open / not done

- The real candidate smoke on the pilot L40S is a coordinator window (README procedure; serve.sh pins 0.90, so the window pauses `marlin2b-vllm`). It needs AP-04's adoption of the production Marlin artifact, so that its revision has a verified manifest.
- **The private admission path through the gateway is not exercised.** The gateway has one `UPSTREAM`, so routing a dev endpoint to its candidate engine is coordinator/AP-06 wiring together with the provider_dev key. The smoke goes to the candidate engine on its loopback port.
- No autoscaling or scale-to-zero is claimed. The fields are literals (`max_replicas: 1`, `warm_policy: always_on`); `expire_after_s` (1 h–7 d) bounds GPU time.
- Ponytail ceilings:
  - One slot and one source directory per controller.
  - Install reads a local directory (object-store fetch of uploaded artifacts later).
  - A smoke retried after its deployment moved on is a 409, not a replay.

## Estimate (remaining for AP-05 to close: wiring, review fixes, the box window)

Optimistic 4 h, likely 7 h, pessimistic 12 h. Confidence: medium.

Basis: every local oracle is green on ap5 (both images), and the real-process kill and gate pass. What remains:
- 4 composition patches.
- The gateway dev-endpoint routing hop, which is AP-06/coordinator and can surface admission-path work.
- One box window. vLLM load and the restore of the serving engine are the timing risk; serving-version.json's encoder limit means the smoke clip must be ≤ 72 s.
