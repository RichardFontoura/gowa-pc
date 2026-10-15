#!/usr/bin/env bash
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
