"""I3B.a / BACKEND-OBSERVE: the metrics module, the protected route, the alert rules and the
dashboard spec. Layer 1 - no container, no network, no GPU.

    apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/recovery/test_observe.py

What is claimed, one case each (ids `test_i3b_obNN`), every one killable by a single edit in
`recovery/mutants_i3b.py`:

* no customer payload, key, URL or raw tenant id can reach an exposition (R59's rule for
  operator surfaces), and an attempt to put one there is itself counted;
* the phase names are E1B's, and the `Server-Timing` header and the histogram use the same;
* `/metrics` answers only a direct loopback peer;
* host, disk and GPU gauges read what the machine says, and fail towards the alert;
* each alert rule refers to a declared metric and an existing runbook section, fires on
  the fault it names and stays silent on a healthy scrape.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import stack                                            # noqa: E402,F401  (infrx on path)

import harness                                          # noqa: E402
import recoverykit as kit                               # noqa: E402

from infrx.observe import alerts, host, metrics, route  # noqa: E402
from infrx.observe.metrics import FAMILIES, PHASES, Registry, tenant_label  # noqa: E402

ORG = "3f0e8a4e-5a4b-4c3d-8e2f-0123456789ab"
# What must never leave through a metric: a prompt, a signed URL, a key, an error message
# with a path in it, a raw tenant id.
PAYLOADS = ("Describe the forklift in frame 12",
            "https://bucket.example/clip.mp4?X-Amz-Signature=abc123",
            "sk-infrx-aaaaaaaabbbbbbbbccccccccdddddddd",
            "FileNotFoundError: /var/lib/infrx/media/3f0e8a4e/v1/source.mp4",
            ORG)


def _hostile_labels(spec) -> dict:
    return {label: PAYLOADS[index % len(PAYLOADS)] for index, (label, _) in enumerate(spec.labels)}


# ------------------------------------------------------------------ sanitization (R59)

def test_i3b_ob01_every_label_is_a_closed_vocabulary_and_payload_never_leaves():
    """Every label of every family is an enum or a pattern; a payload written into any
    label slot is exposed as `other` (a tenant as its hash) and the refusal is counted."""
    reg = Registry("gateway")
    refused = 0
    for name, spec in FAMILIES.items():
        for label, allowed in spec.labels:
            assert isinstance(allowed, (frozenset, re.Pattern)), (name, label)
        if not spec.labels or name == metrics.REJECTED:
            continue
        labels = _hostile_labels(spec)
        refused += sum(label != metrics.TENANT_LABEL for label in labels)
        {"counter": reg.inc, "gauge": lambda n, **kw: reg.set(n, 1.0, **kw),
         "histogram": lambda n, **kw: reg.observe(n, 0.5, **kw)}[spec.kind](name, **labels)
    text = reg.render()
    for payload in PAYLOADS:
        assert payload not in text, payload
    assert 'phase="other"' in text and 'code="other"' in text
    counted = sum(value for (sample, _), value in alerts.parse(text).items()
                  if sample == metrics.REJECTED)
    assert counted == refused > 0


def test_i3b_ob02_a_tenant_is_its_hash_never_its_id():
    """`tenant` is always `tenant_label(org_id)`: stable, distinct per org, never the id."""
    reg = Registry("gateway")
    other = "9d2c1b0a-5a4b-4c3d-8e2f-0123456789ab"
    for org in (ORG, ORG, other):
        reg.inc("infrx_requests_rejected_total", code="capacity_exhausted", tenant=org)
        reg.inc("infrx_jobs_accepted_total", mode="sync", tenant=org)
    text = reg.render()
    assert ORG not in text and other not in text
    assert reg.value("infrx_requests_rejected_total", code="capacity_exhausted",
                     tenant=ORG) == 2.0
    assert f'tenant="{tenant_label(ORG)}"' in text and f'tenant="{tenant_label(other)}"' in text
    assert tenant_label(ORG) != tenant_label(other)
    assert re.fullmatch(r"t_[0-9a-f]{12}", tenant_label(ORG))
    assert reg.value(metrics.REJECTED, family="infrx_requests_rejected_total") is None


def test_i3b_ob03_bad_numbers_are_dropped_and_counted():
    """A negative, NaN, infinite or boolean value never reaches a sum an alert reads."""
    reg = Registry("worker")
    for bad in (-1.0, math.nan, math.inf, True):
        reg.observe("infrx_phase_seconds", bad, phase="prefill")
        reg.inc("infrx_lease_lost_total", bad, kind="inference", detected_by="worker")
    for bad in (math.nan, math.inf, True):              # a negative gauge is a value
        reg.set("infrx_queue_items", bad)
    assert reg.value("infrx_phase_seconds", phase="prefill") is None
    assert reg.value("infrx_lease_lost_total", kind="inference", detected_by="worker") is None
    assert reg.value("infrx_queue_items") is None
    assert reg.value(metrics.REJECTED, family="infrx_phase_seconds") == 4
    assert reg.value(metrics.REJECTED, family="infrx_lease_lost_total") == 4
    assert reg.value(metrics.REJECTED, family="infrx_queue_items") == 3


# ------------------------------------------------------------------ phases and the header

def _bench():
    spec = importlib.util.spec_from_file_location(
        "i3b_bench", harness.REPO_ROOT / "models" / "marlin2b" / "bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_i3b_ob04_phase_names_are_e1bs_and_the_header_uses_the_same_ones():
    """E1B's client parses `Server-Timing`; the histogram records the same mapping under
    the same names, an undeclared name is never echoed into the header, and the client
    reads back every declared phase in milliseconds."""
    bench = _bench()
    assert PHASES == bench.PHASES
    timings = {phase: (index + 1) / 100 for index, phase in enumerate(PHASES)}
    header = metrics.server_timing({**timings, "evil-name": 1.0, "queue ": 2.0})
    assert "evil" not in header
    parsed = bench.parse_server_timing(header)
    assert parsed == {phase: round(seconds * 1000, 1) for phase, seconds in timings.items()}
    reg = Registry("gateway")
    reg.observe_phases(timings)
    observed = {dict(labels)["phase"] for (name, labels) in alerts.parse(reg.render())
                if name == "infrx_phase_seconds_count"}
    assert observed == set(PHASES)


def test_i3b_ob05_the_exposition_is_prometheus_text_that_round_trips():
    """One HELP/TYPE per family, cumulative buckets ending at +Inf == count, every sample
    parseable, and label values escaped."""
    reg = Registry("worker")
    for seconds in (0.004, 0.2, 7.0, 400.0):
        reg.observe("infrx_phase_seconds", seconds, phase="generate")
    reg.inc("infrx_settlements_total", settlement="settled")
    text = reg.render()
    assert text.count("# TYPE infrx_phase_seconds histogram") == 1
    samples = alerts.parse(text)
    buckets = [(dict(labels)["le"], value) for (name, labels), value in samples.items()
               if name == "infrx_phase_seconds_bucket"]
    counts = [value for _, value in sorted(buckets, key=lambda b: float(b[0]))]
    assert counts == sorted(counts) and counts[-1] == 4.0 and buckets[-1][0] == "+Inf"
    key = ("infrx_phase_seconds_sum", (("phase", "generate"), ("process", "worker")))
    assert samples[key] == pytest.approx(407.204)
    line = metrics._line("x", {"a": 'q"uo\\te\nnl'}, 1.0)
    assert line == 'x{a="q\\"uo\\\\te\\nnl"} 1.0'
    assert alerts.parse(line) == {("x", (("a", 'q"uo\\te\nnl'),)): 1.0}


def test_i3b_ob06_the_textfile_is_replaced_whole(tmp_path):
    """A worker's exposition file is written beside and renamed: a reader never sees half."""
    reg = Registry("worker")
    reg.set("infrx_unsettleable_jobs", 2)
    target = tmp_path / "worker.prom"
    reg.write_textfile(str(target))
    assert alerts.parse(target.read_text())[("infrx_unsettleable_jobs",
                                             (("process", "worker"),))] == 2.0
    assert [path.name for path in tmp_path.iterdir()] == ["worker.prom"]


