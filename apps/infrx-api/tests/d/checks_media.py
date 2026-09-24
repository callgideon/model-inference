"""D2 item 5: M3's durable upload rows and media last-use (0010) as SQL-level checks."""
from __future__ import annotations

import psycopg

from infrx.contracts.conformance import builders as b

from . import checks_admission as ca
from . import checks_credit as cc

HANDLE = "upl_" + "A" * 22
DIGEST = "sha256:" + "ab" * 32


def _upload(handle: str = HANDLE, org: str = b.ORG_A, **cols) -> str:
    base = {"handle": f"'{handle}'", "org_id": f"'{org}'", "state": "'created'",
            "max_bytes": "1048576", "accepted_mime": "array['video/mp4']",
            "expires_at": "infrx.now() + interval '7 days'"}
    base.update(cols)
    return (f"insert into infrx.media_uploads ({', '.join(base)}) "
            f"values ({', '.join(base.values())})")


# D10 (0019): a finalized ticket names exactly the bytes received and its source content row;
# the update carries both facts (0019's `upload_complete` is the writer that does).
SOURCE_KEY = f"media/{b.ORG_A}/v1/{'ab' * 8}/source"
SOURCE_ROW = ("insert into infrx.content_objects (org_id, kind, location, object_key, digest, "
              f"bytes, origin, registered_at, eligible_at) values ('{b.ORG_A}', 'source', "
              f"'object_store', '{SOURCE_KEY}', '{DIGEST}', 1000, 'written', infrx.now(), "
              "infrx.now())")
FINALIZE = ("update infrx.media_uploads set state = 'finalized', finalized_at = infrx.now(), "
            f"digest = '{DIGEST}', bytes = 1000, mime = 'video/mp4', duration_s = 12.5, "
            f"storage_ref = '{SOURCE_KEY}', profile_version = 'v1', "
            f"received_bytes = 1000, received_digest = '{DIGEST}', received_at = infrx.now(), "
            "source_generation = 1, source_content_id = (select content_id from "
            f"infrx.content_objects where object_key = '{SOURCE_KEY}') "
            "where org_id = %s and handle = %s and state = 'created' "
            "and infrx.now() < expires_at returning handle")


def check_media_uploads(conn) -> str:
    """M3 request 1: the handle grammar, finalized facts present exactly when finalized,
    the one-way state graph, finalize-once by M3's own UPDATE, immutability after, a
    finalized record kept until its window passes, bytes within max_bytes."""
    refused = (
        ("a malformed handle", _upload("upl_short")),
        ("a handle of the wrong kind", _upload("job_" + "A" * 22)),
        ("a zero max_bytes", _upload(max_bytes="0")),
        ("max_bytes past MAX_MEDIA_BYTES", _upload(max_bytes="67108865")),
        ("no accepted mime", _upload(accepted_mime="array[]::text[]")),
        ("a declared digest of another shape", _upload(declared_digest="'md5:abc'")),
        ("finalized facts on a created upload", _upload(digest=f"'{DIGEST}'")),
        ("a finalized upload without its facts", _upload(state="'finalized'",
                                                        finalized_at="infrx.now()")),
        ("an abort reason on a live upload", _upload(aborted_reason="'x'")),
        ("an expiry before creation", _upload(expires_at="infrx.now() - interval '1 s'")),
    )

    def body():
        for label, sql in refused:
            why = cc.attempt(conn, sql)
            assert why is not None and why.startswith("23"), f"{label}: {why!r}"
        conn.execute(_upload())
        conn.execute(SOURCE_ROW)
        # M3's finalize-once: the first UPDATE wins, the second finds nothing to update
        assert conn.execute(FINALIZE, (b.ORG_A, HANDLE)).fetchall() == [(HANDLE,)], 'failed: conn.execute(FINALIZE, (b.ORG_A, HANDLE)).fetchall() == [(HANDLE,)]'
        assert conn.execute(FINALIZE, (b.ORG_A, HANDLE)).fetchall() == [], 'failed: conn.execute(FINALIZE, (b.ORG_A, HANDLE)).fetchall() == []'
        assert conn.execute(FINALIZE, (b.ORG_B, HANDLE)).fetchall() == [], \
            "another organization finalized the upload"
        after = (
            ("a finalized upload's content", "update infrx.media_uploads set bytes = 999 "
                                             f"where handle = '{HANDLE}'"),
            ("a finalized upload moved back", "update infrx.media_uploads set state = "
                                              f"'created' where handle = '{HANDLE}'"),
            ("a finalized upload aborted", "update infrx.media_uploads set state = 'aborted' "
                                           f"where handle = '{HANDLE}'"),
            ("a re-owned upload", f"update infrx.media_uploads set org_id = '{b.ORG_B}' "
                                  f"where handle = '{HANDLE}'"),
            ("a finalized upload deleted inside its window",
             f"delete from infrx.media_uploads where handle = '{HANDLE}'"),
        )
        for label, sql in after:
            why = cc.attempt(conn, sql)
            assert why is not None and why.startswith("23514"), f"{label}: {why!r}"
        conn.execute("select infrx_test.advance(%s)", (7 * 86400,))
        assert cc.attempt(conn, f"delete from infrx.media_uploads where handle = '{HANDLE}'") \
            is None, "an expired finalized record cannot be collected"
        other = "upl_" + "B" * 22
        conn.execute(_upload(other, max_bytes="10"))
        why = cc.attempt(conn, FINALIZE.replace("%s", "'" + b.ORG_A + "'", 1)
                         .replace("%s", f"'{other}'"))
        assert why is not None and "media_uploads_bytes_within_max" in why, why
        return f"{len(refused)} shapes refused; finalize-once; immutable after; kept to expiry"
    return ca._in_rollback(conn, body)


