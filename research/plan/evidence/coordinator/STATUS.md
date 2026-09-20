# Task pipeline board (coordinator-owned, restart reconciliation source)

A task is complete only when every column is filled. A session restart can kill an agent between any two columns, so after any restart the coordinator re-derives this board from git (`git worktree list`, `git log <base>..<branch>`, `git status` in each `codex-*` worktree) and resumes each task at its first empty column. An uncommitted change in a task worktree means an agent died mid-edit: the next agent for that task reads it and continues. Nothing is marked from an agent's claim alone.

| Column | Meaning |
|---|---|
| Impl | implementation + evidence commits exist on the task branch |
| Review@head | an independent review returned `pass` **on the current branch head** (a review of an older head does not count) |
| Coord | coordinator reran the task commands and read the diff |
| Integrated | merge commit on `claude/infrx-impl`, suites rerun on the merged tree |
| Post-merge | independent critical review of the merged stage completed, findings dispositioned |

| Task | Branch | Impl | Review@head | Coord | Integrated | Post-merge |
|---|---|---|---|---|---|---|
| F1 | `codex/f1-gateway-factory` | `e9c7b26`, `97c43ea`, head `1c2e333` | r1 `fix_required` at `164074b` (2 findings, both fixed); r2 killed by restart — **gap**, covered by post-merge review | done 19:12Z | `be7e984` | **pass** 19:40Z (S0) |
| E1 | `codex/e1-corpus-bench` | `38b07b5`, head `1c7d96d` | r1 `fix_required` (4 findings); fix agent killed twice, uncommitted partial fix in `corpus/build.py` | — | — | — |
| I1 | `codex/i1-inventory` | `4e052f4`, `2f5332f`, head `ae99662` | r1, r2, r3 `fix_required` (latest: 3 doc-consistency findings) | — | — | — |
| F2-py | `codex/f2-contracts-py` | dispatched 19:13Z from `fab9fbe` | — | — | — | — |
| F2-ts | `codex/f2-contracts-ts` | dispatched 19:13Z from `fab9fbe` | — | — | — | — |

Stage reviews: **S0** F1 merged — pass · **S1** G0 contract-ready (F2-py + F2-ts + cross-language vocabulary parity merged; also covers E1/I1 merges) · then one per merge lane of `03-execution-protocol.md` (execution lane, observability lane, console lane) and one before any gate claim (G1, G2).

All other manifest tasks: `planned`, blocked on F2 (or later dependencies) — none started, none skipped.

## Log

- 2026-09-20 19:20Z: Board created after two session restarts. Reconciled from git: no task lost commits; F1 r2 review and E1/I1 fix agents were the interrupted units and are re-dispatched.
- 2026-09-20 19:40Z: **S0 post-merge critical review of F1: pass, no blocking findings.** Independent harness: old gateway vs legacy shim vs bare `create_app()`, 54 scenarios + config records, 8 environments (empty, all non-default, empty strings, malformed numerics), 0 differences; sensitivity drill (re-injected the `97c43ea` defect) detected. 29 passed; baseline test files byte-identical; order-dependence identical at base and head (pre-existing: `test_gateway_auth` must import first); import probe over all 13 `infrx` modules recorded no env read/client/app/file/queue; 400-request stress at `MAX_INFLIGHT=8` identical (peak 8, final 0); `git revert -m 1 be7e984` clean in a scratch clone (note: it also removes the F1 evidence files — keep them on any rollback). Console rerun on the merged tree: 4 passed, lint exit 0, `apps/app` byte-identical to `25b9829`.
  Dispositions of nonblocking notes: (a) never use `unittest.mock.patch.object` on the `gateway` shim — use `create_app()` or plain assignment → rule for all track briefs; (b) names the shim rebinds on modules (`hmac`, `put`, `cost`, `address_allowed`, `THINK`) are process-wide in tests → track tests use `create_app()`, never the shim; (c) `clock` default binds `time.time` at definition → tests inject a clock; (d) pre-existing inflight leak when ASGI `send` fails before the generator starts → owner G2 (API-STREAM), carried in its brief; (e) `Settings.usage_failed_log` derived once → note for config consumers; (f) strengthen the import-side-effect test to walk the package → F2-py follow-up; (g) README/HANDOFF still describe logic in `gateway.py` → README in F2-py scope, HANDOFF by coordinator at G0.