# ------------------------------------------------------------------ the route

def _app():
    from fastapi import FastAPI
    app = FastAPI()
    rt = SimpleNamespace(inflight=3, settings=SimpleNamespace(max_inflight=16),
                         metrics_disks={"root": "/"})
    route.register(app, rt)
    return app, rt


def test_i3b_ob07_metrics_answer_only_a_direct_loopback_peer():
    """The peer must be loopback AND carry no proxy header; anything else gets exactly the
    404 an unknown path gets, so the route is not even advertised through Caddy."""
    from fastapi.testclient import TestClient
    app, rt = _app()
    rt.metrics.inc("infrx_requests_rejected_total", code="rate_limited", tenant=ORG)
    local = TestClient(app, client=("127.0.0.1", 50000))
    served = local.get("/metrics")
    assert served.status_code == 200
    assert served.headers["content-type"] == metrics.CONTENT_TYPE
    assert "infrx_inflight_requests{process=\"gateway\"} 3.0" in served.text
    assert "infrx_inflight_limit{process=\"gateway\"} 16.0" in served.text
    assert "infrx_disk_free_ratio{process=\"gateway\",mount=\"root\"}" in served.text
    unknown = local.get("/no-such-path")
    for client, headers in ((local, {"X-Forwarded-For": "203.0.113.9"}),
                            (local, {"Forwarded": "for=203.0.113.9"}),
                            (local, {"Via": "1.1 caddy"}),
                            (TestClient(app), {}),
                            (TestClient(app, client=("10.0.0.5", 50000)), {})):
        refused = client.get("/metrics", headers=headers)
        assert (refused.status_code, refused.content) == (404, unknown.content), headers
    assert TestClient(app, client=("::1", 50000)).get("/metrics").status_code == 200


