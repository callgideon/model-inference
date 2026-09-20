"""Task-local service names and host ports (08 §8), so two worktrees running real
services at the same time cannot collide - and nothing points at production.

    >>> local_services("d1")["postgres"].container
    'infrx-d1-postgres'

Pure data plus one lookup. Nothing here starts a container; it only fixes the
names, ports, database and object prefix a task is allowed to use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# track -> {service: (host port, extra ports)}
TRACK_SERVICES: dict[str, dict[str, tuple[int, tuple[int, ...]]]] = {
    "d": {"postgres": (55432, ())},
    "q": {"valkey": (56379, ())},
    "t": {"clickhouse": (58123, (59000,)), "s3": (59110, ())},
    "m": {"s3": (59100, ())},
    "e": {"compose": (55500, tuple(range(55501, 55600)))},
}
# G, W, J and C use fakes until integration, so they get no task-local service.
FAKE_ONLY_TRACKS = ("f", "g", "w", "j", "c", "u", "v", "i")


@dataclass(frozen=True)
class LocalService:
    service: str
    container: str
    host_port: int
    database: str
    object_prefix: str
    extra_ports: tuple[int, ...] = field(default_factory=tuple)


def track_of(task_id: str) -> str:
    """"D1" -> "d". A task id is a track letter followed by its number."""
    if not task_id or not task_id[0].isalpha():
        raise ValueError(f"not a task id: {task_id!r}")
    return task_id[0].lower()


def local_services(task_id: str) -> dict[str, LocalService]:
    """The services this task may run, named so parallel worktrees never clash.
    An empty mapping means the task develops against the contract fakes."""
    task = task_id.lower()
    track = track_of(task)
    if track not in TRACK_SERVICES:
        if track in FAKE_ONLY_TRACKS:
            return {}
        raise ValueError(f"unknown track {track!r} for task {task_id!r}")
    return {
        service: LocalService(service=service, container=f"infrx-{task}-{service}",
                              host_port=port, extra_ports=extra, database=f"infrx_{task}",
                              object_prefix=f"test/{task}/")
        for service, (port, extra) in TRACK_SERVICES[track].items()
    }


def all_host_ports() -> dict[int, str]:
    """Every reserved port, so a new allocation can be checked against it."""
    reserved: dict[int, str] = {}
    for track, services in TRACK_SERVICES.items():
        for service, (port, extra) in services.items():
            for number in (port, *extra):
                if number in reserved:
                    raise ValueError(f"port {number} reserved twice: {reserved[number]}")
                reserved[number] = f"{track}/{service}"
    return reserved
