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
    # D2-D5: their R48 PostgreSQL ports sit on their rows below, beside the Valkey they gained
    "d1": {"postgres": 55432}, "d6": {"postgres": 55437},
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
    # E3A's App journey (tests/integration/app/runner.py) runs INSIDE the e4b block: edge 56860,
    # control 56861, App 56870 (E3A-WR-4); E4B and the App journey therefore never run at once.
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
    # G2-FIX (R63): `make check` runs D2's relay drills and Q's harness in ONE pytest process,
    # so Q gets its own e2c Valkey port (`valkey-q`; the Q harness names its container
    # infrx-q3-valkey-55430 from the port). 55430 sits below D1's 55432, clear of every
    # D-harness decoy (port − 30000) and the 555xx E block, and no lane or evidence used it.
    "e2c": {"postgres": 55448, "valkey": 55493, "s3": 55494, "valkey-q": 55430},
    "e1c": {"postgres": 55449},
    # I8's PgBouncer stand-in for the hosted pooler (tests/i/pooler.py)
    "i8": {"postgres": 55450, "valkey": 55495, "pgbouncer": 55496},
    # Coordinator integration lanes (2026-09-25): the union merge lane and the door-revoke
    # migration lane. PostgreSQL 55458/55459 keep their decoys (55498/55499) clear of the
    # live Valkey/S3/pooler ports 55491-55497; the Valkey/S3 ports sit in the unused
    # 55454-55457 gap, below every decoy.
    "union": {"postgres": 55458, "valkey": 55454, "s3": 55455},
    "revoke": {"postgres": 55459},
    # App completion lanes (2026-09-25, user-authorized ahead of BACKEND-READY): one PostgreSQL
    # each for the lanes that test real RLS/RPC grants; A3/U2 use fakes only. These ports have
    # no D-harness decoy use (App lanes never run tests/d/test_pgharness.py).
    "app-c0": {"postgres": 55451}, "app-c3a": {"postgres": 55452}, "app-u3": {"postgres": 55453},
    "app-u4": {"postgres": 55456}, "app-u1r": {"postgres": 55457}, "app-a2": {"postgres": 55460},
    # E3C composes the E2 stack as namespace `e3c` in its own block (56900-56999)
    "e3c": {"postgres": 56932},
    # LW0 (post-launch Lab lanes, R152-R153): every lane port in ONE band, 57500-57599. C and V
    # lanes use `lab-`/`app-` keys so R48's c2/v1 stay fake-only; `dlab` is the only D key (its
    # D-harness decoy, port - 30000, is 27500). G5 and I4 are conditional lanes.
    "dlab": {"postgres": 57500},
    "l2": {"postgres": 57501}, "l3": {"postgres": 57502}, "l4": {"postgres": 57503},
    "i2l": {"postgres": 57504}, "lab-c2": {"postgres": 57505, "s3": 57506},
    "g4f": {"postgres": 57507}, "g4t": {"postgres": 57508},
    "app-c3f": {"postgres": 57509}, "lab-c3l": {"postgres": 57510},
    "j2": {"postgres": 57511, "judge-fake": 57512}, "lab-v1m": {"postgres": 57513},
    "n1": {"postgres": 57514, "s3": 57515}, "n2": {"postgres": 57516, "s3": 57517},
    "n3": {"postgres": 57518, "s3": 57519},
    "b1": {"postgres": 57520, "model-fake": 57521}, "b3": {"postgres": 57522},
    "i5": {"postgres": 57523, "s3": 57524}, "i6": {"postgres": 57525}, "i7": {"postgres": 57526},
    "p1": {"postgres": 57527}, "p2": {"postgres": 57528, "teacher-fake": 57529},
    "p3": {"postgres": 57530, "protocol": 57531},
    "r1": {"postgres": 57532, "valkey": 57533}, "r2": {"postgres": 57534},
    "g5": {"postgres": 57535}, "i4": {"postgres": 57536},
    # LAB-DEPLOY-PREP: `make lab-local`'s E4-ON stage (the consumer E4 subset with every switch
    # ON) runs the D harness on its own PostgreSQL and D2 Valkey; its composed stack borrows
    # the finished e3l block under E3L's runner lock (no 100-port block is free in the band).
    "lab-on": {"postgres": 57537, "valkey": 57538},
    # The Lab E gates compose the E2 stack in their own blocks (TASK_BLOCKS below); the
    # PostgreSQL port is the one the harness derives, 55532 + the block's offset, as e3c's.
    "e3l": {"postgres": 57032}, "e5l": {"postgres": 57132}, "e6l": {"postgres": 57232},
    "e7l": {"postgres": 57332}, "e8l": {"postgres": 57432},
}

# A task's own block of a track service, replacing the track's (host port, extra ports).
# E3B phase 2 runs the E2 stack as compose namespace `e3b2`: E2's layout moved by +1200
# (tests/integration/harness.NAMESPACES), 56700-56799. Its TASK_PORTS postgres port lies
# inside the block (the port the harness derives), which `all_host_ports` allows.
TASK_BLOCKS: dict[str, dict[str, tuple[int, tuple[int, ...]]]] = {
    "e3b2": {"compose": (56700, tuple(range(56701, 56800)))},
    "e4b": {"compose": (56800, tuple(range(56801, 56900)))},
    "e3c": {"compose": (56900, tuple(range(56901, 57000)))},
    # LW0: the Lab E gates (harness.NAMESPACES +1500 ... +1900) ...
    "e3l": {"compose": (57000, tuple(range(57001, 57100)))},
    "e5l": {"compose": (57100, tuple(range(57101, 57200)))},
    "e6l": {"compose": (57200, tuple(range(57201, 57300)))},
    "e7l": {"compose": (57300, tuple(range(57301, 57400)))},
    "e8l": {"compose": (57400, tuple(range(57401, 57500)))},
    # ... and the T lanes, whose ClickHouse native port a TASK_PORTS entry cannot move (it
    # would inherit the track's 59000), so each gets a block of its own inside the Lab band.
    "t2i": {"clickhouse": (57540, (57541,)), "s3": (57542, ())},
    "t2f": {"clickhouse": (57543, (57544,)), "s3": (57545, ()), "postgres": (57549, ())},
    "t3": {"clickhouse": (57546, (57547,)), "s3": (57548, ())},
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
# LW0: the Lab tracks too (L access/app, N datasets, H harnesses, B evaluation, P pipelines,
# R rollout, X expansion); their lanes' ports come only from TASK_PORTS / TASK_BLOCKS.
FAKE_ONLY_TRACKS = ("f", "g", "w", "j", "c", "u", "v", "i", "l", "n", "h", "b", "p", "r", "x")


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