# ------------------------------------------------------------------ host, disk, GPU

def test_i3b_ob08_host_gauges_read_the_machine_and_fail_towards_the_alert(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "meminfo").write_text("MemTotal:       1000 kB\nMemFree:  1 kB\n"
                                  "MemAvailable:    250 kB\n")
    (proc / "self" / "status").write_text("Name: python\nVmRSS:\t  64 kB\n")

    def statvfs(path):
        if path == "/gone":
            raise FileNotFoundError(path)
        return SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=50)

    def smi(argv, **_):
        assert tuple(argv) == host.NVIDIA_SMI
        return SimpleNamespace(returncode=0, stdout="0, 39945, 46068, 7\n")

    reg = Registry("gateway", mounts=("media", "spool"))
    host.collect_host(reg, {"media": "/srv", "spool": "/gone"}, proc=str(proc),
                      statvfs=statvfs, run=smi)
    assert reg.value("infrx_host_memory_bytes", state="available") == 250 * 1024
    assert reg.value("infrx_process_resident_bytes") == 64 * 1024
    assert reg.value("infrx_disk_free_ratio", mount="media") == pytest.approx(0.05)
    assert reg.value("infrx_disk_bytes", mount="media", state="free") == 50 * 4096
    assert reg.value("infrx_disk_free_ratio", mount="spool") == 0.0
    assert reg.value("infrx_gpu_up") == 1.0
    assert reg.value("infrx_gpu_memory_bytes", gpu="0", state="used") == 39945 * 1024 * 1024
    assert reg.value("infrx_gpu_utilization_ratio", gpu="0") == pytest.approx(0.07)
    for broken in (lambda argv, **_: SimpleNamespace(returncode=9, stdout=""),
                   lambda argv, **_: (_ for _ in ()).throw(FileNotFoundError("nvidia-smi"))):
        host.collect_gpu(reg, run=smi)
        host.collect_gpu(reg, run=broken)
        assert reg.value("infrx_gpu_up") == 0.0
        # M3: no frozen reading survives the loss, and none is fabricated (no 0 either)
        text = reg.render()
        assert "infrx_gpu_memory_bytes" not in text and "infrx_gpu_utilization_ratio" not in text
    host.collect_disks(reg, {"media": "/gone"}, statvfs=statvfs)
    assert reg.value("infrx_disk_free_ratio", mount="media") == 0.0
    assert reg.value("infrx_disk_bytes", mount="media", state="free") is None
    # OB-3: `spool` was configured before and is not now - no ratio of it remains either
    assert reg.value("infrx_disk_free_ratio", mount="spool") is None


