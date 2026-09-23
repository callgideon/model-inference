# E4B — Release decision for the Marlin endpoint release candidate

**Template, software half filled. BACKEND-READY is not claimed and cannot be from this
file's current contents.** It needs the box half (P-04 target, the coordinator's maintenance
window), P-20 resolved and the blocking findings of §5 closed; software-only tests cannot
close the gate (18 §E4B acceptance). Public cutover is a separately recorded authorized action.

Protocol: [`models/marlin2b/results/E4B-protocol.md`](../../../../models/marlin2b/results/E4B-protocol.md).
Runner: [`tests/integration/backend/certify.py`](../../../../tests/integration/backend/certify.py).
Endpoint capability document and headless examples: [E4B-endpoint.md](E4B-endpoint.md).
What was built and the runs: the E4B implementation evidence (`E4B-<sha>.md`, this directory).

## 1. Decision

| Field | Value |
|---|---|
| Decision | ⚠️ **PENDING** — not BACKEND-READY |
| Release SHA | ⚠️ TO BE FILLED: the integration SHA the box runs (the box report's `git_head` and `git_head_end`, both clean and equal) |
| Dev-host report | ⚠️ TO BE FILLED: `certify.py --report` at the release SHA, sha256 of the JSON |
| Box report | ⚠️ TO BE MEASURED: the box protocol's `certify.py --box …` report, sha256 of the JSON |
| Decided by, at | the coordinator, after §3's checklist is complete |
| Cutover | a separate, recorded, authorized action after this decision |

## 2. Release identity (computed, never typed)

`certify.py --hashes` prints these from the tree; each report carries them under `hashes`.
Quoted at `b51a548` (the tree the E4B runner was committed on). **The box report's block is
authoritative**: recompute at the release SHA.

| Artifact | Value at `b51a548` |
|---|---|
| serving record `models/marlin2b/serving-version.json` | sha256 `6b1024b662ee8e880842c2c14b2180ca9000aa90c695434426ae8e8557ddd318` |
| `models/marlin2b/serve.sh` | sha256 `9e1f473bd7d8d7ac593468e2e4c4049b0bbff1c78f93cdbf72577b894fb6dd11` |
| engine-options digest (recorded = recomputed from the pinned flags) | `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147` |
| runtime image | `vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42` |
| model | `NemoStation/Marlin-2B@fd111fca4fc7897876fb0d7e9df22ca5ac8ab965`; shards `sha256:5d78fa4d…`, `sha256:01d40ec9…`; tokenizer `sha256:06b95093…`; chat template `sha256:273d8e0e…` (served bytes; registry oid equality ⚠️ TO BE VERIFIED, HF_TOKEN) |
| contract limits (`limits.DEFAULTS`) | sha256 `525eb083ecaa75879a4f0d6fdc0da643ef827d8373d6a5b7bd829c04db205ce9` |
| migrations `apps/app/supabase/migrations/*.sql` | tree sha256 `f373bd1561efe447b377d3bd7f8fb9417f08cd7cb3dcb76235add040da820ef5` |
| deploy tree `apps/infrx-api/deploy/` (I2B) | tree sha256 `828536375dd0ee607f506bf7daa75a8196164d8bc1c316d94185cab0c7a91cdd` |
| rollout tree `infra/rollout/` (I2B) | tree sha256 `372d57cff391e86ce2621381af1fac7f3dce599d02defcfc3a6cd65e899e173e` |
| alert rules `infra/alerts/alerts.json` (I3B) | sha256 `8eec8014fdc120bd54a7779b69a7226d470198a5569c4f7cb6d131a37d5c1243` |
| `apps/infrx-api/uv.lock` | sha256 `cc572b804a41a86caeeff8e612da3a54730ea4cf84737662a8325ac9c93ec24d` |
| `infrx` package (`*.py`) | tree sha256 `6b7d5ae7421b7bf2e83cdcac4ebde05b02f9af9671f5cb41f42f7383c4705131` |
| published release (G6B `marlin_release`) | `nemostation/marlin-2b@2026-09-01`, card `rc_marlin2b_20260901T000000Z_provisional_p01`, engine-options digest `sha256:4444…4444`, image `vllm/vllm-openai:nightly` — **finding B1 below** |
| gateway image | ⚠️ TO BE MEASURED on the box: `docker image inspect` of the image `install.sh` built (`INFRX_CERTIFY_GATEWAY_IMAGE` carries it into the report) |

## 3. Certification checklist

Local = the dev host, the E2 stack, the runner's fake vLLM (every number `fake-engine, not a
measurement`). Box = the box protocol in the implementation evidence. A box row passes only
from the box report.

| Check | Oracle | Local | Box |
|---|---|---|---|
| `e4b.a.protocol` — the gate's stack suite (preflight, services, migrate, rls, backend minus `recovery/`) | BACKEND-JOURNEY, DUR-*, MEDIA-SEC, API-* | ⚠️ see the implementation evidence's local run | SHA-bound: the dev-host report at the release SHA |
| `e4b.a.sop-parity` | MARLIN-SOP, MEDIA-PARITY | ⚠️ see the local run | ⚠️ TO BE MEASURED: paired with W4's E0 `parity.jsonl` |
| `e4b.a.dataset-resume` | MARLIN-SOP, BACKEND-JOURNEY | ⚠️ see the local run (client half only) | ⚠️ TO BE MEASURED: needs the metered route (G2-R1) and the ledger adapters (D5) |
| `e4b.b.preconditions` | BACKEND-DEPLOY | ⚠️ see the local run | ⚠️ TO BE MEASURED |
| `e4b.b.config-pin` | ENGINE-OPT, MEDIA-OPT | ⚠️ see the local run (B1) | ⚠️ TO BE MEASURED against `inventory.sh` (B2) |
| `e4b.b.envelope` | PERF-ENVELOPE | ⚠️ see the local run (12 samples: p95 unknown by design) | ⚠️ TO BE MEASURED: ladder 0.5/1/2 req/s × 120 |
| `e4b.b.soak` | PERF-ENVELOPE, BACKEND-OBSERVE | ⚠️ see the local run | ⚠️ TO BE MEASURED: ½ supported rate × 4 h |
| `e4b.b.overload` | PERF-ENVELOPE | PENDING[BOX] by construction (an engine has no admission) | ⚠️ TO BE MEASURED: 32-request burst from one key |
| `e4b.b.recovery` / `e4b.b.recovery-box` | OPS-RECOVER | ⚠️ see the local run (I3B's `rc*`/`bk*` on the E2 stack) | ⚠️ TO BE MEASURED: the runbook drills of §6, run by the coordinator |

## 4. Measured limits, SLOs, cost and quality — coverage

Nothing below is measured yet. Every criterion is provisional (P-18) unless it says otherwise;
none is an SLO.

| Quantity | Measured by | Criterion (protocol §5) | Value |
|---|---|---|---|
| supported arrival rate (req/s and video-s/s) | `e4b.b.envelope` | highest rung, from 0.5 req/s up, with platform failures < 1 % and no refusal within the cap | ⚠️ TO BE MEASURED |
| TTFT p95, clips ≤ 30 s at ≤ 720p | `e4b.b.envelope` | ≤ 6.0 s (01 §2.3 proposal), ≥ 60 samples | ⚠️ TO BE MEASURED |
| end-to-end p95 per clip-minute | `e4b.b.envelope` | ≤ 45 s | ⚠️ TO BE MEASURED |
| platform-caused failure rate | envelope, soak | < 1 % (`decide.MAX_FAILURE_RATE`) | ⚠️ TO BE MEASURED |
| memory growth over the soak (gateway RSS, GPU used) | `e4b.b.soak` | ≤ 512 MiB host, ≤ 256 MiB GPU (W4's limits) | ⚠️ TO BE MEASURED |
| reconciliation at the end of the soak | `e4b.b.soak` | drift 0, unsettleable 0 | ⚠️ TO BE MEASURED |
| overload answer | `e4b.b.overload` | every refusal 429 + Retry-After + an overload code; no 5xx | ⚠️ TO BE MEASURED |
| video duration accepted | envelope's duration-cap criterion; W4 phase B | applied cap 72 s (P-20 interim); engine ceiling 82 s at the pinned 16,384-token encoder budget; 120 s only after W4 adopts E1 and a new serving version | ⚠️ P-20 open |
| engine start-to-ready | W4 E0/E1 (`candidate.sh` records `start_to_ready_s`) | compared with I2B's `ENGINE_READY_S` 900 s | ⚠️ TO BE MEASURED |
| recovery windows (engine, worker, host, restore RTO) | §6's box drills | measured and published; no availability target (single GPU is not HA, P-16) | ⚠️ TO BE MEASURED |
| output parity | `e4b.a.sop-parity` | `decide.parity_verdict` pass against W4's E0 | ⚠️ TO BE MEASURED |
| caption-event parity vs `reference.py` | E1B L8 | events match; parity, not accuracy | ⚠️ TO BE MEASURED (E1B) |
| accuracy | — | **no claim** (P-07: no rubric, no ground truth) | none |
| cost per successful video-hour | envelope × the hourly price | **not publishable** until P-19 adds a sourced `g6e` row | none |

## 5. Remaining inputs and blocking findings

| Id | What | Owner | Blocks |
|---|---|---|---|
| **B1** | The published serving revision (`service.marlin_release`) carries the fixture placeholder engine-options digest `sha256:4444…` and the moving tag `vllm/vllm-openai:nightly`, while W3/W4's evidence pins `sha256:3c4bb…` and the digest-pinned image. Every CREDIT admission pins that revision (R76/R78), so accepted jobs would name a serving version that is not the one measured. `e4b.b.config-pin` fails on it | G6B / coordinator: `marlin_release` reads `serving-version.json` (or the publish step passes both values) | cutover, BACKEND-READY |
| **B2** | The box's engine runs `--max-num-seqs 32` and no `--allowed-local-media-path` (W3 inventory 2026-09-23T03:19Z, the pre-pin `serve.sh`); the pin is 8 and the media root. I2B's cutover keeps 32 on purpose (FC-1) until W4 phase B | W4 phase B decides the value; then either the pin or the deploy is re-declared | BACKEND-READY |
| P-20 | the duration cap: raise the encoder budget (E1) or cap admission; the cutover applies `MAX_VIDEO_SECONDS` | W4 decides, the cutover applies | E4B certification (15-pending-inputs) |
| P-04 / box window | the coordinator's maintenance window: W4 E0/E1, E1B L0–L8, then the E4B box run | coordinator | every box row |
| G2-R1 | the held cutover that mounts the metered ingress | coordinator | the journeys, dataset ledger, overload, dr11 |
| D5 | settlement (`terminalize`) and the PostgreSQL adapters behind G6B's `Operations` (`build_operations`) | D | dataset ledger reconciliation, dr07c, journeys |
| I2B-R4 | the worker composition root `python -m infrx.worker` | coordinator (I2B request 4) | rc08b |
| M1-L2 | an S3-backed ObjectStore | track M (not scheduled; the pilot's media root is local) | rc05b |
| HF_TOKEN | registry oid equality of the served bytes (`serving-version.json` `registry_oid_equality`) | coordinator, with HF_TOKEN on the box (`inventory.sh`) | a "pinned artifact" claim beyond served-bytes digests |
| P-21 | reserve the task-port blocks from the ephemeral range on the dev host | coordinator / host | flake-free dev-host gates |
| P-18 / P-19 / P-07 / P-01 | owner targets; the `g6e` price row; the SOP rubric; the rates | workload owner / pricing / user | promoting any provisional row; cost; accuracy; the rate card |

## 6. Failure and rollback triggers

| Trigger | Action | Runbook |
|---|---|---|
| any box-run check FAIL before cutover | no cutover; fix, re-run the dev-host and box reports at a new SHA | this file, §3 |
| `e4b.b.config-pin` FAIL | no cutover; re-measure (W4 phase B, M4) and re-declare `certify.DECLARED`, or restore the pinned value | E4B protocol §4 |
| install step 8 refused (exit 2) | R1: abort, previous checkout and gateway | [rollout §2](../../../../infra/rollout/README.md#2-revert-paths) |
| step 8 exit 4, or step 9/10 fails, no pilot request accepted | R2: revert to the backed-up units, engine first | [rollout §2](../../../../infra/rollout/README.md#2-revert-paths) |
| pilot has accepted work and must stop | R3: maintenance 503 (`95-maintenance.sh`) | [rollback.md — Maintenance](../../../../infra/runbooks/rollback.md#maintenance) |
| a rollout to roll back after cutover | rollback drill (I3B `rc10`) | [rollback.md — Rollout rollback](../../../../infra/runbooks/rollback.md#rollout-rollback) |
| the host itself | R4: replace the root volume from the pre-rollout snapshot | [restore.md — Box snapshot](../../../../infra/runbooks/restore.md#box-snapshot) |
| engine down, GPU unavailable, platform failure rate | restart the engine | [restart.md — Engine](../../../../infra/runbooks/restart.md#engine) |
| worker lost, lease lost | restart the worker; the reaper requeues | [restart.md — Worker](../../../../infra/runbooks/restart.md#worker) |
| inflight or rejections saturated | saturation triage | [restart.md — Saturation](../../../../infra/runbooks/restart.md#saturation) |
| queue index lost or stalled | rebuild from the durable snapshot | [index-loss.md — Index loss](../../../../infra/runbooks/index-loss.md#index-loss) |
| disk almost full | disk triage | [disk.md — Disk almost full](../../../../infra/runbooks/disk.md#disk-almost-full) |
| reconciliation drift or unsettleable jobs | never repair money by hand | [reconcile.md — Drift](../../../../infra/runbooks/reconcile.md#drift), [Unsettleable](../../../../infra/runbooks/reconcile.md#unsettleable) |
| a hosted migration to rehearse or undo | backup, restore, check on a scratch project | [restore.md — Hosted backup and restore rehearsal](../../../../infra/runbooks/restore.md#hosted-backup-and-restore-rehearsal) |

## 7. Operator runbook index

| Need | Where |
|---|---|
| deploy, cut over, revert | [infra/rollout/README.md](../../../../infra/rollout/README.md) (I2B) |
| every alert's procedure, SSM wrapper, unit names | [infra/runbooks/README.md](../../../../infra/runbooks/README.md) (I3B) |
| alert rules and dashboard | [infra/alerts/alerts.json](../../../../infra/alerts/alerts.json), [dashboard.json](../../../../infra/alerts/dashboard.json) |
| provision a client: grant, issue/rotate/revoke a key, suspend, publish, cancel, reconcile | [`infrx.operations.cli`](../../../../apps/infrx-api/infrx/operations/cli.py) (G6B) |
| the headless client and the per-item dataset recipe | [`client_example.py`](../../../../apps/infrx-api/client_example.py) (G6B), [`bench.py`](../../../../models/marlin2b/bench.py) (E1B) |
| engine tuning in a window | [`measure/candidate.sh`](../../../../models/marlin2b/measure/candidate.sh), [`decide.py`](../../../../models/marlin2b/measure/decide.py) (W4) |
| certify a candidate | [`certify.py`](../../../../tests/integration/backend/certify.py) (E4B) |
| what the endpoint accepts and answers | [E4B-endpoint.md](E4B-endpoint.md) |

## Verification log

- 2026-09-23 (E4B.c): template authored with the software half filled. Hashes quoted from
  `certify.py --hashes` at `b51a548`; findings B1 and B2 from `certify.py`'s config pin
  (B2 from the committed W3 inventory); every box value is ⚠️ TO BE MEASURED. No box, AWS,
  hosted project or GPU was used.
