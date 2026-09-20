# Evidence and session handback format

Each implementation session creates a uniquely named report under its track directory, for example `d/D2-<short-implementation-sha>.md`. Do not overwrite another session's report. An evidence report can be committed after the implementation commit it identifies. Keep raw logs/artifacts in durable approved storage, linked by immutable digest; redact secrets and private content.

Required report fields:

| Field | Required detail |
|---|---|
| Task and status | ID, owner/session, implemented / integrated / live-verified / blocked |
| Source | Base SHA, implementation SHA, integrated SHA when available, branch/worktree |
| Requirement coverage | Test IDs and the exact invariant each demonstrates |
| Environment | OS/runtime/service/image/model/profile versions, local/staging/production classification |
| Commands | Exact commands, environment variable names only, exit status, UTC time, seed |
| Results | Observed counts/timings/rows and expected result; failures/skips separately |
| Failure drill | Injection point, durable state before/after, duplicate/retry behavior, cleanup result |
| Artifacts | Paths/links with hashes; no credentials, customer prompts or unredacted signed URLs |
| Changes | Owned paths, contract change request, migration/deploy/rollback implications |
| Limits | Unverified external cases, risks and owner; no inferred pass |
| Handback | Next unblocked task, pending coordinator wiring, unresolved findings |

For a new coordinator session, include Current State Summary, Important Context and Immediate Next Steps with the latest integrated SHA and environment allocation. Mark tasks complete in the shared manifest only through the coordinator; preserve these reports as the audit trail.

## Verification log

- 2026-09-20: Report template only. No future implementation test results have been fabricated.
