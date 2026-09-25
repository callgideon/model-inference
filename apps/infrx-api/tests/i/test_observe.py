"""I8 slice 3: continuous monitoring - the rule set, its producers, the durable-truth
exporter on the real schema, the host probe, the canary, delivery and the units.

Failure oracles:
* producers: a rule whose metric nothing produces can never fire. The alerts.json rules
  that have no producer today are pinned by name (a new silent rule fails), and the
  all-produced claim is a strict xfail until W5/G wire the runtime producers.
* the GPU rule: the gateway container's always-0 GPU gauge must not page; the host's must.
* durable truth: a held hold with no wallet reservation is drift, an unknown hold past its
  reconcile_after is overdue, an aged dispatch is a ready backlog - read through the
  transaction pooler, read-only, with only closed-vocabulary labels.
* the canary: its key only ever reaches curl in a 0600 header file; a missing key is
  BLOCKED (exit 3) and a failed request is a 0 and a non-zero exit.
* delivery: no destination is BLOCKED with the alert kept, never a silent success.
"""
from __future__ import annotations

import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from infrx.observe import alerts as evaluator

from . import support
from .pooler import PG_DIRECT, TXN
from .test_ops_steps import calls, run_step, stubs

API = support.API_DIR
OBSERVE = support.REPO / "infra" / "observe"
ALERTS = support.REPO / "infra" / "alerts"
RULES = runpy.run_path(str(OBSERVE / "rules.py"))["merge"](ALERTS)
DURABLE = runpy.run_path(str(OBSERVE / "durable.py"))
DELIVER = runpy.run_path(str(OBSERVE / "deliver.py"))
UNITS = OBSERVE / "systemd"


# --- producers --------------------------------------------------------------------------
HELPERS = {"record_outcome": ("infrx_jobs_terminal_total", "infrx_settlements_total"),
           "record_recovery": ("infrx_recovery_actions_total", "infrx_lease_lost_total",
                               "infrx_settlements_total", "infrx_jobs_terminal_total"),
           "record_queue": ("infrx_queue_depth", "infrx_queue_items",
                            "infrx_queue_oldest_wait_seconds", "infrx_queue_items_limit"),
           "record_reconciliation": ("infrx_reconciliation_runs_total",
                                     "infrx_reconciliation_drift", "infrx_holds_unknown",
                                     "infrx_unsettleable_jobs",
                                     "infrx_reconciliation_last_success_timestamp_seconds"),
           "observe_phases": ("infrx_phase_seconds",),
           "record_pool": ("infrx_db_pool_connections", "infrx_db_pool_requests_waiting",
                           "infrx_db_pool_requests_total", "infrx_db_pool_wait_seconds_total",
                           "infrx_db_pool_timeouts_total",
                           "infrx_db_pool_connection_errors_total",
                           "infrx_db_pool_connections_lost_total"),
           "collect_host": ("infrx_host_cpus", "infrx_host_load1", "infrx_host_memory_bytes",
                            "infrx_process_resident_bytes", "infrx_disk_bytes",
                            "infrx_disk_free_ratio", "infrx_gpu_up", "infrx_gpu_memory_bytes",
                            "infrx_gpu_utilization_ratio")}


def runtime_producers() -> set[str]:
    """Families some runtime code path writes: a direct Registry call outside the metrics
    module, or a helper that is called somewhere outside it. The sanitizer's own counter
    is written by the Registry itself."""
    found = {"infrx_metrics_label_rejected_total"}
    for path in (API / "infrx").rglob("*.py"):
        if path.parent.name == "observe" and path.name in ("metrics.py", "host.py", "__init__.py"):
            continue
        text = path.read_text()
        found |= set(re.findall(r'\.(?:set|inc|observe)\(\s*"(infrx_[a-z_]+)"', text))
        for helper, families in HELPERS.items():
            if re.search(rf"\b{helper}\b", text) and not re.search(rf"def {helper}\(", text):
                found |= set(families)
    return found


def exporter_producers() -> set[str]:
    """What I8's deployed exporters write (the timer runs them on the box)."""
    found = set()
    for name in ("durable.py", "host-probe.sh", "canary.sh"):
        found |= set(re.findall(r"\b(infrx_[a-z_]+)", (OBSERVE / name).read_text()))
    return found - {"infrx_durable_"}


def rule_metrics(rules) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rule in rules:
        names = {rule["metric"]} | ({rule["divide_by"]["metric"]} if "divide_by" in rule else set())
        out[rule["name"]] = {re.sub(r"_(sum|count)$", "", n) for n in names}
    return out


