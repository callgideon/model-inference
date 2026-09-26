# G7-PROVISIONAL — `pricing.credit.provisional` from the card's approval (A3 WR-4)

- Base: `ca226130` (claude/consumer-v1 tip). Code head: `0a01779a` (branch `codex/g7-provisional`); this evidence is the next commit.
- Closes: A3 WR-4 (`codex/app-a3:research/plan/evidence/a/A3-19f8467.md` §Wiring requests item 4).
- Rulings applied: R109 (resolve, then price; the flag only labels the listing's card), P-01 decision (15 "Decisions 2026-09-25"), R117 (the approval test the transition uses). CREDIT/USD untouched.

## Changed paths
- `apps/infrx-api/infrx/gateway/routes/models.py` — constant `PROVISIONAL = True` removed; `provisional(card)` = `transition.unapproved(card.approved_by) is not None`, passed as `credit_provisional` to `published_model.project`.
- `apps/infrx-api/tests/g/test_catalog_truth.py` — two cases (below).
- `apps/infrx-api/tests/g/mutants.py` — two mutants (`provisional_flag_constant`, `provisional_flag_fails_open`).
- No contract or fixture change: `PublishedModel.pricing.credit.provisional` already exists (published_model.py:226); only its source changed.

## Where approval lives
- The served card is `CatalogDirectory.active_rate_card` → `RateCardSnapshot` (records.py:438), read by `state/catalog.py` `_ACTIVE_CARD` (the listing's card). It carries `approved_by`, not the DB column `rate_card_versions.provisional` (0007:258-259).
- Every writer derives both from the same text: G8 `publish-card` refuses any `unapproved(approved_by)` (operations/service.py:429-433); `state/operations.py` writes `provisional = "P-01" in approved_by`; the seed card has `approved_by = 'provisional - P-01 pending'`, `provisional = true`. `unapproved`'s markers (`p-01`, `provisional`, `pending`, case-insensitive, or blank) are a superset of the column's rule, so reading `approved_by` alone never shows a provisional-column card as approved.
- The P-01 launch card `rc_marlin2b_20260925_launch` with approval "Launch price approved by the coordinator under the operator's authorization of 2026-09-25" → `provisional: false`.

## Fail-closed decision
- Absent (`None`), blank or marker-bearing approval → published `provisional: true`, card still listed. Withholding is kept for what R109/R69 withhold (unpriced, not the approved `ACTIVE_RATE_CARD_VERSION`, past a profile); a provisional card is priced and admissible (0007:244-246), so hiding it would hide a price admission charges.
- A catalog that cannot be read is the existing retryable 503 `dependency_unavailable` (`test_catalog_truth__an_unreachable_catalog_is_a_retryable_503_not_a_claim`, R130): no flag is guessed. There is no `False` default anywhere.

## Tests (written first)
- `test_catalog_truth__an_approved_card_is_not_published_as_provisional` — launch card + P-01 approval text → `false`. **Fails before** on `ca226130`: `E  assert True is False` (1 failed, 1 passed of `-k provisional`).
- `test_catalog_truth__an_unapproved_or_unreadable_approval_publishes_provisional` — the seed card as served, and approvals `"provisional - P-01 pending"`, `"approved, P-01 pending"`, `"Pending"`, `""`, `"   "`, `None` → `true`. Passes before (the constant was fail-closed by construction); it guards the new code's fail-open mutant.

## Commands (worktree, `apps/infrx-api` unless noted)
| Command | Exit | Result |
|---|---|---|
| `make api-env` (repo root) | 0 | pinned env synced |
| `uv run --frozen --no-sync pytest -q tests/g/test_catalog_truth.py -k provisional` (before fix) | 1 | 1 failed, 1 passed |
| `uv run --frozen --no-sync pytest -q tests/g/test_catalog_truth.py` (after) | 0 | 16 passed |
| `uv run --frozen --no-sync python -m tests.g.mutants provisional_flag_constant` / `provisional_flag_fails_open` / `unapproved_card_published` / `unpriced_model_published` | 0 each | 1/1 killed each |
| `INFRX_D_TASK=g7 uv run --frozen --no-sync pytest -q tests/g --ignore=tests/g/ops --ignore=tests/g/test_mutants.py` | 0 | 556 passed, 0 failed, 0 skipped |
| `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/g/test_mutants.py` | 0 | 395 passed (35 min) |
| `uv run --frozen --no-sync pytest -q tests/contracts/v2/test_published_model.py` | 0 | 98 passed |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | PASS |

`tests/g/ops` not run: the operations path is untouched (only `transition.unapproved` is imported, read-only).

## App-side consumer
`apps/app/tests/a/content.test.ts` (on `codex/app-a3`) is the consumer: it asserts the Provisional badge/note renders from `price.provisional` (lines 159-192). No App change: once the launch card is the listing's card and `ACTIVE_RATE_CARD_VERSION`, the page shows it without the badge. Its oracle comment "G7 publishes every card provisional" (line 159) becomes stale — A3 may reword it.

## Wiring requests
- None required.
- Optional (D10 + contract owner): carry `rate_card_versions.provisional` in `_ACTIVE_CARD`/`RateCardSnapshot` and OR it into `models.provisional`, only if a writer ever sets the column independently of `approved_by` (none does today).

## Open issues
- The A3 oracle comment above (text only).

## Estimate (remaining)
optimistic 0 h / likely 0.25 h (verify + merge) / pessimistic 1 h; confidence high; basis: 3-file diff, all listed suites green.
