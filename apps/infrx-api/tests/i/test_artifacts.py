"""I8 slice 5: the durable model mirror, its restore, and the backup/PITR policy read.

A fake `aws` maps s3://bucket/key onto a local directory, so the mirror step and the
restore step run end to end against it (sync, cp, list-objects-v2) with no AWS.

Failure oracles: a directory that does not serve the pinned bytes is refused before any
upload; a restore whose bytes differ from the mirror's manifest (a tampered, truncated or
extra object) is DIFFERENT, exit 1; the env file reaches the manifest as names only; the
policy read never prints its token or a connection string, and says BLOCKED without one.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import support
from .test_ops_steps import STEPS, run_step, stubs

RUNBOOKS = support.REPO / "infra" / "runbooks"
ARTIFACTS = RUNBOOKS / "artifacts.py"
SERVING = support.REPO / "models" / "marlin2b" / "serving-version.json"

FAKE_AWS = '''#!{python}
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ["FAKE_S3"])
args = [a for a in sys.argv[1:] if a not in ("--only-show-errors", "--no-progress")]
while "--region" in args:
    i = args.index("--region"); del args[i:i + 2]
excl = []
while "--exclude" in args:
    i = args.index("--exclude"); excl.append(args[i + 1]); del args[i:i + 2]
with (root.parent / "aws.log").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
def local(p):
    return root / p[5:] if p.startswith("s3://") else pathlib.Path(p)
if args[:2] == ["s3", "sync"]:
    src, dst = local(args[2]), local(args[3])
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if f.is_file() and not any(str(rel).startswith(e.rstrip("*")) for e in excl):
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / rel)
elif args[:2] == ["s3", "cp"]:
    src, dst = local(args[2]), local(args[3])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
elif args[:2] == ["s3api", "list-objects-v2"]:
    bucket, prefix = args[args.index("--bucket") + 1], args[args.index("--prefix") + 1]
    base = root / bucket
    for f in sorted((base / prefix).rglob("*")):
        if f.is_file():
            print(f"{{f.relative_to(base)}}\\t{{f.stat().st_size}}")
else:
    sys.exit(254)                    # get-bucket-versioning etc.: denied
'''


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def model_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A weights directory and a serving-version.json that pins it, shaped like W3's."""
    weights = tmp_path / "nvme" / "marlin2b"
    weights.mkdir(parents=True)
    files = {"model-00001-of-00002.safetensors": b"shard-1", "model-00002-of-00002.safetensors": b"shard-2",
             "tokenizer.json": b"{tok}", "chat_template.jinja": b"{{t}}", "config.json": b"{cfg}",
             "generation_config.json": b"{gen}", "preprocessor_config.json": b"{pre}",
             "processor_config.json": b"{proc}", ".cache/huggingface/x.lock": b"lock"}
    for name, data in files.items():
        (weights / name).parent.mkdir(parents=True, exist_ok=True)
        (weights / name).write_bytes(data)
    serving = json.loads(SERVING.read_text())
    serving["model"].update({
        "weight_shard_digests": [sha(b"shard-1"), sha(b"shard-2")],
        "tokenizer_digest": sha(b"{tok}"), "chat_template_digest": sha(b"{{t}}"),
        "config_digest": sha(b"{cfg}"), "generation_config_digest": sha(b"{gen}")})
    pinned = tmp_path / "serving-version.json"
    pinned.write_text(json.dumps(serving))
    return weights, pinned


def run(*args):
    return subprocess.run([sys.executable, str(ARTIFACTS), *args], capture_output=True, text=True)


