#!/usr/bin/env sh
set -eu

: "${UAR_API_HOST:?UAR_API_HOST is required}"
: "${UAR_API_PORT:?UAR_API_PORT is required}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec "$script_dir/.venv/bin/python" -m uvicorn universal_agent_runtime.http_api:create_application_from_environment \
  --factory --host "$UAR_API_HOST" --port "$UAR_API_PORT"
