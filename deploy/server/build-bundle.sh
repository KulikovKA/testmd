#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
output=${1:-"$repository_root/dist/server"}
expected_prefix="$repository_root/dist/"

case "$output" in
  "$expected_prefix"*) ;;
  *) printf '%s\n' "output must be below $repository_root/dist" >&2; exit 64 ;;
esac

for required in \
  "$repository_root/src/universal_agent_runtime" \
  "$repository_root/agent_image" \
  "$repository_root/ui" \
  "$script_dir/pyproject.toml" \
  "$script_dir/.env.example" \
  "$script_dir/build-agent-image.sh" \
  "$script_dir/universal-agent-runtime.service" \
  "$script_dir/universal-agent-runtime-ui.service"; do
  test -e "$required" || { printf '%s\n' "missing required deployment input: $required" >&2; exit 66; }
done

rm -rf "$output"
mkdir -p "$output/src"
cp "$script_dir/pyproject.toml" "$output/pyproject.toml"
cp "$script_dir/.env.example" "$output/.env.example"
cp "$script_dir/run-orchestrator.sh" "$output/run-orchestrator.sh"
cp "$script_dir/run-ui.sh" "$output/run-ui.sh"
cp "$script_dir/build-agent-image.sh" "$output/build-agent-image.sh"
cp "$script_dir/universal-agent-runtime.service" "$output/universal-agent-runtime.service"
cp "$script_dir/universal-agent-runtime-ui.service" "$output/universal-agent-runtime-ui.service"
cp "$script_dir/README.md" "$output/README.md"
cp -R "$repository_root/src/universal_agent_runtime" "$output/src/"
cp -R "$repository_root/agent_image" "$output/"
cp -R "$repository_root/ui" "$output/"
find "$output" -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
find "$output" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
chmod 0755 "$output/build-agent-image.sh" 2>/dev/null || true
chmod 0755 "$output/run-orchestrator.sh" "$output/run-ui.sh"
printf '%s\n' "Deployment bundle created: $output"
