# CERTIFY-TREE: the tree under test where the checkout has no git (`c7e2139`)

Lane: CERTIFY-TREE (Opus fix lane; coordinator Fable). Branch `codex/certify-tree` from
`origin/main` `2d4a88b`. Commits `2043223` (item 1) and `c7e2139` (item 2), plus this report.
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

## Verification log

- 2026-09-24 (CERTIFY-TREE): Authored at `c7e2139`. The tails above come from the commands
  shown, run at that head with `TMPDIR` outside the checkout. The death lines were
  re-derived at that head. The probe ran through `main()` with no git on `PATH`. No box,
  AWS, hosted service, secret or stack was used.
