#!/usr/bin/env python3
"""W3 / PERF-PILOT + DEPLOY-FAILCLOSED: the engine pin.

    uv run --frozen pytest -q tests/w/test_serving.py

`models/marlin2b/serve.sh` and `models/marlin2b/serving-version.json` state one fact
twice - what the engine is started as - so these cases run the real script with a stub
`docker` on `PATH` (argv captured, nothing started) and hold the record to what the script
actually passes. No container, no GPU, no network.

Each case is a thin wrapper over a `check_*` function taking the `models/` directory, so
`tests/w/w3_mutants.py` can run the same check against a mutated copy of the script or
the record (R32: `assertion_kill`, the shared kill rule; a shell script cannot go through
the pytest runner, which compiles every mutated file as Python).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import MediaRef
from infrx.contracts.v2.records import DigestSource
from infrx.worker.engine import MODEL_EOS_TOKEN_IDS
from tests.i.support import preflight

MODELS = pathlib.Path(__file__).resolve().parents[4] / "models"
SCRIPT = pathlib.Path("marlin2b/serve.sh")
RECORD = pathlib.Path("marlin2b/serving-version.json")
LOOPBACK_PUBLISH = "127.0.0.1:8000:8000"
STUB_DOCKER = '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_ARGV"\n'


def launch(models: pathlib.Path, tmp: pathlib.Path, *args: str, **env: str):
    """Run serve.sh with a stub docker. Returns (exit status, docker argv or None, stderr)."""
    bin_dir, weights = tmp / "bin", tmp / "weights"
    bin_dir.mkdir(exist_ok=True)
    weights.mkdir(exist_ok=True)
    (weights / "config.json").write_text("{}")
    docker = bin_dir / "docker"
    docker.write_text(STUB_DOCKER)
    docker.chmod(0o755)
    captured = tmp / "argv"
    captured.unlink(missing_ok=True)
    done = subprocess.run(
        ["bash", str(models / SCRIPT), *args], capture_output=True, text=True, timeout=30,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "WEIGHTS": str(weights),
             "DOCKER_ARGV": str(captured), **env})
    argv = captured.read_text().splitlines() if captured.exists() else None
    return done.returncode, argv, done.stderr


def record_of(models: pathlib.Path) -> dict:
    return json.loads((models / RECORD).read_text())


def served_flags(record: dict, **settings: str) -> list[str]:
    """The record's flags with `${NAME}` replaced by the given settings (default: its own)."""
    values = {**record["settings"], **settings}
    flags = []
    for flag in record["flags"]:
        for name, value in values.items():
            flag = flag.replace("${" + name + "}", str(value))
        flags.append(flag)
    return flags


def options_digest(flags: list[str]) -> str:
    compact = json.dumps(flags, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(compact.encode()).hexdigest()


def values_of(argv: list[str], option: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == option]


# --------------------------------------------------------------------------
# the checks (run by the cases below and by the mutation list)
# --------------------------------------------------------------------------
def check_pinned_launch(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """The engine starts from the recorded image digest, on loopback, with exactly the
    recorded flags, the media root mounted read-only at the path it is allowed under, and
    the I0 pilot gate satisfied - whatever `IMAGE`, `MAX_MODEL_LEN` or `GPU_MEM` the
    environment carries (review PIN-2: they used to replace the recorded values)."""
    record = record_of(models)
    root = tmp / "processing"
    root.mkdir(exist_ok=True)
    status, argv, stderr = launch(models, tmp, PROCESSING_CACHE_DIR=str(root),
                                  IMAGE="vllm/vllm-openai:nightly", MAX_MODEL_LEN="99",
                                  GPU_MEM="0.5")
    assert status == 0 and argv, stderr
    image = record["runtime_image"]
    assert image["ref"] == f"vllm/vllm-openai@{image['digest']}", image
    assert len(image["digest"]) == 71 and image["digest"].startswith("sha256:")
    assert image["ref"] in argv, "serve.sh does not start the recorded image"
    after_image = argv[argv.index(image["ref"]) + 1:]
    assert after_image[0] == "/model"
    assert after_image[1:] == served_flags(record, PROCESSING_CACHE_DIR=str(root)), \
        "the flags served are not the flags recorded"
    assert values_of(argv, "-p") == [LOOPBACK_PUBLISH], "the engine is reachable beyond loopback"
    mounts = values_of(argv, "-v")
    assert f"{root}:{root}:ro" in mounts, mounts          # same path inside, read-only
    assert any(m.endswith(":/model:ro") for m in mounts), mounts
    assert not any(flag.startswith("--api-key") for flag in argv)
    assert record["engine_options_digest"] == options_digest(served_flags(record))
    assert preflight.engine_problems(models / SCRIPT, "pilot") == []


def check_one_source_per_setting(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """A pinned setting is read from its environment name only: a second value on the
    command line is refused before docker runs, and a bad root is refused too."""
    for extra in (("--max-num-seqs", "32"), ("--max-num-seqs=32",), ("--api-key", "k"),
                  ("--allowed-local-media-path", "/")):
        status, argv, _ = launch(models, tmp, *extra)
        assert status == 2 and argv is None, (extra, status)
    status, argv, _ = launch(models, tmp, ENGINE_MAX_NUM_SEQS="32")
    assert status == 0 and values_of(argv, "--max-num-seqs") == ["32"]
    # unset root: no mount beyond the weights and no allowed local path at all
    status, argv, _ = launch(models, tmp)
    assert status == 0 and "--allowed-local-media-path" not in argv
    assert len(values_of(argv, "-v")) == 1
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR="relative/cache")
    assert status == 2 and argv is None
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=str(tmp / "absent"))
    assert status == 1 and argv is None


def check_record_matches_the_code(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """The record states facts the code also states; they must be the same facts. (That
    serve.sh's own defaults are the record's is `check_pinned_launch`'s flag equality.)"""
    record = record_of(models)
    assert tuple(record["eos_token_ids"]) == MODEL_EOS_TOKEN_IDS
    assert record["profile_version"] == MediaRef.model_fields["profile_version"].default
    flags = served_flags(record)
    assert values_of(flags, "--max-model-len") == [str(DEFAULTS.max_context_tokens)]
    assert values_of(flags, "--max-num-seqs") == [str(DEFAULTS.engine_max_num_seqs)]
    for source in (record["model"]["digest_source"], record["runtime_image"]["digest_source"]):
        assert source in {member.value for member in DigestSource}, source


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------
def test_perf_pilot__the_engine_starts_pinned_on_loopback_with_the_recorded_flags(tmp_path):
    check_pinned_launch(MODELS, tmp_path)


def test_perf_pilot__a_pinned_setting_has_one_source(tmp_path):
    check_one_source_per_setting(MODELS, tmp_path)


def test_perf_pilot__the_serving_record_matches_the_code_it_pins(tmp_path):
    check_record_matches_the_code(MODELS, tmp_path)

