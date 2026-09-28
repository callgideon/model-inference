"""I2L-OBS: the Lab observe packaging (the I2L extension of 09:145) - the trace and judge
worker units, storage grants, egress budgets and T3's loss/lag/retention alarms, read as the
files ship (`apps/infrx-api/deploy/lab/observe/*`, `infra/lab/observe/*`). Nothing here touches
systemd, a host path, AWS or anything hosted; every role is OFF until an operator writes its env
file. Each case names the broken packaging it catches.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
API = REPO / "apps" / "infrx-api"
DEPLOY, UNITS = API / "deploy", API / "deploy" / "lab" / "observe"
OBSERVE = REPO / "infra" / "lab" / "observe"
MANIFEST, ALERTS, RUNBOOK = OBSERVE / "observe.json", OBSERVE / "alerts.json", OBSERVE / "README.md"
#: the wiring patches this lane files (the coordinator applies them; tests apply them to a copy)
WIRING = REPO / "research" / "plan" / "evidence" / "e" / "E5L-wiring"
#: Lab-only rules beside T3's RULES (the exporter's own health)
LAB_ONLY = {"TraceGaugesDown"}
sys.path.insert(0, str(API))


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def unit(name: str) -> dict[str, list[str]]:
    """`Key=Value` lines of a unit file, continuations joined, repeated keys kept."""
    text = re.sub(r"\\\n\s*", " ", (UNITS / name).read_text())
    keys: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            key, _, value = line.partition("=")
            keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def docker_run(name: str) -> list[str]:
    argv = shlex.split(unit(name)["ExecStart"][0])
    assert argv[:2] == ["/usr/bin/docker", "run"], argv[:2]
    return argv[2:]


def flag(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv) if token == name]


def no_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def gauges_module():
    spec = importlib.util.spec_from_file_location("lab_trace_gauges", OBSERVE / "trace_gauges.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the units: off by default, bounded, never coupled to the App -----------------------------
def test_i2l_obs__every_observe_unit_is_off_until_its_role_env_file_exists():
    """OFF by default (LAB-M1 needs P-08/P-09/P-10): a unit whose env file is absent is a
    condition-failed no-op, and the runbook never writes one outside its enable step. Oracle: a
    unit that starts without the file puts trace reads or judge egress on the box unasked."""
    roles = manifest()["roles"]
    assert manifest()["enabled"] is False and set(roles) == {"judge", "traces"}
    for role, spec in roles.items():
        env_file = spec["env_file"]
        assert env_file == f"/etc/infrx-lab/{role}.env", role
        for name in spec["units"]:
            assert unit(name).get("ConditionPathExists") == [env_file], name
            if name.endswith(".service"):
                assert unit(name).get("EnvironmentFile") == [env_file], name
    assert sorted(p.name for p in UNITS.iterdir()) == sorted(
        name for spec in roles.values() for name in spec["units"])


def test_i2l_obs__no_app_unit_links_to_an_observe_unit_or_the_reverse():
    """A Lab observe outage must not stop, restart or hold back App inference. Oracle: a
    `PartOf=`/`Requires=` link either way lets `systemctl stop` of one take the other down."""
    links = ("Requires", "Requisite", "BindsTo", "PartOf", "Wants", "After", "Before",
             "Upholds", "PropagatesStopTo", "StopPropagatedFrom")
    app_units = sorted(p.name for p in DEPLOY.glob("*.service"))
    assert app_units, "the App runtime units moved"
    for name in app_units:
        assert "lab" not in no_comments((DEPLOY / name).read_text()).lower(), name
    for name in (p.name for p in UNITS.iterdir()):
        named = " ".join(v for key in links for v in unit(name).get(key, ()))
        for app in app_units:
            assert app.removesuffix(".service") not in named, (name, named)


def test_i2l_obs__each_unit_is_bounded_least_privilege_and_its_own():
    """Least privilege and bounded, as I5's workers: read-only, no capabilities, memory/CPU
    weight/pids caps, a uid outside the runtime's (no media root, no usage log), no volume but
    the ones named read-only, and its own env file - never the gateway's. Oracle: an unbounded
    Lab process starves the gateway; a writable mount lets it change App state."""
    for spec in manifest()["roles"].values():
        for name in spec["units"]:
            if name.endswith(".timer"):
                continue
            argv = docker_run(name)
            run = " ".join(argv)
            assert "--read-only" in argv and flag(argv, "--cap-drop") == ["ALL"], name
            assert flag(argv, "--security-opt") == ["no-new-privileges"], name
            assert flag(argv, "--memory") and flag(argv, "--cpus") and flag(argv, "--pids-limit")
            assert flag(argv, "--cpu-shares") == ["256"], name
            uid = flag(argv, "--user")[0].split(":")[0]
            assert uid not in ("10000", "10001", "10002"), (name, uid)
            assert flag(argv, "--env-file") == [spec["env_file"]], name
            assert "marlin2b-gateway.env" not in run, name
            for volume in flag(argv, "-v"):
                assert volume.endswith(":ro") or volume.startswith("/var/lib/infrx/metrics"), \
                    (name, volume)


def test_i2l_obs__the_judge_worker_runs_its_role_and_drains():
    """The judge unit runs the Lab worker entry point's `judge` role (WR-OBS-1) with a health
    port of its own, and its stop budget outlasts the drain. Oracle: a stop shorter than the
    drain kills a submit between `record_sent` and the provider's answer (an ambiguous run)."""
    argv = docker_run("infrx-lab-judge.service")
    assert argv[-4:] == ["python", "-m", "infrx.lab.workers", "judge"]
    assert "-e" in argv and f"LAB_WORKER_HEALTH_PORT={manifest()['roles']['judge']['health_port']}" in argv
    stop = shlex.split(unit("infrx-lab-judge.service")["ExecStop"][0])
    assert stop[:3] == ["/usr/bin/docker", "stop", "-t"]
    assert int(stop[3]) < int(unit("infrx-lab-judge.service")["TimeoutStopSec"][0])
    assert unit("infrx-lab-judge.service")["StartLimitBurst"] == ["5"]


