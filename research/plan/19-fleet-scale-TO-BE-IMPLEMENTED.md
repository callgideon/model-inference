# 19 — Multi-GPU fleet for the Marlin endpoint: TO BE IMPLEMENTED

**2026-09-24 follow-on proposal:** use [roadmap 23](23-inference-hosting-roadmap.md) for the current discussion framework. This note preserves historical hypotheses/measurements, not a fleet provisioning instruction. I4 exists as a conditional task; no detailed fleet implementation was accepted here. The later bda1586 E1B cells supersede generalizations from run2, and final certification remains distinct. Durable uploads/cleanup and local prepared-file affinity must be resolved before stateless-fleet claims; filesystem snapshots are not loaded GPU-memory snapshots. Two warm/four extra GPUs are not a selected policy.

Status: **TO BE IMPLEMENTED — notes only.** Nothing here is built, provisioned or scheduled.
Written 2026-09-24 from the user's request ("provision 4 more GPUs, and create a middleware or
a distribution handling system that manages the availability and load balancing between GPUs,
and model snapshots for instant scaling and downscaling, with 2 GPUs always on standby - warmed
up") and the user's follow-up ("if we have nothing like that planned yet, just leave it in TO BE
IMPLEMENTED notes"). No instance was launched and no cost was incurred. The manifest
(`tasks.json`, v4) has no package for this; the research tree plans it for the bare-metal cluster
(`research/scaling/10-blueprint.md` §5 scaling policy, §8 phases 1 and 4; `05-autoscaling-and-
predictive-scaling.md`; `06-cold-start.md`), not for the AWS pilot.

## 1. What exists today (measured, release 4226315 on one g6e.2xlarge / 1× L40S)

- One box runs everything: engine (`marlin2b-vllm`, vLLM, `--max-num-seqs 8`), gateway,
  worker (`python -m infrx.worker`: the preparation loop and the inference loop), Valkey (the
  scheduling index, no persistence by design), Caddy edge. Hosted PostgreSQL (Supabase) holds
  every durable fact; M's S3 store holds prepared media.
- Supported arrival rate **0.5 clips/s** on that GPU (E4B run2 envelope); the admission answers
  429 + Retry-After past it. p95 end-to-end ≈ 91 s per clip-minute at 0.5/s (provisional
  target 45 s). Engine start-to-ready 168–181 s (W4 phase B). Weights ≈ 5 GB on the box's
  instance-store NVMe (`/opt/dlami/nvme`, lost on stop); the S3 mirror does not hold them yet.
- The design already separates the pieces a fleet needs: admission and streaming live in the
  gateway and the durable journal (D4: a relay can serve a stream from the journal, so it need
  not be co-located with the worker that produced it); workers pull work from the shared index
  (Q2/Q3: fenced leases, fairness, reconciler rebuild); preparation writes the local media
  cache the engine reads by `file://` path, so a worker and its engine share a node.

## 2. Target shape (proposal, unmeasured)

- **Unit of capacity = one GPU node** (g6e.2xlarge, 1× L40S, the shape the release is certified
  on): engine + worker + local Valkey-less. N active nodes + **2 warm standby nodes** (engine
  loaded and warmed, worker in *standby*: heartbeating, not claiming).
- **Shared state**: one Valkey the whole fleet reaches (the index is a rebuildable cache, so a
  single small instance is enough; the worker's reconciler rebuilds it from PostgreSQL every
  10 s); hosted PostgreSQL as today; M's S3 store as today (any node fetches the prepared
  object; the local `file://` cache is per node).
- **Gateway tier**: the gateway is stateless apart from the journal it reads from PostgreSQL;
  run it on every node behind one edge (an NLB or DNS to the edge nodes), or on two small
  non-GPU nodes. Admission caps (`MAX_ACTIVE_JOBS`, `MAX_PREPARING_JOBS`) become fleet-wide
  numbers the controller sets from the active node count.
- **Load balancing** is the queue: workers claim; no request-level router is needed for the
  async/stream path. Fairness and per-tenant caps stay in the scheduler (Q3).
