"""D10.a: durable upload tickets and the execution-ready marker (0019) as SQL-level checks.

Same contract as `checks_admission.py`: each check raises `AssertionError` on a broken
invariant and returns a short summary, so `migration_mutants.py` can run it against a
single-edit mutant. The database is the "admission" scenario (`checks_admission.
seed_admission`: every migration, the frozen clock, the legacy and CREDIT worlds). Each
check works inside a transaction it rolls back, except the races, which commit on their
own connections exactly as two processes would.

Every call is the boundary the adapter (`state.lifecycle.PgLifecycle`) makes, with the
arguments it builds, so what is checked is what runs.
"""
from __future__ import annotations

import hashlib
import threading

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ExecutionMode, MediaKind, MediaRef
from infrx.contracts.v2.lifecycle import refusal_of
from infrx.state.jobstore import PgJobStore
from infrx.state.lifecycle import lifecycle_error

from . import checks_admission as ca
from . import checks_credit as cc
from .checks_dispatch import LIMITS as PREP_LIMITS
from .checks_leases import lockstep, waiting_on_a_lock

WINDOW_S, GRACE_S, RETENTION_S = 3600.0, 60.0, 600.0
MP4 = ["video/mp4", "video/webm"]


# --------------------------------------------------------------------- driving
def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def c1_org(conn) -> str:
    return cc.personal_org(conn, cc.CONSUMER_1)


def source_ref(org: str, data: bytes, *, handle: str | None = None,
               kind: MediaKind = MediaKind.url, key_org: str | None = None) -> MediaRef:
    d = digest(data)
    return MediaRef(org_id=org, handle=handle or "med_" + d[7:47], kind=kind, digest=d,
                    bytes=len(data), mime="video/mp4", duration_s=4.0,
                    storage_ref=f"media/{key_org or org}/v1/{d[7:23]}/source")


def register(conn, ref: MediaRef, *, org: str | None = None) -> dict:
    """What M does before it writes a source: the content row (0019 `content_register`)."""
    return call(conn, "content_register", {"identity": {
        "org_id": org or ref.org_id, "kind": "source", "location": "object_store",
        "object_key": ref.storage_ref, "digest": ref.digest, "bytes": ref.bytes,
        "job_id": None, "upload_handle": None, "origin": "written"}, "grace_s": GRACE_S})


def call(conn, function: str, args: dict):
    return conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),)).fetchone()[0]


def refusal(conn, function: str, args: dict) -> tuple[str, str | None] | None:
    """(code, lifecycle reason) of a refused call - inside a savepoint, so the caller's
    transaction survives - or None when it answered. A refusal answered as data counts."""
    try:
        with conn.transaction():
            answer = call(conn, function, args)
    except psycopg.Error as failed:
        mapped = lifecycle_error(failed)
        if not isinstance(mapped, errors.DomainError):
            return f"untyped {failed.sqlstate}", str(failed).splitlines()[0][:120]
        reason = refusal_of(mapped)
        return mapped.code, reason.value if reason else None
    if isinstance(answer, dict) and answer.get("refusal"):
        found = answer["refusal"]
        return found["code"], found.get("reason")
    return None


def ready_args(request, idem, *, regime: str = "credit", card: str | None = cc.CARD,
               retention_s: float = RETENTION_S) -> dict:
    return {**PgJobStore(None)._admit_args(regime, request, idem),
            "expectation": {"accounting_regime": regime, "rate_card_version": card},
            "retention_s": retention_s}


def credit_request(conn, world, refs=(), mode=ExecutionMode.async_):
    return ca.credit_request(world, ca.C1_KEY, c1_org(conn), refs=tuple(refs), mode=mode)


def upload(conn, org: str, data: bytes, *, complete: bool = True, **constraints):
    """create -> acknowledge_put [-> complete]: the ticket doc and the ref M stages."""
    ticket = call(conn, "upload_create", {
        "org_id": org, "upload_handle": "upl_" + hashlib.sha256(data + org.encode()
                                                                ).hexdigest()[:40],
        "constraints": {"max_bytes": 1 << 26, "accepted_mime": MP4, **constraints},
        "window_s": WINDOW_S})
    handle = ticket["upload_handle"]
    call(conn, "upload_acknowledge_put", {"org_id": org, "upload_handle": handle,
                                          "bytes": len(data), "digest": digest(data)})
    ref = source_ref(org, data, handle=handle, kind=MediaKind.upload)
    if complete:
        ticket = call(conn, "upload_complete", {"org_id": org, "upload_handle": handle,
                                                "source": ref.model_dump(mode="json"),
                                                "grace_s": GRACE_S})
    return ticket, ref


