#!/usr/bin/env bash
# Configure and build the port with CMake.
#
# Usage: build.sh [PRESET]       (default: linux-vulkan; see CMakePresets.json,
#                                 e.g. linux-vulkan-debug, linux-null)
#
# Input:  the repository sources plus generated recompiled/ and spu/ trees.
# Output: build/<PRESET>/ (executable GowAscensionRecomp).
# When GOWA_PYTHON is set, its directory (a virtualenv's bin/) is put first on
# PATH so CMake finds the ninja and Python installed there.
#
# Environment: BUILD_JOBS   parallel compile jobs (default 6; the generated PPU
#                           sources are large, so lower it when RAM is short).
#              GOWA_PYTHON  see common.sh.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

preset="${1:-linux-vulkan}"
if [[ -n "${GOWA_PYTHON:-}" ]]; then export PATH="$(dirname "$GOWA_PYTHON"):$PATH"; fi
cd "$PORT_DIR"
cmake --preset "$preset"
cmake --build --preset "$preset" --parallel "${BUILD_JOBS:-6}"
