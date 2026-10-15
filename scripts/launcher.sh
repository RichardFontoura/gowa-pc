#!/usr/bin/env bash
# Start the graphical launcher (launcher/gowa_launcher.py).
#
# Usage: launcher.sh [launcher options...]   (arguments go to the launcher)
#
# Uses the Python of the virtualenv in ../.venv, or python3 from PATH if that
# interpreter is missing. If that Python cannot import tkinter and a Tk 8.6
# unpacked locally under scratch/pc-deps/usr/lib exists, that copy is added to
# LD_LIBRARY_PATH and TK_LIBRARY before starting.
#
# Environment: GOWA_PYTHON  Python interpreter to use instead of ../.venv.
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