def test_i2l_obs__the_trace_gauges_run_on_a_timer_from_the_shipped_exporter():
    """The trace role's unit is a oneshot of `infra/lab/observe/trace_gauges.py` every minute,
    writing the textfile the observe cycle reads. Oracle: a unit that names another path or
    file runs code the repository does not ship, or writes where no evaluator reads."""
    service = unit("infrx-lab-trace-gauges.service")
    assert service["Type"] == ["oneshot"]
    argv = docker_run("infrx-lab-trace-gauges.service")
    assert argv[-4:] == ["python", "/observe/trace_gauges.py", "--out",
                         f"/m/{manifest()['roles']['traces']['textfile']}"]
    assert f"{manifest()['roles']['traces']['install_dir']}/infra/lab/observe:/observe:ro" \
        in flag(argv, "-v")
    timer = unit("infrx-lab-trace-gauges.timer")
    assert timer["OnUnitActiveSec"] == ["60s"] and timer["Unit"] == ["infrx-lab-trace-gauges.service"]


# --- names, grants, egress ----------------------------------------------------------------------
def test_i2l_obs__secret_names_only_and_every_name_a_unit_reads_is_declared():
    """Names only: no value in the manifest, and every `${NAME}` a unit reads is declared with
    its exposure. Oracle: an undeclared name is a setting nobody reviews; a DSN, key or URL
    value in the repository is a leaked secret."""
    declared = {item["name"]: item for items in manifest()["env"].values() for item in items}
    for item in declared.values():
        assert set(item) == {"name", "exposure", "purpose"}, item
        assert item["exposure"] in ("server", "secret", "unit"), item
    text = json.dumps(manifest())
    assert not re.search(r"(postgres(ql)?|https?|clickhouse)://[^\s\"]*@", text), "a credential"
    assert not re.search(r"\b(AKIA|ASIA)[A-Z0-9]{12,}", text), "an access key id"
    for name in (p.name for p in UNITS.iterdir()):
        read = set(re.findall(r"\$\{([A-Z_]+)\}", (UNITS / name).read_text()))
        assert read <= set(declared), (name, read - set(declared))
    from infrx.contracts.limits import env_name
    for field in ("judge_mode", "judge_live_budget_usd", "clickhouse_url", "s3_trace_bucket",
                  "trace_spool_dir"):
        assert env_name(field) in declared, field


