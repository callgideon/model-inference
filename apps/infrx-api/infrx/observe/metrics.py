"""I3B.a: operational metrics for the Marlin backend, sanitized by construction.

    reg = Registry("gateway")
    reg.inc("infrx_requests_rejected_total", code="capacity_exhausted", tenant=org_id)
    timings = {"queue": 0.012, "prefill": 0.31}               # seconds
    reg.observe_phases(timings)
    response.headers["Server-Timing"] = server_timing(timings)
    body = reg.render()                                      # Prometheus text format 0.0.4

Why it is built this way. R59's rule for operator surfaces applied to telemetry: nothing a
customer sent, and no identifier a customer did not reveal, leaves through a metric.

* **Every label is a closed vocabulary or configuration.** A family declares, per label,
  the values it may carry: a contracts enum (error codes, terminal causes, settlement
  states, dispatch kinds), the registry's configured mount names (`METRICS_DISKS` keys,
  fixed at construction), or - for the two values only configuration and the machine
  produce, a GPU index and the deployed git revision - a short pattern. Anything else is
  written as `other` and counted in `infrx_metrics_label_rejected_total{family}`, so a
  call site that tries to put a prompt, a URL, a key or an exception message into a label
  cannot, and the attempt is visible. There is no free-text label anywhere.
* **Tenants are hashed.** A `tenant` label is always `tenant_label(org_id)` (a 12-hex
  sha256 prefix), never the id: pseudonymous - an operator who already holds an org id can
  match it - and never reversible from the scrape.
* **Values are numbers or nothing.** A negative or non-finite observation is dropped and
  counted as rejected instead of corrupting a sum an alert reads.
* **Phase names are E1B's** (`models/marlin2b/results/E1B-protocol.md`, `bench.PHASES`):
  the names the benchmark client already parses out of `Server-Timing`. `server_timing()`
  renders the header from the same mapping `observe_phases()` records, so the header and
  the histogram cannot disagree on a name.

No module state: one `Registry` per process, owned by that process's runtime.
Stdlib only - the pinned environment has no Prometheus or OpenTelemetry client, and the
text exposition format is a few lines.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from dataclasses import dataclass
from typing import Collection, Mapping

from ..contracts import errors
from ..contracts.records import (TERMINAL_STATES, ExecutionMode, IndexEvent, LeaseKind,
                                 OutboxKind, SettlementState, TerminalCause, TerminalOutcome)

# E1B protocol §2 / `models/marlin2b/bench.py` PHASES, in the same order.
PHASES = ("retrieval", "decode", "prepare", "queue", "prefill", "generate",
          "journal", "persist", "settle")

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
OTHER = "other"
TENANT_LABEL = "tenant"
SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
           120.0, 300.0)


def _values(enum_type) -> frozenset[str]:
    return frozenset(member.value for member in enum_type)


def tenant_label(org_id: object) -> str:
    """Pseudonymous tenant label: `t_` + the first 12 hex digits of sha256(org id)."""
    return "t_" + hashlib.sha256(str(org_id).encode()).hexdigest()[:12]


DISPATCH_KINDS = frozenset({OutboxKind.prepare_dispatch.value,
                            OutboxKind.inference_dispatch.value})
COMPONENTS = frozenset({"engine", "database", "index", "object_store", "journal",
                        "price_source"})
RECOVERY_ACTIONS = frozenset({"requeued", "prepare_redispatched", "terminalized",
                              "hold_released"})
RECONCILE_RESULTS = frozenset({"ok", "drift", "error"})
RETENTION_ABORTS = frozenset({"dependency_unavailable", "object_store_unavailable"})
RETENTION_RETAINED = frozenset({"claim_held", "claim_lost", "foreign_key", "lease_short",
                                "not_claimable", "not_eligible", "not_found", "not_ready",
                                "reference_live", "stale_lease"})
_TENANT = re.compile(r"t_[0-9a-f]{12}")
_DEVICE = re.compile(r"[0-9]{1,2}")
_MOUNT = re.compile(r"[a-z][a-z_]{0,23}")
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
_PROCESS = re.compile(r"[a-z][a-z_]{0,15}")


@dataclass(frozen=True)
class Spec:
    kind: str                                    # counter | gauge | histogram
    help: str
    labels: tuple[tuple[str, frozenset[str] | re.Pattern], ...] = ()
    buckets: tuple[float, ...] = ()


FAMILIES: dict[str, Spec] = {
    # --- phases (Server-Timing names) -------------------------------------------------
    "infrx_phase_seconds": Spec(
        "histogram", "Time spent in each request phase, named as in Server-Timing.",
        (("phase", frozenset(PHASES)),), SECONDS),
    # --- saturation and rejections ----------------------------------------------------
    "infrx_requests_rejected_total": Spec(
        "counter", "Requests refused before or at admission, by contract error code.",
        (("code", frozenset(errors.ALL_CODES)), (TENANT_LABEL, _TENANT))),
    "infrx_inflight_requests": Spec("gauge", "Requests in flight in this process."),
    "infrx_inflight_limit": Spec("gauge", "The configured in-flight limit (MAX_INFLIGHT)."),
    # WR-I8-3 (G7 WR-4): the large-body gate and the refusal drain, `routes/intake.py`.
    "infrx_large_body_slots_in_use": Spec(
        "gauge", "Large request and upload bodies holding a slot now (LARGE_BODY_LIMIT)."),
    "infrx_large_body_slots_limit": Spec(
        "gauge", "The large-body slots configured (LARGE_BODY_LIMIT)."),
    "infrx_large_body_refused_total": Spec(
        "counter", "Large bodies refused 429 because every slot was held."),
    "infrx_intake_drained_total": Spec(
        "counter", "Refused bodies read to their declared end so the caller reads the refusal.",
        (("code", frozenset({"invalid_api_key", "request_too_large", "capacity_exhausted",
                             "deadline_exceeded"})),)),
    "infrx_queue_depth": Spec("gauge", "Index candidates (pending plus in flight), by kind.",
                              (("kind", DISPATCH_KINDS),)),
    "infrx_queue_items": Spec("gauge", "Index candidates in total."),
    "infrx_queue_items_limit": Spec("gauge", "The index item cap (MAX_INDEX_ITEMS)."),
    "infrx_queue_oldest_wait_seconds": Spec(
        "gauge", "Age of the oldest available pending candidate."),
    # --- accepted outcomes ------------------------------------------------------------
    "infrx_jobs_accepted_total": Spec(
        "counter", "Jobs durably accepted, by execution mode.",
        (("mode", _values(ExecutionMode)), (TENANT_LABEL, _TENANT))),
    "infrx_jobs_terminal_total": Spec(
        "counter", "Jobs that reached a terminal state, by state and cause.",
        (("state", frozenset(state.value for state in TERMINAL_STATES)),
         ("cause", _values(TerminalCause)))),
    "infrx_settlements_total": Spec(
        "counter", "Terminal settlements, by settlement state.",
        (("settlement", _values(SettlementState)),)),
    # --- lease loss and recovery ------------------------------------------------------
    "infrx_lease_lost_total": Spec(
        "counter", "Leases lost: refused to their worker, or reaped after expiry.",
        (("kind", _values(LeaseKind)), ("detected_by", frozenset({"worker", "reaper"})))),
    "infrx_recovery_actions_total": Spec(
        "counter", "What the reaper (JobStore.recover) did.",
        (("action", RECOVERY_ACTIONS),)),
    # --- reconciliation ---------------------------------------------------------------
    "infrx_reconciliation_runs_total": Spec(
        "counter", "Reconciliation passes, by result.", (("result", RECONCILE_RESULTS),)),
    "infrx_reconciliation_last_success_timestamp_seconds": Spec(
        "gauge", "Unix time of the last reconciliation pass that found no drift."),
    "infrx_reconciliation_drift": Spec(
        "gauge", "Drift rows found by the last reconciliation pass; 0 is the only healthy "
                 "value."),
    "infrx_holds_unknown": Spec("gauge", "Holds awaiting unknown-usage reconciliation."),
    "infrx_unsettleable_jobs": Spec("gauge", "Overdue jobs the reaper could not settle."),
    # --- components, host, GPU --------------------------------------------------------
    "infrx_component_up": Spec("gauge", "1 when a required component answered its probe.",
                               (("component", COMPONENTS),)),
    "infrx_host_cpus": Spec("gauge", "Logical CPUs."),
    "infrx_host_load1": Spec("gauge", "One-minute load average."),
    "infrx_host_memory_bytes": Spec("gauge", "Host memory.",
                                    (("state", frozenset({"total", "available"})),)),
    "infrx_process_resident_bytes": Spec("gauge", "Resident memory of this process."),
    "infrx_disk_bytes": Spec("gauge", "Filesystem size and space free to this process.",
                             (("mount", _MOUNT), ("state", frozenset({"total", "free"})))),
    "infrx_disk_free_ratio": Spec(
        "gauge", "Free fraction of a configured filesystem; 0 when it cannot be read.",
        (("mount", _MOUNT),)),
    "infrx_gpu_up": Spec("gauge", "1 when nvidia-smi answered."),
    "infrx_gpu_memory_bytes": Spec("gauge", "GPU memory.",
                                   (("gpu", _DEVICE), ("state", frozenset({"used", "total"})))),
    "infrx_gpu_utilization_ratio": Spec("gauge", "GPU utilization, 0-1.", (("gpu", _DEVICE),)),
    # --- database pool (psycopg_pool pop_stats at scrape, WR-I8-2) ----------------------
    "infrx_db_pool_connections": Spec(
        "gauge", "Connections of this process's database pool.",
        (("state", frozenset({"size", "available", "max"})),)),
    "infrx_db_pool_requests_waiting": Spec("gauge", "Requests queued for a pool connection."),
    "infrx_db_pool_requests_total": Spec("counter", "Connections requested from the pool."),
    "infrx_db_pool_wait_seconds_total": Spec("counter", "Time requests waited for a connection."),
    "infrx_db_pool_timeouts_total": Spec(
        "counter", "Pool requests that ended in an error (timeout, queue full)."),
    "infrx_db_pool_connection_errors_total": Spec(
        "counter", "Failed attempts to open a server connection."),
    "infrx_db_pool_connections_lost_total": Spec(
        "counter", "Pooled connections found broken."),
    # --- retention and the processing cache (M6, WR-I8-M6-1; dashboard.json) ------------
    "infrx_retention_passes_total": Spec(
        "counter", "Retention passes run (RetentionCollector.sweep returned)."),
    "infrx_retention_aborted_total": Spec(
        "counter", "Passes that stopped early (Report.aborted).",
        (("reason", RETENTION_ABORTS),)),
    "infrx_retention_consecutive_aborted_passes": Spec(
        "gauge", "Aborted passes since the last completed one."),
    "infrx_retention_last_success_timestamp_seconds": Spec(
        "gauge", "Unix time the last pass completed without aborting."),
    "infrx_retention_deleted_total": Spec(
        "counter", "Content deleted and acknowledged (Report.deleted), by where it lived.",
        (("location", frozenset({"object_store", "database"})),)),
    "infrx_retention_retained_total": Spec(
        "counter", "Candidates kept this pass (Report.retained), by refusal.",
        (("reason", RETENTION_RETAINED),)),
    "infrx_retention_delete_failed_total": Spec(
        "counter", "Object-store deletes that failed (Report.delete_failed)."),
    "infrx_retention_ack_lost_total": Spec(
        "counter", "Deletes done whose acknowledgement did not commit (Report.ack_lost)."),
    "infrx_retention_pending_delete_seconds": Spec(
        "gauge", "Oldest unfinished delete a pass took over (Report.max_pending_delete_s)."),
    "infrx_processing_cache_bytes": Spec("gauge", "Bytes under PROCESSING_CACHE_DIR."),
    "infrx_processing_cache_evicted_total": Spec(
        "counter", "Cache files removed: above the high water (_make_room) or past their "
                   "life (sweep).", (("reason", frozenset({"high_water", "expired"})),)),
    "infrx_processing_cache_refused_total": Spec(
        "counter", "Puts refused because what is left is pinned (retryable 503)."),
    "infrx_build_info": Spec("gauge", "1, labelled with the deployed git revision and image.",
                             (("revision", _REVISION), ("image", _IMAGE))),
}
FAMILIES["infrx_metrics_label_rejected_total"] = Spec(
    "counter", "Label values or observations refused by the sanitizer.",
    (("family", frozenset(FAMILIES) | {"infrx_metrics_label_rejected_total"}),))
REJECTED = "infrx_metrics_label_rejected_total"


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _allowed(value: str, allowed) -> bool:
    return value in allowed if isinstance(allowed, frozenset) else bool(allowed.fullmatch(value))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float) -> str:
    return "+Inf" if value == math.inf else repr(float(value))


def _line(name: str, labels: Mapping[str, str], value: float) -> str:
    inside = ",".join(f'{key}="{_escape(val)}"' for key, val in labels.items())
    return f"{name}{{{inside}}} {_number(value)}"


class Registry:
    """One process's metrics. Thread-safe (probes run in worker threads); nothing global."""

    def __init__(self, process: str, *, mounts: Collection[str] = ("root",)) -> None:
        if not _PROCESS.fullmatch(process):
            raise ValueError("a process label is lower-case letters and underscores")
        if not all(_MOUNT.fullmatch(mount) for mount in mounts):
            raise ValueError("a mount name is lower-case letters and underscores")
        self.process = process
        self.mounts = frozenset(mounts)             # the only `mount` values this process has
        self._lock = threading.Lock()
        self._samples: dict[str, dict[tuple[str, ...], object]] = {n: {} for n in FAMILIES}

    # --- writing ---------------------------------------------------------------------
    def inc(self, name: str, amount: float = 1.0, **labels: object) -> None:
        self._kind(name, "counter")
        if not _finite(amount) or amount < 0:
            return self._reject(name)
        key = self._key(name, labels)
        with self._lock:
            samples = self._samples[name]
            samples[key] = samples.get(key, 0.0) + amount

    def set(self, name: str, value: float, **labels: object) -> None:
        self._kind(name, "gauge")
        if not _finite(value):
            return self._reject(name)
        key = self._key(name, labels)
        with self._lock:
            self._samples[name][key] = float(value)

    def observe(self, name: str, value: float, **labels: object) -> None:
        spec = self._kind(name, "histogram")
        if not _finite(value) or value < 0:
            return self._reject(name)
        key = self._key(name, labels)
        with self._lock:
            # [cumulative bucket counts..., sum, count]
            counts = self._samples[name].setdefault(key, [0] * len(spec.buckets) + [0.0, 0])
            for index, bound in enumerate(spec.buckets):
                if value <= bound:
                    counts[index] += 1
            counts[-2] += value
            counts[-1] += 1

    def clear(self, name: str) -> None:
        """Forget every series of a gauge family that is re-read whole on each scrape, so a
        GPU or mount that stopped answering does not keep its last value as if current."""
        self._kind(name, "gauge")
        with self._lock:
            self._samples[name].clear()

    def observe_phases(self, timings: Mapping[str, float]) -> None:
        """Record one request's phase timings (seconds), keyed by the Server-Timing names."""
        for phase, seconds in timings.items():
            self.observe("infrx_phase_seconds", seconds, phase=phase)

    # --- reading ---------------------------------------------------------------------
    def value(self, name: str, **labels: object) -> float | None:
        """A counter's or gauge's current value (the histogram count for a histogram)."""
        sample = self._samples[name].get(self._key(name, labels, count=False))
        if isinstance(sample, list):
            return float(sample[-1])
        return sample

    def render(self) -> str:
        """Prometheus text exposition 0.0.4. Every sample carries `process`."""
        lines: list[str] = []
        with self._lock:
            for name, spec in FAMILIES.items():
                samples = self._samples[name]
                if not samples:
                    continue
                lines += [f"# HELP {name} {spec.help}", f"# TYPE {name} {spec.kind}"]
                names = ("process", *(label for label, _ in spec.labels))
                for key, sample in sorted(samples.items()):
                    base = dict(zip(names, (self.process, *key)))
                    if spec.kind != "histogram":
                        lines.append(_line(name, base, sample))
                        continue
                    for bound, count in zip((*spec.buckets, math.inf), (*sample[:-2], sample[-1])):
                        lines.append(_line(f"{name}_bucket", {**base, "le": _number(bound)}, count))
                    lines.append(_line(f"{name}_sum", base, sample[-2]))
                    lines.append(_line(f"{name}_count", base, sample[-1]))
        return "\n".join(lines) + "\n"

    def write_textfile(self, path: str) -> None:
        """For a process with no HTTP listener (worker, reaper): the exposition, replaced
        atomically, for the alert evaluator (or a textfile collector) to read."""
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w") as handle:
            handle.write(self.render())
        os.replace(temporary, path)

    # --- internals -------------------------------------------------------------------
    def _kind(self, name: str, kind: str) -> Spec:
        spec = FAMILIES[name]                       # an unknown family is a programming error
        if spec.kind != kind:
            raise TypeError(f"{name} is a {spec.kind}, not a {kind}")
        return spec

    def _key(self, name: str, labels: Mapping[str, object], *, count: bool = True) -> tuple:
        spec = FAMILIES[name]
        declared = [label for label, _ in spec.labels]
        if sorted(labels) != sorted(declared):
            raise ValueError(f"{name} takes labels {declared}, got {sorted(labels)}")
        key = []
        for label, allowed in spec.labels:
            if label == "mount":
                allowed = self.mounts
            value = str(getattr(labels[label], "value", labels[label]))
            if label == TENANT_LABEL:
                value = tenant_label(value)
            if not _allowed(value, allowed):
                if count:
                    self._reject(name)
                value = OTHER
            key.append(value)
        return tuple(key)

    def _reject(self, name: str) -> None:
        key = (name,)
        with self._lock:
            samples = self._samples[REJECTED]
            samples[key] = samples.get(key, 0.0) + 1


