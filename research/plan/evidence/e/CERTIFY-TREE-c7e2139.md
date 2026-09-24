# CERTIFY-TREE: the tree under test where the checkout has no git (`c7e2139`)

Lane: CERTIFY-TREE (Opus fix lane; coordinator Fable). Branch `codex/certify-tree` from
`origin/main` `2d4a88b`. Commits `2043223` (item 1) and `c7e2139` (item 2), plus this report
(`7bb7b41`). The coordinator later extended the lane with item 3, the deployed duration cap
(`e4a7106`), and item 5, R106 (`7ac77c7`, `0df3fa7`); both sections are below.
No rebase, reset, amend, stash or push. No box, AWS, hosted Supabase, secret, stack or
PostgreSQL was used. Every case below runs locally with `--no-stack` forms and stubs.

## What was wrong

The first E4B box run ran inside `infrx-runtime:422631591845fbd66b590c73d5ff4150318d9d7a`,
which has no `git`. Its `e4b.b.served-build` cell FAILED with "the gateway serves 4226315…,
the report's tree is None". `run.git_head()` answers `{"sha": null, "dirty": null}` when git
is missing (review F2, by design). The cell compared the gateway's `infrx_build_info{revision}`
with that `null` and never used `--release-sha`. The first protocol's step 5 expected this
("if the image has no git, the report's git_head reads null and step 7 records the SHA"), but
no code followed it. The cell also read only the gateway's gauge. The worker has published its
own gauge on 8002 since I2B-R4 (`infrx_build_info{process="worker",…}`).

## Fix

| Item | Commit | Change | Killing case |
|---|---|---|---|
| 1 | `2043223` | `certify.release_head`: when the host has **no git binary** or the checkout has **no `.git`**, the tree under test is `--release-sha`. Both `git_head` and `git_head_end` read `{"sha": <release>, "dirty": null, "source": "--release-sha (no git)"}` (or `(no .git)`). The served-build detail names that source. The state stays unknown, so `release-identity` still FAILs (R97, §6.3). Git that runs is never overridden. If git exits non-zero, the tree stays unknown. If git answers a SHA other than `--release-sha`, both `release-identity` and `e4b.b.served-build` FAIL. Protocol amendment 5(a). | `test_e4b_a_checkout_without_git_is_the_named_release_and_the_report_says_so` |
| 2 | `c7e2139` | `e4b.b.served-build` also reads the worker's gauge. `--box` now needs `--worker-metrics-url` (box: `http://127.0.0.1:8002/metrics`). The gateway's and the worker's revisions must each be the report's tree. Each is read from a page whose `process` label is that process's: `scrape` now returns the label, so a worker URL that points at the gateway's page FAILs. Protocol amendment 5(b). | `test_e4b_the_box_report_is_tied_to_the_build_the_gateway_serves`, `test_e4b_scrape_reads_the_series_the_soak_judges`, `test_e4b_a_box_run_names_its_release_and_reads_its_metrics` (the three `--box` flags, one missing at a time), item 1's case extended |

`test_e4b_only_a_box_run_with_its_preconditions_met_is_a_measurement` changed only its argv
(`--worker-metrics-url`) and the arity of its `served_build_check` stub.

Probe through `main()`, with no git on `PATH` and the real worktree:
`--box --release-sha 4226315…`, with `file://` pages for the gateway and the worker that carry
that revision. Preconditions, config pin, hashes and engine checks were stubbed. Result:
`git_head` = `git_head_end` =
`{"sha": "422631591845fbd66b590c73d5ff4150318d9d7a", "dirty": null, "source": "--release-sha (no git)"}`.
`e4b.b.served-build` PASSED with "the gateway and the worker serve the report's tree 4226315…
(--release-sha (no git)), the gateway from the release image". `release-identity` FAILED with
`["the tree at the start is of unknown state", "the tree at the end is of unknown state"]`.
The exit was 1.

## For the coordinator

- **The box command changes.** Step 5 needs
  `--worker-metrics-url http://127.0.0.1:8002/metrics`. Without it, `--box` is refused
  before anything runs.
- **`release-identity` still FAILs in a git-less image.** This is deliberate: a tree read
  from `--release-sha` has an unknown state, and R97 counts a certification only at one clean
  SHA. Round 2's step 0.7 closes this: an `infrx-certify:<release>` image with git, plus
  `safe.directory` for the read-only `/repo`. Accepting a git-less box report instead
  (`--release-sha` plus a host-side `git status` recorded in step 7) is a ruling for the
  coordinator. The runner does not make it.
- **Not done:** the worker's `image` label is not compared with the release image. Only the
  gateway's image is (`INFRX_CERTIFY_GATEWAY_IMAGE`). Add that comparison if the worker ever
  runs a different image from the gateway.

## Mutants and their death lines (re-derived at `c7e2139`)

Each mutant was applied alone to a throwaway copy with the shared runner's own
`_copy`/`_prepare`/`_pytest` (`--tb=line`), and its named case was run. Every death is an
`AssertionError` or a `Failed` in a test file.

