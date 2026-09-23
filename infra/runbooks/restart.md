# Restart — engine, worker, gateway, host, drain

What a lost process costs and how the system gets it back, per component. The durable
state is PostgreSQL (hosted); everything on the box can die without losing an accepted
job, **as long as the reaper runs and the index is rebuilt** — drills `test_i3b_rc01`,
`rc02`, `rc08`, `rc09` prove that on the reference store, with a real engine process and
the real Valkey index. Conventions and the `ssm` helper: [README.md](README.md).

What recovery guarantees, and what it does not:

| Lost | Accepted jobs | Money | Customer sees |
|---|---|---|---|
| a worker mid-attempt, nothing published yet | requeued as a new generation after the lease TTL (120 s), then completed | charged once, by the attempt that finishes | a delay |
| a worker after output was published | failed `lost_after_publication`, never regenerated | nothing charged; the hold stays until the 24 h unknown-usage release | a failed job with partial output replayable |
| the engine mid-stream | the attempt fails `platform_error` | nothing charged (platform-absorbed) | a failed job; retry is the client's |
| the index (Valkey) | none: rebuilt from PostgreSQL | unchanged | a delay |
| the gateway | none after acceptance: the idempotent retry returns the same job | unchanged | a dropped connection; retry with the same `Idempotency-Key` |

## Triage

```bash
# step: triage.sh (SSM) - what is down, without changing anything
systemctl --no-pager --plain list-units 'marlin2b-*' 'infrx-*' | cat
docker ps --format '{{.Names}}\t{{.Status}}'
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader
curl -s -o /dev/null -w 'engine /health %{http_code}\n' 127.0.0.1:8000/health
curl -s -o /dev/null -w 'gateway /health %{http_code}\n' 127.0.0.1:8001/health
df -B1 --output=target,avail,size / /opt/dlami/nvme
# after I2B wires /metrics: curl -s 127.0.0.1:8001/metrics | grep -E '^infrx_(component_up|gpu_up|disk_free_ratio)'
```

## Component down

