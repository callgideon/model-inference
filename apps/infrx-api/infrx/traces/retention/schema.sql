-- T3: the deletion ledger - one tombstone per (org, request, scope), the receipt of a logical
-- deletion (scope `request`: the trace rows, their content objects and the request's feedback)
-- or of expired content (scope `content`: the objects only; the metadata stays). The version
-- is `state` (1 pending, 2 cleaned), so a cleaned tombstone supersedes its pending one and a
-- repeated delete or expiry never reopens it; reads are FINAL. Every trace, content and
-- feedback read filters these before anything physical has happened, and the shipper and the
-- feedback projector consult them before writing, so a replay never resurrects a deleted
-- request. No TTL: a tombstone must outlive every copy it hides.
-- Additive; the E2 stack applies it only through coordinator wiring (T3 WR-T-3).
CREATE TABLE IF NOT EXISTS trace_deletions
(
    org_id      UUID,
    request_id  UUID,
    scope       LowCardinality(String),
    reason      LowCardinality(String),
    deleted_at  DateTime64(6, 'UTC'),
    cleaned_at  Nullable(DateTime64(6, 'UTC')),
    state       UInt8
)
ENGINE = ReplacingMergeTree(state)
ORDER BY (org_id, request_id, scope)