# ------------------------------------------------------------------ the wiring helpers

def test_i3b_ob09_the_reaper_helper_counts_what_recover_returned():
    """`record_recovery` over real `JobStore.recover()` passes: a lost inference worker is a
    requeue (an inference lease reaped), a worker lost after publication a terminalized
    `lost_after_publication` held unknown - and, a day later, a released hold, counted as
    one settlement and never as a second terminal job. A preparation re-dispatch the store
    returns counts as a reaped preparation lease (the reference store emits it to the
    outbox only, so it is handed over here as the event a store would return)."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.contracts.records import OutboxKind
    world = kit.World()
    reg = Registry("reaper")

    def value(name, **labels):
        return reg.value(name, **labels) or 0

    async def body():
        requeue = await world.queued()
        await world.jobs.claim(requeue.request_id, "dead-worker")
        published = await world.queued(1)
        lease = await world.jobs.claim(published.request_id, "dead-worker")
        await world.stream.append(lease, (kit.delta("seen"),))
        world.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await world.jobs.recover()
        preparing = world.candidate(requeue.request_id, kind=OutboxKind.prepare_dispatch)
        metrics.record_recovery(reg, (*produced, preparing))
        assert value("infrx_recovery_actions_total", action="requeued") == 1
        assert value("infrx_recovery_actions_total", action="prepare_redispatched") == 1
        assert value("infrx_recovery_actions_total", action="terminalized") == 1
        assert value("infrx_lease_lost_total", kind="inference", detected_by="reaper") == 1
        assert value("infrx_lease_lost_total", kind="preparation", detected_by="reaper") == 1
        assert value("infrx_jobs_terminal_total", state="failed",
                     cause="lost_after_publication") == 1
        assert value("infrx_settlements_total", settlement="held_unknown") == 1

        await world.jobs.cancel(requeue.org_id, requeue.job_handle)
        world.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
        metrics.record_recovery(reg, await world.jobs.recover(),
                                released={published.request_id})
        assert value("infrx_recovery_actions_total", action="hold_released") == 1
        assert value("infrx_settlements_total", settlement="released_platform_absorbed") == 1
        assert value("infrx_recovery_actions_total", action="terminalized") == 1
        assert value("infrx_jobs_terminal_total", state="failed",
                     cause="lost_after_publication") == 1
    asyncio.run(body())


# ------------------------------------------------------------------ alerts and dashboard

RULES = json.loads((kit.ROOT / "infra" / "alerts" / "alerts.json").read_text())["rules"]
DASHBOARD = json.loads((kit.ROOT / "infra" / "alerts" / "dashboard.json").read_text())
SUFFIXES = ("_bucket", "_sum", "_count")


def _family(sample: str) -> str:
    for suffix in SUFFIXES:
        if sample.endswith(suffix) and sample[:-len(suffix)] in FAMILIES:
            return sample[:-len(suffix)]
    return sample


def _check_series(where: str, sample: str, match: dict | None) -> None:
    family = _family(sample)
    assert family in FAMILIES, f"{where}: {sample} is not a declared family"
    allowed = dict(FAMILIES[family].labels) | {"process": None}
    for label, wanted in (match or {}).items():
        assert label in allowed, f"{where}: {family} has no label {label}"
        for value in wanted if isinstance(wanted, list) else [wanted]:
            vocabulary = allowed[label]
            assert vocabulary is None or metrics._allowed(value, vocabulary), \
                f"{where}: {label}={value} can never occur"


def test_i3b_ob10_every_rule_and_panel_names_a_declared_metric():
    names = [rule["name"] for rule in RULES]
    assert len(names) == len(set(names))
    for rule in RULES:
        where = rule["name"]
        _check_series(where, rule["metric"], rule.get("match"))
        if "divide_by" in rule:
            _check_series(where, rule["divide_by"]["metric"], rule["divide_by"].get("match"))
            assert rule.get("agg"), f"{where}: a ratio needs an aggregate"
        assert rule["op"] in alerts.OPS and rule["severity"] in ("page", "ticket")
        assert isinstance(rule["threshold"], (int, float))
        # M4: a threshold is exact, or it is marked unmeasured - nothing in between
        status = rule["threshold_status"]
        assert status.startswith("exact") or "⚠️ TO BE VERIFIED (P-18)" in status, where
        assert rule.get("agg") in (None, *alerts.AGGREGATES)
    for row in DASHBOARD["rows"]:
        for panel in row["panels"]:
            _check_series(panel["title"], panel["metric"], {})
            if "compare" in panel:
                _check_series(panel["title"], panel["compare"], {})
            labels = dict(FAMILIES[_family(panel["metric"])].labels) | {"process": None}
            assert set(panel.get("by", ())) <= set(labels), panel["title"]
    shown = {_family(panel[field]) for row in DASHBOARD["rows"] for panel in row["panels"]
             for field in ("metric", "compare") if field in panel}
    assert shown == set(FAMILIES), f"families with no panel: {set(FAMILIES) - shown}"


def _healthy(now: float = 1_000_000.0) -> Registry:
    """What a quiet, well pilot looks like - the scrape no rule may fire on."""
    reg = Registry("gateway")
    for component in metrics.COMPONENTS:
        reg.set("infrx_component_up", 1, component=component)
    reg.set("infrx_gpu_up", 1)
    reg.set("infrx_disk_free_ratio", 0.8, mount="root")
    reg.set("infrx_host_memory_bytes", 64, state="total")
    reg.set("infrx_host_memory_bytes", 40, state="available")
    reg.set("infrx_queue_items", 3)
    reg.set("infrx_queue_items_limit", 500)
    reg.set("infrx_queue_oldest_wait_seconds", 2)
    reg.set("infrx_inflight_requests", 1)
    reg.set("infrx_inflight_limit", 16)
    reg.inc("infrx_jobs_terminal_total", 200, state="succeeded", cause="completed")
    reg.inc("infrx_requests_rejected_total", 2, code="capacity_exhausted", tenant=ORG)
    for phase, seconds in (("prepare", 3.0), ("journal", 0.03), ("settle", 0.1)):
        reg.observe("infrx_phase_seconds", seconds, phase=phase)
    metrics.record_reconciliation(reg, drift=0, holds_unknown=1, unsettleable=0, now=now)
    return reg


# rule -> what the fault does to a healthy registry between two evaluations
FAULTS = {
    "ComponentDown": lambda r: r.set("infrx_component_up", 0, component="engine"),
    "GpuUnavailable": lambda r: r.set("infrx_gpu_up", 0),
    "DiskAlmostFull": lambda r: r.set("infrx_disk_free_ratio", 0.05, mount="root"),
    "DiskFilling": lambda r: r.set("infrx_disk_free_ratio", 0.15, mount="root"),
    "HostMemoryLow": lambda r: r.set("infrx_host_memory_bytes", 3, state="available"),
    "QueueStalled": lambda r: r.set("infrx_queue_oldest_wait_seconds", 601),
    "QueueSaturated": lambda r: r.set("infrx_queue_items", 450),
    "InflightSaturated": lambda r: r.set("infrx_inflight_requests", 16),
    "RejectionsHigh": lambda r: r.inc("infrx_requests_rejected_total", 51,
                                      code="rate_limited", tenant=ORG),
    "PlatformFailureRate": lambda r: (r.inc("infrx_jobs_terminal_total", 98, state="succeeded",
                                            cause="completed"),
                                      r.inc("infrx_jobs_terminal_total", 2, state="failed",
                                            cause="engine_error")),
    "LeaseLost": lambda r: r.inc("infrx_lease_lost_total", kind="inference",
                                 detected_by="reaper"),
    "ReaperTerminalized": lambda r: r.inc("infrx_recovery_actions_total", action="terminalized"),
    "ReconciliationDrift": lambda r: r.set("infrx_reconciliation_drift", 1),
    "ReconciliationStale": lambda r: r.set(
        "infrx_reconciliation_last_success_timestamp_seconds", 1_000_000.0 - 3601),
    "UnsettleableJobs": lambda r: r.set("infrx_unsettleable_jobs", 1),
    "UnknownUsageBacklog": lambda r: r.set("infrx_holds_unknown", 11),
    "PreparationSlow": lambda r: r.observe("infrx_phase_seconds", 121, phase="prepare"),
    "JournalSlow": lambda r: r.observe("infrx_phase_seconds", 0.6, phase="journal"),
    "SettlementSlow": lambda r: r.observe("infrx_phase_seconds", 2.5, phase="settle"),
    "MetricsSanitizerRejections": lambda r: r.observe("infrx_phase_seconds", -1, phase="queue"),
}
# A fault may legitimately trip a second, weaker rule on the same signal.
ALSO = {"DiskAlmostFull": {"DiskFilling"}}


def test_i3b_ob11_each_rule_fires_on_its_fault_and_nothing_fires_when_healthy():
    """Accurate and actionable: on a healthy scrape (evaluated twice, so `increase` rules
    are judged) nothing fires; injecting each rule's fault fires exactly that rule."""
    assert set(FAULTS) == {rule["name"] for rule in RULES}
    now = 1_000_000.0 + 10
    quiet = _healthy()
    previous = alerts.parse(quiet.render())
    assert alerts.evaluate(RULES, previous, previous, now=now) == []
    for name, fault in FAULTS.items():
        reg = _healthy()
        before = alerts.parse(reg.render())
        fault(reg)
        fired = {alert["alert"] for alert in alerts.evaluate(RULES, alerts.parse(reg.render()),
                                                             before, now=now)}
        assert fired == {name} | ALSO.get(name, set()), (name, fired)


