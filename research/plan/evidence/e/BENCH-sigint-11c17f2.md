# BENCH-SIGINT — the double-Ctrl-C case establishes its own SIGINT disposition

Base `92ed083a`, code head `11c17f2c`, branch `codex/bench-sigint`. Release-gate hygiene
fix found by the RELEASE freeze G2 (`make check` at `d3a99e01`, bench-test stage). Test-only;
`models/marlin2b/bench.py` unchanged. No services, docker, ports, AWS or Supabase used.

## Defect
`models/marlin2b/tests/test_leaks.py::test_a_second_ctrl_c_cannot_lose_the_summary` asserted
`sig.getsignal(sig.SIGINT) is sig.default_int_handler` after `bench.sigint_deferred(state)`
("handler restored", base line 545) and again after an embedded run ("an embedded run must
not leave SIGINT ignored in the host process"). `sigint_deferred` restores the PREVIOUS
handler (bench.py:1123-1131), so both assertions only hold when the test process entered
with the default handler. Failure under G2:
`AssertionError: handler restored — assert <Handlers.SIG_IGN: 1> is <built-in function default_int_handler>`.

## Root cause
POSIX/bash rule: when job control is off (a non-interactive shell), a command started
asynchronously (`cmd &`) runs with SIGINT and SIGQUIT ignored. Measured here:
`bash -c 'grep SigIgn /proc/self/status & wait'` → `SigIgn: 0000000000000006` (bits 2 and 3 =
SIGINT, SIGQUIT). CPython installs `default_int_handler` at startup only if SIGINT is SIG_DFL,
so pytest inherits SIG_IGN, `sigint_deferred` faithfully restores SIG_IGN, and the case fails.
A terminal or `setsid` start has SIG_DFL, so the case passed there. The case encoded an
environment assumption instead of establishing it. The same inheritance also reached the
case's end-to-end subprocess: an ignored disposition survives exec
(`python3 -c` with parent SIG_IGN → child `getsignal(SIGINT)` = `1`), so its
`proc.send_signal(SIGINT)` would have been ignored too.

## Change (`models/marlin2b/tests/test_leaks.py` only)
- The case takes pytest's `request`; first lines record the found disposition and install
  `sig.default_int_handler`: `found = sig.signal(sig.SIGINT, sig.default_int_handler)`;
  `request.addfinalizer(lambda: sig.signal(sig.SIGINT, found))` restores it pass or fail.
  (A caught handler is reset to SIG_DFL at exec, so the subprocess now starts with the
  terminal's disposition as well.)
- The mid-case `sig.signal(sig.SIGINT, sig.default_int_handler)` after the
  `ignore_after=True` assertion stays; only its comment changed ("back to the disposition set
  above"). Every assertion is unchanged in text and strength: `is default_int_handler` after
  the plain block, `== SIG_IGN` after `ignore_after=True`, `is default_int_handler` after the
  embedded run, and the subprocess exit 130 / summary / rows checks.

Sibling sweep (`grep -rn "getsignal\|default_int_handler\|SIG_IGN" models/marlin2b/`): only
`test_leaks.py:545/551/552/558` (this case) and
`test_dataset.py:87` — `test_interrupt_then_resume_leaves_exactly_one_logical_result_per_item`
already installs `default_int_handler` and restores `previous` in a `finally`. No other case
changed.

## Commands (from `apps/infrx-api`, `.venv` via `make api-env`)
| # | command | tree | exit | result |
|---|---|---|---|---|
| 1 | `uv run --frozen --no-sync python -c "import signal, sys, pytest; signal.signal(signal.SIGINT, signal.SIG_IGN); sys.exit(pytest.main(['-q','-p','no:cacheprovider','../../models/marlin2b/tests/test_leaks.py','-k','second_ctrl_c']))"` | base | 1 | 1 failed, 14 deselected (line 545, the assertion above) |
| 2 | same as 1, whole directory `../../models/marlin2b/tests` | base | 1 | 1 failed, 120 passed |
| 3 | `bash -c 'python -m pytest -q -p no:cacheprovider <base copy>/models/marlin2b/tests/test_leaks.py & wait $!'` (real async-job disposition; base via `git archive 92ed083a models/marlin2b` into the scratchpad) | base | 1 | 1 failed (this case), 14 passed |
| 4 | same as 1 (SIG_IGN), plus `print(signal.getsignal(SIGINT))` after `pytest.main` | head | 0 | 1 passed, 14 deselected; after = `1` (SIG_IGN handed back) |
| 5 | same as 4 without the SIG_IGN line | head | 0 | 1 passed; after = `default_int_handler` |
| 6 | `uv run --frozen --no-sync pytest -q -p no:cacheprovider ../../models/marlin2b/tests` | head | 0 | 121 passed |
| 7 | same as 2 (SIG_IGN, whole directory) | head | 0 | 121 passed |
| 8 | repo root: `bash -c 'make bench-test > log 2>&1 & wait $!'` (the G2 form) | head | 0 | 121 passed |
| 9 | repo root: `apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py --report …` | head | 0 | 119 mutants: 116 killed, 3 controls survived (e1bc01/02/03), not_killed 0 |
| 10 | `uvx ruff check --output-format concise` (ruff 0.15.12) on the file at head vs base (stdin) | both | 1/1 | 7 findings each (E401 ×1, E402 ×4, E731 ×2), identical: no new finding |

Notes: the directory holds 121 cases, not the 118 the brief expected (counted at base: 120
passed + 1 failed). In run 3 the whole base copy was not used as the comparison because
`test_profile.py::test_every_section7_command_is_the_launchers_and_validates_against_its_filled_base`
reads repo-relative files and fails in an out-of-tree copy under any disposition (`setsid`
too: 1 failed, 25 passed) — a location artefact, unrelated. No mutant names this case (none
did at base either); the lane adds no case, so no mutant is owed.

## Not done / open
- The coordinator update JSON (`evidence/coordinator/updates/`) was not written: the brief
  bounds `git diff --stat 92ed083a..HEAD` to the test file and this evidence file.
- No wiring requests. Remaining effort: 0 h (review only).
