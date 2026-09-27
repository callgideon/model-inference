# Robot/task and inference-freshness contract — discovery draft (X3)

**Task X3**, lane `discovery`, wave LW1, 2026-09-27. Slices X3.a–c of
[14-expansion-gates.md](../../plan/14-expansion-gates.md) §X3. Oracle **ROBOT-CONTRACT**
([04-verification.md](../../plan/04-verification.md):115). Pending input **P-14**
([15-pending-inputs.md](../../plan/15-pending-inputs.md):24).

**Manifest status:** planned
**Trial status:** BLOCKED: P-14 — no design partner, robot, task, recordings, action schema, clocks, latency target or local control owner has been supplied (§4). Hardware enablement is prohibited; X4 may not start.

Conventions: [`research/METHODOLOGY.md`](../../METHODOLOGY.md). `meas.` / `est.` / **⚠️ TO
BE VERIFIED** as defined there. Web sources were fetched on **2026-09-27** and are quoted,
not paraphrased into claims. **No robot, GPU, cloud operation, purchase or code change was
involved.** This is architecture discovery, which the failure oracle says "remains useful"
while the contract is blocked ([tasks.json](../../plan/tasks.json) X3 `failure_oracle`).

---

## 0. Summary

1. **The served model is not a policy.** Marlin-2B emits text for finite recorded clips; it
   produces no actions and nothing it returns is safe to drive hardware with
   ([marlin-sop.md](../marlin-sop.md) §5.4). The work that continues without P-14 is SOP
   analysis of recorded robotics video and offline evaluation
   ([15-pending-inputs.md](../../plan/15-pending-inputs.md):24).
2. **π0.5 is a vision-language-action policy** and is a separate integration; world-model
   annotation is a third one ([05-lab-spec.md](../../platforms/05-lab-spec.md):54; tasks.json
   X3 `acceptance`).
3. **OpenPI's remote-inference interface is the adapter reference, not a latency proof**
   ([08-decisions-and-sources.md](../../platforms/08-decisions-and-sources.md):55;
   [07-api-contracts.md](../../platforms/07-api-contracts.md):69). §1.1 records what it
   actually specifies.
4. **Freshness is the first-class failure.** An action computed from an observation that is
   stale at execution time is a failure even if inference "succeeded"
   ([05-lab-spec.md](../../platforms/05-lab-spec.md):54). §2 defines it; its bound is P-14.

---

## 1. X3.a — Policy, robot and schema record

### 1.1 What the reference adapter specifies (sourced, 2026-09-27)