def test_i3b_ob12_increase_rules_judge_nothing_first_and_count_a_reset_from_zero():
    rule = [r for r in RULES if r["name"] == "LeaseLost"]
    reg = Registry("worker")
    reg.inc("infrx_lease_lost_total", 5, kind="inference", detected_by="reaper")
    first = alerts.parse(reg.render())
    assert alerts.evaluate(rule, first, None) == []            # no previous run
    assert alerts.evaluate(rule, first, first) == []           # nothing new
    restarted = Registry("worker")
    restarted.inc("infrx_lease_lost_total", 1, kind="inference", detected_by="reaper")
    fired = alerts.evaluate(rule, alerts.parse(restarted.render()), first)
    assert [alert["value"] for alert in fired] == [1.0]


def test_i3b_ob13_the_evaluator_cli_reports_an_unreadable_source_as_an_alert(tmp_path,
                                                                           capsys):
    """Silence never means healthy: a missing source is `ScrapeFailed` and exit 1; a
    healthy file is exit 0 and the state file carries the samples to the next run."""
    healthy = tmp_path / "gateway.prom"
    healthy.write_text(_healthy(time.time()).render())
    rules = str(kit.ROOT / "infra" / "alerts" / "alerts.json")
    state = tmp_path / "state.json"
    args = ["--rules", rules, "--source", str(healthy), "--state", str(state)]
    assert alerts.main(args) == 0 and capsys.readouterr().out == ""
    assert alerts.main(args) == 0 and state.exists()
    assert alerts.main([*args, "--source", str(tmp_path / "worker.prom")]) == 1
    fired = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [alert["alert"] for alert in fired] == ["ScrapeFailed"]