def footprint(conn) -> tuple:
    """Everything an admission may own (`checks_admission.footprint`) plus markers and
    manifest rows."""
    return (*ca.footprint(conn), *conn.execute(
        "select (select count(*) from infrx.job_readiness), "
        "(select count(*) from infrx.job_media)").fetchone())


def marker(conn, request_id: str):
    return conn.execute("select source_count, ready_at, retention_s from infrx.job_readiness "
                        "where job_id = %s", (request_id,)).fetchone()


# --------------------------------------------------------------------- checks
def check_ready_marker(conn) -> str:
    """ADMISSION-READY / RV-05: `admit_ready` commits the marker in the admission itself - a
    text-only job with ZERO sources, a media job with its manifest bound to the content row
    generation; `claim_preparation_ready` leases only a marked job. A job the previous
    runtime admitted (`admit`, no marker) is `not_ready` at the new door, still claimable at
    0012's old door (rollback), and past its preparation deadline it is terminalized and
    released by R29 at either door, never left holding CREDIT."""
    world = ca.World(conn)

    def body():
        text = credit_request(conn, world)
        doc = call(conn, "admit_ready", ready_args(text, b.idem(text, "ready-text")))
        assert doc["readiness"] is not None and doc["readiness"]["sources"] == [], doc
        admitted_at = conn.execute("select admitted_at from infrx.jobs where request_id = %s",
                                   (text.request_id,)).fetchone()[0]
        assert marker(conn, text.request_id) == (0, admitted_at, RETENTION_S), \
            "the empty manifest is not a committed marker at the admission instant"
        assert refusal(conn, "claim_preparation_ready", {
            "job_id": text.request_id, "worker_id": "prep-a", "limits": PREP_LIMITS}) is None, \
            "a text-only job with its marker could not be prepared"
        clip = source_ref(c1_org(conn), b"marker-clip")
        row = register(conn, clip)
        media = credit_request(conn, world, (clip,))
        doc = call(conn, "admit_ready", ready_args(media, b.idem(media, "ready-media")))
        (source,) = doc["readiness"]["sources"]
        assert (source["content_id"], source["generation"]) == (row["content_id"], 1), source
        assert source["ref"]["digest"] == clip.digest and source["ref"]["handle"] == clip.handle
        # the legacy USD regime passes the same door and writes the same marker
        usd = b.request(world)
        usd_doc = call(conn, "admit_ready", ready_args(usd, b.idem(usd, "ready-usd"),
                                                       regime="legacy_usd", card=None))
        assert usd_doc["readiness"]["sources"] == [] and marker(conn, usd.request_id), usd_doc
        usd_clip = source_ref(b.ORG_A, b"marker-usd-clip")
        register(conn, usd_clip)
        usd_media = b.request(world, refs=(usd_clip,))
        usd_doc = call(conn, "admit_ready", ready_args(usd_media, b.idem(usd_media, "usd-m"),
                                                       regime="legacy_usd", card=None))
        assert [s["ref"]["handle"] for s in usd_doc["readiness"]["sources"]] == \
            [usd_clip.handle], usd_doc
        # the previous runtime's admission: no marker, and the new door refuses it
        old = credit_request(conn, world)
        ca.admit(conn, old, b.idem(old, "previous-runtime"), regime="credit")
        assert marker(conn, old.request_id) is None, "the previous runtime wrote a marker"
        claim = {"job_id": old.request_id, "worker_id": "prep-a", "limits": PREP_LIMITS}
        assert refusal(conn, "claim_preparation_ready", claim) == ("not_claimable",
                                                                   "not_ready"), \
            "an unmarked job was leased by the new runtime"
        assert refusal(conn, "claim_preparation", claim) is None, \
            "the previous runtime's door changed (rollback would break)"
        # past its preparation phase an unmarked job ends by R29, released, at the new door
        late = credit_request(conn, world)
        ca.admit(conn, late, b.idem(late, "late"), regime="credit")
        deadline = conn.execute("select preparation_deadline_at from infrx.jobs where "
                                "request_id = %s", (late.request_id,)).fetchone()[0]
        conn.execute("select infrx_test.advance(%s)",
                     ((deadline - world.clock.now()).total_seconds(),))
        answer = refusal(conn, "claim_preparation_ready", dict(claim, job_id=late.request_id))
        assert answer == ("already_terminal", None), answer
        held = conn.execute("select state from infrx.credit_wallet_holds where request_id = %s",
                            (late.request_id,)).fetchone()
        assert held == ("released",), f"the unready job kept its hold: {held}"
        return "empty and media manifests marked at admission; unmarked: not_ready, R29 ends it"
    return ca._in_rollback(conn, body)