# --- the Server-Timing header --------------------------------------------------------
def server_timing(timings: Mapping[str, float]) -> str:
    """`{"queue": 0.012}` (seconds) -> `queue;dur=12.0` (milliseconds, RFC Server-Timing).

    Only declared phases, in E1B order: an undeclared name is never echoed into a response
    header, and the benchmark client reports a declared phase that is absent as absent.
    """
    parts = []
    for phase in PHASES:
        seconds = timings.get(phase)
        if _finite(seconds) and seconds >= 0:
            parts.append(f"{phase};dur={seconds * 1000:.1f}")
    return ", ".join(parts)


# --- the wiring helpers: one call each at the owning track's call site ---------------
def record_outcome(reg: Registry, outcome: TerminalOutcome) -> None:
    """A settled terminal outcome (W3's attempt result, G's cancel, the reaper's)."""
    reg.inc("infrx_jobs_terminal_total", state=outcome.state, cause=outcome.cause)
    reg.inc("infrx_settlements_total", settlement=outcome.settlement_state)


def record_recovery(reg: Registry, produced, *, released: Collection[str] = ()) -> None:
    """What one `JobStore.recover()` pass returned: a re-dispatch means a lease was lost
    and reaped; an outcome means the reaper settled a job.

    `released` names the jobs whose returned outcome is an aged unknown-usage hold being
    released - a job that was already terminal. The port returns that as a
    `TerminalOutcome` too, so without it the job would be counted terminal twice; with it,
    it is one settlement and one `hold_released`."""
    for item in produced:
        if isinstance(item, IndexEvent):
            preparing = item.kind is OutboxKind.prepare_dispatch
            reg.inc("infrx_recovery_actions_total",
                    action="prepare_redispatched" if preparing else "requeued")
            reg.inc("infrx_lease_lost_total", detected_by="reaper",
                    kind=LeaseKind.preparation if preparing else LeaseKind.inference)
        elif isinstance(item, TerminalOutcome) and str(item.job_id) in released:
            reg.inc("infrx_recovery_actions_total", action="hold_released")
            reg.inc("infrx_settlements_total", settlement=item.settlement_state)
        elif isinstance(item, TerminalOutcome):
            reg.inc("infrx_recovery_actions_total", action="terminalized")
            record_outcome(reg, item)


