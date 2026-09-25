"""D10.b: the durable content lifecycle and the scrub (0020) as SQL-level checks.

Same contract as `checks_ready.py` (the "admission" scenario; each check in a transaction
it rolls back, the races on committed rows over their own connections). Every call is the
boundary `PgLifecycle` makes, with its arguments.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from infrx.contracts.conformance import builders as b
from infrx.state.jobstore import domain_error

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_dispatch as cdp
from . import checks_leases as cl
from . import checks_ready as cr
from . import checks_settle as cs
from .checks_leases import lockstep

CLAIM_TTL_S = 30.0


# --------------------------------------------------------------------- driving
def _claim_args(row: dict, holder: str) -> dict:
    return {"content_id": str(row["content_id"]), "generation": row["generation"],
            "holder": holder, "claim_ttl_s": CLAIM_TTL_S}


def refused_claim(conn, row: dict, holder: str = "sweeper-a"):
    """(code, reason) of a claim, or None - granted claims are rolled back (a probe)."""
    try:
        with conn.transaction():
            got = cr.refusal(conn, "content_claim", _claim_args(row, holder))
            raise ca._Rollback() if got is None else _Found(got)
    except ca._Rollback:
        return None
    except _Found as found:
        return found.value


class _Found(Exception):
    def __init__(self, value):
        self.value = value


def at(conn, instant) -> None:
    """Move the store clock to exactly `instant` (the equality boundary); a document's
    instant arrives as ISO text."""
    if isinstance(instant, str):
        instant = datetime.fromisoformat(instant)
    now = conn.execute("select infrx.now()").fetchone()[0]
    conn.execute("select infrx_test.advance(%s)", ((instant - now).total_seconds(),))


def candidate_ids(conn) -> set[str]:
    found, after = set(), {}
    while True:
        page = cr.call(conn, "content_candidates", {"limit": 2, **after})
        found |= {str(item["content_id"]) for item in page["items"]}
        if not page["more"]:
            return found
        last = page["items"][-1]
        after = {"after_eligible_at": last["eligible_at"],
                 "after_content_id": str(last["content_id"])}


def row_of(conn, key: str) -> dict:
    cur = conn.execute("select * from infrx.content_objects where object_key = %s", (key,))
    names = [c.name for c in cur.description]
    return {k: (str(v) if k in ("content_id", "org_id", "job_id") and v is not None else v)
            for k, v in zip(names, cur.fetchone())}


def register_key(conn, org: str, key: str, *, kind: str = "source", job_id=None,
                 handle=None, digest=None, origin: str = "written") -> dict:
    return cr.call(conn, "content_register", {"identity": {
        "org_id": org, "kind": kind, "location": "object_store", "object_key": key,
        "digest": digest or cr.digest(key.encode()), "bytes": 1 if origin == "written" else None,
        "job_id": job_id, "upload_handle": handle, "origin": origin},
        "grace_s": cr.GRACE_S})


# --------------------------------------------------------------------- checks
def check_content_liveness(conn) -> str:
    """RETENTION-DURABLE / RV-03: what protects an object is read from persisted rows only -
    a manifest reference while its job runs and until `settled_at + retention_s` (the
    retention its admission captured, never today's configuration); a staged payload by its
    own job once admitted (an orphaned one is collectable); an open ticket's destination; a
    usable ticket's source; and a running job the PREVIOUS runtime admitted, which names its
    key with no manifest row. A referenced object is neither a candidate nor claimable; at
    each persisted instant exactly (equality has passed) it becomes both."""
    world = ca.World(conn)

    def body():
        org = cr.c1_org(conn)
        # 1. a manifest reference, then the captured retention after the job ends
        clip = cr.source_ref(org, b"live-clip")
        row = cr.register(conn, clip)
        request = cr.credit_request(conn, world, (clip,))
        doc = cr.call(conn, "admit_ready", cr.ready_args(request, b.idem(request, "live")))
        at(conn, row["eligible_at"])
        assert str(row["content_id"]) not in candidate_ids(conn), "a used source is a candidate"
        assert refused_claim(conn, row) == ("not_claimable", "reference_live")
        handle = doc["admission"]["job_handle"]
        cr.call(conn, "cancel", {"org_id": org, "job_handle": handle, "limits": cl.LIMITS})
        (ref,) = cr.call(conn, "content_references", {"content_id": str(row["content_id"])})
        settled = conn.execute("select settled_at from infrx.jobs where request_id = %s",
                               (request.request_id,)).fetchone()[0]
        until = settled + timedelta(seconds=cr.RETENTION_S)
        assert ref["retain_until"] is not None, ref
        assert refused_claim(conn, row) == ("not_claimable", "reference_live"), \
            "a source was deletable inside its job's retention"
        at(conn, until)
        assert refused_claim(conn, row) is None and \
            str(row["content_id"]) in candidate_ids(conn), "retention ended but kept"
        # 2. staged payloads: protected by an admitted job, not by a refused request
        kept, dropped = cr.credit_request(conn, world), cr.credit_request(conn, world)
        rows = [register_key(conn, org, f"payloads/{org}/{r.request_id}.json", kind="payload",
                             job_id=r.request_id) for r in (kept, dropped)]
        cr.call(conn, "admit_ready", cr.ready_args(kept, b.idem(kept, "payload")))
        at(conn, max(datetime.fromisoformat(r["eligible_at"]) for r in rows))
        assert refused_claim(conn, rows[0]) == ("not_claimable", "reference_live")
        assert refused_claim(conn, rows[1]) is None, "an orphaned payload is not collectable"
        # 3. an open upload's destination; 4. a usable upload's source
        pending, _ = cr.upload(conn, org, b"live-pending", complete=False)
        destination = register_key(conn, org, f"uploads/{org}/{pending['upload_handle']}",
                                   kind="upload_destination",
                                   handle=pending["upload_handle"])
        done, _ = cr.upload(conn, org, b"live-done")
        source = row_of(conn, cr.source_ref(org, b"live-done").storage_ref)
        at(conn, destination["eligible_at"])
        for protected in (destination, source):
            assert refused_claim(conn, protected) == ("not_claimable", "reference_live"), \
                f"an upload's object was claimable: {protected['content_id']}"
        at(conn, max(datetime.fromisoformat(pending["expires_at"]),
                     datetime.fromisoformat(done["expires_at"])))
        assert refused_claim(conn, destination) is None and refused_claim(conn, source) is None
        # 5. a running job the previous runtime admitted: no manifest row, still protected
        legacy = cr.source_ref(org, b"legacy-clip")
        old = cr.credit_request(conn, world, (legacy,))
        ca.admit(conn, old, b.idem(old, "legacy"), regime="credit")
        found = register_key(conn, org, legacy.storage_ref, digest=legacy.digest,
                             origin="discovered")
        at(conn, found["eligible_at"])
        assert refused_claim(conn, found) == ("not_claimable", "reference_live"), \
            "a key a running job names was deletable because no manifest row said so"
        # 6. a prepared object discovered with no row and no job (M6): kept while a running
        #    job's prepared refs name its key, collectable once none does
        queued = b.request(world)
        ca.admit(conn, queued, b.idem(queued, "prepared-orphan"))
        _, answer = cdp.claim(conn, queued.request_id)
        prepared = cr.source_ref(b.ORG_A, b"orphan-prepared").model_copy(update={
            "storage_ref": f"media/{b.ORG_A}/v1/{'cd' * 8}/prepared"})
        assert cdp.prepare(conn, answer["lease"], media=(prepared,))[0] is None
        orphan = register_key(conn, b.ORG_A, prepared.storage_ref, kind="prepared",
                              origin="discovered")
        assert orphan["identity"]["job_id"] is None
        at(conn, orphan["eligible_at"])
        assert refused_claim(conn, orphan) == ("not_claimable", "reference_live"), \
            "a prepared object a queued job runs on was deletable"
        handle = cl.row(conn, queued.request_id)["job_handle"]
        cr.call(conn, "cancel", {"org_id": b.ORG_A, "job_handle": handle, "limits": cl.LIMITS})
        assert refused_claim(conn, orphan) is None, "an orphaned prepared object is kept"
        return "manifest, retention, payload, destination, source, legacy and orphan rules hold"
    return ca._in_rollback(conn, body)


def check_content_protocol(conn) -> str:
    """The collector's protocol: one unexpired claim at a time (`claim_held`); a lapsed claim
    is superseded by a higher fence and the superseded holder can neither tombstone nor
    acknowledge (`claim_lost`: delete a renewed claim); a tombstoned key refuses re-use and
    re-registration (`content_retiring`) until the acknowledgement, then is generation + 1,
    and a delayed acknowledgement of the old generation changes nothing; a crash after the
    tombstone (or after the external delete) leaves a candidate that a new claim finishes;
    the acknowledgement is idempotent."""
    def body():
        org = cr.c1_org(conn)
        clip = cr.source_ref(org, b"protocol-clip")
        row = cr.register(conn, clip)
        at(conn, row["eligible_at"])
        stale_generation = dict(row, generation=row["generation"] + 1)
        assert cr.refusal(conn, "content_claim", _claim_args(stale_generation, "s")) == \
            ("stale_lease", "claim_lost")
        first = cr.call(conn, "content_claim", _claim_args(row, "s-a"))
        assert cr.refusal(conn, "content_claim", _claim_args(row, "s-b")) == \
            ("not_claimable", "claim_held")
        at(conn, first["expires_at"])
        assert cr.refusal(conn, "content_tombstone", {"claim": first}) == \
            ("stale_lease", "claim_lost"), "an expired claim tombstoned"
        second = cr.call(conn, "content_claim", _claim_args(row, "s-b"))
        assert second["fence"] > first["fence"]
        assert cr.refusal(conn, "content_tombstone", {"claim": first}) == \
            ("stale_lease", "claim_lost"), "a superseded claim tombstoned under a renewed one"
        tombstone = cr.call(conn, "content_tombstone", {"claim": second})
        stale = dict(tombstone, fence=first["fence"])
        assert cr.refusal(conn, "content_acknowledge_delete", {"tombstone": stale}) == \
            ("stale_lease", "claim_lost"), "a superseded holder acknowledged the delete"
        # tombstoned: no new use, no re-registration
        request = cr.credit_request(conn, ca.World(conn), (clip,))
        assert cr.refusal(conn, "admit_ready", cr.ready_args(request, b.idem(request, "ret"))) \
            == ("dependency_unavailable", "content_retiring")
        assert cr.refusal(conn, "content_register", {"identity": {
            "org_id": org, "kind": "source", "location": "object_store",
            "object_key": clip.storage_ref, "digest": clip.digest, "bytes": clip.bytes,
            "job_id": None, "upload_handle": None, "origin": "written"},
            "grace_s": cr.GRACE_S}) == ("dependency_unavailable", "content_retiring"), \
            "a tombstoned object was re-created with the same identity"
        # a crash after the tombstone: the lapsed claim makes it a candidate again
        at(conn, second["expires_at"])
        assert str(row["content_id"]) in candidate_ids(conn), "an unfinished delete was lost"
        third = cr.call(conn, "content_claim", _claim_args(row, "s-c"))
        again = cr.call(conn, "content_tombstone", {"claim": third})
        assert (again["tombstoned_at"], again["fence"]) == (tombstone["tombstoned_at"],
                                                            third["fence"])
        deleted = cr.call(conn, "content_acknowledge_delete", {"tombstone": again})
        assert deleted["state"] == "deleted"
        assert cr.call(conn, "content_acknowledge_delete", {"tombstone": again}) == deleted, \
            "the acknowledgement is not idempotent"
        reborn = cr.register(conn, clip)
        assert (reborn["content_id"], reborn["generation"], reborn["state"]) == (
            row["content_id"], row["generation"] + 1, "live")
        assert cr.refusal(conn, "content_acknowledge_delete", {"tombstone": again}) is None \
            and cr.call(conn, "content_acknowledge_delete", {"tombstone": again}) == reborn, \
            "a delayed acknowledgement touched the new generation"
        return "leased, fenced, generation-checked; crash before/after delete recoverable"
    return ca._in_rollback(conn, body)


def _settled_with_result(conn, world, *, result_ttl_s: float):
    """A CREDIT job settled `completed` with a stored result, the settlement run with
    `result_ttl_s` as the store's configuration at that moment."""
    request, lease = cl.credit_running(conn, world)
    ref = cs.stored(conn, request.request_id)
    args = cs.terminalize_args(lease, cs.propose(request.request_id, usage=(1200, 340),
                                                 ref=ref), "credit")
    args["limits"] = {**args["limits"], "result_ttl_s": result_ttl_s}
    assert cr.refusal(conn, "terminalize", args) is None, "the fixture did not settle"
    return request, ref


def check_content_scrub(conn) -> str:
    """RESULT-EXPIRY / RETENTION-DURABLE for database content: the result is the owner's
    until the PERSISTED `result_expires_at` - the settlement's configuration, not today's -
    and `result_expired` from that instant exactly, scrubbed or not; its content row is
    protected until then; the scrub empties the result body and the request text and keeps
    the job, outcome, usage, settlement, ledger, idempotency tombstone, digests and sizes;
    no path empties a result early, rewrites one, or deletes one."""
    world = ca.World(conn)

    def body():
        request, ref = _settled_with_result(conn, world, result_ttl_s=600.0)
        job = cl.row(conn, request.request_id)
        assert job["result_expires_at"] == job["settled_at"] + timedelta(seconds=600)
        org = str(job["org_id"])
        def read():
            return _read(conn, org, ref)
        assert read() == ("ok", f"result of {request.request_id}")
        result_row = row_of(conn, f"job_results/{request.request_id}")
        payload_row = row_of(conn, f"jobs/{request.request_id}")
        at(conn, job["result_expires_at"] - timedelta(microseconds=1))
        assert read()[0] == "ok", "a result was withdrawn before its persisted expiry"
        assert refused_claim(conn, result_row) == ("not_claimable", "reference_live")
        early = cc.attempt(conn, "update infrx.job_results set body = '', scrubbed_at = "
                                 "infrx.now() where request_id = %s", (request.request_id,))
        assert early is not None and early.startswith("23514"), \
            f"a result was emptied before its persisted expiry: {early}"
        at(conn, job["result_expires_at"])
        assert read() == ("result_expired", None), "a result outlived its persisted expiry"
        # the scrub is the ONLY permitted change: not with a digest or size rewritten
        # beside it (0020's column-by-column guard, whatever CHECK runs first)
        for label, extra in (("digest", "digest = 'sha256:' || repeat('0', 64)"),
                             ("bytes", "bytes = bytes + 1")):
            why = cc.attempt(conn, "update infrx.job_results set body = '', scrubbed_at = "
                                   f"infrx.now(), {extra} where request_id = %s",
                             (request.request_id,))
            assert why is not None and why.startswith("23514") and "append-only" in why, \
                f"the scrub rewrote the result's {label}: {why}"
        before = _accounting(conn, request.request_id)
        for content in (result_row, payload_row):
            granted = cr.call(conn, "content_claim", _claim_args(content, "scrubber"))
            tombstone = cr.call(conn, "content_tombstone", {"claim": granted})
            done = cr.call(conn, "content_acknowledge_delete", {"tombstone": tombstone})
            assert done["state"] == "deleted", done
        stored = conn.execute("select body, bytes, digest, scrubbed_at is not null from "
                              "infrx.job_results where request_id = %s",
                              (request.request_id,)).fetchone()
        assert stored[0] == "" and stored[1] == len(f"result of {request.request_id}") and \
            stored[2].startswith("sha256:") and stored[3], stored
        record, scrubbed = conn.execute("select request_record, content_scrubbed_at from "
                                        "infrx.jobs where request_id = %s",
                                        (request.request_id,)).fetchone()
        assert "messages" not in record and "parameters" not in record and scrubbed, record
        assert record["request_id"] == request.request_id and \
            record["payload_digest"] == request.payload_digest
        assert _accounting(conn, request.request_id) == before, \
            "the scrub changed the job, its settlement, usage, ledger or idempotency"
        assert read() == ("result_expired", None), "a scrubbed result read as empty text"
        running, _lease = cl.credit_running(conn, world, "scrub-running")
        why = cc.attempt(conn, "update infrx.jobs set request_record = "
                               "infrx.scrubbed_request(request_record), content_scrubbed_at = "
                               "infrx.now() where request_id = %s", (running.request_id,))
        assert why is not None and why.startswith("23514"), \
            f"a running job's request was scrubbed: {why}"
        why = cc.attempt(conn, "update infrx.jobs set content_scrubbed_at = infrx.now() "
                               "where request_id = %s", (running.request_id,))
        assert why is not None and why.startswith("23514"), "a scrub was stamped, not done"
        for label, sql in (("rewritten", "update infrx.job_results set body = 'x' "),
                           ("deleted", "delete from infrx.job_results "),
                           ("unscrubbed", "update infrx.job_results set scrubbed_at = null ")):
            why = cc.attempt(conn, sql + "where request_id = %s", (request.request_id,))
            assert why is not None and why.startswith("23514"), f"a result was {label}"
        why = cc.attempt(conn, "update infrx.job_results set scrubbed_at = infrx.now() + "
                               "interval '1 second' where request_id = %s", (request.request_id,))
        assert why is not None and why.startswith("23514") and "append-only" in why, \
            f"a scrubbed result was stamped again: {why}"
        return "persisted expiry exact; scrub keeps metadata, digests and money; append-only"
    return ca._in_rollback(conn, body)


def check_legacy_success_scrub(conn) -> str:
    """R116 (RI-1): a success settled before 0018 carries no persisted expiry (never
    backfilled). Its database content still retires through the normal protocol -
    register_existing -> claim -> tombstone -> acknowledge_delete scrubs it - so no
    constraint may re-check such a row on UPDATE (a NOT VALID success-has-expiry CHECK
    would refuse the scrub and the sweep would retry forever)."""
    world = ca.World(conn)

    def body():
        request, _ref = _settled_with_result(conn, world, result_ttl_s=600.0)
        conn.execute("alter table infrx.jobs disable trigger jobs_result_expiry_immutable")
        why = cc.attempt(conn, "update infrx.jobs set result_expires_at = null "
                               "where request_id = %s", (request.request_id,))
        conn.execute("alter table infrx.jobs enable trigger jobs_result_expiry_immutable")
        assert why is None, f"a success without a persisted expiry cannot be updated: {why}"
        conn.execute("select infrx_test.advance(%s)", (2 * 86_400,))
        for key in (f"job_results/{request.request_id}", f"jobs/{request.request_id}"):
            content = row_of(conn, key)
            try:
                with conn.transaction():
                    granted = cr.call(conn, "content_claim", _claim_args(content, "legacy"))
                    tombstone = cr.call(conn, "content_tombstone", {"claim": granted})
                    done = cr.call(conn, "content_acknowledge_delete", {"tombstone": tombstone})
            except psycopg.Error as refused:
                raise AssertionError(f"{key} of a pre-0018 success could not be retired: "
                                     f"{refused.sqlstate} {refused}") from None
            assert done["state"] == "deleted", done
        scrubbed = conn.execute("select (select scrubbed_at is not null from infrx.job_results "
                                "where request_id = %s), (select content_scrubbed_at is not null "
                                "from infrx.jobs where request_id = %s)",
                                (request.request_id, request.request_id)).fetchone()
        assert scrubbed == (True, True), scrubbed
        return "a pre-0018 success (no persisted expiry) scrubs through the protocol"
    return ca._in_rollback(conn, body)


def _read(conn, org: str, ref: str):
    """("ok", text) of the owner's read, or (code, None) of its refusal."""
    try:
        with conn.transaction():
            text = conn.execute("select infrx.read_result(%s, %s)", (org, ref)).fetchone()[0]
    except psycopg.Error as failed:
        return getattr(domain_error(failed), "code", f"untyped {failed.sqlstate}"), None
    return "ok", text


def _accounting(conn, request_id: str) -> tuple:
    """Everything a scrub must leave exactly as it was."""
    return conn.execute(
        "select j.state, j.outcome_cause, j.settlement_state, j.usage_prompt_tokens, "
        "j.usage_completion_tokens, j.result_ref, j.result_expires_at, j.settled_at, "
        "j.maximum_hold, j.debit, j.idem_payload_hash, j.payload_digest, "
        "(select array_agg(l.amount::text || l.kind) from infrx.credit_ledger l "
        " where l.request_id = j.request_id), "
        "(select array_agg(h.amount::text || h.state) from infrx.credit_wallet_holds h "
        " where h.request_id = j.request_id), "
        "(select array_agg(i.key || i.payload_digest || coalesce(i.expires_at::text, '')) "
        " from infrx.idempotency i where i.request_id = j.request_id), "
        "(select count(*) from public.usage_events u where u.id = j.request_id) "
        "from infrx.jobs j where j.request_id = %s", (request_id,)).fetchone()


# --------------------------------------------------------------------- races
def check_content_races(connect, database: str) -> str:
    """attach/delete at the content row, both orders, and claim/claim:
    1. tombstone first (open), an admission naming the object waits on the row, then is
       `content_retiring` and admits nothing;
    2. admission first (open), the tombstone waits, then sees the reference: `reference_live`;
    3. two collectors: the second claim waits on the row, then is `claim_held`."""
    owner = connect(database)
    world = ca.World(owner)
    org = cr.c1_org(owner)

    def service():
        return cs.service(connect, database)

    def answer(function, args):
        def run(conn):
            got = cr.refusal(conn, function, args)
            return got, None
        return run

    report = []
    clip = cr.source_ref(org, b"race-delete-first")
    row = cr.register(owner, clip)
    at(owner, row["eligible_at"])
    granted = cr.call(owner, "content_claim", _claim_args(row, "s-a"))
    request = cr.credit_request(owner, world, (clip,))
    before = cr.footprint(owner)
    first, second = lockstep(owner, (service(), answer("content_tombstone", {"claim": granted})),
                             (service(), answer("admit_ready", cr.ready_args(
                                 request, b.idem(request, "race-delete-first")))))
    assert first[0] is None and second[0] == ("dependency_unavailable", "content_retiring"), \
        (first, second)
    assert cr.footprint(owner) == before, "an admission behind a tombstone admitted"
    report.append("delete first: the admission waited, then content_retiring")
    clip = cr.source_ref(org, b"race-admit-first")
    row = cr.register(owner, clip)
    at(owner, row["eligible_at"])
    granted = cr.call(owner, "content_claim", _claim_args(row, "s-a"))
    request = cr.credit_request(owner, world, (clip,))
    first, second = lockstep(owner, (service(), answer("admit_ready", cr.ready_args(
                                 request, b.idem(request, "race-admit-first")))),
                             (service(), answer("content_tombstone", {"claim": granted})))
    assert first[0] is None and second[0] == ("not_claimable", "reference_live"), \
        (first, second)
    assert row_of(owner, clip.storage_ref)["state"] == "live"
    report.append("admission first: the tombstone waited, then reference_live")
    clip = cr.source_ref(org, b"race-two-collectors")
    row = cr.register(owner, clip)
    at(owner, row["eligible_at"])
    first, second = lockstep(owner, (service(), answer("content_claim", _claim_args(row, "a"))),
                             (service(), answer("content_claim", _claim_args(row, "b"))))
    assert first[0] is None and second[0] == ("not_claimable", "claim_held"), (first, second)
    report.append("two collectors: one claim")
    return "; ".join(report)


# --------------------------------------------------------------------- privileges
def check_content_privileges(conn) -> str:
    """0020's surface: the protocol is the platform role's alone, its helpers nobody's."""
    boundary = ("content_candidates(jsonb)", "content_claim(jsonb)", "content_tombstone(jsonb)",
                "content_acknowledge_delete(jsonb)", "register_existing_database_content(jsonb)",
                "read_result(uuid,text)")
    internal = ("claim_doc(infrx.content_objects)",
                "content_referenced(infrx.content_objects,timestamp with time zone)",
                "content_row(uuid)",
                "content_recheck(infrx.content_objects,timestamp with time zone)",
                "scrubbed_request(jsonb)",
                "scrub_content(infrx.content_objects,timestamp with time zone)",
                "job_results_guard()", "register_database_content()")
    for role in ("anon", "authenticated", "service_role"):
        for name in (*boundary, *internal):
            allowed, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                    (role, f"infrx.{name}")).fetchone()
            assert allowed == (role == "service_role" and name in boundary), (role, name)
    return f"{len(boundary)} boundary + {len(internal)} internal functions"


__all__ = ["check_content_liveness", "check_content_privileges", "check_content_protocol",
           "check_content_races", "check_content_scrub"]
