"""I3B.a: host, disk and GPU gauges, read at scrape time.

    collect_host(reg, disks={"root": "/", "media": "/var/lib/infrx/media"})

Every reader is injectable (`proc`, `statvfs`, `run`) so a case can hand it a full disk or
a missing GPU without owning one. A disk that cannot be read reports a free ratio of 0 -
an unmountable media root is an outage, and the fail-safe direction is the alert firing.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from .metrics import Registry

NVIDIA_SMI = ("nvidia-smi", "--query-gpu=index,memory.used,memory.total,utilization.gpu",
              "--format=csv,noheader,nounits")
MIB = 1024 * 1024


def _meminfo(proc: Path) -> dict[str, int]:
    values = {}
    for line in (proc / "meminfo").read_text().splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            values[name] = int(rest.split()[0]) * 1024          # kB
    return values


def _resident(proc: Path) -> int | None:
    for line in (proc / "self" / "status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return None


def collect_disks(reg: Registry, disks: Mapping[str, str], *,
                  statvfs: Callable = os.statvfs) -> None:
    for mount, path in disks.items():
        try:
            fs = statvfs(path)
            total, free = fs.f_blocks * fs.f_frsize, fs.f_bavail * fs.f_frsize
        except OSError:
            reg.set("infrx_disk_free_ratio", 0.0, mount=mount)
            continue
        reg.set("infrx_disk_bytes", total, mount=mount, state="total")
        reg.set("infrx_disk_bytes", free, mount=mount, state="free")
        reg.set("infrx_disk_free_ratio", free / total if total else 0.0, mount=mount)


def collect_gpu(reg: Registry, *, run: Callable = subprocess.run) -> None:
    try:
        answer = run(NVIDIA_SMI, capture_output=True, text=True, timeout=5)
        rows = [line.split(",") for line in answer.stdout.strip().splitlines()] \
            if answer.returncode == 0 else []
        parsed = [(i.strip(), float(used) * MIB, float(total) * MIB, float(util) / 100)
                  for i, used, total, util in rows]
    except (OSError, subprocess.SubprocessError, ValueError):
        parsed = []
    reg.set("infrx_gpu_up", 1.0 if parsed else 0.0)
    for index, used, total, utilization in parsed:
        reg.set("infrx_gpu_memory_bytes", used, gpu=index, state="used")
        reg.set("infrx_gpu_memory_bytes", total, gpu=index, state="total")
        reg.set("infrx_gpu_utilization_ratio", utilization, gpu=index)


def collect_host(reg: Registry, disks: Mapping[str, str], *, proc: str = "/proc",
                 statvfs: Callable = os.statvfs, run: Callable = subprocess.run,
                 gpu: bool = True) -> None:
    root = Path(proc)
    reg.set("infrx_host_cpus", os.cpu_count() or 0)
    reg.set("infrx_host_load1", os.getloadavg()[0])
    memory = _meminfo(root)
    if "MemTotal" in memory:
        reg.set("infrx_host_memory_bytes", memory["MemTotal"], state="total")
    if "MemAvailable" in memory:
        reg.set("infrx_host_memory_bytes", memory["MemAvailable"], state="available")
    resident = _resident(root)
    if resident is not None:
        reg.set("infrx_process_resident_bytes", resident)
    collect_disks(reg, disks, statvfs=statvfs)
    if gpu:
        collect_gpu(reg, run=run)
