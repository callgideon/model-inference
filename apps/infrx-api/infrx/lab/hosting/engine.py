"""AP-05b/c/d: the infrastructure adapter - install verified bytes, launch and find an engine
by its resource tag, observe what it actually serves, smoke it.

Launchers (`Launcher`): `LocalLauncher` runs a configured argv as a process group and keeps
`<state_dir>/<tag>.json`; what it reports is read from the RUNNING process (`/proc/<pid>/
cmdline`), the image is declared (no container). `BoxLauncher` (the one approved host, a
coordinator window: infra/lab/hosting/README.md) starts `infrx-candidate@<port>.service` and
reads `docker inspect` of the container serve.sh names. Neither touches a process or unit it
did not start under this tag: the serving engine (`marlin2b-vllm.service`, port 8000) is never
a candidate's.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import httpx

from ...contracts.api import FieldError, Wire
from ...contracts.v2.records import ServingRevision
from ...worker.engine import MODEL_EOS_TOKEN_IDS
from ..artifacts.manifest import FileEntry
from .store import Allocation


class Runtime(Wire):
    """What a running candidate engine is, as observed (never as requested)."""

    image: str                     # the image digest it runs (declared for a local process)
    flags: tuple[str, ...]         # the engine options after the model path
    model_dir: str                 # the directory its model was loaded from
    declared: bool = False         # True when `image` is configuration, not an observation


def options_digest(flags: Sequence[str]) -> str:
    """serving-version.json's `engine_options_digest`: sha256 of the flags' compact JSON."""
    compact = json.dumps(list(flags), separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(compact.encode()).hexdigest()


class Launcher(Protocol):
    async def start(self, allocation: Allocation, model_dir: Path) -> None: ...
    async def inspect(self, allocation: Allocation) -> Runtime | None:
        """The engine running under this allocation's tag, or None."""
        ...

    async def stop(self, allocation: Allocation) -> bool:
        """Stop the engine under this tag only; False when there was none."""
        ...


# ===================================================================== install ===
class InstallRefused(Exception):
    """The source does not hold the verified bytes (missing or another digest)."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def measure(root: Path) -> dict[str, str]:
    """Every file under `root` by relative path -> its sha256 (the bytes, now)."""
    return {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()}


def install(source: Path, files: Sequence[FileEntry], dest: Path) -> dict[str, str]:
    """Copy exactly the manifest's files from `source` into `dest`, each checked against its
    verified digest before it is moved into place; a file already there with its digest is
    kept (a resumed install), anything else in `dest` is removed. Returns `measure(dest)`."""
    dest.mkdir(parents=True, exist_ok=True)
    wanted = {f.relative_path: f.sha256 for f in files}
    for path in [p for p in dest.rglob("*") if p.is_file()]:
        if wanted.get(str(path.relative_to(dest))) is None:
            path.unlink()
    for name, digest in wanted.items():
        target = dest / name
        if target.is_file() and sha256(target) == digest:
            continue
        origin = source / name
        if not origin.is_file():
            raise InstallRefused(f"missing:{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        shutil.copyfile(origin, part)
        if sha256(part) != digest:
            part.unlink()
            raise InstallRefused(f"digest_mismatch:{name}")
        os.replace(part, target)
    return measure(dest)


# =============================================================== local launcher ===
class LocalLauncher:
    """An engine as a local process group (the isolated proof: the fake vLLM on the lane's
    port). `argv` may name `{port}` and `{model_dir}`; the flags it reports are the running
    process's own argv after `--`."""

    def __init__(self, argv: Sequence[str], state_dir: Path, *, image: str) -> None:
        self.argv, self.state_dir, self.image = tuple(argv), Path(state_dir), image

    def _state(self, allocation: Allocation) -> Path:
        return self.state_dir / f"{allocation.resource_tag}.json"

    async def start(self, allocation: Allocation, model_dir: Path) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        argv = [a.replace("{port}", str(allocation.port)).replace("{model_dir}", str(model_dir))
                for a in self.argv]
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        part = self._state(allocation).with_suffix(".part")
        part.write_text(json.dumps({"pid": process.pid, "port": allocation.port,
                                    "tag": allocation.resource_tag}))
        os.replace(part, self._state(allocation))

    def _alive(self, allocation: Allocation) -> tuple[int, list[str]] | None:
        try:
            state = json.loads(self._state(allocation).read_text())
            pid = int(state["pid"])
            stat = Path(f"/proc/{pid}/stat").read_text()
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1]
        except (OSError, ValueError, KeyError):
            return None
        if stat.rpartition(")")[2].split()[0] == "Z" or not any(
                allocation.resource_tag in a for a in argv):
            return None              # exited (a zombie), or a reused pid: not this tag's engine
        return pid, argv

    async def inspect(self, allocation: Allocation) -> Runtime | None:
        found = self._alive(allocation)
        if found is None:
            return None
        _, argv = found
        flags = argv[argv.index("--") + 1:] if "--" in argv else []
        model_dir = argv[argv.index("--model-dir") + 1] if "--model-dir" in argv else ""
        return Runtime(image=self.image, flags=tuple(flags), model_dir=model_dir, declared=True)

    async def _gone(self, allocation: Allocation, pid: int, within_s: float) -> bool:
        for _ in range(int(within_s / 0.05)):
            try:
                os.waitpid(pid, os.WNOHANG)          # reap it when this process started it
            except ChildProcessError:
                pass
            if self._alive(allocation) is None:
                return True
            await asyncio.sleep(0.05)
        return False

    async def stop(self, allocation: Allocation) -> bool:
        found = self._alive(allocation)
        if found is not None:
            pid = found[0]
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except ProcessLookupError:
                    break
                if await self._gone(allocation, pid, 10.0):
                    break
        self._state(allocation).unlink(missing_ok=True)
        return found is not None