From [openpi `docs/remote_inference.md`](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md)
and the [openpi README](https://github.com/Physical-Intelligence/openpi):

| Item | Reference value | Status for our contract |
|---|---|---|
| Transport | websocket (`WebsocketClientPolicy`); server `scripts/serve_policy.py --env=[DROID \| ALOHA \| LIBERO]`, "default: 8000" port | example only |
| Client | `openpi-client` package (`packages/openpi-client`) | example only |
| Observation keys | `observation/image`, `observation/wrist_image` (uint8, 224×224 by default), `observation/state` (unnormalised proprioception), `prompt` | example schema; the partner's is P-14 |
| Action output | `actions` with shape `(action_horizon, action_dim)` | the horizon and dimension are P-14 |
| Call pattern | "you typically only need to call the policy every N steps and execute steps from the predicted action chunk open-loop in the remaining steps" | makes observation age grow across a chunk (§2.1) |
| π0.5 checkpoints | `gs://openpi-assets/checkpoints/pi05_base` (fine-tuning), `pi05_libero`, `pi05_droid` | candidates, not a selected artifact |
| Platforms named | DROID (Franka), ALOHA, LIBERO (simulation), UR5 | none is our partner's robot until P-14 says so |
| Inference memory | "> 8 GB", RTX 4090 given as an example GPU | vendor statement, not `meas.` here |
| Licence | Apache-2.0 (repository header) | ⚠️ TO BE VERIFIED per checkpoint: the weights' terms may differ from the code's; method: read the checkpoint bucket's licence file before any use |

⚠️ **TO BE VERIFIED:** π0.5's action horizon, control frequency and normalisation-statistics
files per checkpoint; the README fetch did not state them. Method: read the per-checkpoint
training config in `src/openpi/training/config.py` at a pinned commit.

### 1.2 Recording format facts (sourced)

[MCAP spec](https://mcap.dev/spec), fetched 2026-09-27: "a modular container file format for
recording timestamped pub/sub messages with arbitrary serialization formats". Each Message
record carries `log_time` and `publish_time`, both `uint64` "nanoseconds since a
user-understood epoch (i.e unix epoch, robot boot time, etc.)"; format major version 0. The
epoch is therefore **not** self-describing: the contract must record which clock each
recording uses (§4 "Clocks"). ROS 2 distribution and rosbag2 storage plugin in use are the
partner's; ⚠️ the ROS 2 releases page returned an access-denied page to this session and is
not cited.

---

## 2. X3.b — Freshness, placement, fallback and evidence

### 2.1 Definitions (method, not targets)

- `t_obs` — capture time of the newest sensor sample in the observation, on the robot clock.
- `t_act(i)` — execution time of action `i` of the returned chunk, `i = 0 … H−1`, at control
  period `Δ`: `t_act(i) = t_recv + i·Δ`.
- **Observation age at execution** `a(i) = t_act(i) − t_obs` = sensor→encode + uplink + queue
  + inference + downlink + `i·Δ`.
- **Stale action.** `a(i) > A_max` for the task's bound `A_max`; the adapter rejects it
  (records `stale`, never executes it) and the robot-side controller applies its fallback.
- **Proposed vs executed.** Every action has two records: what the policy proposed (with
  `t_obs`, chunk id, index `i`) and what the controller executed (or `rejected_stale` /
  `overridden` / `estop`), on the same clock. Outcomes are scored from executed records;
  proposals alone are never scored as behaviour (ROBOT-REPLAY, 04-verification.md:116).

### 2.2 Measurement plan (not run — needs a robot and a placement)

| Boundary | Stamp | Where |
|---|---|---|
| sensor capture | `t_obs` | robot, hardware or driver timestamp |
| request sent / received | `t_tx`, `t_rx` | robot client / serving host |
| inference start / end | `t_inf0`, `t_inf1` | serving host |
| chunk received | `t_recv` | robot client |
| action executed | `t_act(i)` | robot controller |

Clock offset between robot and host is measured, not assumed (e.g. both disciplined to
one PTP/NTP source, offset logged per session). Report `a(i)` distributions per placement
(onboard / site-local / cloud) at the partner's control rate. No general WAN suitability
follows from any single placement (tasks.json X3 `acceptance`).

### 2.3 Fallback and stop authority

The local controller keeps actuator authority and emergency stop; the remote policy only
proposes (X4.c, 14-expansion-gates.md §X4). Who owns that controller, what it does on a
stale or missing chunk (hold, decelerate, safe pose) and who may authorise a hardware trial
are P-14.

---

## 3. X3.c — Offline-first acceptance, adapter mapping, X4 split

### 3.1 Offline and simulator first

Order: (1) recorded-episode replay — feed recorded observations, compare proposals with
recorded executed actions, **no actuation path exists in this mode**; (2) simulator
(LIBERO has a published π0.5 checkpoint, §1.1); (3) only then an authorised, allocated
hardware trial. The metric and passing score at each stage are P-14.

### 3.2 Proposed MCAP ↔ adapter mapping (topic names are the partner's)

| Adapter field | MCAP source | Rule |
|---|---|---|
| `observation/image`, `observation/wrist_image` | camera topics (partner) | message `log_time` → `t_obs`; decode + resize to the policy's input; version the resize |
| `observation/state` | joint-state topic (partner) | units and ordering pinned per schema version; a unit mismatch is a rejection, not a conversion guess |
| `prompt` | task/episode metadata | versioned |
| proposed actions | new topic written by the adapter | chunk id, index `i`, `t_obs`, policy artifact digest |
| executed actions | controller topic (partner) | joined to proposals by chunk id and `i` |

### 3.3 Proposed X4 split (draft — replaced by the approved contract)

Kept inside the parent X4 slices of 14-expansion-gates.md §X4:

1. X4.a1 observation/action codec pinned to the approved schema version; unit and shape rejection.
2. X4.a2 session sequencing and authentication for the policy adapter.
3. X4.b1 freshness rejection at `A_max` per action index; deadline and backpressure behaviour.
4. X4.b2 proposed/executed recording through the §3.2 MCAP mapping.
5. X4.c1 offline replay and simulator failure cases (delay, reorder, disconnect, unit mismatch, late chunk) before any hardware.
6. X4.c2 allocated hardware trial under the partner's local actuator authority and emergency controls.

---

## 4. Contract fields

The ROBOT-CONTRACT failure oracle names the robot schema, latency/freshness target,
stale-action rejection and local control owner; X3.a–c name the rest.
[`../video/check_discovery.py`](../video/check_discovery.py) enforces that every row is sourced
or BLOCKED.

| Field | Value | Source |
|---|---|---|
| Policy artifact | BLOCKED: P-14 — the exact policy checkpoint (π0.5 variant or other) and its digest | — |
| Robot and task | BLOCKED: P-14 — identified design partner, robot model and task | — |
| Camera schema | BLOCKED: P-14 — camera count, placement, resolution, rate and encoding | — |
| Proprioception and state schema | BLOCKED: P-14 — joint/state vector layout, units and rate | — |
| Action schema | BLOCKED: P-14 — action dimension, units, frame and control mode | — |
| Normalization | BLOCKED: P-14 — normalisation statistics and their version for the chosen artifact | — |
| Action horizon | BLOCKED: P-14 — chunk length `H`, control period `Δ` and steps executed per call | — |
| Clocks | BLOCKED: P-14 — robot clock source, MCAP epoch per recording, host synchronisation | — |
| ROS2 and recording versions | BLOCKED: P-14 — ROS 2 distribution, rosbag2 storage plugin and MCAP writer versions | — |
| End-to-end latency | BLOCKED: P-14 — measured `a(i)` at the intended placement (no robot available) | — |
| Freshness target | BLOCKED: P-14 — `A_max` per action index for the task | — |
| Placement | BLOCKED: P-14 — onboard / site-local / cloud choice and network path | — |
| Stale-action rejection | Reject any action with `a(i) > A_max`, record `stale`, never execute; controller applies fallback (§2.1) | 05-lab-spec.md:54; 04-verification.md:116 |
| Local control owner | BLOCKED: P-14 — named owner of the robot-side controller, fallback behaviour and emergency stop | — |
| Proposed-versus-executed evidence | Two records per action on one clock; outcomes scored on executed records (§2.1) | 05-lab-spec.md:54; 04-verification.md:116 |
| Offline and simulator acceptance | BLOCKED: P-14 — metric and passing score for replay and simulator stages | — |
| Hardware trial authorization | BLOCKED: P-14 — who authorises the trial and the allocated robot/time | — |
| Adapter and MCAP mapping | BLOCKED: P-14 — the partner's topic names and message types for §3.2 | — |

---

## 5. What this document does not claim

No robot support, no action output from any served model, no WAN or cloud-placement
suitability, no latency or freshness figure, no arbitrary-modality support, and no licence
clearance for any checkpoint.

---

## Audit log

- 2026-09-27 (X3, lane `discovery`, branch `codex/w5-discovery`, base `9a6c3685`): created.
  X3.a–c drafted as discovery. OpenPI remote-inference and README facts and the MCAP spec
  were fetched 2026-09-27 and quoted; the ROS 2 releases page was access-denied and is not
  cited. Two rules are plan-sourced (stale-action rejection, proposed-versus-executed
  evidence); the other 16 contract fields are BLOCKED on P-14. No robot, GPU, cloud call,
  purchase or code change.
