-- T2F: the feedback projection, one row per durable `infrx.feedback` row (FEEDBACK-ACK).
-- A row is immutable in PostgreSQL (0028), so a redelivered event inserts the identical row
-- again and ReplacingMergeTree keeps one: every read is FINAL. The key leads with the org and
-- the durable request id, the trace projection's join key (late feedback joins by it). No
-- judge column: a judge result is never derived here. `value` is the JSON value.
-- Additive: the E2 stack applies it only through coordinator wiring (T2F WR-T-3).
CREATE TABLE IF NOT EXISTS feedback_events
(
    org_id           UUID,
    request_id       UUID,
    feedback_id      String,
    author_principal String,
    author_role      LowCardinality(String),
    channel          LowCardinality(String),
    name             LowCardinality(String),
    value            String,
    comment          Nullable(String),
    calibration_set  Bool,
    rubric_version   Nullable(UInt16),
    by_operator      Bool,
    created_at       DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree
ORDER BY (org_id, request_id, feedback_id)
-- T3: physical cleanup at 13 months, reads filter logical expiry themselves.
TTL toDateTime(created_at) + INTERVAL 13 MONTH