| Mutant | Death line |
|---|---|
| `no_git_tree_unnamed` | `test_certify.py:902: AssertionError: assert (None == {'dirty': None, 'sha': 'eeee…', 'source': '--release-sha (no git)'})` |
| `git_overridden_by_release_sha` | `test_certify.py:916: AssertionError: assert {'dirty': None, 'sha': 'eeee…', 'source': '--release-sha (None)'} is None` |
| `no_git_binary_ignored` | `test_certify.py:902: AssertionError: assert ({'dirty': Non...ha (no .git)'} == {'dirty': Non...sha (no git)'}` |
| `no_dot_git_ignored` | `test_certify.py:914: AssertionError: assert None == {'dirty': None, 'sha': 'eeee…', 'source': '--release-sha (no .git)'}` |
| `named_tree_recorded_clean` | `test_certify.py:902: AssertionError: assert ({'dirty': Fal...sha (no git)'} == {'dirty': Non...sha (no git)'}` |
| `named_tree_source_dropped` | `test_certify.py:902: AssertionError: assert ({'dirty': Non...eeeeeeeeeeee'} == {'dirty': Non...sha (no git)'}` |
| `start_sample_ignores_release` | `test_certify.py:905: AssertionError: assert ('FAIL', ['th...ree is None']) == ('PASS', 'the...elease image')` |
| `end_sample_ignores_release` | `test_certify.py:909: AssertionError: assert {'dirty': Non...sha (no git)'} == {'dirty': None, 'sha': None}` |
| `served_detail_hides_the_source` (re-anchored in item 2) | `test_certify.py:905: assert ('PASS', 'the...elease image') == ('PASS', 'the...elease image')` |
| `worker_gauge_unread` | `test_certify.py:943: assert ['the gateway...tree is None'] == ['the gateway...tree is None']` |
| `worker_read_at_the_gateway_url` | `test_certify.py:966: AssertionError: assert ('e4b.b.served-build', 'FAIL') == ('e4b.b.served-build', 'PASS')` |
| `page_owner_unchecked` | `test_certify.py:957: assert [] == ["the worker'...ld is unread"]` |
| `build_process_unread` | `test_certify.py:772: AssertionError: assert {'drift': 0.0...0123abc', ...} == {'drift': 0.0...0123abc', ...}` |
| `box_without_worker_metrics_accepted` | `test_certify.py:1071: Failed: DID NOT RAISE <class 'SystemExit'>` |

Existing mutants whose lines this change moved. Each anchor still matches exactly once as a
substring of the loop's more deeply indented line, and each mutant is still killed:
`served_revision_unchecked` → `test_certify.py:939: assert [] == ['the gateway...000000000000']`.
`short_revision_accepted` → `test_certify.py:941: assert [] == ['the gateway...000000000000']`.
`missing_build_info_accepted` → `test_certify.py:945: assert 'publishes no infrx_build_info' in "the gateway's /metrics is the None's page: its build is unread"`.
In the `--box` argument case, each of the three iterations leaves out exactly one flag, so a
flag rule's mutant can die only on its own iteration.

## Tails

The whole E4B list as `make api-mutants` runs it:
`INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_e4b_mutants.py`
(152 mutants over 40 named cases: the 138 before plus 14 new, and the two list-shape tests):

```
........................................................................ [ 46%]
........................................................................ [ 93%]
..........                                                               [100%]
154 passed in 320.09s (0:05:20)
exit 0
```

`apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_certify.py tests/integration/backend/test_endpoint_doc.py`:

```
........................................                                 [100%]
40 passed in 1.20s
```

## Item 3: the deployed duration cap (`e4a7106`)

The coordinator added this item after the box run at `4226315` (out `20260924T165408Z`).

