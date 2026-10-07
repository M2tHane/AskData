#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --frozen uvicorn app.main:app --host "${ASKDATA_API_HOST:-127.0.0.1}" --port "${ASKDATA_API_PORT:-8000}" --no-access-log "$@"
