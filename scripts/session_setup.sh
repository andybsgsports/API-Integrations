#!/usr/bin/env bash
# SessionStart hook: prepare the project for tests/linters in a fresh container.
# Idempotent — safe to run on every session start.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip >/dev/null
pip install --quiet -e ".[dev]" >/dev/null
echo "sanmar-netsuite dev environment ready (.venv active; run: pytest, ruff check src tests, mypy)"
