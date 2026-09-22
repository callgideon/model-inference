# Disk — exhaustion, slow preparation

Conventions: [README.md](README.md). Drill: `test_i3b_rc07` fills a real 256 KiB tmpfs under
M2's processing cache: the disk gauge reads ~0 free and `DiskAlmostFull` fires; preparation
cannot write, the reaper retries it within its budget and then fails the job
`preparation_failed` - **free, hold released, nothing lost**; once space is freed a new job
prepares and completes. A full disk costs availability, not money or accepted jobs.

What writes to disk on the box (infra/README.md §2 budgets, `est.`): the processing cache
and staged media (`/var/lib/infrx/media`, PENDING I2B; M's), the trace spool (T's; it
refuses below 2 GiB free), the engine's compile cache, the gateway's usage logs, journald
and Docker's images and container logs. `/opt/dlami/nvme` is instance store.

## Disk almost full

`DiskAlmostFull{mount}` (< 10 % free, or the mount could not be read at all - the gauge
reports 0 then, on purpose).

```bash
# step: disk-triage.sh (SSM) - read-only
df -B1 --output=target,size,avail,pcent / /opt/dlami/nvme
du -xh --max-depth=2 /var/lib /opt/dlami/nvme 2>/dev/null | sort -h | tail -n 15
docker system df
journalctl --disk-usage
find / -xdev -name '*.part' -mmin +10 -printf '%s %p\n' 2>/dev/null | sort -n | tail
```

Safe to free, in this order:

1. `*.part` files older than a few minutes under the processing cache: a failed cache write
   leaves its temporary behind (**found by `rc07`**: one `.part` per failed attempt stays on
   the full disk - an integration request to M), and nothing ever reads one.
2. `journalctl --vacuum-size=500M`.
3. Docker: stopped containers and dangling images only - `docker container prune -f`,
   `docker image prune -f`. **Never** `docker image prune -a` and never remove the pinned
   engine image: re-pulling it is the long part of an engine restart.
4. Processing-cache entries past their 7-day life: M's sweep (`ProcessingCache.sweep`, run by
   M's collector, PENDING M3's timer) - not by hand, an entry may belong to a live job.

**Never delete**: `usage.jsonl` / `usage_failed.jsonl` (the legacy gateway's only usage
record - copy them to persistent storage first if the disk they are on must be cleared),
staged sources of live jobs, the engine's weights while it is running, anything under
PostgreSQL's or Valkey's data directories.

Then confirm the gauge recovered and nothing was lost:

```bash
# step: disk-verify.sh (SSM)
df -B1 --output=target,avail,pcent / /opt/dlami/nvme
# after I2B wires /metrics:
curl -s 127.0.0.1:8001/metrics | grep '^infrx_disk_free_ratio'
```

and [reconcile.md](reconcile.md#drift): jobs that failed `preparation_failed` during the
episode are free and their holds released (`rc07` asserts both).

## Disk filling

`DiskFilling` (< 20 % free): the same triage, no urgency. Find the grower (`du` twice, a few
hours apart); if it is the cache or the spool, their own caps are misconfigured (I2B/M/T).

## Preparation slow

`PreparationSlow` (mean preparation time since the last evaluation above half of
`PREPARATION_TIMEOUT_S`): media fetch/decode is slow or starved. Check disk first (a nearly
full volume makes every write slow), then CPU (`infrx_host_load1` against `infrx_host_cpus`),
then the fetch path (source hosts timing out show as `media_fetch_failed` refusals). Jobs
that exceed the preparation budget fail `preparation_failed`, free - nothing is lost, and
nothing here should be restarted to "fix" it.

Window: ⚠️ TO BE VERIFIED (P-18) - time from `DiskAlmostFull` firing to the gauge back above
the page threshold on the box.

## Verification log

- 2026-09-22 (I3B.c): Written from `rc07` (real tmpfs, M2's cache, reference store). The
  tmpfs mount needs passwordless sudo on the drill host. Nothing run on the box.
