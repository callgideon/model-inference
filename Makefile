# Canonical check commands. Evidence reports cite these targets, not ad-hoc
# invocations, so every track runs the same thing (research/plan/08 §7).
API := apps/infrx-api

.PHONY: check api-env api-test api-mutants console-test console-lint console-typecheck console-mutants bench-test

# Pinned Python environment in apps/infrx-api/.venv. --frozen = use uv.lock as
# committed; only the coordinator regenerates it.
api-env:
	cd $(API) && uv sync --frozen --all-extras

api-test:
	cd $(API) && uv run --frozen pytest -q

# r1 R32: the whole mutation list (one pytest process per mutant, ~75s). The default
# suite runs a subset; a surviving mutant is a failed suite either way.
# Track mutant lists join here as their task merges (M1, Q1, J1); each gates on INFRX_MUTANTS.
api-mutants:
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/j/test_mutants.py

console-test:
	cd apps/app && pnpm test

console-lint:
	cd apps/app && pnpm lint

# tsc needs Next's generated route types (PageProps/LayoutProps); a checkout that
# never ran next dev/build/typegen fails without them.
console-typecheck:
	cd apps/app && pnpm exec next typegen && pnpm exec tsc --noEmit

# R32/R36: exported console conformance must kill every declared mutant.
# Track runners join here as their task merges (V1, U1, C1). Each exits non-zero on a survivor.
console-mutants:
	cd apps/app && pnpm test:mutants
	cd apps/app && node tests/v/run-mutants.mjs
	cd apps/app && node tests/u/run-mutants.mjs
	cd apps/app && node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs

# E1 owns models/marlin2b/tests. Until it exists this target reports "not run"
# rather than pretending a pass.
bench-test:
	@if [ -d models/marlin2b/tests ]; then \
		$(API)/.venv/bin/python -m pytest -q models/marlin2b/tests; \
	else \
		echo "bench-test: not run - models/marlin2b/tests does not exist yet (E1 owns it)"; \
	fi

check: api-test api-mutants console-test console-lint console-typecheck console-mutants bench-test
