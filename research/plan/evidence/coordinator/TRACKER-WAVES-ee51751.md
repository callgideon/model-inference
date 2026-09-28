# TRACKER-WAVES — post-launch waves in the tracker (WR-LW0-3) + LW0 minors C3/C4

- Lane tracker-waves (wave LW0 carry-over), branch `codex/w5-tracker-waves`, worktree `.claude/worktrees/codex-w5-tracker-waves`, base `49393c56`, implementation head `ee51751a`.
- Brief: wave-5 plan §2, §3 item 9 (second sentence: "Then extend the HTML tracker with LW0-LW6"), §4 (wave table), §5 COMMON; coordinator notes (deliverables 1–4); LANE-RULES.md.
- No docker, no hosted system, no apps/ path, no progress-state.json, tasks.json or 08 §10 edit.

## Changed paths

| Path | Change |
|---|---|
| `research/plan/consumer-v1/07-post-launch-waves.md` (new) | The checked-in wave map: plan §4 table verbatim plus an LW0 row and a "Gate task (lane)" column; the grammar the loader reads; what the tracker shows per wave; audit log. |
| `research/plan/consumer-v1/README.md` (new) | Index line for 01–07 (the directory had no README). |
| `research/plan/scripts/progress.py` | `load_waves` (table parser), `Model(..., waves=None)` loads the map, wave-map checks in `validate`, `wave_views` + `wave_eta`, a "Post-launch waves" section in HTML (+ nav link) and PROGRESS.md, tone for `EXIT MET`. |
| `research/plan/scripts/test_progress.py` | class `Waves`, 5 cases. |
| `research/plan/scripts/test_mutants.py` (new) | Stdlib mutant runner for the Waves decisions: 31 mutants, each one textual edit executed as the `progress` module the cases see. |

## Design (decisions, each killed by a mutant)

- **Map (deliverable 1).** One table row per wave; lanes `name [ID, ID (note)]` separated by ` · `, parentheticals are notes, italic lanes (`*discovery*`) do not gate, a non-manifest ID must be a slice `<manifest ID>-<SUFFIX>` (L2-SQL, L3-SQL, C2-RPC, G5-SQL, I2L-OBS), gate column `ID (lane)` or `(lane)`. An empty/mangled map raises instead of rendering "no waves".
- **check.** Errors: a deferred/activated task in no wave; a task in two waves; a backend/app/baseline task listed; an ID that is neither a manifest ID nor a slice of one; a gate task outside its wave. On the live overlay: 0 wave errors (57 IDs, each once).
- **Section (deliverable 2).** Per wave: implemented k / n over the wave's manifest IDs (slices shown, never counted; non-gating discovery counted in n); tasks by lane in order with the manifest status pill; overlay lanes (slice `(wave5 <wave>`, ID = wave ID, or task in the wave) with activity; exit state; ETA.
- **Exit state.** `EXIT MET` only when every gating task is implemented and every gate lane (matched by `lane <name>` with no `[\w-]` after it, so `lab-operate-x` is not `lab-operate`) exists and is complete; otherwise `PENDING` with reasons. Deviation from the brief's wording "accepted", deliberately: the existing oracle `test_implemented_e4c_without_decision_is_not_green` requires the word ACCEPTED to appear only for recorded gate decisions, and a wave exit is a tracker reading, not a decision (stated in the section note and in 07).
- **ETA.** The milestone arithmetic of `forecast` with `eta_params`, over the wave's live lanes (the LW lanes are task-less, so lanes are the unit): per lane `hours × (1 + review_rework_fraction) + integration_h_per_task`; wall = max(longest lane, serial merges, effort / free slots); confidence = the lowest. Never a date when an open input blocks an open task ("blocked pending"), when a live lane has no/malformed estimate, or when open work has no live lane ("unknown"). A met wave reads `done: exit met`.
- **Unchanged rest.** Section, nav link and map checks exist only when a map is loaded; the test renders with the map and with `waves=[]` and asserts the HTML/Markdown are byte-identical once the section and link are removed, and that `summaries` and categories are equal.

## Commands (exit codes, counts)

| Command | Base `49393c56` | Head `ee51751a` |
|---|---|---|
| `cd research/plan/scripts && python3 -m unittest test_progress.Waves` (seam tests written first, before any implementation) | — | **5 errors** (fails-before): `AttributeError: module 'progress' has no attribute 'load_waves'` ×2, `… 'wave_views'` ×2, `TypeError: Model.__init__() takes from 4 to 5 positional arguments but 6 were given` ×1 |
| `python3 -m unittest test_progress` | 37 OK | 42 OK (exit 0) |
| `python3 -m unittest test_mutants -v` | — | 4 OK: list well-formed (31 anchors each found exactly once), every Waves case named by a mutant, unmutated tracker passes every case, **31/31 mutants killed** (exit 0) |
| `python3 -m unittest test_progress test_validate_plan test_mutants` | 44 run, 1 error | 53 run, 1 error — `test_validate_plan::test_app_cannot_dispatch_early` KeyError at test_validate_plan.py:32, failing at base too (recorded in LW0-7894418.md), not owned, untouched |
| `python3 research/plan/scripts/progress.py check` (live overlay rev 233) | PASS, 0 errors, 32 warnings | PASS, 0 errors, 32 warnings (exit 0) |
| `python3 research/plan/scripts/progress.py render` | — | exit 0; `<section id="waves">` ×1, nav link ×1, rows LW0 LW1 LW2 LW3 LW4 LW5 LW6 C1 C2 C3; `## Post-launch waves` ×1 in PROGRESS.md. Rendered outputs restored afterwards (coordinator-owned generated files; not committed) |
| `python3 research/plan/scripts/validate_plan.py` | PASS | PASS: 949 local Markdown links across 371 documents; ledger current (exit 0) |
| `ruff check progress.py test_progress.py test_mutants.py` | 4 × E731 (pre-existing lambdas) | same 4, no new finding |

