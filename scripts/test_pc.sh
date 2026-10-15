#!/usr/bin/env bash
# Build and run the PC-side unit tests; stops at the first failure.
#
# Usage: test_pc.sh              (no arguments)
#
# Runs, from the repository root:
#   - tests/test_pc_input.cpp      PC input layer against ps3recomp's cellPad
#   - tests/test_pc_game_ini.cpp   game.ini settings parsing and saving
#   - tests/test_pc_*.py and tests/test_launcher_*.py   (unittest, with the
#     Python virtualenv in ../.venv)
#   - ps3recomp's runtime/ppu/tests/test_ppu_diagnostics.cpp
#
# Input:  sources of this repository and of the ps3recomp checkout next to it
#         (../ps3recomp; the PS3RECOMP override is not used here).
# Output: test binaries and objects in scratch/pc-tests/.
# Requires: clang, clang++ and the SDL2 development library.
set -euo pipefail
port_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$port_dir"
mkdir -p scratch/pc-tests
clang -std=c17 -D_GNU_SOURCE -I../ps3recomp/include \
    -c ../ps3recomp/libs/input/cellPad.c -o scratch/pc-tests/cellPad.o
clang++ -std=c++20 -Wall -Wextra -I../ps3recomp/include -I../ps3recomp/libs/input \
    tests/test_pc_input.cpp pc/input.cpp scratch/pc-tests/cellPad.o \
    -lSDL2 -lpthread -o scratch/pc-tests/test_pc_input
scratch/pc-tests/test_pc_input
clang++ -std=c++20 -Wall -Wextra tests/test_pc_game_ini.cpp pc/game_ini.cpp -o scratch/pc-tests/test_pc_game_ini
scratch/pc-tests/test_pc_game_ini
workspace_dir="$(dirname "$port_dir")"
"$workspace_dir/.venv/bin/python" -m unittest discover -s tests -p 'test_pc_*.py' -v
"$workspace_dir/.venv/bin/python" -m unittest discover -s tests -p 'test_launcher_*.py' -v

clang++ -std=c++20 -pthread ../ps3recomp/runtime/ppu/tests/test_ppu_diagnostics.cpp -o scratch/pc-tests/test_ppu_diagnostics
scratch/pc-tests/test_ppu_diagnostics