def test_i3b_ob14_a_scrape_gap_then_recovery_fires_nothing_new(tmp_path, capsys):
    """M1: a failed scrape keeps that source's previous samples in the state, so when it is
    back with nothing new, no `increase` rule judges a counter's lifetime total as new - and
    a failure on the very first run leaves the next run a first run. With two sources, what
    the healthy one reported during the other's gap is paged once, not again on recovery."""
    reg = _healthy(time.time())
    reg.inc("infrx_lease_lost_total", 3, kind="inference", detected_by="reaper")
    reg.inc("infrx_requests_rejected_total", 500, code="rate_limited", tenant=ORG)
    source = tmp_path / "gateway.prom"
    source.write_text(reg.render())
    away = tmp_path / "gone.prom"
    rules = str(kit.ROOT / "infra" / "alerts" / "alerts.json")

    def run(state):
        code = alerts.main(["--rules", rules, "--source", str(source), "--state", str(state)])
        return code, [json.loads(line)["alert"] for line in capsys.readouterr().out.splitlines()]

    state = tmp_path / "state.json"
    assert run(state) == (0, []) and run(state) == (0, [])
    source.rename(away)
    assert run(state) == (1, ["ScrapeFailed"])
    away.rename(source)
    assert run(state) == (0, [])                          # back, nothing new: silence

    first = tmp_path / "first.json"
    source.rename(away)
    assert run(first) == (1, ["ScrapeFailed"])            # the very first run fails
    away.rename(source)
    assert run(first) == (0, [])                          # ... so this one judges nothing

    # Two sources: the worker's goes away while the gateway's gains 60 refusals. The run
    # during the gap pages them (and the gap); the run after the worker is back must not
    # page the same 60 again - the state written during the gap carries both.
    worker = Registry("worker")
    worker.inc("infrx_lease_lost_total", 3, kind="inference", detected_by="reaper")
    worker_file, worker_away = tmp_path / "worker.prom", tmp_path / "worker.gone"
    worker_file.write_text(worker.render())
    both = tmp_path / "both.json"

    def run_both():
        code = alerts.main(["--rules", rules, "--source", str(source), "--source",
                            str(worker_file), "--state", str(both)])
        return code, sorted(json.loads(line)["alert"]
                            for line in capsys.readouterr().out.splitlines())

    assert run_both() == (0, []) and run_both() == (0, [])
    worker_file.rename(worker_away)
    reg.inc("infrx_requests_rejected_total", 60, code="rate_limited", tenant=ORG)
    source.write_text(reg.render())
    assert run_both() == (1, ["RejectionsHigh", "ScrapeFailed"])
    worker_away.rename(worker_file)
    assert run_both() == (0, [])                          # nothing new: nothing paged twice