# ==================================================================== identity ===
def mismatches(serving: ServingRevision, runtime: Runtime | None, models: list[str] | None,
               model_dir: Path, *, served_model: str, harness: tuple[str, str],
               files: dict[str, str] | None = None,
               manifest: Sequence[FileEntry] = ()) -> list[FieldError]:
    """Every way the running engine is not the requested serving revision: its image digest,
    its engine options, the directory it loaded, the model name it serves, the harness and
    preprocessor the platform runs it with, and - when `files` (the installed bytes, measured
    now) are given - every file against the verified manifest (missing, other digest,
    unexpected) and the revision's own weight/tokenizer/template pins. [] = the same."""
    found: list[FieldError] = []

    def bad(field: str, message: str) -> None:
        found.append(FieldError(field=field, code="identity_mismatch", message=message))
    if runtime is None:
        bad("runtime", "no engine runs under this deployment's tag")
        return found
    if runtime.image != (serving.runtime_image_digest
                         or serving.runtime_image_ref.partition("@")[2]):
        bad("runtime_image", "the engine runs another image")
    if options_digest(runtime.flags) != serving.engine_options_digest:
        bad("engine_options", "the engine runs other options")
    if runtime.model_dir != str(model_dir):
        bad("model_dir", "the engine loaded another directory")
    if models is None or served_model not in models:
        bad("served_model", "the engine does not serve the profile's model name")
    if (serving.prompt_harness_ref, serving.preprocessor_profile_version) != harness:
        bad("harness", "the revision's harness or preprocessor is not the profile's")
    if files is None:
        return found
    want = {f.relative_path: f.sha256 for f in manifest}
    for path in sorted(set(want) | set(files)):
        if want.get(path) != files.get(path):
            bad(f"files.{path}", "missing" if path not in files else "unexpected"
                if path not in want else "another digest")
    shards = [files.get(p) for p in sorted(p for p in files if p.endswith(".safetensors"))]
    if shards != list(serving.weight_shard_digests):
        bad("weights", "the shards are not the revision's pins")
    if (files.get("tokenizer.json"), files.get("chat_template.jinja")) != \
            (serving.tokenizer_digest, serving.chat_template_digest):
        bad("tokenizer", "the tokenizer or chat template is not the revision's pin")
    return found


