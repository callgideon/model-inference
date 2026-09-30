# KNOWN-GOOD-REPROOF-4 (task I8, KNOWN-GOOD-PROOF slice; R151 condition 1 for the operator's SECOND hosted window): both rollback targets re-proven through 0059

Lane `codex/w5-known-good-reproof-4`, base `6ca7879f` (the tip; migrations 0001-0059, the newest
0057 trace_consent_read, 0058 lab_variant_identities, 0059 lab_control_grants_2, all LOCAL-ONLY).
`git diff 6ca7879f HEAD -- apps/app/supabase/migrations infra/runbooks` is empty at every commit of
this lane. Commit `1c986c61` = tests first (red; this file's head7). The sections below are
appended step by step.

## Result

Candidate `6ca7879f` (0001-0059). Driver: the committed `infra/runbooks/schema_proof.py`, unchanged since
`fca3ea38` (sha256 `1a840fe3c958cd04db8042cc7b71ab50b53f7fa4f75c1989f12c624d48e12e62`); SHAPE set = the
recorded 14. No driver change was needed.

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | window (UTC) | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:38:19-00:50:20 | `76f674d48558f935576fd32e2094babfe6928b7becd459e90b03b02da5a0e1e6` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:50:20-01:06:14 | `c3d79bd6f6b4100690351a8a942627a426370322a0d889ab13460a06f7515981` |
| bda15866e5700f3856d7142580da842fba9bbd23 | postgres:16@sha256:33f923b0… + shim | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 01:03:06-01:08:50 (third attempt) | `afbb886bcaa9e933aab5f12d1fc6f04305a24329c69600b5bc5608b4d0b9246f` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | postgres:16@sha256:33f923b0… + shim | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:42:14-00:47:48 | `14e00f61373d1bda180add0fdf1ee1ce401926d4f4eaabaaef4d781ef7384cff` |

All on 2026-09-30. Every run ends `PASS through 0059` after `PASS schema history = the candidate's files
0001-0059`; the probe (`tests/d/test_schema_proof_probe.py`) passes in all four. The counts equal the
through-0056 proof's (KNOWN-GOOD-REPROOF-3-8f0e3c9) suite for suite: 0057-0059 add no SHAPE case.

**Port collisions: two, both on plain/bda1586 (key d10), both rerun.**
1. 00:38:19-00:42:14: 6 suites FAIL (composition_pg, credit_jobstore_conformance, credit_schema,
   e3b_drills, jobstore_conformance, journal) with `could not start infrx-d10-postgres: ... failed to bind
   host port 127.0.0.1:55442/tcp: address already in use` in every failing case (output sha256
   `285dc9af803bdd68d204280277eb9198e9bb67e8fabf9613600db0062ecae9a8`, 6 suite logs kept).
2. rerun 00:47:50-00:53:46: `tests/d/test_pgharness.py` 2 failed (`could not start
   infrx-d10-dharness-postgres: ... 127.0.0.1:55482/tcp: address already in use`), 25 suites PASS
   (output sha256 `97fa920dfd03831a4f376002840a7a5a335b56ccaf0072e6565c003ad0d022d4`).
3. rerun 01:03:06-01:08:50: PASS (the row above); no `address already in use` in any kept output.

Both ports sit inside the host's ephemeral range (`ip_local_port_range` 32768-60999): an outgoing
connection's source port, not another lane's listener (no `infrx-d10-*` container existed before or
after). REPROOF-2 open issue 1, now observed.