def _touch_error(conn, ref: str, org: str) -> tuple | None:
    """The whole error a touch answers - SQLSTATE, message, detail, hint, context (the
    RAISE's line, review MC-2b) - with the ref itself replaced, so an unknown ref and
    another tenant's ref can be compared byte for byte (review MC-2). None when the touch
    succeeded."""
    try:
        with conn.transaction():
            conn.execute("select infrx.touch_media_object(%s, %s)", (ref, org))
    except psycopg.Error as failed:
        d = failed.diag
        return tuple(None if v is None else str(v).replace(ref, "<ref>")
                     for v in (failed.sqlstate, d.message_primary, d.message_detail,
                               d.message_hint, d.context))
    return None


def check_media_objects(conn) -> str:
    """M3 request 1 / limit 2: `touch_media_object` stamps a use for its own organization
    only; `delete_media_object_if_idle` deletes only while `last_used_at` is still the
    observed value, so a re-use between the collector's read and its delete wins."""
    def body():
        ref = "media/a/v1/source"
        # an unknown ref: not_found, and nothing is created (update-only, SEC-4)
        why = cc.attempt(conn, "select infrx.touch_media_object(%s, %s)", (ref, b.ORG_A))
        assert why is not None and why.startswith("P0002"), f"an unknown object was touched: {why}"
        assert conn.execute("select count(*) from infrx.media_objects where storage_ref = %s",
                            (ref,)).fetchone()[0] == 0, "a touch created an object row"
        conn.execute("insert into infrx.media_objects (storage_ref, org_id) values (%s, %s)",
                     (ref, b.ORG_A))
        first, = conn.execute("select last_used_at from infrx.media_objects where "
                              "storage_ref = %s", (ref,)).fetchone()
        conn.execute("select infrx_test.advance(30)")
        why = cc.attempt(conn, "select infrx.touch_media_object(%s, %s)", (ref, b.ORG_B))
        assert why is not None and why.startswith("P0002"), \
            f"another organization stamped the object: {why!r}"
        # MC-2: another tenant's EXISTING object answers exactly what a missing one does
        unknown = _touch_error(conn, "media/zz/v1/never-stored", b.ORG_B)
        foreign = _touch_error(conn, ref, b.ORG_B)
        assert unknown is not None and unknown == foreign, \
            f"a foreign touch is distinguishable from a miss: {unknown} != {foreign}"
        unchanged, = conn.execute("select last_used_at from infrx.media_objects where "
                                  "storage_ref = %s", (ref,)).fetchone()
        assert unchanged == first, "another organization's touch moved last_used_at"
        conn.execute("select infrx_test.advance(30)")
        second, = conn.execute("select infrx.touch_media_object(%s, %s)",
                               (ref, b.ORG_A)).fetchone()
        assert second > first, "the owner's touch did not move last_used_at"
        stale, = conn.execute("select infrx.delete_media_object_if_idle(%s, %s)",
                              (ref, first)).fetchone()
        assert stale is False, "an object used after the collector looked was deleted"
        idle, = conn.execute("select infrx.delete_media_object_if_idle(%s, %s)",
                             (ref, second)).fetchone()
        assert idle is True, "an idle object was not deleted"
        assert conn.execute("select count(*) from infrx.media_objects where storage_ref = %s",
                            (ref,)).fetchone()[0] == 0, "the idle object row survived"
        return "update-only, tenant-checked touch; conditional delete loses to a re-use"
    return ca._in_rollback(conn, body)


def check_media_privileges(conn) -> str:
    """RLS on both tables; browser roles hold nothing; the platform role never truncates
    and never deletes a media upload directly outside the guard's rules."""
    for table in ("media_uploads", "media_objects"):
        rls, acl = conn.execute("select relrowsecurity, coalesce(relacl::text, '') from "
                                "pg_class where oid = %s::regclass",
                                (f"infrx.{table}",)).fetchone()
        assert rls, f"infrx.{table} has no row-level security"
        grantees = {item.split("=", 1)[0]: item.split("=", 1)[1].split("/")[0]
                    for item in acl.strip("{}").split(",") if item}
        assert not {"anon", "authenticated", ""} & set(grantees), (table, acl)
        assert "D" not in grantees.get("service_role", ""), f"{table}: service_role truncates"
    return "RLS on, browser roles hold nothing, no TRUNCATE"
