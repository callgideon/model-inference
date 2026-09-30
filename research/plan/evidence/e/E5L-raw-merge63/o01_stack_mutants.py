"""Merge #63: the o01 stack mutants against a pristine baseline of o01's own cases only
(o10's baseline needs the l4 key, held by merge #62). Run from the repo root."""
import importlib.util, pathlib, sys
HERE = pathlib.Path("tests/integration/lab_observe").resolve()
spec = importlib.util.spec_from_file_location("e5l_mutants", HERE / "mutants.py")
ml = importlib.util.module_from_spec(spec); sys.modules["e5l_mutants"] = ml; spec.loader.exec_module(ml)
why = ml.claim_the_kept_stack()
assert why is None, why
names = sys.argv[1:]
chosen = [m for m in ml.STACK_MUTANTS if m.name in names]
assert len(chosen) == len(names), names
cases = tuple(sorted({c for m in chosen for c in m.cases}))
print("baseline cases:", cases, flush=True)
refused = ml.shared.pristine(cases, ml.STACK_RUNNER)
print("pristine:", refused or "PASS", flush=True)
if refused:
    raise SystemExit(2)
survivors = 0
for m in chosen:
    r = ml.shared.run_mutant(m, ml.STACK_RUNNER)
    print(f"{m.name}: {r.outcome.value} -> {r.detail}", flush=True)
    survivors += not r.killed
print(f"{len(chosen) - survivors} killed, {survivors} not killed")
raise SystemExit(1 if survivors else 0)
