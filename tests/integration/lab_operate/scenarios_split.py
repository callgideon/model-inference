"""E3L l11: App inference with the Lab unavailable (SPLIT-CONTRACT; I2L's local proof for
OPS-RECOVER): the App box (E3C's composed gateway + worker + controlled engine on real
PostgreSQL/PostgREST/Valkey/S3, namespace e3l) beside the real Lab web release (`apps/lab`'s
production build, `next start` on 127.0.0.1:57070, its Supabase origin the same PostgREST).

* down: the Lab is SIGKILLed mid-traffic; sync, SSE and async App requests keep serving and
  every accepted job finishes once.
* bad release and rollback: a job is accepted and queued (no worker); the Lab is replaced by a
  release that cannot start (no production build) and crash-loops; the worker starts and the App
  serves sync, SSE and a second async job during the crash loop; the Lab is rolled back to the
  good release and answers again; both accepted jobs ran exactly one inference attempt and
  settled once, and the CREDIT books are conserved.

The Lab control service (L3's factory, WR-I2L-2) is not on this base: its half of the drill (a
control crash loop and rollback) is l10/l06 after L3 merges. Label: a controlled engine, a local
Lab build; not hosted Vercel behaviour.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_world as lab                                 # noqa: E402

world, stack, harness = lab.world, lab.stack, lab.harness
LAB_DIR = harness.REPO_ROOT / "apps" / "lab"
NEXT = LAB_DIR / "node_modules" / ".bin" / "next"
LAB_PORT = harness.PORT_RANGE.start + 70                # e3l: 57070, loopback only


class LabWeb:
    """The Lab web as its own process group, started from a release directory."""

    def __init__(self, workdir: Path) -> None:
        self.workdir, self.starts, self.process = workdir, 0, None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{LAB_PORT}/"

    def start(self, release: Path) -> subprocess.Popen:
        self.starts += 1
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
               "NEXT_TELEMETRY_DISABLED": "1", "NEXT_PUBLIC_LAB_URL": self.url.rstrip("/"),
               "NEXT_PUBLIC_SUPABASE_URL": lab.rest_url(),
               "NEXT_PUBLIC_SUPABASE_ANON_KEY": stack.jwt("anon", ttl_s=3600)}
        log = open(self.workdir / f"lab-web-{self.starts}.log", "wb")
        self.process = subprocess.Popen(
            [str(NEXT), "start", str(release), "-H", "127.0.0.1", "-p", str(LAB_PORT)],
            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            cwd=str(LAB_DIR))
        log.close()
        return self.process

    def answers(self) -> int | None:
        import httpx
        try:
            return httpx.get(self.url, timeout=3.0).status_code
        except httpx.HTTPError:
            return None

    def up(self, timeout: float = 90.0) -> int:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.answers()
            if status is not None:
                return status
            if self.process.poll() is not None:     # the log names a bind collision (harness)
                log = self.workdir / f"lab-web-{self.starts}.log"
                raise AssertionError(f"the Lab web exited {self.process.returncode}: "
                                     + " | ".join(log.read_text(errors="replace")
                                                  .splitlines()[:2]))
            time.sleep(0.2)
        raise AssertionError(f"the Lab web did not answer within {timeout}s")

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            self.process.wait(timeout=30)


@contextlib.contextmanager
def lab_web(workdir: Path):
    if not (LAB_DIR / ".next" / "BUILD_ID").exists() or not NEXT.exists():
        world.invalid("the Lab web is not built: the runner's lab-build stage (pnpm build in "
                      "apps/lab) makes the release this drill starts")
    web = LabWeb(workdir)
    try:
        yield web
    finally:
        web.kill()


def app_serves(trip, tenant, tag: str) -> str:
    """Sync and SSE answered in full, one async job accepted: its request id."""
    sync = trip.send(tenant, "sync", world.TEXT, None)
    assert sync.status_code == 200, f"{tag}: sync {sync.status_code} {sync.text[:200]}"
    sse = trip.send(tenant, "sse", world.TEXT, None)
    assert sse.status_code == 200 and "data: [DONE]" in sse.text, f"{tag}: sse {sse.text[-200:]}"
    accepted = trip.send(tenant, "async", world.TEXT, f"e3l-l11-{tag}")
    assert accepted.status_code == 202, f"{tag}: async {accepted.status_code} {accepted.text[:200]}"
    return accepted.json()["request_id"]


def finished_once(trip, request_ids) -> None:
    for request_id in request_ids:
        assert world.terminal(trip, request_id, timeout=90.0) == "succeeded", request_id
        assert world.attempts(trip, request_id) == 1, f"{request_id} ran more than once"
        world.settled_once(trip, request_id)


def test_l11_the_lab_down_mid_traffic_leaves_app_inference_serving(workdir, record_property):
    with world.composed(workdir) as trip, lab_web(workdir) as web:
        alpha = trip.world.alpha
        web.start(LAB_DIR)
        record_property("lab_up", web.up())
        before = app_serves(trip, alpha, "before")
        web.kill()
        assert web.answers() is None, "premise: the Lab is down"
        during = app_serves(trip, alpha, "lab-down")
        finished_once(trip, (before, during))
        trip.conserved(alpha)


def test_l11_a_bad_lab_release_and_its_rollback_leave_every_accepted_job_finished_once(
        workdir, tmp_path, record_property):
    bad = tmp_path / "bad-release"                       # no production build: cannot start
    bad.mkdir()
    (bad / "package.json").write_text('{"name": "infrx-lab", "private": true}\n')
    with world.composed(workdir, start=("gateway",)) as trip, lab_web(workdir) as web:
        alpha = trip.world.alpha
        web.start(LAB_DIR)
        web.up()
        queued = trip.send(alpha, "async", world.TEXT, "e3l-l11-queued")
        assert queued.status_code == 202, queued.text[:200]
        web.kill()
        exits = []
        for _ in range(3):                                # the unit's restart loop, by hand
            exits.append(web.start(bad).wait(timeout=60))
        assert all(code != 0 for code in exits) and web.answers() is None, exits
        trip.box.start("worker")
        during = app_serves(trip, alpha, "bad-release")
        web.start(LAB_DIR)                                # rollback: the previous release
        record_property("lab", {"crash_loop_exits": exits, "after_rollback": web.up()})
        finished_once(trip, (queued.json()["request_id"], during))
        trip.conserved(alpha)