def check_ready_refusals(conn) -> str:
    """A refused `admit_ready` admits NOTHING - no job, hold, reservation, outbox,
    idempotency mapping, marker or manifest row - and does not consume its key: after the
    refusal's cause is fixed the SAME request and key admit, and again replays. Covers the
    expectation (R69), the pinned capability, the tenant of every source (a ref naming
    another organization's object key), an unregistered, relabelled, repeated or retiring
    source, and an upload that is not finalized or past its window."""
    world = ca.World(conn)

    def body():
        org = c1_org(conn)
        clip = source_ref(org, b"refusal-clip")
        register(conn, clip)
        other_org = cc.personal_org(conn, cc.CONSUMER_2)
        theirs = source_ref(other_org, b"their-clip")
        register(conn, theirs)
        # a ref this org names, pointing at the other organization's object key
        smuggled = source_ref(org, b"their-clip", key_org=other_org)
        retiring = source_ref(org, b"retiring-clip")
        row = register(conn, retiring)
        conn.execute("update infrx.content_objects set state = 'tombstoned', "
                     "tombstoned_at = infrx.now() where content_id = %s", (row["content_id"],))
        pending, pending_ref = upload(conn, org, b"pending-upload", complete=False)
        # a payload row presented as a source; an upload naming another source's content;
        # a source whose delete was acknowledged
        payload_key = f"payloads/{org}/{b.ORG_A}.json"
        call(conn, "content_register", {"identity": {
            "org_id": org, "kind": "payload", "location": "object_store",
            "object_key": payload_key, "digest": digest(b"payload"), "bytes": 7,
            "job_id": b.ORG_A, "upload_handle": None, "origin": "written"},
            "grace_s": GRACE_S})
        not_a_source = MediaRef(org_id=org, handle="med_" + "p" * 40, kind=MediaKind.url,
                                digest=digest(b"payload"), bytes=7, mime="video/mp4",
                                duration_s=1.0, storage_ref=payload_key)
        finalized, finalized_ref = upload(conn, org, b"finalized-upload")
        other_content = clip.model_copy(update={"handle": finalized["upload_handle"],
                                                "kind": MediaKind.upload})
        gone = source_ref(org, b"gone-clip")
        row = register(conn, gone)
        conn.execute("update infrx.content_objects set state = 'tombstoned', tombstoned_at = "
                     "infrx.now() where content_id = %s", (row["content_id"],))
        conn.execute("update infrx.content_objects set state = 'deleted', deleted_at = "
                     "infrx.now() where content_id = %s", (row["content_id"],))
        cases = (
            ("another card", credit_request(conn, world), dict(card="rc_not_this_runtime"),
             ("invalid_request", "expectation_mismatch")),
            ("a legacy expectation", credit_request(conn, world),
             dict(regime="credit", card=None), ("invalid_request", "expectation_mismatch")),
            ("another org's key", credit_request(conn, world, (smuggled,)), {},
             ("not_found", "not_found")),
            ("another org's ref", credit_request(conn, world, (theirs,)), {},
             ("not_found", "not_found")),
            ("never registered", credit_request(conn, world, (source_ref(org, b"never"),)),
             {}, ("not_found", "not_found")),
            ("a relabelled digest", credit_request(conn, world, (clip.model_copy(
                update={"digest": digest(b"lie")}),)), {}, ("not_found", "not_found")),
            ("a repeated source", credit_request(conn, world, (clip, clip)), {},
             ("invalid_request", "invalid_manifest")),
            ("a retiring source", credit_request(conn, world, (retiring,)), {},
             ("dependency_unavailable", "content_retiring")),
            ("an upload not finalized", credit_request(conn, world, (pending_ref,)), {},
             ("invalid_request", "upload_not_finalized")),
            ("a payload as a source", credit_request(conn, world, (not_a_source,)), {},
             ("not_found", "not_found")),
            ("an upload naming other content", credit_request(conn, world, (other_content,)),
             {}, ("not_found", "not_found")),
            ("a deleted source", credit_request(conn, world, (gone,)), {},
             ("not_found", "not_found")),
        )
        for label, request, kw, expected in cases:
            before = footprint(conn)
            idem = b.idem(request, f"refused-{label}")
            got = refusal(conn, "admit_ready", ready_args(request, idem, **kw))
            assert got == expected, f"{label}: {got} != {expected}"
            assert footprint(conn) == before, f"{label}: a refused admission left rows"
        # capability: the pinned revision stops taking video / streaming (a savepoint the
        # case rolls back: the catalog row is immutable, so it is patched as its owner)
        usd_clip = source_ref(b.ORG_A, b"refusal-usd-clip")
        register(conn, usd_clip)
        legacy = {"regime": "legacy_usd", "card": None}
        for patch, request, kw, expected in (
                ('{"input_modalities": ["text"]}', credit_request(conn, world, (clip,)), {},
                 "unsupported_media"),
                ('{"stream_output": false}',
                 credit_request(conn, world, mode=ExecutionMode.stream), {},
                 "unsupported_parameter"),
                # the legacy regime's pinned revision is its canonical model_revision's
                ('{"input_modalities": ["text"]}', b.request(world, refs=(usd_clip,)), legacy,
                 "unsupported_media")):
            try:
                with conn.transaction():
                    conn.execute("alter table infrx.serving_versions disable trigger "
                                 "serving_versions_immutable")
                    conn.execute("update infrx.serving_versions set capability = capability "
                                 "|| %s::jsonb where serving_version_id = %s",
                                 (patch, cc.SERVING))
                    conn.execute("alter table infrx.serving_versions enable trigger "
                                 "serving_versions_immutable")
                    before = footprint(conn)
                    got = refusal(conn, "admit_ready",
                                  ready_args(request, b.idem(request, patch), **kw))
                    assert got == (expected, None), f"{patch}: {got}"
                    assert footprint(conn) == before, f"{patch}: a refused admission left rows"
                    raise ca._Rollback()
            except ca._Rollback:
                pass
        # the key a refusal answered is not consumed: fix the cause, the same request admits
        request = credit_request(conn, world, (pending_ref,))
        idem = b.idem(request, "retry-after-refusal")
        assert refusal(conn, "admit_ready", ready_args(request, idem)) == \
            ("invalid_request", "upload_not_finalized")
        call(conn, "upload_complete", {"org_id": org, "upload_handle": pending["upload_handle"],
                                       "source": pending_ref.model_dump(mode="json"),
                                       "grace_s": GRACE_S})
        first = call(conn, "admit_ready", ready_args(request, idem))
        assert first["admission"]["replayed"] is False and first["readiness"]["sources"], first
        again = call(conn, "admit_ready", ready_args(request, idem))
        assert again["admission"]["replayed"] is True and \
            again["readiness"] == first["readiness"], "a replay recorded another marker"
        return f"{len(cases) + 2} refusals admitted nothing; the key survived its refusal"
    return ca._in_rollback(conn, body)


