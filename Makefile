# Canonical check commands. Evidence reports cite these targets, not ad-hoc
# invocations, so every track runs the same thing (research/plan/08 §7).
API := apps/infrx-api

.PHONY: integration consumer-local backend-certify app-e2e backend-local check api-env api-test api-mutants console-test console-lint console-typecheck console-mutants bench-test console-c0-real console-pg console-c3a-real console-u3-real console-c3f-real lab-test lab-lint lab-typecheck lab-build lab-mutants lab-e2e lab-operate lab-evaluate lab-observe lab-rollout lab-improve lab-compositions

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
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/m/test_pilot_mutants.py tests/m/test_s3_mutants.py tests/m/test_retention_mutants.py tests/q/test_mutants.py tests/q/test_valkey_mutants.py tests/q/test_reconcile_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_prep_worker_mutants.py tests/w/test_w5_mutants.py tests/t/test_trace_mutants.py tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_code_mutants_d4.py tests/d/test_code_mutants_d5.py tests/d/test_signup.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/g/uploads/test_uploads_mutants.py tests/g/jobs/test_jobs_mutants.py tests/i/test_mutants.py tests/contracts/lab/test_mutants.py tests/h/test_mutants.py tests/t/ship/test_mutants.py tests/t/feedback/test_mutants.py tests/t/retention/test_mutants.py tests/d/test_code_mutants_l2sql.py tests/d/test_code_mutants_d6f.py tests/d/test_code_mutants_d7.py tests/n/imports/test_mutants.py tests/n/versions/test_mutants.py tests/d/test_upgrade_lab.py tests/g/feedback/test_mutants.py tests/b/runner/test_mutants.py tests/b/reports/test_mutants.py tests/b/checkpoints/test_mutants.py tests/i/lab_eval/test_mutants.py tests/j/submit/test_mutants.py tests/r/control/test_mutants.py tests/r/optimization/test_mutants.py tests/p/annotations/test_mutants.py tests/p/training/test_mutants.py tests/n/lineage/test_mutants.py tests/g/lab_auth/test_mutants.py tests/g/lab_control/test_mutants.py tests/g/lab_traces/test_mutants.py tests/r/routing/test_mutants.py tests/content/test_mutants.py tests/g/trace_export/test_mutants.py tests/j/calibration/test_mutants.py tests/p/teachers/test_mutants.py tests/g/lab_evaluations/test_mutants.py tests/g/lab_pipelines/test_mutants.py tests/g/lab_releases/test_mutants.py tests/w/test_lab_workers_mutants.py tests/g/lab_datasets/test_mutants.py tests/g/lab_checkpoints/test_mutants.py
	# Lab PG mutant lists run in their own process: their pristine baseline starts the D harness itself (LW1 integration F1)
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/access/test_mutants.py tests/h/test_mutants.py tests/d/test_l2sql_self_mutants.py tests/d/test_code_mutants_d6j.py tests/d/test_code_mutants_l3sql.py tests/d/test_code_mutants_d9.py tests/l/control/test_mutants.py tests/d/test_code_mutants_c2rpc.py tests/d/test_code_mutants_d8.py
	INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_e4b_mutants.py
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab/test_mutants.py && cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_operate/test_mutants.py
	cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_evaluate/test_mutants.py
	cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_observe/test_mutants.py
	cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_rollout/test_mutants.py
	cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_improve/test_mutants.py
	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_pipeline/test_mutants.py tests/i/lab_rollout/test_mutants.py

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
	cd apps/app && node tests/u/run-mutants.mjs
	cd apps/app && node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs
	cd apps/app && node tests/a/run-mutants.mjs
	cd apps/app && node tests/a/run-catalog-mutants.mjs
	cd apps/app && node tests/c/feedback/run-mutants.mjs

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

# C3F FEEDBACK-ACK / LAB-ACCESS: App own-feedback + Lab review doors over real Supabase PostgreSQL + PostgREST,
# then the SQL mutants of the doors (Docker; fails visibly without it). Not part of check.
console-c3f-real:
	cd $(API) && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/feedback/stack.py && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/feedback/stack.py --mutants

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

# LW0 (R154): the Lab is a standalone package like apps/app (its own lockfile; no pnpm workspace).
# Install once: cd apps/lab && pnpm install --frozen-lockfile. Until L1 lands apps/lab/app,
# typecheck and build report "not run" rather than a pass, as bench-test does.
# LW0 fix C1: without that install, the targets that run the Lab's own binaries fail fast and
# name the install (as gates.py app_e2e does for apps/app) - a failure, never a "not run".
LAB_INSTALLED = test -d apps/lab/node_modules || { echo "$@: FAIL - apps/lab/node_modules missing: cd apps/lab && pnpm install --frozen-lockfile" >&2; exit 1; }

lab-test:
	cd apps/lab && pnpm test

lab-lint:
	@$(LAB_INSTALLED)
	cd apps/lab && pnpm lint

lab-typecheck:
	@if [ -d apps/lab/app ]; then \
		$(LAB_INSTALLED); \
		cd apps/lab && pnpm exec next typegen && pnpm exec tsc --noEmit; \
	else \
		echo "lab-typecheck: not run - apps/lab/app does not exist yet (L1 owns it)"; \
	fi

lab-build:
	@if [ -d apps/lab/app ]; then \
		$(LAB_INSTALLED); \
		cd apps/lab && pnpm build; \
	else \
		echo "lab-build: not run - apps/lab/app does not exist yet (L1 owns it)"; \
	fi

