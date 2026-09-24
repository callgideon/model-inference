# Canonical check commands. Evidence reports cite these targets, not ad-hoc
# invocations, so every track runs the same thing (research/plan/08 §7).
API := apps/infrx-api

.PHONY: integration check api-env api-test api-mutants console-test console-lint console-typecheck console-mutants bench-test

# Pinned Python environment in apps/infrx-api/.venv. --frozen = use uv.lock as
# committed; only the coordinator regenerates it.
api-env:
	cd $(API) && uv sync --frozen --all-extras

api-test:
	cd $(API) && uv run --frozen pytest -q

# r1 R32: the whole mutation list (one pytest process per mutant, ~75s). The default
# suite runs a subset; a surviving mutant is a failed suite either way.
# Track mutant lists join here as their task merges (M1, Q1, J1, W1, T1, D1, G1, M1-L2 — D's list needs Docker and skips visibly without it; M1-L2's S3 mutants skip visibly without INFRX_M_S3_ENDPOINT); each gates on INFRX_MUTANTS.
api-mutants:
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/q/test_valkey_mutants.py tests/q/test_reconcile_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/t/test_trace_mutants.py tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_code_mutants_d4.py tests/d/test_code_mutants_d5.py tests/d/test_signup.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/g/uploads/test_uploads_mutants.py tests/g/jobs/test_jobs_mutants.py tests/i/test_mutants.py tests/m/test_s3_mutants.py tests/m/test_pilot_mutants.py tests/w/test_worker_main_mutants.py

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
# The contracts runner covers both entries (v1 conformance and the v2 suites, F2P wire-in item 11).
console-mutants:
	cd apps/app && node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants
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

# Real service evidence is separate from unit checks; Docker absence must fail visibly.
# Optional arguments: make integration INTEGRATION_ARGS="--layer 1 --no-mutants"
integration:
	$(API)/.venv/bin/python tests/integration/run.py $(INTEGRATION_ARGS)
