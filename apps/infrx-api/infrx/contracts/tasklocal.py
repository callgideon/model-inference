"""Task-local service names and host ports (08 §8), so two worktrees running real
services at the same time cannot collide - and nothing points at production.

    >>> local_services("d1")["postgres"].container
    'infrx-d1-postgres'

Pure data plus one lookup. Nothing here starts a container; it only fixes the
names, ports, database and object prefix a task is allowed to use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# r1 R48: PostgreSQL is per **task**, not per track. D1-D6 and C1 each get their own
# port, because more than one of them will be open at once - a coordinator running D2's
# migration while C1's console suite holds a database is the normal case, and they used to
# be handed the same 55432. A task not listed here falls back to its track's port.
TASK_PORTS: dict[str, dict[str, int]] = {
    "d1": {"postgres": 55432}, "d2": {"postgres": 55433}, "d3": {"postgres": 55434},
    "d4": {"postgres": 55435}, "d5": {"postgres": 55436}, "d6": {"postgres": 55437},
    "c1": {"postgres": 55441},
    # R63: per-task Valkey ports for Q lanes (the track port 56379 stays the shared default)
    "q2": {"valkey": 55461}, "q3": {"valkey": 55462},
    # D2's relay drills need both a PostgreSQL and a Valkey of their own
    "d2": {"postgres": 55433, "valkey": 55463},
}

# track -> {service: (host port, extra ports)}
TRACK_SERVICES: dict[str, dict[str, tuple[int, tuple[int, ...]]]] = {
    "d": {"postgres": (55432, ())},
    "q": {"valkey": (56379, ())},
    "t": {"clickhouse": (58123, (59000,)), "s3": (59110, ())},
    "m": {"s3": (59100, ())},
    "e": {"compose": (55500, tuple(range(55501, 55600)))},
}
# G, W and J use fakes until integration, so they get no task-local service. C is here
# too: only C1 has a database (R48), which `TASK_PORTS` grants it directly.
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
    services = dict(TRACK_SERVICES.get(track, {}))
    # r1 R48: a task-specific port overrides (or grants) its track's.
    for service, port in TASK_PORTS.get(task, {}).items():
        services[service] = (port, services.get(service, (port, ()))[1])
    if not services:
        if track in FAKE_ONLY_TRACKS:
            return {}
        raise ValueError(f"unknown track {track!r} for task {task_id!r}")
    return {
        service: LocalService(service=service, container=f"infrx-{task}-{service}",
                              host_port=port, extra_ports=extra, database=f"infrx_{task}",
                              object_prefix=f"test/{task}/")
        for service, (port, extra) in services.items()
    }


def all_host_ports() -> dict[int, str]:
    """Every reserved port, so a new allocation can be checked against it.

    Task ports (R48) win over their track's default, and a collision anywhere is an error:
    two tasks on one port is exactly the clash this module exists to prevent.
    """
    reserved: dict[int, str] = {}
    for track, services in TRACK_SERVICES.items():
        for service, (port, extra) in services.items():
            for number in (port, *extra):
                owner = f"{track}/{service}"
                if number in reserved and reserved[number] != owner:
                    raise ValueError(f"port {number} reserved twice: {reserved[number]}")
                reserved[number] = owner
    for task, services in TASK_PORTS.items():
        for service, port in services.items():
            owner = f"{task}/{service}"
            clash = reserved.get(port)
            # A task may take its track's default port (D1 keeps 55432); it may not take
            # another task's, or another service's.
            if clash is not None and clash != f"{track_of(task)}/{service}":
                raise ValueError(f"port {port} reserved twice: {clash} and {owner}")
            reserved[port] = owner
    return reserved
