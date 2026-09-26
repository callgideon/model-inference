#!/usr/bin/env python3
"""E1B WC-6a / WC-6b (E1B-protocol §7.2, D-13 option A): the committed L8 copies.

No box, no GPU, no docker: each script runs against a scratch NVME tree with stub
`systemctl`, `docker`, `curl`, `nvidia-smi`, `git` and python on PATH.

    python -m pytest models/marlin2b/tests/test_e1b_l8.py -q
"""
import json
import os
import re
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1B = os.path.join(os.path.dirname(HERE), "e1b")
SCRIPTS = {name: os.path.join(E1B, name) for name in ("l8ref.sh", "l8served.sh")}
SYNTH = json.load(open(os.path.join(os.path.dirname(HERE), "corpus-synth", "manifest.json"),
                       encoding="utf-8"))
SAMPLES = ["samples/sample-10s.mp4", "samples/Big_Buck_Bunny_360_10s_1MB.mp4"]
DRILL = re.compile(r"^drill=\S+ runbook=\S+ ssm=\S+ t0=\S+ recovered=\S+ measured_s=\d+ "
                   r"bound_s=\d+ verdict=(PASS|FAIL) correctness=(PASS|FAIL: .+)$", re.M)
STUBS = {
    "systemctl": 'echo "$*" >> "$STUB_LOG/systemctl"; [ "$1" = is-active ] && echo active; exit 0',
    "docker": 'echo "$*" >> "$STUB_LOG/docker"\n'
              'case "$*" in *"{{json .Args}}"*) echo \'["--max-num-seqs","8"]\';; '
              '*"{{.Image}}"*) echo sha256:img;; *--format*) echo exited;; *) exit 1;; esac',
    "curl": 'case "$*" in *metrics*) printf "vllm:num_requests_running 0\\nvllm:num_requests_waiting 0\\n";; esac; exit 0',
    "nvidia-smi": "echo '1 MiB, 46068 MiB'",
    "git": "echo abc1234",
    "py": 'echo "$*" >> "$STUB_LOG/py"; echo "wall 1" >&2; echo \'{"raw": ""}\'',
}


def clips():
    """The 11 paths each script's CLIPS names, relative to NVME."""
    out = {}
    for name, path in SCRIPTS.items():
        block = re.search(r'^CLIPS="([^"]*)"$', open(path, encoding="utf-8").read(), re.M).group(1)
        out[name] = [line.replace("$NVME/", "") for line in block.splitlines()]
    return out


def box(tmp, missing=()):
    """(env, script text by name): a scratch NVME holding the clips except `missing`, stubs on PATH."""
    nvme, stub, log = (os.path.join(tmp, d) for d in ("nvme", "bin", "log"))
    for d in (stub, log):
        os.makedirs(d)
    for rel in clips()["l8ref.sh"]:
        if rel not in missing:
            os.makedirs(os.path.dirname(os.path.join(nvme, rel)), exist_ok=True)
            with open(os.path.join(nvme, rel), "wb") as f:
                f.write(b"\0" * 16)
    for name, body in STUBS.items():
        path = os.path.join(stub, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    texts = {name: open(path, encoding="utf-8").read().replace("/opt/dlami/nvme", nvme)
             .replace("/opt/pytorch/bin/python3", os.path.join(stub, "py"))
             for name, path in SCRIPTS.items()}
    env = {"PATH": f"{stub}:/usr/bin:/bin", "EUTC": "20260926T0000Z", "STUB_LOG": log}
    return env, texts, log


def calls(log, tool):
    path = os.path.join(log, tool)
    return open(path).read().splitlines() if os.path.exists(path) else []


def test_both_halves_parse_and_name_the_same_eleven_in_cap_clips():
    """Oracle: a copy that does not parse, a 120 s clip (over the 82 s cap: the product never
    sends it, so it proves nothing about in-cap parity) or a missing sop00-sop08 clip, or the
    two halves on different clip lists (l8compare.py would pair nothing)."""
    for path in SCRIPTS.values():
        assert subprocess.run(["bash", "-n", path]).returncode == 0, path
    in_cap = [c["file"] for c in SYNTH["clips"] if c["duration_s"] <= 82]
    assert [f.rsplit("/", 1)[1][:5] for f in in_cap] == [f"sop0{i}" for i in range(9)]
    listed = clips()
    assert listed["l8ref.sh"] == listed["l8served.sh"] == \
        [f"w3-corpus/{f}" for f in in_cap] + SAMPLES


def test_the_reference_half_is_a_declared_stop_that_silences_and_restores_the_alert_cycle():
    """E1BP-5. Oracle: the engine stopped while the alert cycle runs (its ComponentDown page
    would be an undeclared outage), the cycle left stopped after the restore, or no
    drills.md-format line for the stop."""
    with tempfile.TemporaryDirectory() as tmp:
        env, texts, log = box(tmp)
        done = subprocess.run(["bash", "-c", texts["l8ref.sh"]], env=env, capture_output=True,
                              text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        assert [c for c in calls(log, "systemctl") if not c.startswith("is-active")] == [
            "stop infrx-observe.timer", "stop marlin2b-vllm", "start marlin2b-vllm",
            "start infrx-observe.timer"]
        assert "restored=yes" in done.stdout and "silence=infrx-observe.timer" in done.stdout
        line = DRILL.search(done.stdout)
        assert line and "ssm=FILL" in line.group(0) and "verdict=PASS" in line.group(0), done.stdout
        assert len(calls(log, "py")) == 1 + 11          # the version probe, then one per clip


def test_a_missing_clip_refuses_before_the_engine_or_the_alerts_are_touched():
    """The sop00-sop08 precondition. Oracle: a half that stops the engine (or silences the
    alerts, or sends a request) and only then finds a clip missing - the window would lose
    WC-7's cold start for a parity result over fewer clips than §7.4 names."""
    for name in SCRIPTS:
        with tempfile.TemporaryDirectory() as tmp:
            env, texts, log = box(tmp, missing=("w3-corpus/sop-synth-v1/sop06-60s-640x360.mp4",))
            done = subprocess.run(["bash", "-c", texts[name]], env=env, capture_output=True,
                                  text=True, timeout=60)
            assert done.returncode == 2 and "refused" in done.stdout and "sop06" in done.stdout, name
            assert calls(log, "systemctl") == calls(log, "docker") == calls(log, "py") == [], name
