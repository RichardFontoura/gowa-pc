#!/usr/bin/env bash
# Diagnose hangs: run the game under gdb for a fixed time, then dump the
# backtrace of every thread and print a condensed per-thread summary.
#
# Usage: hang_dump.sh [SECONDS [DEPTH]]
#   SECONDS  how long the game runs before it is interrupted (default 15)
#   DEPTH    frames per thread in the backtrace (default 30)
#
# Input:  the built executable (build.sh) and the extracted game files.
# Output (analysis/):
#   hang_bt.txt    full gdb output with all backtraces
#   hang_run.log   the game's stdout/stderr
# The summary from summarize_bt.py is printed on stdout.
#
# Signals the runtime uses internally (SIGSEGV, SIGBUS, SIGUSR1/2, SIGPIPE,
# SIGALRM) are passed through silently. After SECONDS a background job sends
# SIGINT to the process (matched by its 15-character kernel name
# "GowAscensionRec"), gdb stops, dumps the stacks and kills the game.
#
# Environment:
#   GDB_EXTRA  extra gdb commands run after the backtrace (optional).
#   PRESET and the VFS variables (see common.sh), and the runtime tuning
#   variables documented in run.sh, with the same defaults (except that
#   PS3RECOMP_VK_WINDOW is not set here).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

seconds="${1:-15}"
depth="${2:-30}"
exe="$(port_exe)"
setup_vfs_env
export SPU_INTERP_UNLIFTED="${SPU_INTERP_UNLIFTED:-1}"
export SPURS_PM_OWNS_READY="${SPURS_PM_OWNS_READY:-1}"
export GCM_SEMA_ACQUIRE="${GCM_SEMA_ACQUIRE:-1}"
export GCM_PARK_RESYNC_MS="${GCM_PARK_RESYNC_MS:-2000000000}"
export GCM_FLIP_MIN_MS="${GCM_FLIP_MIN_MS:-250}"
export GCM_FLIP_PACE_WINDOW="${GCM_FLIP_PACE_WINDOW:-3}"
export SPURS_KERN_THREADS="${SPURS_KERN_THREADS:-6}"
export SPURS_KERN_PARALLEL="${SPURS_KERN_PARALLEL:-7,13,14,15,16}"
export SPURS_KERN_BUSY_RERUN="${SPURS_KERN_BUSY_RERUN:-1}"
export SPURS_KERN_IDLE_SHIFT="${SPURS_KERN_IDLE_SHIFT:-0}"
export PS3RECOMP_VK_GUEST_PROGRAMS="${PS3RECOMP_VK_GUEST_PROGRAMS:-1}"

mkdir -p "$ANALYSIS"
log="$ANALYSIS/hang_run.log"
bt="$ANALYSIS/hang_bt.txt"
gdb_script="$(mktemp)"
trap 'rm -f "$gdb_script"' EXIT

cat > "$gdb_script" <<EOF
set pagination off
set print thread-events off
set print inferior-events off
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
handle SIGPIPE nostop noprint pass
handle SIGALRM nostop noprint pass
run "$ELF" > "$log" 2>&1
thread apply all bt $depth
${GDB_EXTRA:-}
kill
quit
EOF

(
    sleep "$seconds"
    pkill -INT -x GowAscensionRec
) &

gdb -q -batch -x "$gdb_script" "$exe" > "$bt" 2>&1 || true
wait

"$PY" "$PORT_DIR/scripts/summarize_bt.py" "$bt"
echo "pilhas completas em $bt, saída do jogo em $log" >&2