# Every alerts.json rule nothing produces at the I8 head (runtime call sites + I8's
# exporters): W5 (worker: queue, reaper, reconciliation), G (rejections) and the component
# probes own the producers. Pinned so a NEW rule without a producer fails here.
KNOWN_UNPRODUCED = {"ComponentDown", "QueueStalled", "QueueSaturated", "RejectionsHigh",
                    "PlatformFailureRate", "LeaseLost", "ReaperTerminalized", "UnsettleableJobs"}


def _unproduced(rules) -> set[str]:
    produced = runtime_producers() | exporter_producers()
    return {name for name, metrics in rule_metrics(rules).items() if not metrics <= produced}


def test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones():
    alerts = json.loads((ALERTS / "alerts.json").read_text())["rules"]
    assert _unproduced(alerts) == KNOWN_UNPRODUCED
    # I8's durable exporter is what makes these fire at all today (F4)
    assert {"ReconciliationDrift", "ReconciliationStale", "UnknownUsageBacklog"}.isdisjoint(
        _unproduced(alerts))
    ops = json.loads((ALERTS / "operations.json").read_text())
    pending = set(ops["pending_producers"])
    for name, metrics in rule_metrics(ops["rules"]).items():
        assert metrics <= exporter_producers() | runtime_producers() | pending, (name, metrics)
    assert pending.isdisjoint(runtime_producers() | exporter_producers()), \
        "a pending producer landed: move it out of pending_producers"


@pytest.mark.xfail(strict=True, reason="F4: W5/G own the runtime producers of "
                                       + ", ".join(sorted(KNOWN_UNPRODUCED)))
def test_ops_continuous__every_alert_rule_names_a_metric_something_produces():
    assert _unproduced(json.loads((ALERTS / "alerts.json").read_text())["rules"]) == set()


class _Pool:
    """psycopg_pool's `pop_stats()` shape: gauges always, counters only once they moved."""
    pops = 0

    def pop_stats(self):
        self.pops += 1
        return {"pool_min": 2, "pool_max": 6, "pool_size": 4, "pool_available": 1,
                "requests_waiting": 3, "requests_num": 10, "requests_wait_ms": 2500,
                "requests_errors": 1, "connections_errors": 2, "connections_lost": 1}


POOL_SERIES = {'infrx_db_pool_connections{process="%s",state="size"} 4.0',
               'infrx_db_pool_connections{process="%s",state="available"} 1.0',
               'infrx_db_pool_connections{process="%s",state="max"} 6.0',
               'infrx_db_pool_requests_waiting{process="%s"} 3.0',
               'infrx_db_pool_requests_total{process="%s"} 20.0',
               'infrx_db_pool_wait_seconds_total{process="%s"} 5.0',
               'infrx_db_pool_timeouts_total{process="%s"} 2.0',
               'infrx_db_pool_connection_errors_total{process="%s"} 4.0',
               'infrx_db_pool_connections_lost_total{process="%s"} 2.0'}


def _gateway_scrape(rt):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from infrx.observe import route
    app = FastAPI()
    route.register(app, rt)
    client = TestClient(app, client=("127.0.0.1", 1))
    return [client.get(route.PATH).text for _ in range(2)][-1]


