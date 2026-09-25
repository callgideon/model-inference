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
from tests.i import support
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


def run_script(models: pathlib.Path, tmp: pathlib.Path, script: str, stubs: dict[str, str],
               **env: str) -> subprocess.CompletedProcess:
    """Run a measurement script with stub commands on `PATH` (nothing real is called)."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o755)
    return subprocess.run(["bash", str(models / "marlin2b/measure" / script)],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), **env})


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
    for bad in ("0", "-1", "abc", "08", "8 --api-key k"):          # review PIN-3
        status, argv, _ = launch(models, tmp, ENGINE_MAX_NUM_SEQS=bad)
        assert status == 2 and argv is None, (bad, status)
    # unset root: no mount beyond the weights and no allowed local path at all
    status, argv, _ = launch(models, tmp)
    assert status == 0 and "--allowed-local-media-path" not in argv
    assert len(values_of(argv, "-v")) == 1
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR="relative/cache")
    assert status == 2 and argv is None
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=str(tmp / "absent"))
    assert status == 1 and argv is None
    # a root the engine could read the host through, refused before its existence is checked
    for root in ("/", "/etc", "/home", "/model/cache", "/opt/../etc"):
        status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=root)
        assert status == 2 and argv is None, (root, status)
    (tmp / "cache").mkdir(exist_ok=True)
    for walked in (f"{tmp}/cache/../cache", f"{tmp}/cache/", f"{tmp}//cache"):
        status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=walked)
        assert status == 2 and argv is None, (walked, status)
    # canonical as spelled, but a symlink to a refused directory (confirmation PINC-2);
    # a symlink to an allowed one is fine
    link = tmp / "link-etc"
    link.unlink(missing_ok=True)
    link.symlink_to("/etc")
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=str(link))
    assert status == 2 and argv is None, (str(link), status)
    fine = tmp / "link-cache"
    fine.unlink(missing_ok=True)
    fine.symlink_to(tmp / "cache")
    status, argv, _ = launch(models, tmp, PROCESSING_CACHE_DIR=str(fine))
    assert status == 0 and values_of(argv, "--allowed-local-media-path") == [str(fine)]


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


def sweep_inputs(tmp: pathlib.Path) -> dict[str, str]:
    """A checkout with bench.py and a corpus manifest, and a built corpus, for concurrency.sh."""
    repo = tmp / "repo" / "models" / "marlin2b"
    (repo / "corpus").mkdir(parents=True, exist_ok=True)
    (repo / "bench.py").write_text("")
    (repo / "corpus" / "manifest.json").write_text("{}")
    (tmp / "corpus-cache").mkdir(exist_ok=True)
    return {"REPO": str(tmp / "repo"), "CORPUS_CACHE": str(tmp / "corpus-cache"),
            "PY": str(tmp / "bin" / "bench"), "OUT": str(tmp / "out"), "LEVELS": "1 2"}


def check_the_sweep_survives_a_failed_scrape(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """measure/concurrency.sh (review PIN-4): one `/metrics` scrape that fails - a 2 s
    timeout at c = 32 is plausible - is an empty sample; the sampler goes on, and so does
    the sweep (every level, the report). An engine log without the KV lines (rotated) is a
    missing line, not a failed sweep."""
    stubs = {
        "docker": 'case "$*" in *Args*) echo \'["/model","--max-num-seqs","32"]\' ;; '
                  "*Image*) echo sha256:0 ;; esac\n",
        # the first scrape times out (curl's 28), every later one answers
        "curl": 'n=$(cat "$CURL_CALLS" 2>/dev/null || echo 0); echo $((n + 1)) > "$CURL_CALLS"\n'
                '[ "$n" -gt 0 ] || exit 28\n'
                "printf 'vllm:num_requests_running 1\\nvllm:num_requests_waiting 0\\n'\n",
        "nvidia-smi": 'echo "1000, 50"\n',
        "bench": 'case "$*" in *verify*) echo "verified 72 file(s), 0 missing, 0 error(s)" ;; '
                 '*--report*) echo "report rows" ;; *) sleep 1.5 ;; esac\n'}
    done = run_script(models, tmp, "concurrency.sh", stubs, **sweep_inputs(tmp),
                      CURL_CALLS=str(tmp / "curl-calls"))
    assert done.returncode == 0, (done.returncode, done.stderr[-400:])
    for line in ("level=1 peak_", "level=2 peak_", "### report", "report rows", "artifacts="):
        assert line in done.stdout, (line, done.stdout[-600:])
    (run_dir,) = (tmp / "out").iterdir()
    sampled = [row.split("\t")[0] for row in
               (run_dir / "samples.tsv").read_text().splitlines()[1:]]
    assert "1" in sampled and "2" in sampled, f"a level went unsampled: {sampled}"


def check_the_sweep_counts_its_metrics_exactly(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """measure/concurrency.sh (the 2026-09-23 box sweep): `vllm:num_requests_waiting` is the
    total, not a prefix - vLLM also exports `num_requests_waiting_by_reason{...}`, and a
    prefix match added it in, doubling a nonzero waiting peak. A peak of zero prints `0`,
    not an empty field that reads like "not sampled"."""
    stubs = {
        "docker": 'case "$*" in *Args*) echo \'["/model","--max-num-seqs","32"]\' ;; '
                  "*Image*) echo sha256:0 ;; esac\n",
        "curl": "printf 'vllm:num_requests_running{engine=\"0\"} 0.0\\n"
                "vllm:num_requests_waiting{engine=\"0\"} 1.0\\n"
                "vllm:num_requests_waiting_by_reason{engine=\"0\",reason=\"capacity\"} 1.0\\n'\n",
        "nvidia-smi": 'echo "1000, 0"\n',
        "bench": 'case "$*" in *verify*) echo "verified 72 file(s), 0 missing, 0 error(s)" ;; '
                 '*--report*) echo "report rows" ;; *) sleep 0.5 ;; esac\n'}
    done = run_script(models, tmp, "concurrency.sh", stubs, **{**sweep_inputs(tmp), "LEVELS": "1"})
    assert done.returncode == 0, (done.returncode, done.stderr[-400:])
    assert "level=1 peak_running=0 peak_waiting=1 " in done.stdout, done.stdout[-600:]
    (run_dir,) = (tmp / "out").iterdir()
    waiting = {row.split("\t")[3] for row in
               (run_dir / "samples.tsv").read_text().splitlines()[1:]}
    assert waiting == {"1"}, f"waiting samples {waiting}: the by-reason series was counted"
    assert "corpus=verified 72 file(s), 0 missing, 0 error(s)" in done.stdout


def check_the_sweep_refuses_an_unverified_corpus(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """measure/concurrency.sh (the 2026-09-23 box sweep): the corpus precondition is
    `corpus/build.py verify`, not the directory's existence - a cache that does not verify
    is refused (exit 2) before anything is written or the engine is loaded."""
    stubs = {
        "docker": 'case "$*" in *Args*) echo \'["/model","--max-num-seqs","32"]\' ;; '
                  "*Image*) echo sha256:0 ;; esac\n",
        "curl": "exit 7\n", "nvidia-smi": "exit 9\n",
        "bench": 'case "$*" in *verify*) echo "c012: not in cache" >&2; '
                 'echo "verified 71 file(s), 1 missing, 1 error(s)"; exit 1 ;; '
                 '*) echo loaded >> "$BENCH_CALLS" ;; esac\n'}
    done = run_script(models, tmp, "concurrency.sh", stubs, **sweep_inputs(tmp),
                      BENCH_CALLS=str(tmp / "bench-calls"))
    assert done.returncode == 2, (done.returncode, done.stderr[-400:])
    assert "refused: the corpus does not verify" in done.stderr, done.stderr[-400:]
    assert not (tmp / "out").exists() and not (tmp / "bench-calls").exists()


def check_the_sweep_labels_a_failed_run(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """measure/concurrency.sh (confirmation PINC-1): an engine that fails every level (bench
    exits 3, so `--report` finds no rows and exits 1) is a labelled run that still reaches
    `artifacts=`; no container is a `refused:` precondition, exit 2, nothing written."""
    stubs = {
        "docker": 'case "$*" in *Args*) echo \'["/model","--max-num-seqs","32"]\' ;; '
                  "*Image*) echo sha256:0 ;; esac\n",
        "curl": "printf 'vllm:num_requests_running 0\\n'\n",
        "nvidia-smi": 'echo "1000, 0"\n',
        "bench": 'case "$*" in *verify*) echo "verified 72 file(s), 0 missing, 0 error(s)" ;; '
                 '*--report*) exit 1 ;; *) exit 3 ;; esac\n'}
    done = run_script(models, tmp, "concurrency.sh", stubs, **sweep_inputs(tmp))
    assert done.returncode == 0, (done.returncode, done.stderr[-400:])
    for line in ("level=1 bench_exit=3", "level=2 bench_exit=3", "report_exit=1", "artifacts="):
        assert line in done.stdout, (line, done.stdout[-600:])
    stubs["docker"] = 'echo "Error: No such object: marlin2b-8000" >&2; exit 1\n'
    gone = tmp / "gone"
    gone.mkdir()
    done = run_script(models, gone, "concurrency.sh", stubs, **sweep_inputs(gone))
    assert done.returncode == 2 and "refused: no container" in done.stderr, (
        done.returncode, done.stderr[-400:])
    assert not (gone / "out").exists(), "a refused sweep wrote a run directory"


def check_inventory_refuses_a_missing_container(models: pathlib.Path, tmp: pathlib.Path) -> None:
    """measure/inventory.sh (review PIN-5): no container is a failed precondition - exit 2
    and a `precondition=` line - not an `image_equals_pin=no` that reads like an answer.
    With the container there it inventories as before."""
    quiet = {"curl": "exit 7\n", "nvidia-smi": "exit 9\n"}
    done = run_script(models, tmp, "inventory.sh", {
        **quiet, "docker": 'echo "Error: No such object: marlin2b-8000" >&2; exit 1\n'},
        WEIGHTS=str(tmp))
    assert done.returncode == 2, (done.returncode, done.stdout[-400:])
    assert "precondition=failed" in done.stdout and "image_equals_pin" not in done.stdout
    pin = record_of(models)["runtime_image"]["digest"]
    done = run_script(models, tmp, "inventory.sh", {**quiet, "docker": f'echo "{pin}"\n'},
                      WEIGHTS=str(tmp))
    assert done.returncode == 0 and "image_equals_pin=yes" in done.stdout, done.stdout[-400:]


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------
@support.LINUX_USERLAND
def test_perf_pilot__the_engine_starts_pinned_on_loopback_with_the_recorded_flags(tmp_path):
    check_pinned_launch(MODELS, tmp_path)


@support.LINUX_USERLAND
def test_perf_pilot__a_pinned_setting_has_one_source(tmp_path):
    check_one_source_per_setting(MODELS, tmp_path)


def test_perf_pilot__the_serving_record_matches_the_code_it_pins(tmp_path):
    check_record_matches_the_code(MODELS, tmp_path)


def test_perf_pilot__the_concurrency_sweep_survives_a_failed_metrics_scrape(tmp_path):
    check_the_sweep_survives_a_failed_scrape(MODELS, tmp_path)


def test_perf_pilot__the_concurrency_sweep_labels_a_failed_run_and_refuses_without_a_container(
        tmp_path):
    check_the_sweep_labels_a_failed_run(MODELS, tmp_path)


@support.LINUX_USERLAND
def test_perf_pilot__the_concurrency_sweep_counts_its_metrics_exactly(tmp_path):
    check_the_sweep_counts_its_metrics_exactly(MODELS, tmp_path)


def test_perf_pilot__the_concurrency_sweep_refuses_an_unverified_corpus(tmp_path):
    check_the_sweep_refuses_an_unverified_corpus(MODELS, tmp_path)


def test_perf_pilot__the_inventory_refuses_a_missing_container(tmp_path):
    check_inventory_refuses_a_missing_container(MODELS, tmp_path)

