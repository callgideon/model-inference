# Migrations: conventions

**State 2026-10-01.** Migrations 0001–0059 are applied on hosted Supabase. The last window applied 0057–0059 on 2026-09-30T08:01Z, MIGRATION_DIGEST `9566fa25…`. These files are hashed by the plan digest (`infrx/state/migrations.py`, `tests/integration/pgstate.py`), so they are immutable. Do not edit any of them, comments included. A fix goes in the next free number.

## The `LOCAL-ONLY` header is a merge-time marker

Files 0027–0059 carry a header such as `-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.` That sentence was true when the file merged. It is not the file's current hosted state, and it is never updated afterwards, because an edit would change the applied bytes.

**The state of record is the plan, not the header.** Which migrations are hosted, and since when, is recorded in:

- the coordinator session record `research/plan/evidence/coordinator/2026-09-24-session-03.md` (the window lines, for example lines 589–595);
- the log of `research/plan/consumer-v1/09-path-to-internal-testing.md`;
- the overlay field `deployed.hosted_schema` in `research/plan/evidence/coordinator/progress-state.json`.

## A new migration (0060 and later)

1. Only the lab-sql/D10 lane writes SQL. It takes the next free number at merge (R150) and starts the file with the same `LOCAL-ONLY` header.
2. A hosted apply needs R151's three conditions (R201): a KNOWN-GOOD re-proof through the new file, a reviewed edit of `EXPECTED_PENDING` in `infra/rollout/hosted-migrate.sh`, and an operator window. R264 governs how the window's commits are pushed.
3. After the window, the plan records the new hosted level. The file itself does not change.

Rulings: `research/plan/08-contracts-v1-encoding.md` §10 (R150, R151, R201, R264). Written by wave 6 lane plan-ledger (audit INT-06; coordinator decision 1 in audit §7).
