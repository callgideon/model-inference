# Checkpoint 2: the three mutation-list items (codex/ckpt2-lists)

Base `4600c37` (integration head `claude/backend-impl` after checkpoint 2, code tree `27af05a`).
Fix lane: tests and mutant lists only. **No product code changed**, and none of the three
items is a product defect. `TMPDIR=/tmp/claude-1000/lists-tmp` (outside the checkout)
throughout. No containers, ports, AWS, box or secrets were used.

| Item | Was | Commit | Now |
|---|---|---|---|
| 1 contracts `attach_takes_a_ref_it_never_made` | `broken_runner` (`AttributeError@media.py`) | `a977f48` | killed |
| 2 E4B `engine_target_skips_to_ledger` | survived | `18ab3e9` | killed |
| 2 E4B `published_digest_read_from_the_record` | survived | `18ab3e9` | killed |
| 3 W3 `sigint_not_handled` | `broken_runner` (`TimeoutExpired`) in checkpoint runs | none (evidence only) | killed 3/3 in the foreground; the cause is the detached launch, not load (below) |

## 1. contracts: `attach_takes_a_ref_it_never_made`

**What was wrong.** The mutant replaced the fake's attach guard
`if indexed is None or indexed.digest != ref.digest:` with `if False:`. For the case's first
forged ref (never materialized), `indexed` is `None`, so `owned` became `(None,)`. Before
`2972df1`, that `None` was bound silently and the case asserted `forged ref 0 was attached`.
MPILOT's write-once attach added `len({ref.handle for ref in owned})`, which now reads
`None.handle`. The mutant therefore died by an `AttributeError` inside the fake, which is
not an assertion the case makes. The product guard is intact: on the unmutated code a
`None` never reaches that line.

**Fix (`a977f48`).** The mutant now reproduces the shape the fake had before F2R item 4:
the store is not consulted, and the caller's copy is bound. The three-line anchor
(guard, `raise`, `owned.append(indexed)`) becomes `owned.append(ref)`. It stays a single
edit, needs no `dies_by`, and the case and the invariant are unchanged.

Kill line (the mutant applied to a copy, case `media_sec__a_partial_request_stages_nothing`):

```
>                   raise AssertionError(f"forged ref {n} was {what}")
E                   AssertionError: forged ref 0 was attached
infrx/contracts/conformance/services.py:368: AssertionError
```

Run tails:

```
$ INFRX_MUTANTS=all .venv/bin/python -m pytest -q tests/contracts/test_mutants.py -k "attach_takes_a_ref_it_never_made or well_formed or covered_by_a_mutant"
3 passed, 445 deselected in 6.56s
$ .venv/bin/python -m tests.contracts.mutants attach_takes_a_ref_it_never_made
[killed       ] attach_takes_a_ref_it_never_made: 1 failed, 787 deselected in 0.75s
1/1 killed
$ .venv/bin/python -m pytest -q tests/contracts/test_conformance.py            # pristine
181 passed in 4.77s
```

A wider selection also passed: the neighbouring `fake_attach_*` and `staging_takes_a_ref_*`
mutants, plus the pristine-baseline self-test (`7 passed, 441 deselected`).

## 2. E4B: two survivors

**`engine_target_skips_to_ledger`** (`if not target["metered"]:` becomes `if False:`), case
`test_e4b_the_dataset_drill_pends_on_the_owner_it_needs_and_passes_only_reconciled`.
Since `f956bab`, the engine target (no ledger at all) and a metered target without
DATABASE_URL both return `PENDING` owned by `BOX`. Under the mutant, the engine target
falls through to the second branch and returns the same status and owner. The case
checked only status and owner, so the mutant survived. The witness is now the reason the
engine target gives: `assert "metered endpoint" in entry["detail"]`. The E4B rule still
holds, because the check keeps `PENDING` / `["BOX"]`. The docstring now says the ledger
half is PENDING on BOX, where it said D5.

```
E       AssertionError: client invariants hold; `infrx.operations.cli.build_operations` refuses without the deployment's DATABASE_URL, so the tenant's ledger cannot be read here
E       assert 'metered endpoint' in "client invariants hold; `infrx.operations.cli.build_operations` refuses without the deployment's DATABASE_URL, so the tenant's ledger cannot be read here"
```