def record_queue(reg: Registry, stats: Mapping[str, object], *,
                 max_items: int | None = None) -> None:
    """`Scheduler.stats()` (Q1/Q2 shape) into the saturation gauges."""
    for kind, depth in dict(stats.get("depth_by_kind") or {}).items():
        reg.set("infrx_queue_depth", depth, kind=kind)
    reg.set("infrx_queue_items", stats.get("items", 0))
    reg.set("infrx_queue_oldest_wait_seconds", stats.get("oldest_wait_s", 0.0))
    if max_items is not None:
        reg.set("infrx_queue_items_limit", max_items)


def record_reconciliation(reg: Registry, *, drift: int, holds_unknown: int,
                          unsettleable: int, now: float) -> None:
    """One reconciliation pass: drift rows from the detector views, the unknown-usage
    backlog and the reaper's unsettleable set."""
    reg.inc("infrx_reconciliation_runs_total", result="ok" if drift == 0 else "drift")
    reg.set("infrx_reconciliation_drift", drift)
    reg.set("infrx_holds_unknown", holds_unknown)
    reg.set("infrx_unsettleable_jobs", unsettleable)
    if drift == 0:
        reg.set("infrx_reconciliation_last_success_timestamp_seconds", now)


def record_pool(reg: Registry, stats: Mapping[str, float]) -> None:
    """`AsyncConnectionPool.pop_stats()` at scrape: gauges as read, counters as the deltas
    since the previous pop (psycopg_pool omits a counter that has not moved)."""
    for state in ("size", "available", "max"):
        reg.set("infrx_db_pool_connections", stats.get(f"pool_{state}", 0), state=state)
    reg.set("infrx_db_pool_requests_waiting", stats.get("requests_waiting", 0))
    reg.inc("infrx_db_pool_requests_total", stats.get("requests_num", 0))
    reg.inc("infrx_db_pool_wait_seconds_total", stats.get("requests_wait_ms", 0) / 1000)
    reg.inc("infrx_db_pool_timeouts_total", stats.get("requests_errors", 0))
    reg.inc("infrx_db_pool_connection_errors_total", stats.get("connections_errors", 0))
    reg.inc("infrx_db_pool_connections_lost_total", stats.get("connections_lost", 0))