**What was wrong.** The box run's `e4b.a.sop-parity` FAILED with "refused by the candidate".
- Parity runs against the engine, not the gateway. W4's E0 baseline and the release engine
  both refuse the parity set's four 112 s clips and three 120 s clips. The engine has a
  16,384-token encoder budget, so its ceiling is 82 s (`W4-phaseB-20260923T2155Z.md`: "video
  item with 21504 embedding tokens exceeds the pre-allocated encoder cache size 16384").
- `decide.parity_verdict` counts every candidate refusal as a problem, so the cell could never
  pass on those clips.
- The pilot's admission refuses every clip over `MAX_VIDEO_SECONDS=82` (P-20 decision B).
- The envelope, soak and overload cells judged by the interim `applied_cap_s` (72) and the
  engine ceiling, not by the deployed cap.
- The envelope accepted any 4xx as the over-cap refusal.
- The dataset drill scheduled 112 s clips.

**Fix** (one commit):
- **The cap.** `deployed_cap_s` reads `MAX_VIDEO_SECONDS` once, at the run's start, with the
  gateway's own `pilot_from_env`. When it is unset, the tree's default (120) applies. The run
  records it in `target.max_video_seconds` and the config pin's `deployed_max_video_seconds`,
  and passes it to the parity, dataset and load cells. `CRITERIA["applied_cap_s"]` is removed.
- **The refusal.** `OVER_CAP` is 400 `unsupported_media`, param `messages`. It is tied to
  `MediaProfile.check`, and it matches the coordinator's measurement of c051 on the pilot.
  bench.py's rows carry the status and the code; its allowlist does not keep the param.
- **Parity.** Only the within-cap clips (c039, c024) are paired with E0.
  - Each over-cap clip goes to the gateway through `admission_answer`, bench.py's own
    request, and must get `OVER_CAP`.
  - A within-cap refusal FAILs, and so does an over-cap clip the gateway accepts or refuses
    with another answer.
  - An engine target has no admission to ask, so those clips pend on `BOX`.
- **Envelope.** `duration_cap` requires the typed refusal over the cap and FAILs a typed
  refusal within it. A clip at the cap is within it. Failures, soak and overload are judged
  over the clips within the cap.
- **Dataset drill.** It reads `corpus-within-cap.json`, the licensed manifest less the
  over-cap clips, with the corpus's own cache root. The first run and the resume read the
  same file. An item refused as over the cap anyway FAILs the drill: the gateway's cap is
  then not the runner's.

Protocol amendment 5(c) records this.

**Reproduction.**
- **The refusal.** `test_e4b_the_deployed_cap_and_its_refusal_are_the_media_layers` runs the
  M layer's `MediaProfile.pinned(pilot_from_env())` with `MAX_VIDEO_SECONDS=82`. A 112 s probe
  raises exactly `OVER_CAP`, and an 82 s probe is admitted.
- **The local run.** The local target (the runner's fake engine; namespace `e4b`) ran with
  `MAX_VIDEO_SECONDS=82`, `--no-stack`, at clean `e4a7106` (report sha256 `37dd4489fd42833a`):
  - `target.max_video_seconds` read 82.0.
  - sop-parity passed on the 2 within-cap clips and pended on `BOX` for the 7 over-cap
    clips ("unasked: an engine target has no admission").
  - The dataset drill read 60 within-cap clips (the longest 72 s) and passed its client half
    (4 accepted, then interrupted, then resumed).
  - The envelope counted 11 of 12 attempts: the one attempt on a 112 s clip was the cap's.
  - `release-identity` PASSED.
  - `e4b.b.preconditions` FAILED on this host's App dev servers, the known dev-host
    exemption.
- **Not reproduced: the gateway's own answer.** Nothing here ran the gateway's admission
  end to end; that needs the stack's auth and ledger. `admission_answer` is checked against
  a stdlib server that answers the way the pilot does.

**Cases:**
- `test_e4b_the_deployed_cap_and_its_refusal_are_the_media_layers`
- `test_e4b_parity_pairs_the_clips_within_the_cap_and_asks_admission_for_the_rest`
- `test_e4b_admission_is_asked_as_bench_asks_and_only_its_code_and_param_are_kept`
- `test_e4b_the_dataset_drill_schedules_only_clips_within_the_deployed_cap`
- `test_e4b_the_run_reads_the_deployed_cap_once_and_every_cell_judges_by_it`
- `test_e4b_an_envelope_rung_judges_the_duration_cap_apart_from_its_failures`, rewritten.
  The 78 s "band" clip is now within the cap, and a refusal of it FAILs.
- The other cells' cases now pass the cap: `CAP` = 82, or `TREE_CAP` = 120 for the original
  parity case.

**Mutants and their death lines** (30 new plus the rung mutants that anchor on changed
lines; re-derived at `e4a7106` with the shared runner's `_copy`/`_prepare`/`_pytest`; every
death is an `AssertionError` in `test_certify.py`):

| Mutant | Death line |
|---|---|
| `cap_read_from_the_tree` | `test_certify.py:262: assert 120.0 == 82.0` |
| `over_cap_code_typed_wrong` | `test_certify.py:268: AssertionError: assert {'code': 'uns...': 'messages'} == {'code': 'inv...': 'messages'}` |
| `over_cap_param_typed_wrong` | `test_certify.py:268: AssertionError: assert {'code': 'uns...': 'messages'} == {'code': 'uns...'param': None}` |
| `cap_unrecorded_in_the_target` | `test_certify.py:619: assert (None, 82.0) == (82.0, 82.0)` |
| `cap_unrecorded_in_the_pin` | `test_certify.py:619: assert (82.0, None) == (82.0, 82.0)` |
| `parity_judged_at_the_tree_cap` | `test_certify.py:621: AssertionError: assert {'dataset': 8...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `dataset_judged_at_the_tree_cap` | `test_certify.py:621: AssertionError: assert {'dataset': 1...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `load_cells_judged_at_the_tree_cap` | `test_certify.py:621: AssertionError: assert {'dataset': 8...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `engine_target_asked_for_admission` | `test_certify.py:626: AssertionError: assert (82.0, 'direct') == (82.0, None)` |
| `gateway_never_asked` | `test_certify.py:621: AssertionError: assert {'dataset': 8... (82.0, None)} == {'dataset': 8...0, 'gateway')}` |
| `parity_pairs_over_cap_clips` | `test_certify.py:316: AssertionError: assert ('FAIL', 'c01...he candidate') == ('PASS', '2 clips')` |
| `over_cap_read_from_the_outcome` | `test_certify.py:336: AssertionError: assert 'PENDING' == 'FAIL'` |
| `parity_cap_exclusive` | `test_certify.py:316: AssertionError: assert ('PASS', '1 clips') == ('PASS', '2 clips')` |
| `over_cap_unasked` | `test_certify.py:322: AssertionError: assert {'c012-bbb108...mission', ...} == {'c012-bbb108...ssages'}, ...}` |
| `over_cap_acceptance_accepted` | `test_certify.py:330: AssertionError: {'code': None, 'http_status': 200, 'param': None}` |
| `over_cap_refusal_untyped` | `test_certify.py:330: AssertionError: {'code': 'invalid_request', 'http_status': 400, 'param': None}` |
| `engine_target_over_cap_passes` | `test_certify.py:340: AssertionError: assert ('PASS', None, []) == ('PENDING', ['BOX'], [])` |
| `acceptance_read_as_a_refusal` | `test_certify.py:386: AssertionError: assert [{'code': 'un...param': None}] == [{'code': 'un...param': None}]` |
| `param_assumed` | `test_certify.py:386: AssertionError: assert [{'code': 'un...: 'messages'}] == [{'code': 'un...param': None}]` |
| `code_unallowlisted` | `test_certify.py:386: AssertionError: assert [{'code': 'un...param': None}] == [{'code': 'un...param': None}]` |
| `key_not_sent` | `test_certify.py:391: AssertionError: assert ('/v1/chat/co...arer ', 'm@1') == ('/v1/chat/co...56789', 'm@1')` |
| `cap_exclusive_at_the_boundary` | `test_certify.py:876: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...'at']}, 'BOX')` |
| `untyped_over_cap_refusal_accepted` | `test_certify.py:869: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |
| `overload_counts_capped_clips` | `test_certify.py:983: assert ["refusals wi...d_request')]"] == []` |
| `dataset_corpus_unfiltered` | `test_certify.py:563: AssertionError: assert [{'derived': ...4', ...}, ...] == [{'derived': ...4', ...}, ...]` |
| `dataset_corpus_cap_exclusive` | `test_certify.py:563: AssertionError: assert [{'derived': ...4', ...}, ...] == [{'derived': ...4', ...}, ...]` |
| `dataset_corpus_cache_moved` | `test_certify.py:565: AssertionError: assert '.claude/corpus-cache' == '/tmp/claude-.../corpus-cache'` |
| `first_run_on_the_full_corpus` | `test_certify.py:587: AssertionError: assert ['/tmp/claude...hin-cap.json'] == ['/tmp/claude...hin-cap.json']` |
| `resume_on_the_full_corpus` | `test_certify.py:587: AssertionError: assert ['/tmp/claude...anifest.json'] == ['/tmp/claude...hin-cap.json']` |
| `capped_items_accepted` | `test_certify.py:592: assert ('PENDING', '..., no ledger)') == ('FAIL', ["it...her: ['i6']"])` |
| `over_ceiling_judged_as_failures` | `test_certify.py:865: AssertionError: assert {'answered': ...: 'pass', ...} == {'answered': ...: 'pass', ...}` |
| `within_cap_refusal_accepted` | `test_certify.py:876: AssertionError: assert ('duration_ca...ong']}, 'BOX') == ('duration_ca...ong']}, 'BOX')` |
| `admitted_long_clip_accepted` | `test_certify.py:869: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |

## Item 5: R106, a cancelled job's replay is terminal (`7ac77c7`)

The coordinator added this item after the box rerun at `4226315` (out `20260924T172244Z`).

**R106** is the cancelled-replay rule, numbered by the coordinator at `318904e` (08 §10): a
cancelled job's replay is terminal for that key.

**What was wrong.** `e4b.a.dataset-resume` FAILed "items not terminal after the resume" for
exactly the two items the drill's SIGINT interrupted (c037 and c059).
- Their first attempts ended in the client's `ReadError`. A client that left is a committed
  cancel (R21).
- The resume re-sent each under the same key. The replay answered HTTP 200, then a stream
  error event with code `state_conflict`. That is the relay's `_refusal` for a cancelled job
  (R91).
- `bench.is_terminal` treated that row as a failure. Every resume would re-send it forever,
  so it could never become terminal.

**Fix** (one commit):
- **bench.py.** A replay (`Idempotency-Replayed: true`) whose stream answers
  `state_conflict` is now `CANCELLED_REPLAY` (`"cancelled_by_interruption"`), and
  `is_terminal` accepts it.
  - Only the allowlisted code is read, never the text.
  - A fresh request's `state_conflict`, or a replay's other error, stays a failure.
  - The summary counts the new outcome.
  - Narrowing: the coordinator asked for "a request that carried an Idempotency-Key". bench
    sends that header on every request, so a condition on it could never fail and no mutant
    could kill it. The rule reads the gateway's `Idempotency-Replayed` instead. The relay sets
    it on every replay, a looked-up one included (`jobstore.lookup`: "marked replayed").