def test_ops_recover__the_manifest_pins_the_served_bytes_and_records_names_only(tmp_path):
    weights, pinned = model_dir(tmp_path)
    env = tmp_path / "gateway.env"
    image = "sha256:" + "e" * 64
    env.write_text(f"# INFRX_ENV_SCHEMA 1:x\nINFRX_IMAGE={image}\nDATABASE_URL=postgresql://u:"
                   f"{support.MARKER}@h/d\n")
    out = tmp_path / "out"
    done = run("manifest", "--weights", str(weights), "--serving-version", str(pinned),
               "--release", "c" * 40, "--env-file", str(env), "--out", str(out))
    assert done.returncode == 0, done.stderr
    doc = json.loads((out / "manifest.json").read_text())
    assert support.MARKER not in (out / "manifest.json").read_text()
    assert doc["env_names"] == ["INFRX_IMAGE", "DATABASE_URL"] and doc["runtime_image"]["id"] == image
    assert doc["unpinned_processor_files"] == ["processor_config.json", "preprocessor_config.json"]
    roles = {f["path"]: f["role"] for f in doc["files"]}
    assert roles["model-00001-of-00002.safetensors"] == "weights"
    assert roles["preprocessor_config.json"] == "processor" and roles["chat_template.jinja"] == "template"
    assert not any(p.startswith(".cache/") for p in roles)
    assert doc["engine_image"]["digest"].startswith("sha256:")
    assert len((out / "SHA256SUMS").read_text().splitlines()) == len(doc["files"])
    # a directory that does not serve the pinned bytes is refused, nothing written
    (weights / "model-00002-of-00002.safetensors").write_bytes(b"another model")
    shutil.rmtree(out)
    done = run("manifest", "--weights", str(weights), "--serving-version", str(pinned),
               "--release", "c" * 40, "--out", str(out))
    assert done.returncode == 2 and "REFUSED: the weight shards" in done.stderr and not out.exists()


def test_ops_recover__the_real_serving_record_has_the_fields_the_manifest_checks():
    serving = json.loads(SERVING.read_text())
    runpy_globals = {}
    exec(compile(ARTIFACTS.read_text(), str(ARTIFACTS), "exec"), runpy_globals)
    for field in runpy_globals["PINNED"]:
        assert serving["model"][field].startswith("sha256:"), field
    assert len(serving["model"]["weight_shard_digests"]) == 2
    assert serving["runtime_image"]["ref"].endswith(serving["runtime_image"]["digest"])


def _aws(tmp_path):
    stub = stubs(tmp_path, "git")
    (stub / "git.out").write_text("c" * 40 + "\n")
    (stub / "aws").write_text(FAKE_AWS.format(python=sys.executable))
    (stub / "aws").chmod(0o755)
    fake = tmp_path / "s3"
    (fake / "approved-bucket").mkdir(parents=True)
    return stub, fake


