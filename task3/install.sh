#!/usr/bin/env bash
# Install deps, build the Docker image, and serve the /extract API.
#   ./install.sh             # bootstrap uv + uv sync + build + run
#   ./install.sh --restart   # rebuild image with current code + restart container
#
# --restart skips the uv bootstrap and the local 'uv sync' step (those only
# need to run once per dependency change), but it DOES re-run 'docker build'
# so any code edits under src/ or prompts/ make it into the running image.
# 'docker build' reuses cached layers when pyproject.toml/uv.lock are
# unchanged, so the rebuild on a code-only edit is fast.

set -euo pipefail

IMAGE="sec_toolbox:latest"
CONTAINER="sec_toolbox"
PORT="${PORT:-8080}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Auto-load env vars from task3/.env if present so the API key doesn't need
# to be re-exported in every shell. Variables already in the shell env take
# precedence — .env only fills in what's missing.
if [[ -f .env ]]; then
  while IFS='=' read -r key val; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    [[ -z "${!key:-}" ]] && export "$key=$val"
  done < .env
fi

restart_only=0
if [[ "${1:-}" == "--restart" ]]; then
  restart_only=1
fi

stop_existing() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo ">> stopping existing container: $CONTAINER"
    docker rm -f "$CONTAINER" >/dev/null
  fi
}

build_image() {
  echo ">> building image: $IMAGE"
  docker build -t "$IMAGE" .
}

run_container() {
  echo ">> starting container on port $PORT"
  docker run -d --name "$CONTAINER" \
    -p "${PORT}:8080" \
    -e DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
    -e TASK3_API_KEY="${TASK3_API_KEY:-}" \
    -e TASK3_MODEL_BASE_URL="${TASK3_MODEL_BASE_URL:-}" \
    -e TASK3_MODEL_NAME="${TASK3_MODEL_NAME:-}" \
    "$IMAGE" >/dev/null
  echo ">> serving at http://localhost:${PORT}/extract"
}

if [[ $restart_only -eq 1 ]]; then
  build_image
  stop_existing
  run_container
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo ">> uv not found; installing"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo ">> syncing python deps (uv sync)"
uv sync

build_image
stop_existing
run_container
