#!/usr/bin/env bash
# Install deps, build the Docker image, and serve the /extract API.
#   ./install.sh             # install + build + run (idempotent)
#   ./install.sh --restart   # stop and re-run an already-built container

set -euo pipefail

IMAGE="sec_toolbox:latest"
CONTAINER="sec_toolbox"
PORT="${PORT:-8080}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

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
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "image $IMAGE not built yet; run ./install.sh first" >&2
    exit 1
  fi
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

echo ">> building image: $IMAGE"
docker build -t "$IMAGE" .

stop_existing
run_container
