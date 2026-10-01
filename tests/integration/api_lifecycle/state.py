"""AP-11a: the runner's two private files and its redaction.

* the **state file** (0600, written atomically after every change): the run's identity and
  target, each mutation's Idempotency-Key, canonical request hash and outcome (`pending`
  before the request leaves, `done` with the safe outputs after), stage checkpoints, the
  resources the run created with how to clean each up, and the request counters. Never a
  credential, a response body or customer content.
* the **secrets file** (0600, read only): credentials by name - sessions from the identity
  provider, the operator's bootstrap credential, and (isolated mode only) declared fixture
  keys. `redact` scrubs every value it holds, plus the shapes of credentials it does not.

What replaces what: tests/integration/harness.py's STATE_FILE is a world-readable run
note, not a resumable ledger; nothing before this kept a request hash per operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, UTC
from pathlib import Path

PRIVATE = 0o600


class InvalidRun(Exception):
    """A run that cannot be trusted (INVALID, exit 4): an exposed private file, a state from
    another target, a recorded mutation re-sent with another body, a refused configuration."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(value) -> str:
    """The canonical request hash: sorted keys, no whitespace, UTF-8."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def require_private(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise InvalidRun(f"{path.name} must be 0600 (owner-only), is {oct(mode)}")


def write_private(path: Path, payload: dict) -> None:
    """Atomic and owner-only from the first byte: a temporary file created 0600, fsynced,
    renamed over the old one, so a crash leaves the previous state or the new one."""
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE)
    try:
        os.fchmod(fd, PRIVATE)
        os.write(fd, json.dumps(payload, indent=1, sort_keys=True).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


class State:
    def __init__(self, path: Path, data: dict, resumed: bool) -> None:
        self.path, self.data, self.resumed = path, data, resumed

    @classmethod
    def open(cls, path: Path, *, target: str) -> State:
        if path.exists():
            require_private(path)
            data = json.loads(path.read_text())
            if data.get("target") != target:
                raise InvalidRun(f"the state file belongs to target {data.get('target')!r}, "
                                 f"not {target!r}: resume it with its own config")
            return cls(path, data, resumed=True)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        state = cls(path, {"version": 1, "run_id": str(uuid.uuid4()), "target": target,
                           "created_at": utc_now(), "stages": {}, "mutations": {},
                           "owned": [], "counters": {"requests": 0, "inference": 0}}, False)
        state.save()
        return state

    def save(self) -> None:
        write_private(self.path, self.data)

    @property
    def counters(self) -> dict:
        return self.data["counters"]

    def mutation(self, name: str) -> dict | None:
        return self.data["mutations"].get(name)

    def begin(self, name: str, method: str, route: str, request_hash: str) -> dict:
        """Recorded BEFORE the request leaves: a crash after this line resumes with this key."""
        entry = {"key": str(uuid.uuid4()), "method": method, "route": route,
                 "request_hash": request_hash, "status": "pending", "began": utc_now()}
        self.data["mutations"][name] = entry
        self.save()
        return entry

    def finish(self, name: str, outputs: dict) -> None:
        entry = self.data["mutations"][name]
        entry.update(status="done", outputs=outputs, finished=utc_now())
        self.save()

    def own(self, kind: str, resource_id: str, cleanup: dict | None) -> None:
        """A resource this run created; `cleanup` = {method, origin, route, actor} or None
        when nothing may remove it (it expires by retention)."""
        owned = self.data["owned"]
        if not any(row["id"] == resource_id for row in owned):
            owned.append({"kind": kind, "id": resource_id, "cleanup": cleanup,
                          "created": utc_now()})
            self.save()

    def checkpoint(self, sid: str, status: str, outputs: dict) -> None:
        self.data["stages"][sid] = {"status": status, "outputs": outputs, "at": utc_now()}
        self.save()


# A credential's shape, for values the secrets file does not hold (a key minted mid-run,
# a token echoed by a server): bearer values, sk- keys, JWTs, a DSN's password.
SHAPES = (
    (re.compile(r"(?i)\bbearer\s+[\w\-.~+/=]+"), "<redacted:bearer>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "<redacted:key>"),
    (re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]+"), "<redacted:jwt>"),
    (re.compile(r"(\b[a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@"), r"\1<redacted>@"),
)
SECRET_FIELDS = frozenset({"password", "secret", "token", "access_token", "refresh_token",
                           "authorization", "apikey", "api_key", "cookie", "set-cookie"})


class Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    @classmethod
    def load(cls, path: Path | None) -> Secrets:
        if path is None:
            return cls({})
        require_private(path)
        values = json.loads(path.read_text())
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise InvalidRun("the secrets file must be a JSON object of strings")
        return cls(values)

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def redact(self, value):
        if isinstance(value, dict):
            return {k: "<redacted>" if str(k).lower() in SECRET_FIELDS else self.redact(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        if not isinstance(value, str):
            return value
        for name, secret in sorted(self.values.items(), key=lambda kv: -len(kv[1])):
            if len(secret) >= 6:
                value = value.replace(secret, f"<redacted:{name}>")
        for shape, replacement in SHAPES:
            value = shape.sub(replacement, value)
        return value