def test_i2l_obs__storage_grants_are_object_prefixes_under_the_trace_root_never_a_bucket():
    """Grants are object prefixes: the trace role reads/writes/deletes under T2I's content
    prefix only, the judge reads only, and no observe role touches the App's media, upload or
    payload prefixes. Oracle: a bucket-wide grant lets a Lab worker read or delete App media."""
    from infrx.traces.ship import CONTENT_PREFIX
    grants = manifest()["storage_grants"]
    assert {g["role"] for g in grants} == {"judge", "traces"}
    for grant in grants:
        assert grant["bucket_setting"] == "S3_TRACE_BUCKET", grant
        prefix = grant["prefix"]
        assert prefix == "${OBJECT_PREFIX}" + CONTENT_PREFIX, grant
        assert not any(p in prefix for p in ("media/", "uploads/", "payloads/", "lab/")), grant
    actions = {g["role"]: set(g["actions"]) for g in grants}
    assert actions["judge"] == {"s3:GetObject"}
    assert actions["traces"] == {"s3:GetObject", "s3:PutObject", "s3:DeleteObject"}


def test_i2l_obs__judge_egress_is_the_local_fake_and_a_zero_budget_until_p10():
    """Egress budgets: the judge may reach only the hosts J2's adapter allows (the local fake)
    with a live budget of 0 USD and `dry_run` as the mode, until P-10 approves a provider and
    its rates; the trace role reaches only its own stores. Oracle: a manifest that allows
    another host, or a non-zero budget, would be an approval nobody gave."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.judge.submit import LOCAL_HOSTS
    egress = manifest()["egress"]
    assert set(egress["judge"]["hosts"]) == set(LOCAL_HOSTS)
    assert egress["judge"]["budget"] == {"JUDGE_MODE": DEFAULTS.judge_mode,
                                         "JUDGE_LIVE_BUDGET_USD": str(DEFAULTS.judge_live_budget_usd),
                                         "approval": "P-10"}
    assert DEFAULTS.judge_mode == "dry_run" and DEFAULTS.judge_live_budget_usd == 0
    assert egress["traces"]["hosts_from"] == ["CLICKHOUSE_URL", "S3_ENDPOINT_URL",
                                              "LAB_DATABASE_URL"]


# --- T3's alarms and the exporter -------------------------------------------------------------
def test_i2l_obs__the_alarms_are_t3s_rules_with_runbook_anchors_and_no_name_clash():
    """WR-T-5: T3's `RULES` exactly (same names, metrics, thresholds, severities), each with a
    runbook anchor that exists, in the evaluator's shape; no name clashes with the App's rule
    files, so a merge cannot shadow one. Oracle: a hand-copied threshold drifts from the code."""
    from infrx.traces.retention.policy import RULES
    doc = json.loads(ALERTS.read_text())
    rules = doc["rules"]
    t3 = [r for r in rules if r["name"] not in LAB_ONLY]
    assert [{k: v for k, v in r.items() if k != "runbook"} for r in t3] == list(RULES)
    assert {r["name"] for r in rules} - {r["name"] for r in t3} == LAB_ONLY
    headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
                for h in re.findall(r"^#+ (.+)$", RUNBOOK.read_text(), re.M)}
    for rule in rules:
        path, _, anchor = rule["runbook"].partition("#")
        assert path == "infra/lab/observe/README.md" and anchor in headings, rule["runbook"]
    app = {r["name"] for f in (REPO / "infra" / "alerts").glob("*.json")
           for r in json.loads(f.read_text()).get("rules", ())}
    assert not app & {r["name"] for r in rules}


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def loss(self):
        if self.fail:
            raise ConnectionError("clickhouse://user:secret@db:8123 refused")
        return {"full": (200, 4, 1000), "minimal": (100, 0, 0)}

    async def pending(self, limit, after=None):
        from infrx.traces.retention.policy import Tombstone
        return [Tombstone("o", "r", "request", "owner",
                          datetime(2026, 9, 1, tzinfo=timezone.utc))]


class FakeOutbox:
    async def lag(self):
        return {"pending": 3, "oldest_s": 1200.0}


def test_i2l_obs__the_exporter_writes_t3s_gauges_and_a_failure_is_up_0_never_silence(tmp_path,
                                                                                    capsys):
    """The exporter renders `retention.gauges` (loss, deletion backlog, projection lag, spool
    bytes) as Prometheus text with `infrx_trace_gauges_up 1`, every T3 alarm's metric present;
    a failure writes `up 0`, exits 1 and prints no value. Oracle: a silent exporter blinds every
    trace alarm; a printed DSN leaks a credential."""
    from infrx.traces.retention.policy import RULES, Retention, firing
    exporter = gauges_module()
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "trace-000001.seg").write_bytes(b"x" * 1234)
    (spool / "trace.lock").write_bytes(b"y" * 99)                    # not a segment
    clock = lambda: datetime(2026, 9, 3, tzinfo=timezone.utc)          # noqa: E731
    retention = Retention(FakeStore(), None, None, None, clock=clock)
    values = asyncio.run(exporter.collect(retention, outbox=FakeOutbox(), spool_dir=spool))
    assert values["infrx_trace_spool_bytes"] == 1234
    assert values["infrx_trace_loss_ratio"] == 4 / 300
    assert values["infrx_trace_deletion_backlog_seconds"] == timedelta(days=2).total_seconds()
    assert values["infrx_feedback_projection_lag_seconds"] == 1200.0
    assert {r["metric"] for r in RULES} <= set(values)
    assert set(firing(values)) == {"TraceDeletionBacklogOld", "FeedbackProjectionLagging",
                                   "TraceLossHigh"}
    out = tmp_path / "lab-traces.prom"
    assert exporter.write(out, values) == 0
    text = out.read_text()
    assert "infrx_trace_gauges_up 1\n" in text and "infrx_trace_spool_bytes 1234\n" in text
    broken = Retention(FakeStore(fail=True), None, None, None, clock=clock)
    code = exporter.run(out, lambda: (broken, None, None))
    assert code == 1 and out.read_text() == "infrx_trace_gauges_up 0\n"
    printed = capsys.readouterr()
    assert "ConnectionError" in printed.err and "secret" not in printed.out + printed.err


def test_i2l_obs__an_exporter_that_wrote_up_0_fires_an_alarm_through_the_evaluator(tmp_path,
                                                                                   capsys):
    """A failed exporter's textfile (`up 0` alone) is fresh and non-empty, so ScrapeFailed stays
    quiet: `TraceGaugesDown` must fire over it through the real evaluator (`infrx.observe.
    alerts`), and stay quiet over a healthy file. Oracle: without it a broken exporter silences
    every trace alarm (E5L fix round, 0-F3)."""
    from infrx.observe import alerts
    from infrx.traces.retention.policy import Retention
    exporter = gauges_module()
    out = tmp_path / "lab-traces.prom"
    clock = lambda: datetime(2026, 9, 3, tzinfo=timezone.utc)          # noqa: E731
    broken = Retention(FakeStore(fail=True), None, None, None, clock=clock)
    assert exporter.run(out, lambda: (broken, None, None)) == 1
    rules = json.loads(ALERTS.read_text())["rules"]
    down = [a for a in alerts.evaluate(rules, alerts.parse(out.read_text()))]
    assert [(a["alert"], a["severity"]) for a in down] == [("TraceGaugesDown", "page")], down
    assert alerts.main(["--rules", str(ALERTS), "--source", str(out), "--max-age", "900"]) == 1
    fired = [json.loads(line)["alert"] for line in capsys.readouterr().out.splitlines()]
    assert fired == ["TraceGaugesDown"], fired
    assert exporter.write(out, {"infrx_trace_spool_bytes": 0.0}) == 0
    assert alerts.evaluate(rules, alerts.parse(out.read_text())) == []


def test_i2l_obs__no_two_lab_worker_units_share_a_health_port():
    """Every Lab worker unit runs on the host network, so each `LAB_WORKER_HEALTH_PORT` is its
    own (the judge's is the manifest's). Oracle: two units on one port - the judge and the
    annotation worker both on 8014 (0-LO-INT-1) - and the second cannot bind /readyz."""
    ports: dict[str, str] = {}
    for path in sorted((DEPLOY / "lab").rglob("*.service")):
        for port in re.findall(r"LAB_WORKER_HEALTH_PORT=(\d+)", path.read_text()):
            assert port not in ports, (port, ports.get(port), path.name)
            ports[port] = path.name
    judge = str(manifest()["roles"]["judge"]["health_port"])
    assert ports.get(judge) == "infrx-lab-judge.service", ports
    # the sibling lanes' reservations (lab-workers: annotation 8014, training 8015, rollout 8016)
    assert judge not in {"8011", "8012", "8013", "8014", "8015", "8016"}, judge


def wired_checkout(root: Path) -> Path:
    """The monitor's sources as the coordinator will merge them: this tree's `infra/observe`,
    `infra/alerts`, step 72 and `infra/lab/observe`, with each E5L wiring patch applied unless
    the tree already carries it."""
    for part in ("infra/observe", "infra/alerts", "infra/lab/observe", "infra/rollout/steps"):
        shutil.copytree(REPO / part, root / part, ignore=shutil.ignore_patterns("__pycache__"))
    for patch in sorted(WIRING.glob("WR-OBS-*.diff")):
        forward = subprocess.run(["git", "apply", "--check", str(patch)], cwd=root,
                                 capture_output=True, text=True)
        if forward.returncode == 0:
            done = subprocess.run(["git", "apply", str(patch)], cwd=root, capture_output=True,
                                  text=True)
        else:
            done = subprocess.run(["git", "apply", "-R", "--check", str(patch)], cwd=root,
                                  capture_output=True, text=True)
        assert done.returncode == 0, (patch.name, forward.stderr, done.stderr)
    return root


def between(text: str, start: str, end: str) -> str:
    assert start in text and end in text, (start, end)
    return text[text.index(start):text.index(end)]


def test_i2l_obs__the_pinned_monitor_copy_holds_the_lab_files_and_an_old_pin_keeps_app_alerts(
        tmp_path):
    """Step 72 pins the monitor's copy (`/opt/infrx/observe`); the gauges unit mounts
    `infra/lab/observe` from it and WR-OBS-2's cycle reads its alarms there. So WR-OBS-5 must
    copy that directory into the pin, and WR-OBS-2 must merge the Lab rules only when the pin
    has them. Runs step 72's copy block and WR-OBS-2's merge block as shipped (patched copy).
    Oracle: a pin without the Lab files makes rules.py exit 1 and the evaluator gets an empty
    rule file - every App alert silent (0-F4, 1-LO-SCOPE-1)."""
    src = wired_checkout(tmp_path / "src")
    env = {"PATH": os.environ["PATH"], "repo": str(src)}
    step = (src / "infra/rollout/steps/72-observe-install.sh").read_text()
    pinned = tmp_path / "pinned"
    copy = between(step, 'mkdir -p "$next/infra"', 'echo "$RELEASE"')
    done = subprocess.run(["bash", "-euo", "pipefail", "-c", copy], env={**env, "next": str(pinned)},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    install = manifest()["roles"]["traces"]["install_dir"]
    mount = [v.split(":")[0] for v in flag(docker_run("infrx-lab-trace-gauges.service"), "-v")
             if v.endswith(":/observe:ro")]
    assert len(mount) == 1 and mount[0].startswith(install + "/"), mount
    for name in ("alerts.json", "trace_gauges.py"):
        assert (pinned / mount[0].removeprefix(install + "/") / name).is_file(), name

    observe = (src / "infra/observe/observe.sh").read_text()
    merge = between(observe, "lab_rules=() lab_sources=()", 'chmod 0644 "$rules"')
    enabled = tmp_path / "traces.env"
    enabled.write_text("")
    merge = merge.replace("/etc/infrx-lab/traces.env", str(enabled)) \
        + '\nprintf "%s\\n" "${lab_sources[@]}" > "$sources"\n'

    def cycle(repo: Path) -> tuple[set[str], list[str]]:
        rules, sources = tmp_path / "rules.json", tmp_path / "sources"
        ran = subprocess.run(["bash", "-uo", "pipefail", "-c", merge], capture_output=True,
                             text=True, env={**env, "repo": str(repo), "rules": str(rules),
                                             "sources": str(sources)})
        assert ran.returncode == 0, ran.stderr
        assert rules.read_text().strip(), "an empty rule file: every App alert silent"
        return ({r["name"] for r in json.loads(rules.read_text())["rules"]},
                sources.read_text().split())

    app = {r["name"] for f in ("alerts.json", "operations.json")
           for r in json.loads((REPO / "infra/alerts" / f).read_text())["rules"]}
    lab = {r["name"] for r in json.loads(ALERTS.read_text())["rules"]}
    names, sources = cycle(pinned)
    assert app | lab <= names and sources == ["--source", "/m/lab-traces.prom"]
    shutil.rmtree(pinned / "infra" / "lab")                          # a pre-WR-OBS-5 pin
    names, sources = cycle(pinned)
    assert app <= names and not lab & names, "the App's rules must survive a pin without Lab's"
    assert sources == ["--source", "/m/lab-traces.prom"], "its textfile still scrapes (ScrapeFailed)"