def check_upload_ticket(conn) -> str:
    """UPLOAD-RESTART at the row: the received bytes are written once, a finalized ticket is
    exactly its receipt and names its source content row, an aborted one names its reason,
    a completion needs its measured duration, and the platform role writes tickets only
    through the boundary (0010's direct DML grant is taken back)."""
    def body():
        org = c1_org(conn)
        open_ticket, _ = upload(conn, org, b"row-open", complete=False)
        handle = open_ticket["upload_handle"]
        refused = (
            ("a rewritten receipt", "update infrx.media_uploads set received_bytes = 1 "
                                    f"where handle = '{handle}'"),
            ("a finalized ticket without its source row",
             "update infrx.media_uploads set state = 'finalized', finalized_at = infrx.now(), "
             "digest = received_digest, bytes = received_bytes, mime = 'video/mp4', "
             f"duration_s = 1, storage_ref = 'k', profile_version = 'v1' where handle = '{handle}'"),
            ("an abort without its reason", "update infrx.media_uploads set state = 'aborted' "
                                            f"where handle = '{handle}'"),
        )
        for label, sql in refused:
            why = cc.attempt(conn, sql)
            assert why is not None and why.startswith("23514"), f"{label}: {why!r}"
        no_duration = source_ref(org, b"row-open", handle=handle, kind=MediaKind.upload
                                 ).model_copy(update={"duration_s": None})
        got = refusal(conn, "upload_complete", {"org_id": org, "upload_handle": handle,
                                                "source": no_duration.model_dump(mode="json"),
                                                "grace_s": GRACE_S})
        assert got == ("invalid_request", None), got
        big = call(conn, "upload_create", {
            "org_id": org, "upload_handle": "upl_" + "b" * 30,
            "constraints": {"max_bytes": 10, "accepted_mime": MP4}, "window_s": WINDOW_S})
        got = refusal(conn, "upload_acknowledge_put", {
            "org_id": org, "upload_handle": big["upload_handle"], "bytes": 11,
            "digest": digest(b"x" * 11)})
        assert got == ("request_too_large", "too_large"), got
        # `expire` closes open tickets past their window, at most `limit` per call, once
        for n in range(2):
            call(conn, "upload_create", {
                "org_id": org, "upload_handle": f"upl_{'e' * 29}{n}",
                "constraints": {"max_bytes": 10, "accepted_mime": MP4}, "window_s": WINDOW_S})
        conn.execute("select infrx_test.advance(%s)", (WINDOW_S,))
        due = conn.execute("select count(*) from infrx.media_uploads where state = 'created' "
                           "and expires_at <= infrx.now()").fetchone()[0]
        assert due >= 3, due
        assert call(conn, "upload_expire", {"limit": 2}) == 2, "expire ignored its limit"
        assert call(conn, "upload_expire", {"limit": 1000}) == due - 2
        assert call(conn, "upload_expire", {"limit": 1000}) == 0, "expire is not idempotent"
        for sql in (f"update infrx.media_uploads set state = 'expired' where handle = '{handle}'",
                    "insert into infrx.media_uploads (handle, org_id, state, max_bytes, "
                    f"accepted_mime, expires_at) values ('upl_{'x' * 30}', '{org}', 'created', "
                    "1, array['video/mp4'], infrx.now() + interval '1 h')"):
            why = cc.attempt(conn, sql, session="service")
            assert why is not None and why.startswith("42501"), \
                f"the platform role wrote a ticket directly: {why!r}"
        return f"{len(refused)} row rules; duration required; platform writes via the boundary"
    return ca._in_rollback(conn, body)