# ================================================================= the box ===
SERVING_PORT = 8000                       # marlin2b-vllm.service: never a candidate's


class BoxLauncher:
    """The one approved host (infra/lab/hosting/README.md): a candidate is the unit
    `infrx-candidate@<port>.service` - serve.sh with WEIGHTS = the installed directory on
    GPU 0 - whose container serve.sh names `marlin2b-<port>`. The unit's environment file
    `<env_dir>/<port>.env` carries the deployment's tag: only a container whose file names
    this tag is this deployment's. What it reports is `docker inspect`'s: the image the
    container runs, its arguments after the image, the directory mounted at /model."""

    def __init__(self, env_dir: Path, *, run: Callable[..., subprocess.CompletedProcess]
                 = subprocess.run) -> None:
        self.env_dir, self.run = Path(env_dir), run

    def _env(self, allocation: Allocation) -> Path:
        return self.env_dir / f"{allocation.port}.env"

    def _mine(self, allocation: Allocation) -> bool:
        try:
            text = self._env(allocation).read_text()
        except OSError:
            return False
        return f"INFRX_HOSTING_TAG={allocation.resource_tag}\n" in text

    def _unit(self, allocation: Allocation) -> str:
        return f"infrx-candidate@{allocation.port}.service"

    async def start(self, allocation: Allocation, model_dir: Path) -> None:
        if allocation.port == SERVING_PORT:
            raise ValueError("the serving engine's port is never a candidate's")
        self.env_dir.mkdir(parents=True, exist_ok=True)
        part = self._env(allocation).with_suffix(".part")
        part.write_text(f"INFRX_HOSTING_TAG={allocation.resource_tag}\nWEIGHTS={model_dir}\n")
        os.replace(part, self._env(allocation))
        await asyncio.to_thread(self.run, ["systemctl", "start", self._unit(allocation)],
                                check=True, capture_output=True, text=True, timeout=60)

    async def inspect(self, allocation: Allocation) -> Runtime | None:
        if not self._mine(allocation):
            return None
        found = await asyncio.to_thread(
            self.run, ["docker", "inspect", f"marlin2b-{allocation.port}"],
            capture_output=True, text=True, timeout=30)
        try:
            (doc,) = json.loads(found.stdout or "[]")
        except ValueError:
            return None
        if found.returncode != 0 or not doc.get("State", {}).get("Running"):
            return None
        mounts = {m.get("Destination"): m.get("Source") for m in doc.get("Mounts", [])}
        return Runtime(image=str(doc.get("Image", "")), flags=tuple(doc.get("Args", [])[1:]),
                       model_dir=str(mounts.get("/model", "")), declared=False)

    async def stop(self, allocation: Allocation) -> bool:
        if not self._mine(allocation):
            return False
        await asyncio.to_thread(self.run, ["systemctl", "stop", self._unit(allocation)],
                                check=True, capture_output=True, text=True, timeout=120)
        self._env(allocation).unlink(missing_ok=True)
        return True


# ================================================================= the engine ===
SMOKE_PROMPT = "Describe what happens in this video."
SMOKE_MAX_TOKENS = 64
# est.: a text-only prompt of the harness is < 100 tokens; the shortest corpus clip expands
# to ~1,960 video tokens under the profile (marlin2b/results/notes.md), so 256 separates them
MIN_VIDEO_PROMPT_TOKENS = 256


def _smoke(name: str, message: str) -> FieldError:
    return FieldError(field=f"smoke.{name}", code="smoke_failed", message=message)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


