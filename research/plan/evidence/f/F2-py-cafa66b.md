# F2 (Python half) — review r7: the content half of the minimal guard, and evidence placement

## Task and status

| Field | Value |
|---|---|
| Task | F2-py — two remaining items (one ruled fix-now as a privacy invariant, DEC-10) plus four cheap nonblocking ones |
| Status | **implemented** (fakes only; not integrated) |
| Reviewed head | `fd934d4` — both r6 blockers closed, 179/179 genuine, runner sound, 165,888 R37 permutation sequences with 0 violations |
| Base SHA | `fab9fbe` |
| Implementation SHA | `d8026f0` + the anchor refresh below |

Counts are quoted from the **Commands** section.

## F1 — a `minimal` capture was pinned only against the mode, not the content

The guard was `if envelope.mode is not TraceMode.minimal or envelope.carries_content:`,
and keeping **only the mode half** survived all 156 conformance tests (the reviewer's
y3c): a raw-assembled `minimal` envelope carrying content
(`mode=minimal, content_bytes=2000, content_ref=…`) would be stored for a customer who
consented to metadata only. The committed mutant replaced the whole condition, so it died
through the mode half and never exercised the content half.

`trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope` now finishes a `minimal`
capture with exactly that `model_construct` envelope and asserts it is dropped, nothing is
queued and no bytes are charged. **Both halves are declared separately**:
`minimal_capture_keeps_only_the_mode_half` (the reviewer's y3c) and
`minimal_capture_keeps_only_the_content_half`, alongside the existing whole-condition
mutant. All three are killed.

## Nonblocking items, all applied

| # | Item | Fix | Mutant |
|---|---|---|---|
| n1 | the **live** path trusted the envelope's mode: a capture opened `full` handed an `off` envelope queued a mislabelled row (`'off', 0, False, 'none'`) and released its charge with **no loss counted** | "the capture decides" applies on the accumulating path too: mode mismatch → dropped, `malformed`, charge released, exactly one loss counted. New case `trace_bounds__a_live_capture_also_decides_its_own_mode` | `live_capture_trusts_the_envelope_mode` |
| n2 | a `full` capture with a `None` deadline that was abandoned or context-exited **without** `finish` counted no loss (`_close` returned early for every no-op capture) | a `full`-mode request whose trace was never capturable always yields exactly one loss; an `off`/`minimal` capture counts none, having nothing to lose | `unrecordable_capture_counts_no_loss`, `abandon_keeps_its_bytes` |
| n3 | `self.lost_reason or TraceLossReason.abandoned` never fell through, because `none` is the truthy string `"none"`: after the fake's `crash()` a drop was filed under `none` | `is TraceLossReason.none`, `_drop` asserts a real reason, and `trace_bounds__no_loss_is_ever_counted_under_none` asserts no trace path ever counts a loss under `none` — across crash, abandon and reap | `drop_reason_falls_back_on_truthiness` |
| n4 | G1 had no guidance for a failed `full` capture | the README tells G never to fall back to `offer()` for one — finish the capture so the loss is marked and counted (and `offer` refuses content anyway) | — |

## F2 — evidence placement and stale text

- The r6 corrections had been appended to a file named after the r5 *evidence commit*
  (`F2-py-9ca0083.md`), so a reader of the report that made the claims
  (`F2-py-4657d02.md`, named for its implementation SHA) found nothing. The corrections now
  live in **`F2-py-4657d02.md`**, in its body and its verification log;
  `F2-py-9ca0083.md` is a six-line pointer holding no claims of its own.
- `ports.py` (both places) and the README's G1 note no longer say a no-op capture's
  `finish` "behaves as `offer`". They state the rule: identity is checked; a capture opened
  `off` stores nothing; one opened `minimal` stores a metadata-only envelope and refuses
  content however it is labelled; one opened `full` that never accumulated finishes as
  stripped metadata with exactly one counted loss (`abandoned`), never `loss_reason: none`.
  `grep -c 'behaves as .offer'` is **0** in both files.
- The r6 report's sentence "the semantics … are now what the port says" is corrected in
  place: at that SHA the port still understated the rule, and the text followed here.

## Runner note

The n2 rewrite of `_close` moved the lines two committed mutants pointed at, and the
runner reported them **`misdeclared`** rather than killed — which fails the run, which is
what that outcome is for. Both were re-aimed at the guards that now hold those rules and
are killed; the interim output is in the command log below.

## Commands

```
$ uv sync --frozen --all-extras
Checked 41 packages in 0.34ms
exit=0

$ uv run --frozen pytest -q
539 passed, 2 warnings in 11.46s
exit=0

$ uv lock --check
Resolved 44 packages in 0.86ms
exit=0

$ uv run --frozen pytest -q tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/test_app_factory.py
29 passed, 2 warnings in 1.25s
exit=0

$ uv run --frozen pytest -q tests/contracts/test_conformance.py
158 passed in 2.22s

$ uv run --frozen pytest -q tests/contracts/test_mutants.py
19 passed in 7.64s

$ uv run --frozen python tests/contracts/mutants.py --list | tail -1
184 mutants over 127 named cases

$ uv run --frozen python tests/contracts/mutants.py | tail -1
182/184 killed; misdeclared: ['abandon_keeps_its_bytes', 'capture_counts_its_loss_twice']

$ grep -c 'behaves as .offer' infrx/contracts/ports.py infrx/contracts/README.md
infrx/contracts/README.md:0
infrx/contracts/ports.py:0

$ counts
modules walked: 38 | optional deps loaded: none
conformance cases: 135
fixtures: 40 | http codes: 27

$ uv run --frozen python tests/contracts/mutants.py | tail -1 (after the anchor refresh)
184/184 killed

$ make api-test
539 passed, 2 warnings in 11.44s
exit=0

$ make api-mutants
194 passed in 98.09s (0:01:38)
exit=0

$ git diff --stat fab9fbe -- <baseline tests> gateway.py infrx/{auth,media,gateway,usage}
(no output above = byte-identical) exit=0
```

194 = 184 mutants + 6 runner self-tests + the positive control + 3 list/coverage guards.
Console tests/lint not run (console paths); `bench-test` reports "not run".

## Open questions

None.

## Verification log

- 2026-09-21: r7 pass, base `fab9fbe`. The content half of the `minimal` capture guard is
  pinned by a `model_construct` envelope and declared as two separate mutants (y3c and its
  mirror); "the capture decides" now holds on the live path; an unrecordable `full`
  capture always counts exactly one loss; `none` can no longer be a drop reason, asserted
  in `_drop` and by a case across crash, abandon and reap. The r6 corrections were moved
  into the report that made the claims, `F2-py-9ca0083.md` reduced to a pointer, and the
  "behaves as `offer`" text removed from `ports.py` and the G1 note (grep: 0 in both).
  Quoted above: 539 tests pass, 184/184 mutants killed (`make api-mutants` 194 passed in
  98.09s), 29 baseline tests byte-identical, 38 modules walked with no optional dependency,
  135 conformance cases, 40 fixtures, 27 HTTP codes. Two mutant anchors were reported
  `misdeclared` after the n2 rewrite and re-aimed in the same pass. No cloud, GPU,
  container, paid provider or production resource was touched.