def check_register_guards(conn) -> str:
    """`register` (0019), the write every content path shares: another organization's key,
    a key outside every tenant prefix and an unknown organization are `not_found` (retained,
    never registered); other bytes at a live key are `bytes_changed`; a tombstoned key is
    `content_retiring` until its delete is acknowledged; the first registration's
    eligibility is never reset."""
    def body():
        org, other = c1_org(conn), cc.personal_org(conn, cc.CONSUMER_2)
        clip = source_ref(org, b"guarded-clip")
        first = register(conn, clip)

        def identity(**over):
            return {"identity": {"org_id": org, "kind": "source", "location": "object_store",
                                 "object_key": clip.storage_ref, "digest": clip.digest,
                                 "bytes": clip.bytes, "job_id": None, "upload_handle": None,
                                 "origin": "written", **over}, "grace_s": GRACE_S}
        for label, args, expected in (
                ("another org's key", identity(org_id=other), ("not_found", "not_found")),
                ("outside every prefix", identity(object_key=f"secrets/{org}/x"),
                 ("not_found", "not_found")),
                # M6 finding: a dot segment inside the prefix names another tenant's key
                ("a '..' segment inside the prefix",
                 identity(object_key=f"media/{org}/../{other}/x"), ("not_found", "not_found")),
                ("an unknown organization", identity(org_id="00000000-0000-4000-8000-"
                                                            "0000000000ff"),
                 ("not_found", "not_found")),
                ("other bytes at a live key", identity(digest=digest(b"other")),
                 ("state_conflict", "bytes_changed"))):
            got = refusal(conn, "content_register", args)
            assert got == expected, f"{label}: {got}"
        # ...and the table refuses it from any writer (the CHECK backs the function)
        why = cc.attempt(conn, "insert into infrx.content_objects (org_id, kind, location, "
                         "object_key, digest, bytes, origin, registered_at, eligible_at) "
                         f"values ('{org}', 'source', 'object_store', "
                         f"'uploads/{org}/./../{other}/x', '{clip.digest}', 1, 'written', "
                         "infrx.now(), infrx.now())")
        assert why is not None and "content_objects_key_in_tenant_prefix" in why, why
        conn.execute("select infrx_test.advance(1)")
        again = call(conn, "content_register", identity(origin="discovered"))
        assert again["eligible_at"] == first["eligible_at"], "a re-registration reset the grace"
        conn.execute("update infrx.content_objects set state = 'tombstoned', tombstoned_at = "
                     "infrx.now() where content_id = %s", (first["content_id"],))
        got = refusal(conn, "content_register", identity())
        assert got == ("dependency_unavailable", "content_retiring"), \
            f"a tombstoned object was re-created with the same identity: {got}"
        return "tenant prefix, organization, bytes and tombstone guards hold; grace persisted"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- races
