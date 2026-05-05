#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/extracted
exec uv run python -m extract_agent --queue ../test_source.md --all --out-dir data/extracted "$@"
