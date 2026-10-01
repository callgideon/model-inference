# E4C run 1 on the live release 41693d5d — 2026-10-01 (window e4c-side-41693d5d-20261001T1755Z)

Certify run `20261001T181109Z` on the pilot box (RELEASE = the installed 41693d5d; hosted 0001–0059; the window's log dir `~/infrx-e4c/20261001T175557Z` on the coordinator host). Artifacts: `models/marlin2b/results/E4C-box-41693d5/run1-20261001T181109Z/` (report.json, certify.log, work/). Here: freeze.json, profiles.sha256, the h6 inventory, cells.log and the final report poll.

| Cell | Verdict | Finding | Classification (05 §7) |
|---|---|---|---|
| preconditions, config-pin, served-build, sop-parity | ok | the box served the report's tree; the pinned engine; 2 clips parity; cap 82 s refused over-cap | — |
| e4b.a.dataset-resume | FAIL | the one recorded problem: "jobs without exactly one hold" (every settled job 0) — the tenant view `infrx.active_holds` lists held/unknown only; the usage-record, Σ charged = ledger fall, reserved and no-hold-left-active checks raised nothing | oracle defect — corrected on main at 6462ed06 (hold-row count dropped); under the corrected oracle this run's problem list is empty |
| e4b.b.envelope (declared 0.5/s) | FAIL | core verdicts pass (failure rate, answered, rejections, client exit); ttft_p95_short 5.69 s (≤ 6), latency_p95 8.86 s (≤ 9); **e2e_p95_per_clip_minute 102.3 s (> 90 s, E4B-protocol §5)**, p50 15.3 over 126 accepted | performance finding at the declared rate (short clips: latency normalised per clip-minute); criteria unchanged mid-flight |
| e4b.b.soak (0.25/s × 14,400 s) | PENDING | failure rate 16/3374 pass, answered 3358, host growth −85.7 MiB pass, latency drift p50 2.86→3.02 pass, client exit 0, VALID; **gpu_growth and reconciled_at_end UNKNOWN** — the series `infrx_gpu_memory_bytes`, `infrx_reconciliation_drift`, `infrx_unsettleable_jobs` come from the observe exporter's textfiles, and step 72 (observe install) never ran on this box (73-observe-status: 0 timers, no /var/lib/infrx/metrics) | environment gap — install observe (72), rerun the soak |
| e4b.b.overload (burst 32) | FAIL | bench cell not VALID: driver lag 1.020 s > the profile's 1.0 s bound (the client limited the offered load by 20 ms) | invalid measurement — rerun the cell |

Not run in this window (the stop at the cells step): h6-after-s4, the E1B cells (e1b, wc6a/wc6b, wc7), the tenant-2 key and journeys, wc9, drift-end, cleanup. wc0-stop ran afterwards (`--only wc0-stop`). The +40,000 CREDIT test allocation on the certify org stands. The two outage drills were answered skip (NOT RUN). O4–O6 and the canary: skipped by decision.

## Verification log

- 2026-10-01T22:37Z: written by the coordinator from report.json and the window logs; secrets scan of the copied files clean.
