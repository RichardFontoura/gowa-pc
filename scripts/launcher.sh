#!/usr/bin/env bash
set -euo pipefail
port_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(dirname "$port_dir")"
python_bin="${GOWA_PYTHON:-$workspace_dir/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then python_bin=python3; fi
local_tk="$port_dir/scratch/pc-deps/usr/lib"
if [[ -f "$local_tk/libtk8.6.so" ]] && ! "$python_bin" -c 'import tkinter' 2>/dev/null; then
    export LD_LIBRARY_PATH="$local_tk${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export TK_LIBRARY="$local_tk/tk8.6"
fi
exec "$python_bin" "$port_dir/launcher/gowa_launcher.py" "$@"
