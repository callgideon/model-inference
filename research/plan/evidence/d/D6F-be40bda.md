# D6F follow-up — T3's feedback scrub, WR-G4F-2 (cut session) and WR-C3F-1 session doors (lab-sql, LW2)

- Base `eb0734d7` · scrub + WR-G4F-2 `9b8cf423` (cut session, 0035) · WR-C3F-1 `928f942e` (0038) · code head `be40bda4` · branch `codex/w5-lab-sql-lw2`
- LOCAL-ONLY migrations (R151). The `feedback` flag is unchanged (OFF by default): `public.submit_feedback` writes only through `infrx.accept_feedback`, which owns it. `public.lab_review_feedback` is read-only and answers `not_found` to anyone without a current provider membership AND a current grantor grant (none exist hosted).

## Changed paths (all owned)
- `0035_feedback_scrub.sql` (cut session; reviewed, unchanged), `0038_feedback_doors.sql` (body byte-identical to `apps/app/tests/c/feedback/proposed_doors.sql` on the tip from its first `create`; `diff` empty).
- `infrx/state/feedback.py` (cut session): `accept_with_replay(...) -> (Feedback, replayed)` (WR-G4F-2: a replay answers the stored row whose id is not the one this call generated; `accept` is its first element, port unchanged) and `scrub(...)` (T3).
- `tests/d/test_d6f_scrub.py` (5), `test_d6f_doors.py` (2), `test_d6f_units.py`, `code_mutants_d6f.py` (`SCRUB` 13, `DOORS` 12 SQL), `test_code_mutants_d6f.py`.

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| (cut session) `test_d6f_scrub.py` without 0035 / with | `9b8cf423` | 1 -> 0 | 5 failed (`UndefinedFunction infrx.scrub_feedback`) -> 5 + 16 + 8 passed |
| `test_d6f_doors.py` without 0038 (seam first, with 0036-0039 aside; D6J evidence) | pre-`928f942e` | 1 | both checks failed |
| `pytest -q tests/d/test_d6f_doors.py` | `928f942e` | 0 | 2 passed (first run: a wrong column name in the check, fixed) |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d6f.py` first run | pre | 1 | 65 passed, 1 survivor `c3f_submit_forged_author` (the mutant only swapped in an absent field; now forges the org id as the author) |
| same | `928f942e` | 0 | **66 passed** (in the same 214-passed run): 19 + 13 + 12 SQL and 19 Python mutants killed, 3 list checks |

## Oracles
FEEDBACK-ACK: provenance is the server's (author = the session user, channel console, role customer); a provenance field in the body refused; replay one row; non-member / unknown / malformed request `not_found`; anon 42501. LAB-ACCESS (C3F's review): developer+ with the grantor's current feedback/provider_sharing grant reads signals only (no principal/org/marker, never a calibration label); viewer `forbidden`; other provider, consumer-only, narrower, expired, revoked and unknown `not_found`.

## Wiring
- WR-LSQ-3 (`EXPECTED_FUNCTION_CALLERS`, D6J evidence) for the two doors; WR-LSQ-1 pin.
- WR-LSQ-7 (feedback lane, at merge): C3F's `stack.py` stops applying `proposed_doors.sql` once 0038 is in the set (it already skips when a migration defines the doors); `feedback.py`'s route may now report `replayed` from `accept_with_replay` (WR-G4F-2, Python only).

## Estimate (remaining, D6F follow-ups): 0.5/1/2 h, confidence medium.