**`published_digest_read_from_the_record`** (`current_config` reads the record's digest
where it should read G6B's published one), case
`test_e4b_the_config_pin_names_every_setting_that_moved_past_its_evidence`. G6B has
published the record's digest since `351d084`, and the endpoint document was regenerated
with it in `c761d6a`. On the real tree the two sources therefore agree, and the case's
equality assertion could not tell them apart. The witness now moves the published release
(`published_release` monkeypatched to a `sha256:44…` digest). It requires the pin to name
exactly `published_engine_options_digest`. This is a tree check that the local target can
judge, so it involves no BOX.

```
E       AssertionError: assert [] == ['published_e...tions_digest']
E         Right contains one more item: 'published_engine_options_digest'
```

Run tails (from the repository root):

```
$ .venv/bin/python tests/integration/backend/e4b_mutants.py engine_target_skips_to_ledger published_digest_read_from_the_record
[killed       ] engine_target_skips_to_ledger: 1 failed, 28 deselected in 0.92s
[killed       ] published_digest_read_from_the_record: 1 failed, 28 deselected in 0.94s
2/2 killed
$ INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_e4b_mutants.py
140 passed in 310.06s (0:05:10)          # 138 mutants killed + the 2 list checks; 05:11:13-05:16:23Z, load 5.91 -> 7.15
$ apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/backend/test_endpoint_doc.py
39 passed in 1.48s
```

## 3. W3: `sigint_not_handled`

Foreground runs, alone (`cd apps/infrx-api && INFRX_MUTANTS=all .venv/bin/python -m pytest -q tests/w/test_w3_mutants.py -k sigint_not_handled`):

| Run | Start (UTC) | `uptime` load before -> after | Result |
|---|---|---|---|
| 1 | 05:06:32 | 4.15, 5.20, 5.47 -> 4.55, 5.22, 5.47 | `1 passed, 86 deselected in 22.26s` (killed) |
| 2 | 05:16:34 | 7.04, 6.29, 5.85 -> 6.68, 6.26, 5.86 | `1 passed, 86 deselected in 21.96s` (killed) |
| 3 | 05:20:56 | 5.28, 5.54, 5.64 -> 4.92, 5.44, 5.60 | `1 passed, 86 deselected in 21.78s` (killed) |

The mutant was killed every time it ran alone. It was also killed at load 7, the highest
load in this session. **Load is not the cause.**

**Cause: the detached launch.** `checkpoint2.sh` is started with
`setsid nohup bash … &`. A background job of a non-interactive shell starts with SIGINT
ignored (`SigIgn: …06`, which is SIGINT plus SIGQUIT), and every process below it
inherits that. On the unmutated tree, `WorkerService.serve` installs its own SIGINT handler
with `add_signal_handler`, so the case passes either way. Under the mutant nothing installs
a handler, and the child also inherits the ignored SIGINT. It ignores the case's SIGINT
and keeps serving its 60 s attempt. `child.communicate(timeout=20)` then raises
`TimeoutExpired`, and the runner correctly classifies that as `broken_runner`. The same
root cause was recorded for G2's run (`research/plan/evidence/g/G2-e5e7d3a.md`: "an
artifact of detached execution"), and `checkpoint2.sh`'s header already excludes
`bench-test` for this reason.

It reproduces exactly at low load. The same command, launched with SIGINT set to `SIG_IGN`
before `exec` (what `nohup … &` passes down), ran 05:18:33-05:19:15Z at load 4.74:

```
E       AssertionError: sigint_not_handled is broken_runner (SIGINT drains like SIGTERM): undeclared exception deaths ['TimeoutExpired@subprocess.py']: 1 failed, 13 deselected in 22.32s. ...
1 failed, 86 deselected in 41.90s
```

A scratch probe ran the SIGINT half of the case under an inherited `SIG_IGN`
(`serve_and_terminate(60.0, 0.2, SIGINT)`, on a runner layout copy):

```
pristine as committed    -> (0, {... 'released': ['00000001-…'], 'state': 'running'}) (0.8s)
pristine proposed reset  -> (0, {... 'released': ['00000001-…'], 'state': 'running'}) (0.8s)
mutant  as committed    -> TimeoutExpired (20.5s)
mutant  proposed reset  -> (-2, {}) (0.6s)          # `assert status == 0` fails: an assertion kill
```

**Proposed, not applied** (the item says to change nothing when the mutant dies alone).
Make the case independent of how the suite was launched. Add one line at the top of
`CHILD` in `apps/infrx-api/tests/w/test_service.py`:

```python
import signal; signal.signal(signal.SIGINT, signal.default_int_handler)
```

With this line, the child behaves like a process started by a service manager or a
terminal, which is the situation the case describes. The pristine drain does not change,
and the mutant dies on the case's `status == 0` assertion under both foreground and
detached launches. The alternative, running the `w3-sigint` stage outside
`nohup … &`, fixes only one caller of `make api-mutants`. A load-tolerant bound is not
needed: the drain is below 1 s in every run above.

## Verification log

- 2026-09-24: Items 1 and 2 fixed in `a977f48` and `18ab3e9` (tests and lists only). The
  three foreground runs, the detached reproduction and the probe for item 3 are recorded
  above. Nothing pushed; nothing created outside `/tmp/claude-1000/lists-tmp` and the
  scratchpad (both cleaned).