- **certify.py.** The drill FAILs an item accepted twice, and a cancelled item not replayed
  exactly once. A passing drill states what it proved, in its detail and in
  `measured.sop`: "MARLIN-SOP: no second accepted item, nothing re-sent after it was
  terminal, and N item(s) cancelled by the interruption, each terminal after exactly one
  replay of its key (R106)". `measured.cancelled_by_interruption` lists the items.
- **The ledger half: inferred from the contract, not yet seen on the box.** The conformance
  case `dur_settle__cancel_records_its_cause_and_settles_by_r21` says a cancel carries no
  usage, and that after output its hold stays held (`held_unknown`) until the platform
  releases it. A drill that interrupts streams mid-output would therefore always fail
  "reserved restored" within its 300 s wait. The reserved total now accounts for the
  cancelled jobs' holds that are still held. A usage record for a cancelled job still FAILs
  Σ charged, because a cancel carries no usage.
- **The envelope.** The rerun's over-cap refusals appear in every rung (8 ×
  `unsupported_media` at r = 0.5). A case now pins that they never lower the supported rate.
  Item 3's cap already excludes them from failures, rejections, soak and overload.

Protocol amendment 5(d) records this.

**Cases:**
- `test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption`
  (bench)
- `test_e4b_an_item_the_interruption_cancelled_is_terminal_after_one_replay`
- The resume case (an item accepted twice) and the rung case (the supported rate with the
  cap's refusals) are extended.

**E1B list mutants**, each killed by the bench case (the runner's own results, at
`7ac77c7`):
- `e1bm25`: a cancelled replay not terminal.
- `e1bm26`: never classified.
- `e1bm27`: a fresh request classified.
- `e1bm28`: another code classified.
- `e1bm29`: not counted.

**E4B list mutants and their death lines** (re-derived at `7ac77c7`):

| Mutant | Death line |
|---|---|
| `cancelled_items_uncounted` | `test_certify.py:504: AssertionError: assert ([] == []` |
| `second_replay_accepted` | `test_certify.py:510: assert [] == ["cancelled i...play: ['i3']"]` |
| `accepted_twice_unchecked` | `test_certify.py:421: assert ["terminal it...sume: ['i1']"] == ["terminal it...once: ['i1']"]` |
| `cancel_holds_unaccounted` | `test_certify.py:519: assert ["reserved 0 ...n's cancels)"] == []` |
| `released_cancel_holds_counted` | `test_certify.py:520: assert ["reserved 0 ...n's cancels)"] == []` |
| `sop_property_unstated` | `test_certify.py:541: AssertionError: assert ('PASS', 'reconciled') == ('PASS', 'rec...s key (R106)')` |
| `over_cap_refusals_bias_the_rate` | `test_certify.py:921: AssertionError: assert {'answered': ...: 'pass', ...} == {'answered': ...: 'pass', ...}` |
| `reserved_unchecked` | `test_certify.py:485: AssertionError: assert [] == ['reserved 0 -> 1']` |

The E1B list's `e1bm07` ("a failed item is not terminal") anchored on the line item 5
changed, and the whole E1B list reported it stale. `0df3fa7` re-anchors it, and it is still
killed.

One local run at `7ac77c7` was discarded. During it, the fake engine on the `e4b`
namespace's port 56880 answered 1,337 prompt tokens after its first parity clip.
`pilotbox.ENGINE_PROMPT_TOKENS` is the only 1,337 in the tree. Another process reset the
engine; no code here was at fault. The rerun, with nothing else on the port, gave:
- report sha256 `a229e1ec5956ce27`;
- `release-identity` PASS at `7ac77c7`;
- sop-parity: the 2 clips within the cap passed, the 7 over it pend on `BOX`;
- dataset-resume, envelope and soak: PENDING on `BOX`;
- `e4b.b.preconditions`: FAIL on this host's App dev servers, the known exemption.

## Tails at the head

The whole E4B list, as `make api-mutants` runs it, at `7ac77c7`. That is 189 mutants over
46 named cases: 138 at the start of the lane, plus 14, 30 and 7 new, plus the two
list-shape tests. `0df3fa7` changes only the E1B list's file, which no E4B case reads.

```
........................................................................ [ 37%]
........................................................................ [ 75%]
...............................................                          [100%]
191 passed in 407.74s (0:06:47)
exit 0
```

At item 3 (`e4a7106`): `184 passed in 392.72s (0:06:32)`, exit 0.

`test_certify.py` + `test_endpoint_doc.py` at `0df3fa7`:

```
..............................................                           [100%]
46 passed in 1.86s
```

`models/marlin2b/tests` (`make bench-test`) at `0df3fa7`: `68 passed in 5.72s`.

The E1B list (`models/marlin2b/tests/mutants.py`) at `0df3fa7`:
`{'mutants': 41, 'killed': 38, 'controls_survived': 3, 'not_killed': 0, 'problems': None}`.

The W and G suites that load bench.py (`tests/w/test_serving.py`, `test_w4.py`,
`test_service.py`, `tests/g`), at `7ac77c7` before the re-anchor:
`612 passed, 2 warnings in 284.86s`.

## Polish round (`codex/certify-polish` from `4db74b6`)

This round was run after the verifier passed `9b467f8` and the coordinator merged it at
`4db74b6`. It covers the verifier's nonblocking findings and box run2's
(`20260924T172244Z`), on a new branch.

**R106 as corrected** (`9b3b851`, 08 §10): a job cancelled by the client (a disconnect or an
explicit cancel) replays `state_conflict`, while a job the deadline ended replays
`deadline_exceeded` and is retried. A cancelled replay is terminal for its key. It carries no
usage and no debit, and its hold stays held until the platform releases it. A resume drill
counts it as "cancelled by the interruption" only when the first attempt was the client's own
tear; a platform-side cancel is listed apart and fails the drill.

The item-5 section above predates that correction. Its ledger inference ("inferred from the
contract") is now R106's own text.

| Item | Commit | Change |
|---|---|---|
| N1 | `08cc0aa` | the reserved allowance is the cancelled replays' holds only: a quarantined 400 row's leaked hold FAILs 'reserved …' |
| N2 | `0ba23c7` | parity's `OVER_CAP` is pinned on its param: a param-less `unsupported_media` (the relay's post-admission preparation failure) FAILs |
| N3 | `96317c4` | the drill's over-cap refusal check scans the first run |
| N4 | `0740ab5` | `cap_verdict` is shared; on a gateway the soak reports a cap breach, never a pass; `load_cells` passes `gateway` |
| N5 | `f6f9bad` | "cancelled by the interruption" only after the client's own tear (a transport `error_class`); any other cancelled replay is "cancelled by the platform" and FAILs; 5(d) reworded in place |
| N7 | `a49dec8` | bench.py's rule also requires `error_class == "stream_error_event"`; 5(d) reads "a replay whose stream answered `state_conflict`" |
| N9 | `4a4b828` | bench.py's `denominators.cancelled_replay_excluded`, so the buckets sum to `scheduled` |
| N10 | `31c1d30` | §4's envelope row and §5's `applied_cap_s` row are marked "Superseded by 5(c)" in place; the protocol test is two-directional with an explicit superseded list |
| N12 | `4eaac29` | each bench run is bounded by its own schedule (requests over rate) plus 900 s, and the soak by at least its seconds plus 900; `parity.py` keeps the hour |
| N14 | `c3ac6ae` | a box rung sends 120, or the fewest more for which bench's own schedule holds 60 short clips (135 today); the tiny scale keeps 12 |
| N15 | `9894a5e` | an over-cap attempt refused 429 for capacity is not judged by the cap and is named; an admitted (200) one still FAILs; `admission_answer` asks a 429 once more after its Retry-After |
| run2 e2e | `7456816` | each latency row prints its p50 and accepted count beside the p95; amendment 5(e) |

run2's e2e p95 (about 91 s per clip-minute against the provisional 45 s) is a measurement and
the criterion stands. The cell now prints the p50 and the accepted count beside it, so the
release decision can quote both.

**This round's mutants, E4B list** (death lines derived at `7456816` with the shared runner's
`_copy`/`_prepare`/`_pytest`; every one an assertion in `test_certify.py`):

