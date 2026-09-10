#!/usr/bin/env sh
set -eu

image_tag=${1:-uar-agent:0.1.0}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec docker build --pull=false --tag "$image_tag" "$script_dir/agent_image"
