#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

preset="${1:-linux-vulkan}"
export PATH="$WORKSPACE/.venv/bin:$PATH"
cd "$PORT_DIR"
cmake --preset "$preset"
cmake --build --preset "$preset" --parallel "${BUILD_JOBS:-6}"
