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
import hashlib
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx

from ...contracts.api import Wire
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

    async def stop(self, allocation: Allocation) -> bool:
        """Stop the engine under this tag only; False when there was none."""


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


# ================================================================= the engine ===
class Engine:
    """The candidate engine's HTTP surface on its allocated loopback port."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None,
                 host: str = "127.0.0.1") -> None:
        self.transport, self.host = transport, host

    def client(self, allocation: Allocation, timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"http://{self.host}:{allocation.port}",
                                 transport=self.transport, timeout=timeout_s)

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