- **Fleet controller** ("distribution handling system"): a node registry in PostgreSQL
  (`infrx.nodes`: id, instance id, state ∈ {booting, warming, standby, active, draining,
  stopped}, last heartbeat, GPU metrics, serving version, release); state transitions driven
  by worker heartbeats + the AWS API; scale rules from `infrx_queue_depth`, in-flight per node
  and the prefill backlog (blueprint §5.2: queue-depth on Marlin): promote a standby (instant —
  the worker starts claiming) and boot a replacement standby; demote an idle active node to
  standby, stop the surplus beyond 2 standby after a stabilisation window (anti-thrash:
  ≤ 1 scale-down per 10 min, 30-min stabilisation, drain before stop — blueprint phase 4 exit
  criteria). Nothing predictive until the σ test passes (blueprint §5.3).
- **Model snapshots** = a golden image per certified release: AMI (root) + an EBS snapshot
  holding the weights, the runtime images and the release checkout, versioned by release SHA
  and serving version; boot script verifies digests, starts the engine, runs the warm-up
  requests, reports *warm*. Weights must also be mirrored to the project bucket (today only
  DeepSeek is) so a node can rebuild from S3 if the snapshot lags. Target: standby-to-active
  0 s; boot-to-warm ≈ engine start-to-ready + image boot (⚠️ TO BE MEASURED; the pilot's
  engine start is ~3 min).
- **Warm standby** = engine up, CUDA graphs captured, a warm-up request answered, worker
  heartbeating in standby. The cost of a standby is a full node-hour.

## 3. Cost and capacity facts to settle before provisioning (⚠️ TO BE VERIFIED)

- g6e.2xlarge on-demand in us-east-1 ≈ $1.86/h (`est.`, AWS list price; not in
  `research/cross-cutting/cloud-pricing.md`, which must gain the row before any number is
  quoted elsewhere). Four more nodes ≈ $7.4/h ≈ $180/day always on; two permanent standby ≈
  $90/day of idle GPU.
- Capacity: GPU instances are scarce in us-east-1 (CLAUDE.md: `g6e` needs AZ retries; 1d
  worked). Check the "Running On-Demand G and VT instances" vCPU quota (5 × 8 vCPU = 40)
  before any launch.
- Alternative shape: one g6e.12xlarge (4× L40S) is one failure domain and cannot downscale
  per GPU; it fits "4 replicas" but not "instant scaling and downscaling". Not recommended.

## 4. Work packages when this is scheduled (each an isolated `codex/<task>` lane)

1. **SNAPSHOT (W/I)**: weights → S3 mirror (from the box, maintenance window); golden AMI +
   EBS snapshot build from a certified release; boot script with digest checks and warm-up;
   measured boot-to-warm. Gate: a fresh node serves the smoke (W12) from the snapshot alone.
2. **FLEET-STATE (D/Q)**: `infrx.nodes` schema + heartbeats + standby mode in the worker
   (claims paused, engine kept warm) + fleet-wide admission caps; conformance + mutants.
3. **FLEET-CTRL (I/Q)**: the controller (AWS launch/stop from the image, promote/demote,
   drain, anti-thrash), with a fake cloud API for tests; runbooks (scale, node loss, image
   rotation); alerts.
4. **EDGE (I)**: shared Valkey, edge fan-out (NLB or DNS), security groups; the rollout
   runbook extended to N nodes (one release per fleet, rolling install, standby first).
5. **E-track**: the certification protocol gains fleet cells (node loss during a stream,
   promote under load, scale-down drains, snapshot boot time).

## 5. Open decisions for the user

- Confirm the unit (single-GPU nodes) and the standing cost of two warm standby nodes.
- Whether the 4 GPUs are provisioned before the fleet software exists (idle cost) or at
  package 1's gate (the coordinator's recommendation).

## Verification log

- 2026-09-24: Written as notes only, per the user's instruction; no AWS call, no launch, no
  cost. Facts from E4B run2 (`models/marlin2b/results/E4B-box-4226315/run2-…`), W4 phase B,
  the Q/D/M designs on `claude/backend-impl`, and `research/scaling/10-blueprint.md`.
