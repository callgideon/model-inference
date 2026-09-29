"""0-LO3-RV-1 red/green: st_worker_never_restarted with branch B forced (the first worker
finishes the job before its kill). 'red' is the handback's case (no premise check), 'green'
the fix."""
import os, sys, time
os.environ["INFRX_E2_NAMESPACE"] = "e5l"
import importlib.util
spec = importlib.util.spec_from_file_location("e5l_mutants", "tests/integration/lab_observe/mutants.py")
m = importlib.util.module_from_spec(spec); sys.modules["e5l_mutants"] = m; spec.loader.exec_module(m)
import pathlib
_orig_pytest = m.shared._pytest
_n = [0]
def _save(*a, **k):
    done = _orig_pytest(*a, **k)
    _n[0] += 1
    pathlib.Path("/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/lo3fix/forced-%d.out" % _n[0]).write_text((done.stdout or "") + "\n--- stderr\n" + (done.stderr or ""))
    return done
m.shared._pytest = _save
print("claim:", m.claim_the_kept_stack(), flush=True)
old = ('        trip.box.start("worker")\n        trip.box.kill("worker")\n        time.sleep(1.0)\n'
       '        trip.box.start("worker")\n')
new = ('        trip.box.start("worker")\n'
       '        world.terminal(trip, accepted.json()["request_id"], timeout=60.0)\n'
       '        trip.box.kill("worker")\n        time.sleep(1.0)\n')
premise = '        assert worker is not None and worker.poll() is None, "premise: the worker came back"\n'
for name in sys.argv[1:]:
    mu = m._m(f"forced_{name}", "the drill judges the job after the worker came back", m.TRACE,
              old + ("" if name == "red" else "        # The premise"),
              new + ("" if name == "red" else "        # The premise"), m.O09_WORKER)
    orig = m.shared._prepare
    def prep(api, mutant, runner, red=(name == "red")):
        refused = orig(api, mutant, runner)
        if red and refused is None:
            p = api / m.TRACE
            p.write_text(p.read_text().replace(premise, "        pass\n"))
        return refused
    m.shared._prepare = prep
    t = time.time()
    m.shared.pristine = lambda *a, **k: None   # probe only: the pristine baseline is the full lists' own
    r = m.shared.run_mutant(mu, m.STACK_RUNNER)
    m.shared._prepare = orig
    print(name, r.outcome, f"{time.time()-t:.0f}s", r.detail[:400], flush=True)