def test_i3b_ob15_a_stale_or_empty_source_is_scrape_failed_not_silence(tmp_path, capsys):
    """M5: a readable file the worker stopped rewriting (older than `--max-age`) and an
    exposition with no sample are `ScrapeFailed`, like an unreadable one; a fresh healthy
    file with the same bound is silent."""
    import os
    rules = str(kit.ROOT / "infra" / "alerts" / "alerts.json")
    worker = tmp_path / "worker.prom"
    worker.write_text(_healthy(time.time()).render())

    def fired(*extra):
        code = alerts.main(["--rules", rules, "--source", str(worker), *extra])
        return code, [json.loads(line)["alert"] for line in capsys.readouterr().out.splitlines()]

    assert fired("--max-age", "60") == (0, [])
    old = time.time() - 600
    os.utime(worker, (old, old))
    assert fired("--max-age", "60") == (1, ["ScrapeFailed"])
    assert fired() == (0, [])                             # no bound given: not judged on age
    worker.write_text("\n")                               # what an empty Registry renders
    assert fired() == (1, ["ScrapeFailed"])


def test_i3b_ob16_names_labels_mounts_and_buckets_are_pinned():
    """M2/M6: a process or mount name outside its syntax is refused at construction; a
    label the family does not declare is an error, never silently dropped; a `mount` that
    is a word but not a configured mount is `other` and counted; the histogram bounds are
    exactly the declared ones plus +Inf."""
    for bad in ("bad name", "Gateway", ""):
        with pytest.raises(ValueError):
            Registry(bad)
    with pytest.raises(ValueError):
        Registry("gateway", mounts=("media", "Prompt Leak"))
    reg = Registry("gateway", mounts=("root", "media"))
    with pytest.raises(ValueError):
        reg.inc("infrx_settlements_total", settlement="settled", request_id="r-1")
    reg.set("infrx_disk_free_ratio", 0.5, mount="prompt_leak")
    reg.set("infrx_disk_free_ratio", 0.7, mount="media")
    reg.observe("infrx_phase_seconds", 0.2, phase="queue")
    samples = alerts.parse(reg.render())
    assert {dict(labels)["mount"] for (name, labels) in samples
            if name == "infrx_disk_free_ratio"} == {"other", "media"}
    assert reg.value(metrics.REJECTED, family="infrx_disk_free_ratio") == 1
    bounds = [dict(labels)["le"] for (name, labels) in samples if name == "infrx_phase_seconds_bucket"]
    assert bounds == ["0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5",
                      "5.0", "10.0", "30.0", "60.0", "120.0", "300.0", "+Inf"]
