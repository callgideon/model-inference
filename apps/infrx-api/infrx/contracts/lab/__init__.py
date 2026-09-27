"""Contracts for the Lab (F3): datasets, evaluation, training and rollout.

Additive: nothing in v1 or v2 imports this package, and no v1/v2 file changed
(`tests/contracts/lab/test_lab_contracts.py` pins their bytes).

* `records` - immutable content-addressed refs, the eleven `lab.*.1` record schemas,
  one validator (`parse` / `validate`) with five refusal reasons, idempotency keys,
  the four data-rights gates and the stable rollout cohort.
* `states` - the case/attempt/run/harness/checkpoint/annotation/external-run machines.
* `fakes` - an in-memory, provider-scoped, content-addressed catalog.

The TypeScript half is `packages/shared/contracts/lab/` and reads the same fixtures.
"""
