#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy app scripts
uv run lint-imports --config importlinter.ini
uv run pytest tests scripts/test_composition.py "$@"