| Mutant | Death line |
|---|---|
| `v_cancelled_from_any_non_accepted` | `test_certify.py:553: assert [] == ["reserved 0 ...n's cancels)"]` |
| `v_parity_param_ignored` | `test_certify.py:333: AssertionError: {'code': 'unsupported_media', 'http_status': 400, 'param': None}` |
| `v_capped_second_run_only` | `test_certify.py:694: assert ('PENDING', '..., no ledger)') == ('FAIL', ["it...her: ['i2']"])` |
| `v_soak_judges_over_cap` | `test_certify.py:1128: AssertionError: assert (['failure_rat...atency_drift'] == ['failure_rat...atency_drift']` |
| `soak_cap_breach_unreported` | `test_certify.py:1131: AssertionError: assert ('pass' == 'pass'` |
| `soak_reports_a_cap_pass` | `test_certify.py:1137: AssertionError: assert 'duration_cap' not in ['failure_rate', 'answered', 'duration_cap', 'host_growth_mib', 'gpu_growth_mib', 'reconciled_at_end', ...]` |
| `soak_gateway_unwired` | `test_certify.py:1232: AssertionError: assert ('e4b.b.soak', 'PENDING') == ('e4b.b.soak', 'FAIL')` |
| `v_platform_cancel_counted_as_the_interruption` | `test_certify.py:532: AssertionError: stream_error_event` |
| `platform_cancel_passes` | `test_certify.py:533: AssertionError: stream_error_event` |
| `stream_error_counted_as_a_tear` | `test_certify.py:532: AssertionError: stream_error_event` |
| `http_answer_counted_as_a_tear` | `test_certify.py:532: AssertionError: http_502` |
| `platform_cancels_unrecorded` | `test_certify.py:580: AssertionError: assert ('FAIL', [], []) == ('FAIL', [], ['i3'])` |
| `criterion_dropped_from_the_runner` | `test_certify.py:744: AssertionError: assert {'applied_cap...wth_mib', ...} == {'applied_cap...wth_mib', ...}` |
| `superseded_criterion_revived` | `test_certify.py:745: AssertionError: assert not ({'applied_cap_s'} & {'applied_cap_s', 'e2e_p95_s_per_clip_minute', 'max_failure_rate', 'max_gpu_growth_mib', 'max_host_growth_mib', 'p95_min_` |
| `superseded_row_unmarked` | `test_certify.py:746: assert False` |
| `superseded_envelope_row_unmarked` | `test_certify.py:748: AssertionError: assert 'Superseded by 5(c)' in '/ `e4b.b.envelope` / `bench.py` open loop, one run per rate of the ladder, `--retries 0`, `--max-tokens 128,512,1024`...a` |
| `v_soak_cut_at_an_hour` | `test_certify.py:1259: AssertionError: assert {'envelope-r0...sonl': 3600.0} == {'envelope-r0...onl': 15300.0}` |
| `schedule_ignores_the_rate` | `test_certify.py:1259: AssertionError: assert {'envelope-r0...sonl': 4500.0} == {'envelope-r0...onl': 15300.0}` |
| `no_margin_after_the_schedule` | `test_certify.py:1259: AssertionError: assert {'envelope-r0...onl': 14400.0} == {'envelope-r0...onl': 15300.0}` |
| `v_rung_unsized` | `test_certify.py:1228: assert {120} == {135}` |
| `tiny_rung_sized` | `test_certify.py:1204: AssertionError: assert [('envelope-r...nl', 2.0, 20)] == [('envelope-r...nl', 2.0, 20)]` |
| `sizing_counts_every_clip` | `test_certify.py:1047: AssertionError: 120` |
| `sizing_below_the_declared_rung` | `test_certify.py:1048: AssertionError: assert 135 == 1000` |
| `short_class_any_resolution` | `test_certify.py:1017: AssertionError: short1080` |
| `v_capacity_refusal_judged_as_the_cap` | `test_certify.py:990: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |
| `v_any_over_cap_refusal_excused` | `test_certify.py:977: AssertionError: assert {'answered': ...: 'pass', ...} == {'answered': ...: 'pass', ...}` |
| `capacity_refusals_unrecorded` | `test_certify.py:990: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |
| `admission_not_retried_after_capacity` | `test_certify.py:396: AssertionError: assert [{'code': 'un...': None}, ...] == [{'code': 'un...ssages'}, ...]` |
| `tail_quoted_without_its_p50` | `test_certify.py:1023: AssertionError: assert [('e2e_p95_pe... 60)', 'BOX')] == [('e2e_p95_pe... 60)', 'BOX')]` |

