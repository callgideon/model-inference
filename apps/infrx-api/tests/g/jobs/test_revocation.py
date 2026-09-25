#!/usr/bin/env python3
"""DUR-RLS (G7, G8's acceptance finding): the revocation bound on the read routes.

Admission re-reads the key inside its own transaction, so a revoked key's next POST is
refused at once (D2). Every other route authenticates from the identity cache
(`auth/keys.py`), so a revoked key keeps reading its own organization's jobs for at most
KEY_TTL (60 s) after the gateway last fetched it - while the identity source answers. The
bound is stated, not removed: there is no cross-process signal from the revoking process
(operator CLI, console) to every gateway's cache, and a second cache authority would be
worse than a bounded one. This case pins it on every job route.
"""
from __future__ import annotations

from .. import relay_support as rs
from .test_jobs import post, refusal
from .world import JobsWorld, job_path, send

ROUTES = (("GET", ""), ("GET", "/result"), ("GET", "/events"), ("DELETE", ""))


def test_dur_rls__a_revoked_key_reads_its_jobs_for_at_most_key_ttl():
    world = JobsWorld()
    assert post(world).status == 202                       # fetched and cached now
    handle = world.handle()
    rt = world.app.state.runtime
    rt.sb = rs.support.supabase(rows=({**world.row, "revoked_at": "2026-09-20T12:00:00Z"},))
    world.clock.advance(rt.settings.key_ttl)               # the last instant of the bound
    inside = rs.run(send(world.app, "GET", job_path(handle)))
    assert inside.status == 200, inside.body
    world.clock.advance(1)                                 # past it: re-read, revoked
    for method, tail in ROUTES:
        reply = rs.run(send(world.app, method, job_path(handle, tail)))
        assert refusal(reply) == (401, "invalid_api_key"), (method, tail, reply.body)
    assert rt.settings.key_ttl == 60                       # the bound the copy states