def test_ops_continuous__both_processes_export_their_db_pool_at_scrape():
    """WR-I8-2: the gateway's /metrics and the worker's read the pool's pop_stats at each
    scrape - gauges as read, counters accumulated across pops (two scrapes: 2x). Oracle: a
    scrape that skips the pool, reads counters as gauges, or wait in ms."""
    import asyncio
    from types import SimpleNamespace
    from infrx.observe.metrics import Registry
    from infrx.worker import WorkerService
    rt = SimpleNamespace(metrics=Registry("gateway"), lifetime=SimpleNamespace(pool=_Pool()))
    text = _gateway_scrape(rt)
    assert {s % "gateway" for s in POOL_SERIES} <= set(text.splitlines())

    async def worker():
        service = WorkerService(loop=None, jobs=None, engine=None, metrics=Registry("worker"),
                                pool=_Pool())
        server = await asyncio.start_server(service._probe, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /metrics HTTP/1.1\r\nhost: x\r\n\r\n")
            body = (await reader.read()).decode()
            writer.close()
        server.close()
        return body
    assert {s % "worker" for s in POOL_SERIES} <= set(asyncio.run(worker()).splitlines())


def test_ops_continuous__the_merged_rule_set_is_versioned_and_well_formed():
    names = [rule["name"] for rule in RULES["rules"]]
    assert len(names) == len(set(names)) and RULES["version"] == "a1+o1"
    anchors = {}
    for rule in RULES["rules"]:
        assert rule["op"] in evaluator.OPS and rule["severity"] in ("page", "ticket")
        status = rule["threshold_status"]
        assert status.startswith(("exact", "derived")) or "⚠️ TO BE VERIFIED (P-18)" in status
        document, _, anchor = rule["runbook"].partition("#")
        path = support.REPO / document
        assert path.is_file(), rule["name"]
        anchors.setdefault(path, {re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower())
                                  .replace(" ", "-") for line in path.read_text().splitlines()
                                  if re.match(r"#{1,6} ", line)})
        assert anchor in anchors[path], f"{rule['name']}: no section #{anchor}"
    bad_ops = json.loads((ALERTS / "operations.json").read_text())
    bad_ops["rules"].append({**bad_ops["rules"][0], "name": "ComponentDown"})
    with pytest.raises(SystemExit, match="defined twice"):           # the merge refuses
        _merge_with(bad_ops)


def _merge_with(ops: dict):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "alerts.json").write_text((ALERTS / "alerts.json").read_text())
        (Path(tmp) / "operations.json").write_text(json.dumps(ops))
        return runpy.run_path(str(OBSERVE / "rules.py"))["merge"](Path(tmp))


def _expo(text: str):
    return evaluator.parse(text)


def test_ops_continuous__the_gateways_blind_gpu_gauge_does_not_page_but_the_hosts_does():
    gateway = 'infrx_gpu_up{process="gateway"} 0\n'
    quiet = evaluator.evaluate(RULES["rules"], _expo(gateway + 'infrx_gpu_up{process="host"} 1\n'))
    assert "GpuUnavailable" not in {a["alert"] for a in quiet}
    down = evaluator.evaluate(RULES["rules"], _expo(gateway + 'infrx_gpu_up{process="host"} 0\n'))
    assert "GpuUnavailable" in {a["alert"] for a in down}
    # alerts.json alone (no override) is the false page the override removes
    alone = json.loads((ALERTS / "alerts.json").read_text())["rules"]
    assert "GpuUnavailable" in {a["alert"] for a in evaluator.evaluate(alone, _expo(gateway))}