def test_ops_recover__mirror_then_restore_round_trips_and_detects_a_changed_object(tmp_path):
    weights, pinned = model_dir(tmp_path)
    repo = tmp_path / "repo"
    (repo / "models" / "marlin2b").mkdir(parents=True)
    shutil.copy2(pinned, repo / "models" / "marlin2b" / "serving-version.json")
    (repo / "models" / "marlin2b" / "serve.sh").write_text("#!/bin/sh\n")
    (repo / "infra").mkdir()
    shutil.copytree(RUNBOOKS, repo / "infra" / "runbooks")
    stub, fake = _aws(tmp_path)
    env_file = tmp_path / "gateway.env"
    env_file.write_text(f"INFRX_IMAGE=sha256:{'e' * 64}\nSUPABASE_SERVICE_ROLE_KEY={support.MARKER}\n")
    env = {"RELEASE": "c" * 40, "MIRROR_URL": "s3://approved-bucket/infrx/mirror/", "REPO": str(repo),
           "WEIGHTS": str(weights), "ENV_FILE": str(env_file), "FAKE_S3": str(fake)}
    done = run_step((STEPS / "80-mirror-artifacts.sh").read_text(), stub, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "8/8 match the manifest" in done.stdout and "manifest read back equal" in done.stdout
    assert "versioning: unknown" in done.stdout and support.MARKER not in done.stdout + done.stderr
    mirrored = fake / "approved-bucket" / "infrx" / "mirror"
    assert not (mirrored / "weights" / ".cache").exists()

    restore = {**env, "NVME": str(tmp_path / "replacement"), "RESTORE_ID": "20260924T230000Z"}
    done = run_step((STEPS / "81-restore-artifacts.sh").read_text(), stub, env=restore)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "EQUAL files=8" in done.stdout
    assert "timing fetch_s=" in done.stdout and "timing verify_s=" in done.stdout
    (mirrored / "weights" / "tokenizer.json").write_bytes(b"{tampered}")
    shutil.rmtree(tmp_path / "replacement")
    done = run_step((STEPS / "81-restore-artifacts.sh").read_text(), stub, env=restore)
    assert done.returncode == 1 and "changed: tokenizer.json" in done.stdout

    # refusals before anything is fetched or moved
    for bad in ({"MIRROR_URL": "s3://approved-bucket"}, {"MIRROR_URL": "https://x/"}):
        assert run_step((STEPS / "80-mirror-artifacts.sh").read_text(), stub,
                        env={**env, **bad}).returncode == 2
    assert run_step((STEPS / "81-restore-artifacts.sh").read_text(), stub,
                    env={**restore, "MODE": "reboot"}).returncode == 2
    assert run_step((STEPS / "81-restore-artifacts.sh").read_text(), stub,
                    env={**restore, "RESTORE_ID": "$(id)"}).returncode == 2
    swap = run_step((STEPS / "81-restore-artifacts.sh").read_text(), stub,
                    env={**restore, "MODE": "swap", "RELEASE": "d" * 40})
    assert swap.returncode == 2 and "30-pause.sh extracts it" in swap.stderr


class _Api(http.server.BaseHTTPRequestHandler):
    answers: dict = {}
    seen: list = []

    def do_GET(self):                                   # noqa: N802
        _Api.seen.append((self.path, self.headers.get("Authorization")))
        body = json.dumps(_Api.answers.get(self.path, {})).encode()
        self.send_response(200 if self.path in _Api.answers else 404)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_ops_recover__the_policy_read_reports_pitr_backups_and_the_pooler_without_secrets(
        tmp_path):
    token = f"{support.MARKER}-token"
    ref = "fcbnscgsymzdykendbrc"
    _Api.answers = {
        f"/v1/projects/{ref}/database/backups": {
            "region": "us-east-2", "pitr_enabled": False, "walg_enabled": True,
            "backups": [{"status": "COMPLETED", "is_physical_backup": True,
                         "inserted_at": "2026-09-24T03:00:00.000Z"}]},
        f"/v1/projects/{ref}/config/database/pooler": [
            {"pool_mode": "transaction", "db_port": 6543, "default_pool_size": 15,
             "max_client_conn": 200, "connection_string": f"postgresql://x:{support.MARKER}@h/d"}]}
    server = http.server.HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dumps = tmp_path / "infrx-backups" / "hosted-20260924T050746Z"
    dumps.mkdir(parents=True)
    (dumps / "SHA256SUMS").write_text("x")
    script = RUNBOOKS / "supabase_policy.py"
    args = [sys.executable, str(script), "--api", f"http://127.0.0.1:{server.server_port}",
            "--dumps", str(tmp_path / "infrx-backups")]
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              env={**os.environ, "SUPABASE_ACCESS_TOKEN": token})
        assert done.returncode == 0, done.stderr
        report = json.loads(done.stdout)
        assert support.MARKER not in done.stdout + done.stderr
        assert report["backups"]["pitr_enabled"] is False and report["backups"]["physical"] == 1
        assert report["pooler"] == [{"database_type": None, "pool_mode": "transaction",
                                     "db_port": 6543, "default_pool_size": 15,
                                     "max_client_conn": 200}]
        assert report["rpo"].startswith("daily backups, no PITR")
        assert report["local_dumps"]["newest"] == dumps.name and report["local_dumps"]["sha256sums"]
        assert _Api.seen[0][1] == f"Bearer {token}"
        done = subprocess.run(args, capture_output=True, text=True,
                              env={k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"})
        assert done.returncode == 3 and "BLOCKED" in json.loads(done.stdout)["api"]
        assert json.loads(done.stdout)["rpo"].startswith("no hosted backup seen")
    finally:
        server.shutdown()