`ComponentDown{component}` names what failed its probe. Go to: `engine` → [Engine](#engine);
`index` → [index-loss.md](index-loss.md#index-loss); `database` or `journal` → check the
hosted project's status page and the pooler first (the database is not on the box; nothing
here restarts it), then [reconcile.md](reconcile.md#drift) once it is back; `object_store`
→ the bucket/endpoint (PENDING M3: no S3 adapter in the runtime yet; drill `rc05` shows the
behaviour: preparation retries within its budget, then fails free); `price_source` →
[rollback.md](rollback.md#maintenance) until the rate card resolves.
`JournalSlow` is the same database path measured rather than down.

## Engine

Alerts: `ComponentDown{component="engine"}`, `GpuUnavailable`, `PlatformFailureRate`.
Drill: `test_i3b_rc02` (a real engine process SIGKILLed mid-stream: the attempt fails
`platform_error`, debit 0, nothing published; a restarted engine serves the queue).

```bash
# step: engine-restart.sh (SSM)
set -euo pipefail
nvidia-smi >/dev/null                      # no GPU = a host problem, go to "Host"
systemctl restart marlin2b-vllm.service    # docker stop (10 s drain today) + start; PENDING W3/I2B: 120 s drain
for i in $(seq 1 120); do                  # model load; the window is not measured yet
  curl -sf 127.0.0.1:8000/health >/dev/null && break; sleep 5
done
curl -sf 127.0.0.1:8000/v1/models
journalctl -u marlin2b-vllm.service --since '-15 min' --no-pager | tail -n 40
```

Window: ⚠️ TO BE VERIFIED (P-18) — measure SIGKILL of the engine container to the first
successful `/health` on the box (the model is 5.44 GB on NVMe; vLLM pre-allocates the KV
pool at start). Local `rc02` restarts a fake engine in under a second and says nothing about
vLLM. **Cold start after a host stop:** the NVMe is instance store and loses the weights;
the download needs a valid `HF_TOKEN` (`/model-inference/hf_token`) and Hugging Face being
up ⚠️ (no S3 mirror of Marlin exists; infra/README.md §6).

## Worker

Alerts: `LeaseLost`, `ReaperTerminalized`. Drills: `test_i3b_rc01` (a worker dies holding two
leases), `rc08` (drain). **PENDING W3/I2B**: the worker process and its unit
(`infrx-worker.service`, proposed) and the reaper timer do not exist on the box yet.

```bash
# step: worker-restart.sh (SSM) - PENDING I2B unit names
set -euo pipefail
systemctl restart infrx-worker.service
systemctl is-active infrx-worker.service
# The reaper requeues the dead worker's prepublication attempts once their lease expires
# (LEASE_TTL_S = 120 s) and fails the published ones honestly; nothing here has to touch
# a job. Confirm afterwards with reconcile.md#drift.
```

A worker that restarts **does not resume** its old leases (r1 R46): a new generation is the
only way back, so a restart inside 120 s just waits for the reaper.

**The box drill must SIGKILL the worker process** (`systemctl kill -s KILL
infrx-worker.service`, then start it), not `restart` it: the local drills' worker "kill" is a
task cancellation, whose `finally` acknowledges the in-flight candidate (`WorkerLoop.claim_one`),
so the index's own expiry path - an unacknowledged candidate resurfacing after `LEASE_TTL_S`
with no acknowledge - is exercised by no drill yet. Pass: the in-flight job is requeued by
the reaper (or failed `lost_after_publication` if it had published) within TTL plus one
reaper tick. ⚠️ TO BE VERIFIED (P-18) until the coordinator runs it.

## Gateway

The gateway holds no durable state: after acceptance the job is in PostgreSQL, before it
nothing was accepted. `test_i3b_rc03` is **PENDING G1R/G2** (no metered route mounted); the
store half (crash after commit, idempotent retry = same job) is E3B's `dr01`.

```bash
# step: gateway-restart.sh (SSM)
set -euo pipefail
systemctl restart marlin2b-gateway.service
for i in $(seq 1 30); do curl -sf 127.0.0.1:8001/health >/dev/null && break; sleep 1; done
curl -s -o /dev/null -w 'public /v1/models %{http_code}\n' https://marlin2b.callbill.ai/v1/models
```

## Drain

Planned stops (deploys, host maintenance) drain rather than kill. Drill `test_i3b_rc08`: the
draining worker stops claiming, waits up to its bound, then **releases** what it could not
finish without settling it; the reaper requeues it after the lease TTL; queued candidates
stay in the index. The process-level SIGTERM path is `rc08b`, **PENDING W3/I2B** (a worker
entry point and a unit with `TimeoutStopSec` ≥ the drain bound; infra/README.md §2 proposes
120 s). Today's engine unit drains for docker's default 10 s: in-flight generation beyond
that is cut and fails `platform_error`, free.

```bash
# step: drain.sh (SSM) - PENDING I2B/W3 drain hook; today: stop admission first
set -euo pipefail
# 1. stop admission: rollback.md#maintenance (hosted, after 0006 is applied) or stop the gateway
# 2. let the worker finish or release: systemctl stop infrx-worker.service   (PENDING I2B)
# 3. stop the engine last
systemctl stop marlin2b-vllm.service
```

## Host

Alerts: `HostMemoryLow`, `GpuUnavailable`, several `ComponentDown`s at once. Drill
`test_i3b_rc09` loses the worker, the engine process and Valkey together and recovers every
accepted job (the store survives - on the box it is hosted). **[cost] [irreversible for the
instance store]** A stop/start moves the instance to new hardware and **empties
`/opt/dlami/nvme`** (weights, today's `usage.jsonl`); a reboot does not.

1. Take a snapshot first if anything on the root volume changed since the last one
   ([restore.md](restore.md#box-snapshot)).
2. Prefer `reboot` to stop/start:
   ```bash
   # coordinator host
   $AWS ec2 reboot-instances --instance-ids "$BOX"
   $AWS ec2 wait instance-status-ok --instance-ids "$BOX"
   ```
3. Bring the box back in dependency order, each waiting for the previous one's readiness:
   engine ([Engine](#engine)) → Valkey (empty by design: [index-loss.md](index-loss.md#index-loss)
   rebuilds it) → worker ([Worker](#worker)) → gateway ([Gateway](#gateway)).
4. Reconcile: [reconcile.md](reconcile.md#drift).

Window: ⚠️ TO BE VERIFIED (P-18) — reboot to first successful authenticated request, and
separately stop/start including the weight download.

## Saturation

`InflightSaturated` / `RejectionsHigh` are load, not failure: the gateway refuses with 429 +
`Retry-After` and admission refuses with `capacity_exhausted`, and nothing is lost. Do not
restart anything and do not raise `MAX_INFLIGHT`, `--max-num-seqs` or the index caps from a
page: those limits come from E1B/W4 measurements. Check `infrx_queue_oldest_wait_seconds`
([index-loss.md](index-loss.md#queue-stalled)) and whether the engine is actually generating
(GPU utilization). Refusals by hashed tenant show whether one tenant is the cause.

## Verification log

- 2026-09-22 (I3B.c): Written from drills rc01/rc02/rc03/rc08/rc09 (local, E2's stack) and the
  I1B inventory's unit names. No step has run on the box; every window is ⚠️.
- 2026-09-23 (I3B fix round, D4): the box worker drill is a SIGKILL of the process, because
  the local drills' cancellation acknowledges the candidate a real crash would leave in flight.
