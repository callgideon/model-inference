-- T2I: the trace projection, one row per spooled record (TRACE-RECOVER, TRACE-TENANT).
-- `trace_id` is the spool's stable identity `<segment>:<position>` (T1): a replay after a
-- crash or a lost ack inserts the same key again and ReplacingMergeTree keeps one, so every
-- read is FINAL. The version is `content_stored`: the retry that stored the content
-- supersedes the metadata-only row, never the reverse. `org_id` leads the key and every
-- read binds it as a parameter. No content here: it is an object at `content_key`.
-- Additive: the E2 stack applies it only through coordinator wiring (T2I WR-1).
CREATE TABLE IF NOT EXISTS trace_envelopes
(
    org_id                 UUID,
    trace_id               String,
    request_id             UUID,
    key_id                 UUID,
    mode                   LowCardinality(String),
    started_at             DateTime64(6, 'UTC'),
    completed_at           Nullable(DateTime64(6, 'UTC')),
    loss_reason            LowCardinality(String),
    content_complete       Bool,
    content_bytes          UInt64,
    content_key            Nullable(String),
    content_stored         UInt8,
    request_schema_version UInt32,
    model_revision         String,
    price_version          String,
    serving_version_id     Nullable(UUID),
    rate_card_version      Nullable(String),
    policy_version         Nullable(String)
)
ENGINE = ReplacingMergeTree(content_stored)
ORDER BY (org_id, trace_id)
-- T3: physical cleanup of metadata at 13 months, reads filter logical expiry themselves.
TTL toDateTime(started_at) + INTERVAL 13 MONTH
