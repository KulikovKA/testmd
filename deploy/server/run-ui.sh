#!/usr/bin/env sh
set -eu

: "${UAR_UI_HOST:?UAR_UI_HOST is required}"
: "${UAR_UI_PORT:?UAR_UI_PORT is required}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec "$script_dir/.venv/bin/python" "$script_dir/ui/server.py"
