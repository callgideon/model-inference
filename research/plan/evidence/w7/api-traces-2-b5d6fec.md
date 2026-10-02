# AP-07 remainder (lane api-traces-2, wave 7 batch 2) — evidence

Base `b05eb6f4` · branch `codex/w7-api-traces-2` · key `ap7` (PG 57559, ClickHouse 57560/57561, MinIO 57562) · 2026-10-02. Named after the first slice's head `b5d6fec`; later slices append below.

## Published protocol: `infrx.traces.eligible` (consumed by api-judge-2's `infrx/judge/start.py`)

```python
from infrx.traces.eligible import TraceEligible

eligible = TraceEligible(connect, retention)   # connect: state.jobstore.connector(dsn)
                                               # retention: T3 Retention; retention.traces = ClickHouseProjection
await eligible(grantor_org_id: str, model_id: str, limit: int) -> list[tuple[str, bool]]
```

- Matches `infrx.judge.start.Eligible = Callable[[str, str, int], Awaitable[Sequence[tuple[str, bool]]]]` exactly; `start.start` calls it with `(request["grantor_org_id"], request["model_id"], MAX_CANDIDATES)`.
- Answer: `(request_id, has_video)` pairs, unique, newest first, at most `limit`. `limit` outside 1..200 (`judge.dryrun.scan_bound`) → `errors.InvalidRequest`. PostgreSQL failure → `errors.DependencyUnavailable`.
- Offered only when: the grantor's CURRENT grant to the model's provider permits `external_judging` on `request_content` AND `response_content` for that model (database clock); the row is the grantor's on one of the model's `infrx.serving_versions`; `mode = 'full'`, `content_stored = 1`, inside T3's content bound, no `request`/`content` tombstone.
- `has_video`: the request's `infrx.job_media` includes a finalized `staged_media` row with mime `video/*`.
- Composition for the judge role (WR-2 of api-judge, `infrx/lab/workers/__main__.py:_judge`): `limits, retention = _traces(mode, env)` already exists there; `eligible = TraceEligible(connect, retention)`.