**E1B list** (the runner's own results at `7456816`):
- `e1bm30`: a non-stream answer classified.
- `e1bm31`: the denominator dropped.
- `e1bm27` and `e1bm28` were re-anchored on the new rule line.

**N11: item 3's table refreshed at `7456816`** (the table in item 3's section above is kept as
derived at `e4a7106`):

| Mutant | Death line |
|---|---|
| `cap_read_from_the_tree` | `test_certify.py:262: assert 120.0 == 82.0` |
| `over_cap_code_typed_wrong` | `test_certify.py:268: AssertionError: assert {'code': 'uns...': 'messages'} == {'code': 'inv...': 'messages'}` |
| `over_cap_param_typed_wrong` | `test_certify.py:268: AssertionError: assert {'code': 'uns...': 'messages'} == {'code': 'uns...'param': None}` |
| `cap_unrecorded_in_the_target` | `test_certify.py:721: assert (None, 82.0) == (82.0, 82.0)` |
| `cap_unrecorded_in_the_pin` | `test_certify.py:721: assert (82.0, None) == (82.0, 82.0)` |
| `parity_judged_at_the_tree_cap` | `test_certify.py:723: AssertionError: assert {'dataset': 8...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `dataset_judged_at_the_tree_cap` | `test_certify.py:723: AssertionError: assert {'dataset': 1...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `load_cells_judged_at_the_tree_cap` | `test_certify.py:723: AssertionError: assert {'dataset': 8...0, 'gateway')} == {'dataset': 8...0, 'gateway')}` |
| `engine_target_asked_for_admission` | `test_certify.py:728: AssertionError: assert (82.0, 'direct') == (82.0, None)` |
| `gateway_never_asked` | `test_certify.py:723: AssertionError: assert {'dataset': 8... (82.0, None)} == {'dataset': 8...0, 'gateway')}` |
| `parity_pairs_over_cap_clips` | `test_certify.py:316: AssertionError: assert ('FAIL', 'c01...he candidate') == ('PASS', '2 clips')` |
| `over_cap_read_from_the_outcome` | `test_certify.py:339: AssertionError: assert 'PENDING' == 'FAIL'` |
| `parity_cap_exclusive` | `test_certify.py:316: AssertionError: assert ('PASS', '1 clips') == ('PASS', '2 clips')` |
| `over_cap_unasked` | `test_certify.py:322: AssertionError: assert {'c012-bbb108...mission', ...} == {'c012-bbb108...ssages'}, ...}` |
| `over_cap_acceptance_accepted` | `test_certify.py:333: AssertionError: {'code': None, 'http_status': 200, 'param': None}` |
| `over_cap_refusal_untyped` | `test_certify.py:333: AssertionError: {'code': 'invalid_request', 'http_status': 400, 'param': None}` |
| `engine_target_over_cap_passes` | `test_certify.py:343: AssertionError: assert ('PASS', None, []) == ('PENDING', ['BOX'], [])` |
| `acceptance_read_as_a_refusal` | `test_certify.py:396: AssertionError: assert [{'code': 'un...ssages'}, ...] == [{'code': 'un...ssages'}, ...]` |
| `param_assumed` | `test_certify.py:396: AssertionError: assert [{'code': 'un...ssages'}, ...] == [{'code': 'un...ssages'}, ...]` |
| `code_unallowlisted` | `test_certify.py:396: AssertionError: assert [{'code': 'un...ssages'}, ...] == [{'code': 'un...ssages'}, ...]` |
| `key_not_sent` | `test_certify.py:403: AssertionError: assert ('/v1/chat/co...arer ', 'm@1') == ('/v1/chat/co...56789', 'm@1')` |
| `cap_exclusive_at_the_boundary` | `test_certify.py:1002: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...'at']}, 'BOX')` |
| `untyped_over_cap_refusal_accepted` | `test_certify.py:984: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |
| `overload_counts_capped_clips` | `test_certify.py:1149: assert ["refusals wi...d_request')]"] == []` |
| `dataset_corpus_unfiltered` | `test_certify.py:658: AssertionError: assert [{'derived': ...4', ...}, ...] == [{'derived': ...4', ...}, ...]` |
| `dataset_corpus_cap_exclusive` | `test_certify.py:658: AssertionError: assert [{'derived': ...4', ...}, ...] == [{'derived': ...4', ...}, ...]` |
| `dataset_corpus_cache_moved` | `test_certify.py:660: AssertionError: assert '.claude/corpus-cache' == '/tmp/claude-.../corpus-cache'` |
| `first_run_on_the_full_corpus` | `test_certify.py:682: AssertionError: assert ['/tmp/claude...hin-cap.json'] == ['/tmp/claude...hin-cap.json']` |
| `resume_on_the_full_corpus` | `test_certify.py:682: AssertionError: assert ['/tmp/claude...anifest.json'] == ['/tmp/claude...hin-cap.json']` |
| `capped_items_accepted` | `test_certify.py:687: assert ('PENDING', '..., no ledger)') == ('FAIL', ["it...her: ['i6']"])` |
| `over_ceiling_judged_as_failures` | `test_certify.py:977: AssertionError: assert {'answered': ...: 'pass', ...} == {'answered': ...: 'pass', ...}` |
| `within_cap_refusal_accepted` | `test_certify.py:1002: AssertionError: assert ('duration_ca...ong']}, 'BOX') == ('duration_ca...ong']}, 'BOX')` |
| `admitted_long_clip_accepted` | `test_certify.py:984: AssertionError: assert ('duration_ca...': []}, 'BOX') == ('duration_ca...': []}, 'BOX')` |

**Tails at `7456816`.** The whole E4B list, as `make api-mutants` runs it: 217 mutants
over 48 named cases, plus the two list-shape tests.

```
........................................................................ [ 98%]
...                                                                      [100%]
219 passed in 486.17s (0:08:06)
exit 0
```

- `test_certify.py` + `test_endpoint_doc.py`: `48 passed in 2.01s`.
- `models/marlin2b/tests` (`make bench-test`): `68 passed in 5.96s`.
- The E1B list (`models/marlin2b/tests/mutants.py`): `{'mutants': 43, 'killed': 40, 'controls_survived': 3, 'not_killed': 0, 'problems': None}`, exit 0.

**Follow-ups: nothing dropped, and every coordinator item was done.** The verifier's N6
(R106's wording) and N8 (a `--box` run with no `MAX_VIDEO_SECONDS`) are not items of this
round: N6 is the coordinator's corrected ruling, and N8 was left optional. The W and G suites
that load bench.py last ran at `7ac77c7` (612 passed). They were not rerun in this
time-boxed round; bench.py changed only in N7 and N9.

## Verification log

- 2026-09-24 (CERTIFY-TREE): Authored at `c7e2139`. The tails above come from the commands
  shown, run at that head with `TMPDIR` outside the checkout. The death lines were
  re-derived at that head. The probe ran through `main()` with no git on `PATH`. No box,
  AWS, hosted service, secret or stack was used.
- 2026-09-24 (CERTIFY-TREE): Items 3 and 5 added at `0df3fa7` (the coordinator's two
  requests after the box runs at `4226315`). R106 is the coordinator's ruling (`318904e`).
  The tails come from the commands named, run at the heads stated, with `TMPDIR` outside
  the checkout. The death lines were re-derived at those heads. The local runs used the
  `e4b` namespace and no stack. No box, AWS, hosted service, secret or PostgreSQL was used.
- 2026-09-24 (CERTIFY-POLISH): The polish round was added at `7456816` on
  `codex/certify-polish` (from `4db74b6`). Its death lines and tails come from the commands
  named, run at that head with `TMPDIR` outside the checkout. No box, AWS, hosted service,
  secret or stack was used, and neither the e4b nor the e3b2 namespace.
