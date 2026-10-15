#!/usr/bin/env bash
# Start the graphical launcher (launcher/gowa_launcher.py).
#
# Usage: launcher.sh [launcher options...]   (arguments go to the launcher)
#
# Uses $GOWA_PYTHON, or python3 from PATH. If that Python cannot import
# tkinter and GOWA_TK_DIR points to an unpacked Tk 8.6 library directory
# (containing libtk8.6.so and tk8.6/), that copy is added to LD_LIBRARY_PATH
# and TK_LIBRARY before starting.
#
# Environment: GOWA_PYTHON  Python interpreter to use.
#              GOWA_TK_DIR  fallback Tk 8.6 library directory.
set -euo pipefail
port_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${GOWA_PYTHON:-python3}"
local_tk="${GOWA_TK_DIR:-}"
if [[ -n "$local_tk" && -f "$local_tk/libtk8.6.so" ]] && ! "$python_bin" -c 'import tkinter' 2>/dev/null; then
    export LD_LIBRARY_PATH="$local_tk${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export TK_LIBRARY="$local_tk/tk8.6"
fi
exec "$python_bin" "$port_dir/launcher/gowa_launcher.py" "$@"
