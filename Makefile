.PHONY: install test lint typecheck fmt check clean

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check src tests

fmt:
	. .venv/bin/activate && ruff check --fix src tests

typecheck:
	. .venv/bin/activate && mypy

check: lint typecheck test

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
