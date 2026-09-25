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
    # D3's drills run D2's Valkey relay on the lane's own container
    "d3": {"postgres": 55434, "valkey": 55464},
    # D4's relay/journal drills and G2's relay drill each get a Valkey of their own
    "d4": {"postgres": 55435, "valkey": 55465},
    "g2": {"valkey": 55466},
    # E3B phase 2 runs the E2 stack as compose namespace `e3b2` in the reserved block
    # 56700-56799 (the harness derives service ports from INFRX_E2_NAMESPACE; a block is
    # not a single port, so the PostgreSQL port the harness actually derives for it is mirrored here)
    "d5": {"postgres": 55436, "valkey": 55467},
    "e3b2": {"postgres": 56732},
    # E3B phase 3 IR3F-2(b): the gate's `make api-test` runs the D suites while the e3b2 stack
    # holds 56732, so they get a D task of their own outside the block (containers infrx-e3b2d-*)
    "e3b2d": {"postgres": 55438, "valkey": 55468},
    # E4B certifies on the E2 stack as namespace `e4b` (56800-56899, E4B request 1)
    "e4b": {"postgres": 56832},
    # Wave 4 (consumer v1, program 22, 2026-09-24): one PostgreSQL (+ Valkey / S3 where the lane
    # drives them) per lane so the twelve worktrees run their real-service suites concurrently.
    # 55442-55460 and 55469-55499 were free; 555xx belongs to the E compose block. The D
    # harness decoy sits 40 above every PostgreSQL port (tests/d/test_pgharness.py: 55472-55490
    # for the ports below), so the Valkey/S3 ports stay outside that band.
    "d10": {"postgres": 55442, "valkey": 55469},
    "m5": {"postgres": 55443, "s3": 55470},
    "m6": {"postgres": 55444, "s3": 55471},
    "w5": {"postgres": 55445, "valkey": 55491},
    "g7": {"postgres": 55446},
    "g8": {"postgres": 55447, "valkey": 55492},
    "e2c": {"postgres": 55448, "valkey": 55493, "s3": 55494},
    "e1c": {"postgres": 55449},
    # I8's PgBouncer stand-in for the hosted pooler (tests/i/pooler.py)
    "i8": {"postgres": 55450, "valkey": 55495, "pgbouncer": 55496},
    # Coordinator integration lanes (2026-09-25): the union merge lane and the door-revoke
    # migration lane. PostgreSQL 55458/55459 keep their decoys (55498/55499) clear of the
    # live Valkey/S3/pooler ports 55491-55497; the Valkey/S3 ports sit in the unused
    # 55454-55457 gap, below every decoy.
    "union": {"postgres": 55458, "valkey": 55454, "s3": 55455},
    "revoke": {"postgres": 55459},
    # E3C composes the E2 stack as namespace `e3c` in its own block (56900-56999)
    "e3c": {"postgres": 56932},
}

# A task's own block of a track service, replacing the track's (host port, extra ports).
# E3B phase 2 runs the E2 stack as compose namespace `e3b2`: E2's layout moved by +1200
# (tests/integration/harness.NAMESPACES), 56700-56799. Its TASK_PORTS postgres port lies
# inside the block (the port the harness derives), which `all_host_ports` allows.
TASK_BLOCKS: dict[str, dict[str, tuple[int, tuple[int, ...]]]] = {
    "e3b2": {"compose": (56700, tuple(range(56701, 56800)))},
    "e4b": {"compose": (56800, tuple(range(56801, 56900)))},
    "e3c": {"compose": (56900, tuple(range(56901, 57000)))},
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
    services.update(TASK_BLOCKS.get(task, {}))
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
    for track, services in (*TRACK_SERVICES.items(), *TASK_BLOCKS.items()):
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
            if clash is not None and clash != f"{track_of(task)}/{service}" \
                    and not clash.startswith(f"{task}/"):         # inside its own block
                raise ValueError(f"port {port} reserved twice: {clash} and {owner}")
            reserved[port] = owner
    return reserved