def _service(connect, database):
    conn = connect(database)
    conn.execute("set role service_role")
    conn.execute("set statement_timeout = '30s'")
    return conn


def _rpc(function: str, args: dict):
    def run(conn):
        return refusal(conn, function, args), None
    return run


def _answer(function: str, args: dict):
    def run(conn):
        try:
            return None, call(conn, function, args)
        except psycopg.Error as failed:
            mapped = lifecycle_error(failed)
            return (getattr(mapped, "code", failed.sqlstate),
                    getattr(refusal_of(mapped), "value", None)), None
    return run


def check_ready_races(connect, database: str) -> str:
    """Two connections, committed state (DUR-ADMIT / UPLOAD-RESTART / ADMISSION-READY):
    1. finalize/finalize - the second completion waits on the ticket row, then answers the
       SAME finalized ticket (one content row); a different source is `bytes_changed`;
    2. complete/admit - an admission naming the upload waits for the completion's ticket
       lock and then admits against the finalized source;
    3. admit/abort - an abort behind an admission waits, then finds the ticket finalized;
    4. same key twice - the second admission waits on the scope lock and REPLAYS; after a
       rolled-back first attempt it admits afresh (a refusal never binds the key)."""
    owner = connect(database)
    world = ca.World(owner)
    org = c1_org(owner)
    report = []
    # 1. finalize / finalize
    ticket, ref = upload(owner, org, b"race-finalize", complete=False)
    args = {"org_id": org, "upload_handle": ticket["upload_handle"],
            "source": ref.model_dump(mode="json"), "grace_s": GRACE_S}
    first, second = lockstep(owner, (_service(connect, database), _answer("upload_complete", args)),
                             (_service(connect, database), _answer("upload_complete", args)))
    assert first[0] is None and second[0] is None and first[1] == second[1], (first, second)
    rows = owner.execute("select count(*) from infrx.content_objects where object_key = %s",
                         (ref.storage_ref,)).fetchone()[0]
    assert rows == 1, f"two completions registered {rows} content rows"
    other = source_ref(org, b"race-finalize-other", handle=ticket["upload_handle"],
                       kind=MediaKind.upload)
    got = refusal(owner, "upload_complete", dict(args, source=other.model_dump(mode="json")))
    assert got == ("state_conflict", "bytes_changed"), got
    report.append("finalize/finalize: one ticket, one row")
    # 2. complete / admit
    ticket, ref = upload(owner, org, b"race-complete-admit", complete=False)
    request = credit_request(owner, world, (ref,))
    completing = _service(connect, database)
    completing.execute("begin")
    call(completing, "upload_complete", {"org_id": org, "upload_handle": ticket["upload_handle"],
                                         "source": ref.model_dump(mode="json"),
                                         "grace_s": GRACE_S})
    admitting = _service(connect, database)
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("admit", _answer(
        "admit_ready", ready_args(request, b.idem(request, "race-ca")))(admitting)))
    thread.start()
    try:
        waiting_on_a_lock(owner, admitting.info.backend_pid)
    finally:
        completing.execute("commit")
        thread.join(10)
    code, doc = out["admit"]
    assert code is None and doc["readiness"]["sources"][0]["ref"]["handle"] == \
        ticket["upload_handle"], out
    report.append("complete/admit: the admission waited and admitted the finalized source")
    # 3. admit / abort
    ticket, ref = upload(owner, org, b"race-admit-abort")
    request = credit_request(owner, world, (ref,))
    first, second = lockstep(
        owner, (_service(connect, database),
                _answer("admit_ready", ready_args(request, b.idem(request, "race-aa")))),
        (_service(connect, database),
         _answer("upload_abort", {"org_id": org, "upload_handle": ticket["upload_handle"],
                                  "refusal": "digest_mismatch"})))
    assert first[0] is None and second[0] == ("state_conflict", "upload_not_open"), \
        (first, second)
    report.append("admit/abort: the abort waited and found the ticket finalized")
    # 4. the same key twice: commit -> replay; rollback -> a fresh admission
    request = credit_request(owner, world)
    args = ready_args(request, b.idem(request, "race-key"))
    first, second = lockstep(owner, (_service(connect, database), _answer("admit_ready", args)),
                             (_service(connect, database), _answer("admit_ready", args)))
    assert first[0] is None and second[0] is None, (first, second)
    assert second[1]["admission"]["replayed"] is True and \
        second[1]["admission"]["request_id"] == first[1]["admission"]["request_id"] and \
        second[1]["readiness"] == first[1]["readiness"], "the second admission did not replay"
    request = credit_request(owner, world)
    args = ready_args(request, b.idem(request, "race-key-rollback"))
    held = _service(connect, database)
    held.execute("begin")
    call(held, "admit_ready", args)
    racer = _service(connect, database)
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("second", _answer(
        "admit_ready", args)(racer)))
    thread.start()
    try:
        waiting_on_a_lock(owner, racer.info.backend_pid)
    finally:
        held.execute("rollback")
        thread.join(10)
    code, doc = out["second"]
    assert code is None and doc["admission"]["replayed"] is False, out
    assert owner.execute("select count(*) from infrx.jobs where request_id = %s",
                         (request.request_id,)).fetchone()[0] == 1
    report.append("same key: replay after commit, fresh admission after rollback")
    return "; ".join(report)


