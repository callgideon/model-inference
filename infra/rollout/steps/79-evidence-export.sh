#!/usr/bin/env bash
# I8 slice 7, box, read-only: the operational state as ONE JSON document for the evidence
# record (the coordinator saves this step's SSM output under research/plan/evidence/i/).
# Names, ids, counts, timings and 0/1 gauges only - never an env value, key, DSN or URL:
# the release and image the env file pins, the units and timers, the three metrics
# textfiles, the undelivered-alert count, the install backups (named for -> hold), the
# release bundles on the NVMe, disk use. Bounded: every read has a timeout or a line cap.
set -euo pipefail
env_file=${ENV_FILE:-/etc/marlin2b-gateway.env}
metrics=${METRICS_DIR:-/var/lib/infrx/metrics}
backups=${BACKUPS:-/var/backups/infrx}
releases=${RELEASES_DIR:-/opt/dlami/nvme/releases}
python3 - "$env_file" "$metrics" "$backups" "$releases" <<'PY'
import json, os, pathlib, subprocess, sys, tarfile, time
env_file, metrics, backups, releases = map(pathlib.Path, sys.argv[1:])
def run(*argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
env = {}
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            env[name] = value
schema = next((l for l in env_file.read_text().splitlines() if l.startswith("# INFRX_ENV_SCHEMA ")), "") \
    if env_file.exists() else ""
def held(tar):
    try:
        with tarfile.open(tar) as t:
            member = t.extractfile("etc/marlin2b-gateway.env")
            for line in member.read().decode().splitlines():
                if line.startswith("INFRX_RELEASE_SHA="):
                    return line.split("=", 1)[1]
    except (OSError, KeyError, tarfile.TarError, AttributeError):
        pass
    return None
def samples(path):
    out = []
    if path.exists():
        for line in path.read_text().splitlines()[:200]:
            if line and not line.startswith("#"):
                out.append(line)
    return out
doc = {
    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "release": env.get("INFRX_RELEASE_SHA"), "image": env.get("INFRX_IMAGE"),
    "mode": env.get("INFRX_MODE"), "env_schema": schema[len("# INFRX_ENV_SCHEMA "):] or None,
    "env_names": sorted(env),
    "units": {u: run("systemctl", "is-active", u) for u in (
        "marlin2b-vllm", "marlin2b-gateway", "infrx-worker", "infrx-valkey",
        "infrx-observe.timer", "infrx-canary.timer")},
    "failed_units": run("systemctl", "--failed", "--no-legend", "--plain").splitlines()[:20],
    "metrics": {name: samples(metrics / f"{name}.prom") for name in ("host", "durable", "canary")},
    "undelivered_alerts": len((metrics / "undelivered.jsonl").read_text().splitlines())
        if (metrics / "undelivered.jsonl").exists() else 0,
    "backups": {d.name: held(d / "files.tar") for d in sorted(backups.glob("*/"))[-20:]}
        if backups.exists() else {},
    "release_bundles": sorted(p.stem for p in releases.glob("*.bundle"))[-20:] if releases.exists() else [],
    "disk": run("df", "-h", "--output=target,pcent,avail", "/", "/opt/dlami/nvme").splitlines(),
}
print(json.dumps(doc, indent=1))
PY
