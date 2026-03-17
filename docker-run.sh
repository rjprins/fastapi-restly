#!/bin/bash
# Run fastapi-restly-dev container with Claude Code settings and project mounted

# Get the directory where this script lives (fastapi-restly root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker run -it --rm \
    -u "$(id -u):$(id -g)" \
    -v "$HOME/.claude:$HOME/.claude" \
    -v "$HOME/.config:$HOME/.config:ro" \
    -v "$SCRIPT_DIR:/app" \
    -w /app \
    -e HOME="$HOME" \
    fastapi-restly-dev \
    "${@:-bash}"