# --------------------------------------------------------------------- privileges
def check_ready_privileges(conn) -> str:
    """0019's surface: the boundary functions are the platform role's alone, the internal
    ones nobody's; the new relations are readable by the platform role only and writable by
    no role (the SECURITY DEFINER boundary writes them), with RLS on."""
    boundary = ("content_register(jsonb)", "upload_create(jsonb)",
                "upload_acknowledge_put(jsonb)", "upload_complete(jsonb)",
                "upload_abort(jsonb)", "upload_resolve(jsonb)", "upload_expire(jsonb)",
                "readiness_doc(uuid)", "admit_ready(jsonb)", "claim_preparation_ready(jsonb)",
                "readiness_cutover_check()", "content_references(jsonb)")
    internal = ("lifecycle_code(text)", "lifecycle_refuse(text,text)",
                "lifecycle_refusal(text,text)", "content_objects_guard()",
                "content_doc(infrx.content_objects)",
                "register_content(jsonb,double precision)",
                "upload_doc(infrx.media_uploads)", "upload_row(uuid,text,text)",
                "bind_source(infrx.jobs,jsonb,integer)",
                "check_pinned_capability(infrx.jobs,jsonb)", "media_uploads_guard()")
    for role in ("anon", "authenticated", "service_role"):
        for name in (*boundary, *internal):
            allowed, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                    (role, f"infrx.{name}")).fetchone()
            assert allowed == (role == "service_role" and name in boundary), (role, name)
    for table in ("content_objects", "job_readiness", "media_uploads"):
        rls, = conn.execute("select relrowsecurity from pg_class where oid = %s::regclass",
                            (f"infrx.{table}",)).fetchone()
        assert rls, f"infrx.{table} has no row-level security"
        for role in ("anon", "authenticated", "service_role"):
            for verb in ("select", "insert", "update", "delete", "truncate"):
                has, = conn.execute("select has_table_privilege(%s, %s, %s)",
                                    (role, f"infrx.{table}", verb)).fetchone()
                assert has == (role == "service_role" and verb == "select"), (role, table, verb)
    return f"{len(boundary)} boundary + {len(internal)} internal functions; 3 relations read-only"


__all__ = ["check_ready_marker", "check_ready_privileges", "check_ready_races",
           "check_ready_refusals", "check_register_guards", "check_upload_ticket"]