# LAB-E2E: the Lab e2e suites (apps/lab/tests/e2e: observe, evaluate, improve, rollout - the E5L o10,
# E6L j10, E7L i08 and E8L k10 UI cells) against the built Lab app on the l4 key; not in check (Docker).
# Their stack mutant list: LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs (in apps/lab).
lab-e2e:
	@$(LAB_INSTALLED)
	cd apps/lab && pnpm build && LAB_E2E_BUILT=1 LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test --test-concurrency=1 tests/e2e/observe/stack.test.ts tests/e2e/evaluate/stack.test.ts tests/e2e/improve/stack.test.ts tests/e2e/rollout/stack.test.ts

# Lab mutant runners join here as their lanes merge (and console-mutants' tests/v line when V1M
# removes tests/v); each exits non-zero on a survivor.
lab-mutants:
	cd apps/lab && node tests/l/shell/run-mutants.mjs && node tests/l/ui/run-mutants.mjs
	cd apps/lab && node tests/c/review/run-mutants.mjs
	cd apps/lab && node tests/v/detail/run-mutants.mjs && node tests/v/judge/run-mutants.mjs && node tests/v/list/run-mutants.mjs
	cd apps/lab && node tests/c/judge/run-mutants.mjs
	cd apps/lab && node tests/r/run-mutants.mjs
	cd apps/lab && node tests/p/run-mutants.mjs
	cd apps/lab && node tests/c/content/run-mutants.mjs
	cd apps/lab && node tests/b/run-mutants.mjs
	cd apps/lab && node tests/n/run-mutants.mjs

check: api-test api-mutants console-test console-lint console-typecheck console-mutants console-built bench-test lab-test lab-lint lab-typecheck lab-build lab-mutants

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

# WR-C3-MK: the COMPOSITION-3 real-Postgres composition proofs sit outside the mutant runners;
# this runs each on its own tasklocal key (docker; not in check). The checkpoints proof runs on p3
# because the foreign infrx-b3-postgres holds the b3 key.
lab-compositions:
	cd $(API) && INFRX_D_TASK=p2 .venv/bin/python -m pytest -q tests/p/teachers/test_teachers_composition_pg.py
	cd $(API) && INFRX_D_TASK=j2 .venv/bin/python -m pytest -q tests/j/calibration/test_calibration_composition_pg.py
	cd $(API) && INFRX_D_TASK=r1 .venv/bin/python -m pytest -q tests/r/routing/test_routing_composition_pg.py
	cd $(API) && INFRX_D_TASK=p3 .venv/bin/python -m pytest -q tests/p/training/test_training_composition_pg.py
	cd $(API) && INFRX_D_TASK=p3 .venv/bin/python -m pytest -q tests/b/checkpoints/test_checkpoints_composition_pg.py
	cd $(API) && INFRX_D_TASK=p2 .venv/bin/python -m pytest -q tests/g/lab_pipelines/test_lab_teachers_composition_pg.py tests/w/test_lab_workers_teachers_pg.py
	cd $(API) && INFRX_D_TASK=n3 .venv/bin/python -m pytest -q tests/w/test_lab_workers_lineage_pg.py
	cd $(API) && INFRX_D_TASK=p3 .venv/bin/python -m pytest -q tests/b/checkpoints/test_checkpoints_sources_pg.py
	cd $(API) && INFRX_D_TASK=j2 .venv/bin/python -m pytest -q tests/w/test_lab_workers_judge_pg.py
	cd $(API) && INFRX_D_TASK=n3 .venv/bin/python -m pytest -q tests/w/test_lab_workers_imports_pg.py
	cd $(API) && INFRX_D_TASK=r2 .venv/bin/python -m pytest -q tests/r/control/test_control_pass_pg.py

# E3L: the LAB-OPERATE gate (tests/integration/lab_operate); not in check. verdict.json lands in the evidence dir.
lab-operate:
	$(API)/.venv/bin/python tests/integration/lab_operate/runner.py --out $(CURDIR)/research/plan/evidence/e/E3L-raw-$(shell git rev-parse --short HEAD)

# E6L: the LAB-EVALUATE gate (tests/integration/lab_evaluate); not in check. verdict.json lands in the evidence dir.
lab-evaluate:
	$(API)/.venv/bin/python tests/integration/lab_evaluate/runner.py --out $(CURDIR)/research/plan/evidence/e/E6L-raw-$(shell git rev-parse --short HEAD)

# E5L: the LAB-OBSERVE gate (tests/integration/lab_observe); not in check. verdict.json lands in the evidence dir.
# INFRX_E5L_PROJECT=e5l2 runs it under compose project infrx-e5l2 (same ports; the foreign infrx-e5l_* volumes are never touched).
lab-observe:
	$(API)/.venv/bin/python tests/integration/lab_observe/runner.py --out $(CURDIR)/research/plan/evidence/e/E5L-raw-$(shell git rev-parse --short HEAD)

# E8L: the LAB-ROLLOUT gate (tests/integration/lab_rollout); not in check. verdict.json lands in the evidence dir.
lab-rollout:
	$(API)/.venv/bin/python tests/integration/lab_rollout/runner.py --out $(CURDIR)/research/plan/evidence/e/E8L-raw-$(shell git rev-parse --short HEAD)

# E7L: the LAB-IMPROVE gate (tests/integration/lab_improve); not in check. verdict.json lands in the evidence dir.
lab-improve:
	$(API)/.venv/bin/python tests/integration/lab_improve/runner.py --out $(CURDIR)/research/plan/evidence/e/E7L-raw-$(shell git rev-parse --short HEAD)
