# AP-05 hosting: the one approved host and the real candidate smoke

**Coordinator only. Never run by a lane.** This directory is the infrastructure adapter of
the private-deployment controller (`apps/infrx-api/infrx/lab/hosting/`, contracts.md §5):
the one approved host/profile is the pilot box (`i-0e8449a4ffca29bab`, one L40S 46 GB) with
the Marlin profile `marlin2b-vllm-l40s-bf16-v1` (models/marlin2b/serving-version.json).

| File | What it is |
|---|---|
| `infrx-candidate@.service` | the candidate unit: `serve.sh` with `PORT=%i GPU=0 WEIGHTS=<installed dir>`, container `marlin2b-%i`, started/stopped only by the controller's `BoxLauncher`; port 8000 refused |
| `window.sh` | the box step (`infra/rollout/ssm.sh`): `STATE=open` pauses the serving engine and installs the unit; `STATE=close` stops every candidate and restores the serving engine |

## Why a window

`serve.sh` pins `--gpu-memory-utilization 0.90`, so the L40S cannot hold the serving engine
and a candidate at once. A candidate therefore runs only while `marlin2b-vllm.service` is
stopped, inside a maintenance window the coordinator opens (no consumer traffic is evicted:
the edge answers 503 + Retry-After for the window's duration). The window never overlaps an
E4C run. Locally (and in every lane proof) the engine is `tests/integration/fake_vllm.py`
on the lane's port (ap5: 57557), launched by `LocalLauncher` - `tests/ap05`,
`tests/integration/lab_hosting`.

## Procedure (one window, ~30-45 min est.: engine stop, candidate load ~3-5 min, smoke, restore)

Preconditions: a release carrying AP-05 (0062 applied in an R151 window - 0062 is LOCAL-ONLY
until then), the Lab control service with `LAB_HOSTING` on (wiring WR-AP05-2), the hosting
worker role (WR-AP05-3), the AP-04 adoption of the production Marlin artifact (so its serving
revision has a verified manifest), a test provider workspace and a developer session. No E4C
window active.

1. `infra/rollout/ssm.sh infra/rollout/steps/95-maintenance.sh RELEASE=<sha>` (edge 503,
   gateway drained).
2. `infra/rollout/ssm.sh infra/lab/hosting/window.sh STATE=open RELEASE=<sha> PORT=8100`
   (serving engine stopped, GPU free, unit installed). Exit 3 = refused, nothing changed.
3. Start the hosting worker role with `HOSTING_LAUNCHER=box`, `HOSTING_SLOT=pilot-l40s/candidate-0`,
   `HOSTING_PORT=8100`, `HOSTING_MODEL_ROOT=/opt/dlami/nvme/hosting`,
   `HOSTING_SOURCE_DIR=/opt/dlami/nvme/marlin2b` (the measured served bytes),
   `HOSTING_SMOKE_VIDEO=<a verified-corpus clip of at most 72 s>` (engine_limits:
   the encoder cache rejects longer clips) and `HOSTING_ENV_DIR=/etc/infrx-lab/hosting`.
4. Through the API only (the lifecycle runner's stage 04/05, or the Lab):
   `POST /lab/v1/control/deployments` (the adopted revision, `expire_after_s=3600`) -> poll the
   operation until `succeeded`; `GET .../readiness` shows the identity receipt: the installed
   bytes equal the artifact manifest and the revision pins, `docker inspect` reports the pinned
   image digest, the serve.sh flags (options digest `sha256:3c4bbface…`) and `/model` mounted
   from the installed directory. `POST .../smoke` -> poll -> `ready_private`; the smoke receipt
   carries the HTTP status, wall time, prompt/completion tokens (a video prompt is >= 256
   prompt tokens) and the clip's digest.
5. `POST .../retire` -> poll -> `retired`, allocation `released`, unit stopped, directory removed.
6. `infra/rollout/ssm.sh infra/lab/hosting/window.sh STATE=close RELEASE=<sha>` (every
   candidate stopped, serving engine restarted and `/health` 200; exit 4 = it did not answer:
   do not reopen, escalate), then `infra/rollout/steps/56-resume.sh RELEASE=<sha>`.

Abort at any point: step 6 (it is idempotent and stops only `infrx-candidate@*` units and
`marlin2b-<port>` containers named by the controller's env files, never port 8000).

## What the window proves and what it does not

It proves 05b-05e on real hardware: a task-owned port and unit, the pinned engine's real
identity, one real finite-video request through the candidate, receipts, drain and cleanup.
It does NOT prove the private admission path through the gateway (the gateway has one
`UPSTREAM`; routing a dev endpoint to a candidate engine and its provider_dev key are AP-06 /
coordinator wiring), concurrent serving and candidate (impossible on this GPU), or any
autoscaling.