# --- the durable-truth exporter, on the real schema through the transaction pooler -------
def test_ops_continuous__durable_truth_reads_holds_backlog_and_drift_through_the_pooler(
        i8_stack, tmp_path):
    import psycopg
    user, org = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(i8_stack.dsn(PG_DIRECT), autocommit=True) as conn:
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"i8-{user[:8]}@example.com"))
        conn.execute("insert into public.organizations (id, name, slug, created_by) "
                     "values (%s, 'i8', %s, %s)", (org, f"i8-{org[:8]}", user))   # + its wallet
        # Holds and a dispatch without the jobs they belong to: the rows the exporter reads,
        # nothing else. `replica` skips the foreign-key triggers on this one connection
        # (superuser, this test's own database) - the admission path is D's to test.
        conn.execute("set session_replication_role = replica")
        conn.execute("insert into infrx.credit_holds (request_id, org_id, amount, state) "
                     "values (%s, %s, 1, 'held')", (str(uuid.uuid4()), org))
        conn.execute("insert into infrx.credit_holds (request_id, org_id, amount, state, "
                     "reconcile_after) values (%s, %s, 0, 'unknown', now() - interval '1 hour')",
                     (str(uuid.uuid4()), org))
        conn.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                     "available_at) values (%s, %s, %s, 'inference_dispatch', "
                     "now() - interval '700 seconds')", (str(uuid.uuid4()), str(uuid.uuid4()), org))
    out = tmp_path / "durable.prom"
    env = {**os.environ, "MONITOR_DATABASE_URL": i8_stack.dsn(TXN)}
    done = subprocess.run([sys.executable, str(OBSERVE / "durable.py"), "--out", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    samples = evaluator.parse(out.read_text())
    value = {(name, dict(labels).get("state") or dict(labels).get("kind")): v
             for (name, labels), v in samples.items()}
    assert value[("infrx_durable_up", None)] == 1
    assert value[("infrx_durable_holds", "held")] >= 1 and value[("infrx_durable_holds", "unknown")] >= 1
    assert value[("infrx_durable_unknown_overdue", None)] >= 1
    assert value[("infrx_holds_unknown", None)] >= 1
    assert value[("infrx_reconciliation_drift", None)] >= 1        # a hold nobody reserved
    assert value[("infrx_durable_ready_backlog", "inference_dispatch")] >= 1
    assert value[("infrx_durable_ready_backlog_seconds", "inference_dispatch")] >= 690
    for state in ("preparing", "queued", "running"):
        assert ("infrx_durable_jobs", state) in value                # a 0 is written, not omitted
    # closed vocabulary: every label value is one this file declares
    allowed = set(DURABLE["ACTIVE"] + DURABLE["TERMINAL"] + DURABLE["HOLD_STATES"]
                  + DURABLE["DISPATCH"] + DURABLE["GC_KINDS"]) | {"durable"}
    assert {v for (_, labels) in samples for _, v in labels} <= allowed
    fired = {a["alert"] for a in evaluator.evaluate(RULES["rules"], samples)}
    assert {"ReconciliationDrift", "UnknownUsageOverdue", "ReadyBacklogOld"} <= fired
    # the monitor's DSN never leaves the process, and a dead database is a 0, not silence
    assert "infrx-i8-local" not in done.stdout + done.stderr
    bad = {**os.environ, "MONITOR_DATABASE_URL": i8_stack.dsn(TXN).replace("55496", "55479")}
    done = subprocess.run([sys.executable, str(OBSERVE / "durable.py"), "--out", str(out)],
                          capture_output=True, text=True, env=bad)
    assert done.returncode == 1 and "infrx-i8-local" not in done.stderr
    assert evaluator.parse(out.read_text())[("infrx_durable_up", (("process", "durable"),))] == 0
    assert "DurableExporterDown" in {a["alert"] for a in evaluator.evaluate(
        RULES["rules"], evaluator.parse(out.read_text()))}


class _Recorder:
    """A connection that records every statement collect() sends and answers empty."""

    def __init__(self):
        self.sql = []

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)


def test_ops_continuous__durable_truth_sends_only_reads_inside_a_read_only_transaction():
    """Until D10's read-only login exists the monitor runs as the runtime login (today's
    privileged `postgres`): the read-only transaction is the only thing between it and a
    write. Oracle: dropping `set transaction read only`, or any statement that is not a
    SELECT or a SET LOCAL, fails here."""
    conn = _Recorder()
    DURABLE["collect"](conn)
    assert conn.sql[0] == "set transaction read only"
    others = [s for s in conn.sql[1:] if not re.match(r"(select|set local) ", s)]
    assert others == [] and len(conn.sql) > 10


def test_ops_continuous__durable_truth_uses_the_transaction_port_by_default():
    runtime = "postgresql://u:p@db.example:5432/postgres?sslmode=require"
    moved = DURABLE["dsn_from_env"]({"DATABASE_URL": runtime})
    assert "port=6543" in moved and "5432" not in moved
    assert DURABLE["dsn_from_env"]({"DATABASE_URL": runtime, "MONITOR_DATABASE_URL": "x"}) == "x"


# --- the host probe -----------------------------------------------------------------------
def test_ops_continuous__the_host_probe_reports_gpu_engine_units_disks_and_the_edge(tmp_path):
    stub = stubs(tmp_path, "nvidia-smi", "curl", "systemctl", "du")
    (stub / "du.out").write_text("12345\t/x\n")
    caddy = tmp_path / "caddy"
    (caddy / "infrx").mkdir(parents=True)
    (caddy / "infrx" / "Caddyfile.maintenance").write_text("maintenance")
    (caddy / "Caddyfile").write_text("maintenance")
    out = tmp_path / "host.prom"
    env = {"OUT": str(out), "CADDY_DIR": str(caddy), "PROCESSING_CACHE_DIR": str(tmp_path)}
    done = run_step((OBSERVE / "host-probe.sh").read_text(), stub, env=env)
    assert done.returncode == 0, done.stderr
    samples = evaluator.parse(out.read_text())
    labels = {(n, dict(l).get("unit") or dict(l).get("mount")): v for (n, l), v in samples.items()}
    assert labels[("infrx_gpu_up", None)] == 1 and labels[("infrx_engine_up", None)] == 1
    assert labels[("infrx_edge_maintenance", None)] == 1
    assert labels[("infrx_processing_cache_bytes", None)] == 12345
    assert {("infrx_unit_active", u) for u in ("marlin2b-vllm", "marlin2b-gateway",
                                               "infrx-worker", "infrx-valkey")} <= set(labels)
    assert ("infrx_disk_free_ratio", "root") in labels
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    # the GPU and the engine gone: both page
    env_down = {**env, "STUB_EXIT_nvidia_smi": "9", "STUB_EXIT_curl": "7"}
    run_step((OBSERVE / "host-probe.sh").read_text(), stub, env=env_down)
    fired = {a["alert"] for a in evaluator.evaluate(RULES["rules"], evaluator.parse(out.read_text()))}
    assert {"GpuUnavailable", "EngineDown", "PublicEdgeDown", "EdgeInMaintenance"} <= fired


# --- the canary --------------------------------------------------------------------------
CURL_CANARY = '''#!{python}
import json, os, pathlib, stat, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
header = args[args.index("-H") + 1][1:]
body = args[args.index("--data-binary") + 1][1:]
with (here / "curl.log").open("a") as log:
    log.write(json.dumps({{"argv": args, "header": pathlib.Path(header).read_text(),
                          "mode": oct(stat.S_IMODE(os.stat(header).st_mode)),
                          "body_head": pathlib.Path(body).read_text()[:200]}}) + "\\n")
out = args[args.index("-o") + 1]
pathlib.Path(out).write_text(os.environ.get("CANARY_ANSWER", '{{"choices":[{{}}]}}'))
print(os.environ.get("CANARY_CODE", "200") + " 1.5")
'''


def _canary(tmp_path, **env):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "curl").write_text(CURL_CANARY.format(python=sys.executable))
    (stub / "curl").chmod(0o755)
    (stub / "curl.log").unlink(missing_ok=True)
    out = tmp_path / "canary.prom"
    done = run_step((OBSERVE / "canary.sh").read_text(), stub, env={"OUT": str(out), **env})
    log = [json.loads(l) for l in (stub / "curl.log").read_text().splitlines()] \
        if (stub / "curl.log").exists() else []
    return done, evaluator.parse(out.read_text()), log


