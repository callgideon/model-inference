# Canonical check commands. Evidence reports cite these targets, not ad-hoc
# invocations, so every track runs the same thing (research/plan/08 §7).
API := apps/infrx-api

.PHONY: check api-env api-test console-test console-lint bench-test

# Pinned Python environment in apps/infrx-api/.venv. --frozen = use uv.lock as
# committed; only the coordinator regenerates it.
api-env:
	cd $(API) && uv sync --frozen --all-extras

api-test:
	cd $(API) && uv run --frozen pytest -q

console-test:
	cd apps/app && pnpm test

console-lint:
	cd apps/app && pnpm lint

# E1 owns models/marlin2b/tests. Until it exists this target reports "not run"
# rather than pretending a pass.
bench-test:
	@if [ -d models/marlin2b/tests ]; then \
		$(API)/.venv/bin/python -m pytest -q models/marlin2b/tests; \
	else \
		echo "bench-test: not run - models/marlin2b/tests does not exist yet (E1 owns it)"; \
	fi

check: api-test console-test console-lint bench-test