Not applicable: `make api-test`/console/lab targets and real-service suites (no apps/ path, no SQL, no docker; the plan scripts are not in `make check`).

### What the live render shows (overlay rev 233, 2026-09-28T20:5xZ)

| Wave | Implemented | Exit | ETA |
|---|---|---|---|
| LW0 | 0 / 0 | EXIT MET (lane LW0 complete) | done |
| LW1 | 7 / 10 (X1/X3/X5 discovery planned, do not gate) | EXIT MET | done |
| LW2 | 10 / 11 | PENDING: L4 planned in the manifest | forecast from lane V1M (still `blocked` in the overlay) |
| LW3 | 4 / 10 | PENDING: I2L, E3L, C3L, C2, G4T, R1 planned | forecast from W5-EVAL-RUNNER (review) |
| LW4 | 2 / 12 | PENDING: 10 planned | forecast from lab-sql-lw4 + lab-sql-integration-2 |
| LW5 | 3 / 7 | PENDING: E5L, E6L, I6, I7; lab-observe, lab-evaluate in review | forecast (composition-2 longest) |
| LW6 | 0 / 2 | PENDING: E7L, E8L; lab-improve running, lab-rollout review | forecast |
| C1 / C2 / C3 | 0 / 1, 0 / 1, 0 / 3 | PENDING (conditional; no lanes) | unknown: no live lane |

Observations for the coordinator (tracker readings, not changed here): the task-keyed lanes `D7` and `V1M` are still `blocked` although both tasks are implemented (they feed LW1/LW2 lane lists and LW2's ETA); `L4`, `I2L`, `C2`, `G4T`, `R1`, the LW4 ten, `I6`/`I7` are `planned` in tasks.json although their lanes are complete — manifest status updates are coordinator work.

## LW0 minors C3/C4 (deliverable 3): not tracker items — recorded, not applied

- **0-LW0-C3** (D10 split case hidden by the module Docker skip, no mutant; `apps/infrx-api/tests/d/test_upgrade_d10.py:31-55`). Outside this lane (apps/ path, brief forbids). Still open at `49393c56`. Wiring request **WR-TW-1** below.
- **0-LW0-C4** (R150 lets `0027` go to the backend R147 follow-up; R151's title/body freeze hosted at 0026 and call every 0027+ a Lab migration; `08-contracts-v1-encoding.md` R150/R151). The brief forbids 08 §10 edits. Still open. Proposed ruling text (unnumbered) under WR-TW-2.

## Wiring requests

1. **WR-TW-1 (coordinator or the next D-owning lane; closes 0-LW0-C3).** Move the pure case out of the Docker-skipped module: new `apps/infrx-api/tests/d/test_upgrade_split.py` = `from .test_upgrade_d10 import _split` + the case `test_a_later_migration_is_neither_the_0018_world_nor_d10` moved verbatim; delete it from `test_upgrade_d10.py`. Proof: `DOCKER_HOST=unix:///nonexistent uv run --frozen pytest -q tests/d/test_upgrade_split.py -rs` → 1 passed (today: SKIPPED). A mutant is not possible in the D lists (they mutate SQL/`infrx`, not test helpers); record that in the D evidence.
2. **WR-TW-2 (proposed ruling, amends R151; closes 0-LW0-C4).** "Lab migrations are local-only. Any hosted apply past 0026 — the R147 backend follow-up that R150 lets take the next number included — needs all three conditions of R151 (known-good re-proof, a new `EXPECTED_PENDING`, an operator window). R150's number-at-merge rule is unchanged. The D10 upgrade split keeps every file from 0027 on out of both the 0018 world and D10, whichever product owns it; a backend 0027 therefore brings its own upgrade case."
3. **WR-TW-3 (progress-state).** Add a task-less lane `TRACKER-WAVES` (slice `(wave5 LW0 lane tracker-waves) WR-LW0-3 + LW0 minors`) so `updates/TRACKER-WAVES-20260928T2052Z.json` ingests (as WR-LW0-1 did for LW0); then `apply-updates` renders the section into progress.html/PROGRESS.md.
4. **WR-TW-4 (optional, docs).** `06-progress-tracker.md` or program 22 §"Authoritative package" may link `consumer-v1/07-post-launch-waves.md` (not owned here; the new consumer-v1/README.md already indexes it).

## Deviations

- Paths: `research/plan/scripts/test_mutants.py` is outside the listed owned paths; the implementer instructions require the lane's own runner-visible `test_mutants.py`, and the plan scripts had no mutant list. The consumer-v1 README did not exist, so the "index line" is a new two-section README.
- The wave exit label is `EXIT MET`, not "accepted" (reason above).

## Estimate (remaining)

Optimistic 0.25 h / likely 0.5 h / pessimistic 1.25 h, confidence medium. Basis: the TRACKER lane's original 2/4/8 h at a third (0.7/1.3/2.7 h total); implementation, tests, mutants and evidence are done; what remains is one review round, the WR-TW-3 overlay lane and the coordinator's merge + render.
