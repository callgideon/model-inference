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
| F1 | `codex/f1-gateway-factory` | `e9c7b26`, `97c43ea`, head `1c2e333` | r1 `fix_required` at `164074b` (2 findings, both fixed); r2 killed by restart — **gap**, covered by post-merge review | done 19:12Z | `be7e984` | running |
| E1 | `codex/e1-corpus-bench` | `38b07b5`, head `1c7d96d` | r1 `fix_required` (4 findings); fix agent killed twice, uncommitted partial fix in `corpus/build.py` | — | — | — |
| I1 | `codex/i1-inventory` | `4e052f4`, `2f5332f`, head `ae99662` | r1, r2, r3 `fix_required` (latest: 3 doc-consistency findings) | — | — | — |
| F2-py | `codex/f2-contracts-py` | dispatched 19:13Z from `fab9fbe` | — | — | — | — |
| F2-ts | `codex/f2-contracts-ts` | dispatched 19:13Z from `fab9fbe` | — | — | — | — |

Stage reviews planned: **S0** F1 merged (running) · **S1** G0 contract-ready (F2-py + F2-ts + cross-language vocabulary parity merged; also covers E1/I1 merges) · then one per merge lane of `03-execution-protocol.md` (execution lane, observability lane, console lane) and one before any gate claim (G1, G2).

All other manifest tasks: `planned`, blocked on F2 (or later dependencies) — none started, none skipped.

## Log

- 2026-09-20 19:20Z: Board created after two session restarts. Reconciled from git: no task lost commits; F1 r2 review and E1/I1 fix agents were the interrupted units and are re-dispatched.