class Engine:
    """The candidate engine's HTTP surface on its allocated loopback port."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None,
                 host: str = "127.0.0.1") -> None:
        self.transport, self.host = transport, host

    def client(self, allocation: Allocation, timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"http://{self.host}:{allocation.port}",
                                 transport=self.transport, timeout=timeout_s)

    async def smoke(self, allocation: Allocation, *, model: str, video: bytes,
                    timeout_s: float) -> tuple[dict, list[FieldError]]:
        """One bounded finite-video chat request, as the worker sends one (data: URL video,
        the model's EOS ids, greedy, short). What it observed, and every way it is not a
        served video answer: a refusal, another model, an empty answer, a prompt with no
        video tokens (a text-only answer is not a modality smoke), no usage, an abnormal end."""
        body = {"model": model, "max_tokens": SMOKE_MAX_TOKENS, "temperature": 0,
                "stream": False, "stop_token_ids": list(MODEL_EOS_TOKEN_IDS),
                "messages": [{"role": "user", "content": [
                    {"type": "video_url", "video_url": {
                        "url": "data:video/mp4;base64," + base64.b64encode(video).decode()}},
                    {"type": "text", "text": SMOKE_PROMPT}]}]}
        observed: dict = {"video_sha256": "sha256:" + hashlib.sha256(video).hexdigest(),
                          "video_bytes": len(video), "timeout_s": timeout_s}
        started = time.monotonic()
        try:
            async with self.client(allocation, timeout_s) as c:
                answer = await c.post("/v1/chat/completions", json=body)
        except httpx.TimeoutException:
            return observed | {"elapsed_ms": _ms(started)}, [_smoke("timeout", "no answer in time")]
        except httpx.HTTPError:
            return observed | {"elapsed_ms": _ms(started)}, [_smoke("unreachable", "no engine")]
        try:
            doc = answer.json()
        except ValueError:
            doc = {}
        choice = (doc.get("choices") or [{}])[0] if isinstance(doc, dict) else {}
        text = str((choice.get("message") or {}).get("content") or "")
        usage = doc.get("usage") or {} if isinstance(doc, dict) else {}
        observed |= {"elapsed_ms": _ms(started), "status": answer.status_code,
                     "served_model": doc.get("model") if isinstance(doc, dict) else None,
                     "finish_reason": choice.get("finish_reason"),
                     "prompt_tokens": usage.get("prompt_tokens"),
                     "completion_tokens": usage.get("completion_tokens"),
                     "text_sha256": "sha256:" + hashlib.sha256(text.encode()).hexdigest()}
        if answer.status_code != 200:
            return observed, [_smoke("status", f"the engine answered {answer.status_code}")]
        checks = (("served_model", observed["served_model"] == model, "another model answered"),
                  ("content", bool(text.strip()), "an empty answer"),
                  ("video", _int(usage.get("prompt_tokens")) >= MIN_VIDEO_PROMPT_TOKENS,
                   "the prompt carried no video tokens"),
                  ("usage", _int(usage.get("completion_tokens")) >= 1, "no completion usage"),
                  ("finish_reason", choice.get("finish_reason") in ("stop", "length"),
                   "the generation did not end normally"))
        return observed, [_smoke(name, why) for name, ok, why in checks if not ok]

    async def in_flight(self, allocation: Allocation) -> int:
        """vLLM's `vllm:num_requests_running` (summed over its labels); 0 when the engine no
        longer answers - a dead engine has nothing left to finish."""
        try:
            async with self.client(allocation, 5.0) as c:
                answer = await c.get("/metrics")
        except httpx.HTTPError:
            return 0
        total = 0.0
        for line in answer.text.splitlines():
            name, _, value = line.rpartition(" ")
            if name.startswith("vllm:num_requests_running"):
                try:
                    total += float(value)
                except ValueError:
                    continue
        return int(total)

    async def models(self, allocation: Allocation) -> list[str] | None:
        """The model ids it serves, or None while it does not answer."""
        try:
            async with self.client(allocation, 5.0) as c:
                answer = await c.get("/v1/models")
            if answer.status_code != 200:
                return None
            return [str(m.get("id")) for m in answer.json().get("data", [])]
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