def test_ops_continuous__the_canary_sends_one_text_and_one_video_request_with_a_hidden_key(
        tmp_path):
    key = f"{support.MARKER}-canary-key"
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 64)
    done, samples, log = _canary(tmp_path, INFRX_CANARY_KEY=key, CANARY_VIDEO=str(clip))
    assert done.returncode == 0, done.stderr
    assert len(log) == 2 and key not in done.stdout + done.stderr
    for call in log:
        assert not any(key in a for a in call["argv"]), "the key reached curl's argv"
        assert f"Authorization: Bearer {key}" in call["header"] and call["mode"] == "0o600"
        assert "--max-time" in call["argv"] and "Idempotency-Key" not in call["header"]
    assert '"max_tokens":8' in log[0]["body_head"] and "data:video/mp4;base64," in log[1]["body_head"]
    up = {dict(l).get("kind"): v for (n, l), v in samples.items() if n == "infrx_canary_up"}
    assert up == {"text": 1, "video": 1}

    done, samples, _ = _canary(tmp_path, INFRX_CANARY_KEY=key, CANARY_VIDEO=str(clip),
                               CANARY_CODE="503")
    assert done.returncode == 1
    assert "CanaryFailed" in {a["alert"] for a in evaluator.evaluate(RULES["rules"], samples)}
    done, samples, _ = _canary(tmp_path, INFRX_CANARY_KEY=key, CANARY_VIDEO=str(clip),
                               CANARY_MAX_BYTES="10")                # an over-bound clip
    assert done.returncode == 1 and len([k for k in samples if k[0] == "infrx_canary_up"]) == 2
    done, samples, log = _canary(tmp_path)                            # no key: BLOCKED
    assert done.returncode == 3 and log == [] and "BLOCKED" in done.stderr
    assert "CanaryNotConfigured" in {a["alert"] for a in evaluator.evaluate(RULES["rules"], samples)}


# --- delivery ----------------------------------------------------------------------------
FIRING = [{"alert": "StuckHolds", "severity": "page", "value": 1, "labels": {},
           "summary": "s", "runbook": "infra/runbooks/observe.md#stuck-holds"},
          {"alert": "OutboxGcLag", "severity": "ticket", "value": 9e4,
           "labels": {"kind": "outbox"}, "summary": "s", "runbook": "r"}]


