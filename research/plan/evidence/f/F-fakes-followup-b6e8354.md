# F fakes follow-up — R93 and D4 request 10 (evidence)

## Task and status

- **Task:** F contract-fakes follow-up lane (re-dispatch; the first agent died before its first commit). R93 (E3B2 request 3) and D4 request 10a/10b; 10c assessed and not done.
- **Owner:** Opus 5.5 implementation session. Fable coordinates.
- **Status: implemented** (fakes, exported conformance cases, mutants). **Not integrated.** The new exported cases were run on the fakes only; this lane used no Docker, so their PostgreSQL runs (`tests/d/test_{jobstore,streamstore}_conformance.py`) are the integration's (Limits 1).

## Source

| | |
|---|---|
| Base (integration head, D4 merged) | `ae0f2a2ef1e49e2b40de1c60ae046bf3739d7d08` |
| Implementation SHA | `b6e8354dfc070c890a106200e799ac019b5692c1` |
| Branch / worktree | `codex/f-fakes-followup` / `.claude/worktrees/codex-ffakes` |
| Commits (`git log --oneline ae0f2a2..b6e8354`) | `10df1d0` item 1, `7981619` item 2, `b6e8354` item 3 |

## Per-item table

| Item | Commit | Killing case (exported unless noted) | Mutants (`tests/contracts/mutants.py`) | Oracle / ruling |
|---|---|---|---|---|
| 1. R93: the fake's inference requeue publishes its `IndexEvent` under the id of the fresh `inference_dispatch` row it inserts (0016 `infrx.index_event(o, j)` already does). | `10df1d0` | `dur_outbox__a_requeue_publishes_its_own_fresh_dispatch_row` (jobstore): the event id is exactly the one new `inference_dispatch` row's id and not the first row's; and a lapsed preparation lease adds one `prepare_dispatch` row with a new id (OB-5b). | `requeue_event_id_minted_apart` (restores `self.ids.event_id()`); `lapsed_preparation_reopens_its_old_row` (re-publishes the acknowledged row). Anchor of `lost_inference_emits_a_prepare_dispatch` follows the moved emit. | DUR-OUTBOX, R93, D2 OB-5b |
| 2. D4 10a (M1): `FakeStreamStore.append` refuses a batch holding a NUL character (key or string, any depth), a lone UTF-16 surrogate, NaN or ±Inf with `JournalWriteFailed`, before fencing, storing or charging anything. Rule copied from D4's `infrx.state.journal._journalable` (contracts never import `infrx.state`; the docstring names the source). | `7981619` | `dur_output__an_unjournalable_event_refuses_the_whole_batch` (streamstore): nine payloads × bad event last/first/middle; nothing stored; after cancel the job's stored bytes are its terminal event's alone; `"\\u0000"` text, `-0.0` and `1e308` stored. An untyped answer (jsonb's raw 22P05/22P02, the fake's old `UnicodeEncodeError`) is reported as an assertion naming it. **Agreement test** (tests/d, D's file, the flip D4 asked for): `tests/d/test_journal_units.py::test_append__refuses_what_jsonb_cannot_store_before_sending_it` now asserts the fake and `PgStreamStore` refuse exactly the same payloads over one table (6 UNJOURNALABLE + 3 SURROGATES refused, 3 JOURNALABLE stored); it used to assert the fake STORES the six. | `unjournalable_payload_stored` (rule dropped); `only_the_last_event_is_checked` (`events[-1:]`); `surrogates_are_journalable`; `keys_are_not_checked`; `non_finite_numbers_are_journalable`; `the_escape_text_is_refused` (`"\\u0000"` text refused); `a_refused_batch_is_charged` (bytes stored before the refusal). | DUR-OUTPUT, D4 M1/A1/A2/A4 |
| 3. D4 10b (J4): after a full prune the journal continues past `pruned_to`. Appends and the terminal event number from `_last_sequence` (chunks of the generation plus the watermark of the same generation - 0017's `v_next` and terminal trigger); "expired" is D4's definition, a watermark and no chunk left (`_expired`), replacing the for-ever `expired_jobs` flag. | `b6e8354` | `dur_output__a_journal_pruned_to_nothing_continues_past_its_watermark` (streamstore): prune (1,1..2) to nothing, append → (1,3); `read_owned(None)` is `replay_gap` 410; from `1-2` the page is `c`; prune again to nothing, cancel → terminal event at (1,4), readable from `1-3`. | `pruned_journal_restarts_at_one`; `the_terminal_event_restarts_below_the_watermark` (the pre-change terminal numbering); `a_pruned_journal_expires_for_ever` (also named on `dur_output__a_pruned_prefix_is_an_explicit_replay_gap`). | DUR-OUTPUT, D4 J4 / Limit 4(a) |
| 4. D4 10c (optional): jsonb byte measure + admitted-reservation ceiling. | — | — | — | **Not done** (below) |

**R93 wording.** The ruling says "a preparation lease lapses ... prepare_dispatch". In both stores a lapsed *preparation* lease publishes no `IndexEvent` at all (0016 returns `[]`; pinned by `dur_output__every_requeued_candidate_carries_the_right_kind`); the divergence E3B2 request 3 and finding F7 name is the *inference* requeue (`recover` → fresh `inference_dispatch` + `IndexEvent`). Item 1 fixes that branch (E3B's exact diff) and pins the preparation half as "fresh row, new id", which both stores already did.

**Item 4 decision: not done.**
- **(a) jsonb measure.** Not a few lines: it needs a jsonb text renderer (`": "`/`", "` separators, jsonb key order - shorter keys first -, `numeric` fixed point with `-0` normalised to `0`, PostgreSQL's string escaping), and it changes the `bytes` of every chunk the fake reports, which other tracks' fake-based tests and the byte arithmetic of `dur_cap__a_job_cannot_store_past_its_journal_reservation` rely on. **Delta remains** = D4 Limit 5: the fake measures compact JSON; PostgreSQL stores `octet_length(payload::text)`, one byte more per separator and exponent-form numbers in fixed point (`{"logprob": 1e300}` 18 vs 314). An event under `JOURNAL_EVENT_MAX_BYTES` on the fake measure can be over it on PostgreSQL.
- **(b) admitted-reservation ceiling (M4).** Not observable through the port today, so a change would have no killable mutant: the fake's `_Journal` keeps the limits the store was built with - `retune` replaces `jobs.limits`/`stream.limits`, never `jobs.journal.limits` - so every job's ceiling already equals its admission-time reservation and the append-time global term cannot fire. Probe (`/tmp/claude-1000/ffakes/m4_probe.py`, exit 0): `append of 8000 B after retune to 4096: [8014]`, `jobs.limits.journal_job_reserve_bytes = 4096 | jobs.journal.limits.journal_job_reserve_bytes = 16777216`, `reservation of a job admitted after the retune: 16777216`. **Delta remains:** a retune of `JOURNAL_JOB_RESERVE_BYTES`/`JOURNAL_TOTAL_BYTES` does not reach the fake's journal at all, so a job admitted after it still reserves 16777216 where PostgreSQL reserves the retuned value. Fix when wanted: `retune` sets `jobs.journal.limits`; `_Journal.store` takes its ceiling from `self.reserved[job_id]` and drops the append-time global term; one retune case with two mutants.

## Requirement coverage

- **DUR-OUTBOX / R93 / OB-5b:** `dur_outbox__a_requeue_publishes_its_own_fresh_dispatch_row` - one requeue event, whose id is the single new `inference_dispatch` row (order-independent: ids before vs after `recover`); a lapsed preparation lease adds exactly one `prepare_dispatch` row and all ids stay distinct.
- **DUR-OUTPUT / D4 M1:** `dur_output__an_unjournalable_event_refuses_the_whole_batch` - `JournalWriteFailed` for NUL (string, key, nested list), lone surrogates (string, key, list), NaN, +Inf, nested -Inf, wherever the bad event sits; empty page; stored bytes after cancel equal the terminal chunk's; ordinary JSON incl. the escape text, -0.0 and 1e308 stored.
- **DUR-OUTPUT / D4 J4:** `dur_output__a_journal_pruned_to_nothing_continues_past_its_watermark` - numbering and the terminal event continue past the watermark; a replay from the start is an explicit gap, never an empty page or `journal_expired`, while chunks exist.
- **Fake/adapter agreement (M1):** `tests/d/test_journal_units.py::test_append__refuses_what_jsonb_cannot_store_before_sending_it`.

## Environment

Local dev box only (Linux 7.0.0-1010-aws, Python 3.12.3, uv 0.11.8, pytest 8.4.2, pydantic 2.13.5; `make api-env` → `uv sync --frozen --all-extras`, "Checked 41 packages"). No Docker, no PostgreSQL, no Valkey, no hosted project, no AWS, no secrets.

## Commands (from `apps/infrx-api`, tree `b6e8354`)

| Command | UTC | Exit | Tail (quoted) |
|---|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py` | 14:41:37Z–14:43:08Z | 0 | `1051 passed in 90.11s (0:01:30)` (head `ae0f2a2`: `1048 passed in 103.85s`; +3 = the three new exported cases on the fakes) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/contracts/test_mutants.py` (detached, `/tmp/claude-1000/ffakes/mutants-all.log`) | 14:41:25Z–15:01:43Z | 0 | `438 passed in 1216.99s (0:20:16)` - every one of the 418 declared mutants (`python -m tests.contracts.mutants --list`: `418 mutants over 214 named cases`; this lane added 12 `_m(` entries - 380 → 392 in the file) killed, plus the list's own checks |
| `uv run --frozen pytest -q -p no:cacheprovider tests/d/test_journal_units.py` (no Docker) | 14:43:16Z | 0 | `6 passed in 0.31s` |
| `uv run --frozen python -m tests.d.code_mutants_d4` (the flipped test is its target) | 14:43:17Z–14:43:56Z | 0 | `17/17 killed` |
| `uv run --frozen pytest -q -p no:cacheprovider tests/g` | 14:44:01Z–14:46:12Z | 0 | `405 passed, 2 warnings in 129.23s (0:02:09)` - **same count as the head** (`ae0f2a2`: `405 passed, 2 warnings in 108.21s`); the relay's fake journal never prunes to nothing nor sends an unjournalable payload in those tests |
| `uv run --frozen pytest -q -p no:cacheprovider tests/w -x` (tree `10df1d0`, item 1: the reaper uses `recover`) | after 14:29:04Z (start not recorded) | 0 | `157 passed in 151.47s (0:02:31)` |
| targeted, per item before each commit: `uv run --frozen python -m tests.contracts.mutants <names>` | before 14:29:04Z / 14:37:04Z / 14:39:47Z (the commits) | 0 | item 1 `4/4 killed`; item 2 `7/7 killed`; item 3 (+ five neighbouring stream mutants) `8/8 killed` |

## Results

- New exported cases: 3 (one jobstore, two streamstore). New mutants: 12 (2 + 7 + 3); one anchor updated.
- No skips or failures in any run above. The full-list mutant run is quoted in the table.

## Failure drill

Not applicable (fakes). The refusal drill for M1 is the case itself: a refused batch leaves no chunk and no stored bytes.

## Artifacts

Logs (local, not committed), sha256: `base-contracts.log` 197432933a7a562aaabcc4a316cedc025ffdb9e945742420ca78b0556658dbbd, `base-g.log` 0120510b08e907555e9cc13475e32f69ff4a07f65995d2baa51c92c969cddc60, `contracts.log` 28be94f0d2fa21073be87aa0bc099d98d83185c03d423db5d00dd6e1ac597c1a, `d-units.log` 3859a8a3532362c90f9d8855c22c69d3913aa5222ea21b52d54d0b578118c15b, `g.log` a490918838d3de4ba629341a3b3b5d6f5be6e4575e1d67a59d5b0d549ebf17cf, `mutants-all.log` 5eecc2c7816f036dfb81aca40757e6c52e1023ab54e88de0cacc67988df958a8, `m4_probe.py` 6c57de623ffca05a905f6c05855264abb2bcdc9a1eb9b1642eb46a79a03a2ba8 (all under `/tmp/claude-1000/ffakes/`).

## Changes

- `apps/infrx-api/infrx/contracts/fakes/state.py`: `import math`; module `_journalable` (new); `FakeJobStore._recover_job` (inference requeue branch); `FakeStreamStore.__init__` (`expired_jobs` removed), `_expired` and `_last_sequence` (new), `append`, `read_owned`, `finalize_in_transaction`, `write_terminal`, `expire`. `lookup`/`_replay` untouched and nothing reformatted.
- `apps/infrx-api/infrx/contracts/conformance/jobs.py`: three cases, registered in `jobstore_cases()`/`streamstore_cases()`.
- `apps/infrx-api/tests/contracts/mutants.py`: 12 mutants, one anchor.
- `apps/infrx-api/tests/d/test_journal_units.py` (D's file; the flip D4 request 10a asked F to make): the pinned "fake stores them" assertion became the fake/adapter agreement over one table.
- No port, record, migration or adapter change. Not touched: `tests/integration` (E3B2's).
- Trial merges (`git merge-tree --write-tree`, no worktree change): with `codex/g2-chat-relay` exit 0; with `codex/e3b-phase2-gate` and `origin/codex/e3b-phase2-gate` exit 0.

## Limits

1. **PostgreSQL runs of the new exported cases not made here.** They join `tests/d/test_jobstore_conformance.py` and `tests/d/test_streamstore_conformance.py` (strict partitions: a case not in `PENDING` must pass). Expected to pass from the code, not from a run: the requeue event is 0016's `infrx.index_event(o, j)` of the inserted row; the M1 refusal is `PgStreamStore.append`'s pre-send check; the stored bytes after cancel are `journal_stored_bytes` = the terminal chunk's `octet_length` (0011 `journal_bytes_charged` with the reservation released); J4 is 0017's `v_next` (`greatest(..., journal_pruned_sequence)` for the same generation), the terminal trigger's `journal_pruned_sequence + 1` and `read_journal`'s `replay_gap` below the watermark. `1e308` is compared on the way back by its `content` only, because jsonb re-renders it in fixed point.
2. **E3B2 dr04[fake] goes red on a tree with both branches** until E3B2 deletes its fake-only branch: `tests/integration/backend/test_drills.py` (on `codex/e3b-phase2-gate`, ~l.256) asserts for the fake `events[0].event_id not in (first, again)`, and its own comment says it "goes red the day the fake is fixed". Keep the `else` assertion (`events[0].event_id == again != first`) for both backends.
3. Item 4 deltas (measure; retune not reaching the fake's journal) remain, as described above.
4. The G2 relay tests were run against this fake at 405 (unchanged); G2's own branch tests were not run on a merged tree.

## Handback

- Next: coordinator merge; then run `tests/d/test_jobstore_conformance.py` and `tests/d/test_streamstore_conformance.py` on both images (Limits 1).
- **E3B2:** drop dr04's `if backend == "fake":` branch (R93) at its merge - it is red against this fake.
- **G2:** this lane changed, in `infrx/contracts/fakes/state.py`, `_recover_job` (requeue event id), `FakeStreamStore.append` (journalable refusal before the lock; sequence from `_last_sequence`), `read_owned`/`finalize_in_transaction` (`_expired()` instead of `expired_jobs`), `write_terminal` (sequence), `expire` (no flag), the new `_journalable`/`_expired`/`_last_sequence`, and removed `FakeStreamStore.expired_jobs`. Trial merge with `codex/g2-chat-relay` is clean.
- **D:** Limit 4(a) J4 and 4(c) M1 fake deltas are closed; D4's evidence text may drop them. Limit 4(b) M4 and Limit 5 stand (item 4).
- **W:** request 9's note "the fake still stores it" is obsolete: the fake now refuses NUL / lone surrogate / NaN / ±Inf like `PgStreamStore`.

## Verification log

- 2026-09-23: Items 1–3 implemented at `b6e8354` on `ae0f2a2`; item 4 assessed and declined with a probe; commands above run on the committed tree; no Docker, hosted or shared environment touched.
- 2026-09-23: Full contracts mutant list finished (15:01:43Z, exit 0, 438 passed); evidence completed.
