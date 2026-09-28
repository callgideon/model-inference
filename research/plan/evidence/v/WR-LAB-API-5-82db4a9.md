# WR-LAB-API-5 — the Lab adapter over GET /lab/v1/traces[/{request_id}] (lane v1m, wave LW2)

- Base `8ae65fa8` · code head `82db4a9e` · branch `codex/w5-v1m`. Full record: `V1M-82db4a9.md`.
- Built: `apps/lab/lib/services/traces/port.ts` (`httpTraces`, `offlineTraces`, `LIST_LIMIT` = the route's default 50) and `server.ts` (`labTraces(env)`: `LAB_TRACES_API_URL` + Lab Supabase config, else off; the Lab session's own access token as `Authorization: Bearer`).
- Mapping (as the brief): 401/403 → denied, 404 → not_found, else (422, 5xx, network, unreadable) → unavailable; no token → unavailable, nothing sent. Only the route's named fields (typed) survive; a malformed row fails the read closed; a granted detail needs `grant_ref` (WR-LAB-API-6).
- Red first: `node --test tests/v/list/adapter.test.ts` exit 1 `ERR_MODULE_NOT_FOUND … lib/services/traces/port.ts`; `wiring.test.ts` exit 1 (no server.ts).
- Tests: V1M-A01…A04, W01…W02; mutants V1M-X01…X33 (all killed, `node tests/v/list/run-mutants.mjs` 71/71). Real route: `stack.test.ts` S01–S06 6/6 on lab-v1m + infrx-t2i-clickhouse.
- Wiring: WR-V1M-4 documents `LAB_TRACES_API_URL` in `.env.example`; WR-LAB-API-6 adds `grant_ref` to the route.
- Estimate (remaining): 0.5/1/2 h (merge + rerun), confidence medium; basis: thin adapter, proven against the real route.

## Audit log

- 2026-09-28: written at `82db4a9e`.