class _Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _deliver(monkeypatch, tmp_path, stdin_alerts, *args, url=None, status=200):
    sent = []
    deliver = runpy.run_path(str(OBSERVE / "deliver.py"))

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return _Answer(status)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    if url:
        monkeypatch.setenv("ALERT_WEBHOOK_URL", url)
    else:
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(a) + "\n" for a in stdin_alerts)))
    code = deliver["main"](["--state", str(tmp_path / "state.json"),
                            "--undelivered", str(tmp_path / "undelivered.jsonl"), *args])
    return code, sent


def test_ops_continuous__delivery_sends_changes_only_and_blocks_without_a_destination(
        monkeypatch, tmp_path):
    code, sent = _deliver(monkeypatch, tmp_path, FIRING)              # P-25 unset
    assert code == 3 and sent == [] and not (tmp_path / "state.json").exists()
    assert "StuckHolds" in (tmp_path / "undelivered.jsonl").read_text()
    url = "https://hooks.example.invalid/" + support.MARKER
    code, sent = _deliver(monkeypatch, tmp_path, FIRING, url=url)
    assert code == 0 and len(sent) == 1 and "[FIRING page] StuckHolds" in sent[0]["text"]
    code, sent = _deliver(monkeypatch, tmp_path, FIRING, url=url)     # unchanged: silent
    assert code == 0 and sent == []
    code, sent = _deliver(monkeypatch, tmp_path, FIRING, "--repeat-s", "0", url=url)
    assert code == 0 and "STILL FIRING page] StuckHolds" in sent[0]["text"] \
        and "OutboxGcLag" not in sent[0]["text"]                        # tickets do not repeat
    code, sent = _deliver(monkeypatch, tmp_path, FIRING[1:], url=url)
    assert "[RESOLVED] StuckHolds" in sent[0]["text"]
    code, sent = _deliver(monkeypatch, tmp_path, FIRING, url=url, status=500)
    assert code == 4 and json.loads((tmp_path / "state.json").read_text()).keys() == \
        {DELIVER["key"](FIRING[1])}                                     # retried next run
    assert all(support.MARKER not in json.dumps(message) for message in sent)


# --- the units ----------------------------------------------------------------------------
def test_ops_continuous__the_monitoring_units_are_valid_and_scheduled():
    names = ["infrx-observe.service", "infrx-observe.timer", "infrx-canary.service",
             "infrx-canary.timer"]
    done = subprocess.run(["systemd-analyze", "verify", "--man=no",
                           *[str(UNITS / n) for n in names]], capture_output=True, text=True)
    assert (done.stdout + done.stderr).strip() == ""
    observe = (UNITS / "infrx-observe.timer").read_text()
    canary = (UNITS / "infrx-canary.timer").read_text()
    assert "OnUnitActiveSec=60s" in observe and "OnUnitActiveSec=10min" in canary
    assert "EnvironmentFile=/etc/infrx-canary.env" in (UNITS / "infrx-canary.service").read_text()
    service = (UNITS / "infrx-observe.service").read_text()
    assert "EnvironmentFile=-/etc/infrx-alert.env" in service and "Type=oneshot" in service


def test_ops_continuous__the_test_alert_is_marked_and_names_its_owner_and_runbook(
        monkeypatch, tmp_path):
    """Slice 4: the delivery proof sends ONE message that cannot be mistaken for a real
    alert, carrying the owner, the escalation and the runbook; its recovery reuses the
    nonce. Without a destination it is BLOCKED (exit 3), never a pass."""
    monkeypatch.setenv("ALERT_OWNER", "sofia")
    monkeypatch.setenv("ALERT_ESCALATION", "pager")
    code, sent = _deliver(monkeypatch, tmp_path, [], "--test", url="https://hooks.example.invalid/x")
    assert code == 0 and len(sent) == 1 and sent[0]["test"] is True
    text = sent[0]["text"]
    assert text.startswith("[TEST FIRING]") and "NO ACTION REQUIRED" in text
    assert "Owner: sofia" in text and "Escalation: pager" in text and "#delivery-test" in text
    nonce = sent[0]["nonce"]
    code, sent = _deliver(monkeypatch, tmp_path, [], "--test-resolve", nonce,
                          url="https://hooks.example.invalid/x")
    assert code == 0 and sent[0]["text"].startswith("[TEST RESOLVED]") and nonce in sent[0]["text"]
    code, sent = _deliver(monkeypatch, tmp_path, [], "--test")
    assert code == 3 and sent == []
