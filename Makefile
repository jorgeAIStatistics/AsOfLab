# AsOfLab developer commands. Every target works from a fresh clone.
#
# `make setup` uses uv when it is installed and falls back to a plain virtual
# environment with pip and the exported lock file otherwise.

PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
# Port for `make serve`. When 8000 is taken on your machine: make serve PORT=8020
PORT ?= 8000

.PHONY: setup test test-fast lint demo ui serve bench experiments validation results clean

setup:
	@if command -v uv >/dev/null 2>&1; then \
		echo "Using uv"; uv sync --all-extras --frozen; \
	else \
		echo "uv not found: using $(PYTHON) -m venv and requirements.lock"; \
		$(PYTHON) -m venv $(VENV) && \
		$(BIN)/python -m pip install --quiet --upgrade pip && \
		$(BIN)/python -m pip install --quiet --require-hashes -r requirements.lock && \
		$(BIN)/python -m pip install --quiet --no-deps -e .; \
	fi
	@$(BIN)/asoflab version

test:
	$(BIN)/python -m pytest -q

test-fast:
	$(BIN)/python -m pytest -q -m "not slow" tests/unit

lint:
	$(BIN)/ruff check src tests benchmarks scripts
	$(BIN)/ruff format --check src tests benchmarks scripts
	$(BIN)/mypy

demo:
	$(BIN)/asoflab demo

ui:
	cd web && npm ci && npm run build

serve:
	$(BIN)/asoflab serve --runs runs/demo --port $(PORT)

bench:
	$(BIN)/python benchmarks/run_benchmarks.py --profile standard

experiments:
	$(BIN)/asoflab experiment matrix --out results/experiments --workers 4

validation:
	$(BIN)/python scripts/seeded_bug_report.py --trials 10
	$(BIN)/python scripts/recovery_report.py

results:
	$(BIN)/python scripts/render_results.py

clean:
	rm -rf runs .pytest_cache .mypy_cache .ruff_cache .hypothesis tests/counterexamples
