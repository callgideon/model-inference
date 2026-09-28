import dataclasses, importlib.util, shutil, sys
API = "/home/rey/workspace/rey/code/model-inference/.claude/worktrees/codex-w5-lab-rollout/apps/infrx-api"
sys.path.insert(0, API)
spec = importlib.util.spec_from_file_location("i_mutants", f"{API}/tests/i/mutants.py")
m = importlib.util.module_from_spec(spec); sys.modules["i_mutants"] = m; spec.loader.exec_module(m)
orig = m._layout
def fixed(root):
    api = orig(root)
    shutil.copytree(m.REPO / "infra" / "lab" / "observe", root / "infra" / "lab" / "observe")
    return api
mut = next(x for x in m.MUTANTS if x.name == "gateway_public_bind")
for label, layout in (("as-is", orig), ("with infra/lab/observe", fixed)):
    runner = dataclasses.replace(m.RUNNER, layout=layout)
    r = m._SHARED.run_mutant(mut, runner)
    print(label, r.outcome, str(r.detail)[:160])
