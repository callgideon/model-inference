# Canonical check commands. Evidence reports cite these targets, not ad-hoc
# invocations, so every track runs the same thing (research/plan/08 §7).
API := apps/infrx-api

.PHONY: integration consumer-local backend-certify app-e2e backend-local check api-env api-test api-mutants console-test console-lint console-typecheck console-mutants bench-test console-c0-real console-pg console-c3a-real console-u3-real

# Pinned Python environment in apps/infrx-api/.venv. --frozen = use uv.lock as
# committed; only the coordinator regenerates it.
api-env:
	cd $(API) && uv sync --frozen --all-extras

api-test:
	cd $(API) && uv run --frozen pytest -q

# r1 R32: the whole mutation list (one pytest process per mutant, ~75s). The default
# suite runs a subset; a surviving mutant is a failed suite either way.
# Track mutant lists join here as their task merges (M1, M pilot, M1-L2, Q1, J1, W1, T1, D1-D5, G1, I; E4B below — D's list needs Docker and skips visibly without it; M1-L2's S3 mutants skip visibly without INFRX_M_S3_ENDPOINT); each gates on INFRX_MUTANTS.
# E4B's list lives beside its runner in tests/integration/backend (outside apps/infrx-api), so it
# runs from the root with the pinned interpreter, through the same shared R83 runner.
api-mutants:
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/m/test_pilot_mutants.py tests/m/test_s3_mutants.py tests/m/test_retention_mutants.py tests/q/test_mutants.py tests/q/test_valkey_mutants.py tests/q/test_reconcile_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_prep_worker_mutants.py tests/t/test_trace_mutants.py tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_code_mutants_d4.py tests/d/test_code_mutants_d5.py tests/d/test_signup.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/g/uploads/test_uploads_mutants.py tests/g/jobs/test_jobs_mutants.py tests/i/test_mutants.py
	INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_e4b_mutants.py

console-test:
	cd apps/app && pnpm test

console-lint:
	cd apps/app && pnpm lint

# tsc needs Next's generated route types (PageProps/LayoutProps); a checkout that
# never ran next dev/build/typegen fails without them.
console-typecheck:
	cd apps/app && pnpm exec next typegen && pnpm exec tsc --noEmit

# R32/R36: exported console conformance must kill every declared mutant.
# Track runners join here as their task merges (V1, U1, C1, A2, A3). Each exits non-zero on a survivor.
# The contracts runner covers both entries (v1 conformance and the v2 suites, F2P wire-in item 11).
console-mutants:
	cd apps/app && node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants
	cd apps/app && node tests/v/run-mutants.mjs
	cd apps/app && node tests/u/run-mutants.mjs
	cd apps/app && node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs
	cd apps/app && node tests/a/run-mutants.mjs
	cd apps/app && node tests/a/run-catalog-mutants.mjs

# C0 CONSOLE-TENANT through real Supabase PostgreSQL + PostgREST (Docker; fails visibly without it).
# Gate for C0 / APP-M1 and E3A; rerun on the merged SHA once 0022 lands (WR-7).
console-c0-real:
	cd $(API) && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/stack.py

# C3A DUR-RLS / CONSOLE-FLOWS: the trusted actions through real Supabase PostgreSQL + PostgREST
# (Docker; fails visibly without it). Gate for C3A / APP-M1 and E4.
console-c3a-real:
	cd $(API) && INFRX_D_TASK=app-c3a INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/actions_stack.py

# U3 DUR-RLS / DUR-CAP / CONSOLE-FLOWS: operator console over real Supabase PostgreSQL + PostgREST (Docker).
console-u3-real:
	cd $(API) && INFRX_D_TASK=app-u3 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/u/operator_stack.py

# U1R/U4: the App's read adapters against real PostgreSQL as the browser principal, each on its
# own task-local instance (D harness). A missing Docker prints SKIP and exits 0, as tests/d does.
console-pg:
	cd $(API) && INFRX_D_TASK=app-u1r uv run --frozen python ../app/tests/u/credit_world.py
	cd $(API) && INFRX_D_TASK=app-u4 uv run --frozen python ../app/tests/u/request_world.py

# E1 owns models/marlin2b/tests. Until it exists this target reports "not run"
# rather than pretending a pass.
bench-test:
	@if [ -d models/marlin2b/tests ]; then \
		$(API)/.venv/bin/python -m pytest -q models/marlin2b/tests; \
	else \
		echo "bench-test: not run - models/marlin2b/tests does not exist yet (E1 owns it)"; \
	fi

# I2A WR-I2A-2: the built-bundle cases (I2A-BUILT-01/02) need .next; without a build they skip visibly.
console-built:
	cd apps/app && pnpm build && node --test tests/i2a/*.test.ts

check: api-test api-mutants console-test console-lint console-typecheck console-mutants console-built bench-test

# Real service evidence is separate from unit checks; Docker absence must fail visibly.
# Optional arguments: make integration INTEGRATION_ARGS="--layer 1 --no-mutants"
integration:
	$(API)/.venv/bin/python tests/integration/run.py $(INTEGRATION_ARGS)

# E2C: the local verification gates (tests/integration/ENVIRONMENT.md). Each script exits
# 0 PASS, 1 FAIL, 3 BLOCKED/NOT RUN, 4 INVALID and prints its verdict.json path; make turns
# any nonzero into 2, so read the verdict (or run the script) for the exact class.
# GATE_ARGS examples: "--out DIR", "--break-seam readiness", "--certify-profile P -- --scale tiny".
consumer-local:
	tests/integration/consumer-local.sh $(GATE_ARGS)

backend-certify:
	tests/integration/backend-certify.sh $(GATE_ARGS)

app-e2e:
	tests/integration/app-e2e.sh $(GATE_ARGS)

# E3C: the local backend gate on its own namespace; verdict.json lands in E3C_OUT.
backend-local:
	$(API)/.venv/bin/python tests/integration/backend/e3c/runner.py --out "$${E3C_OUT:-$${TMPDIR:-/tmp}/infrx-e3c}" $(E3C_ARGS)
