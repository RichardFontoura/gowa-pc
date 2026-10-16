#!/usr/bin/env bash
# Run the recompiled game.
#
# Usage: run.sh [game options...]   (extra arguments go to the executable)
#
# Input:  the built executable (build.sh), the user-supplied game/EBOOT.ELF,
#         game/disc/ and, if present, game/update/ mounted as the virtual file
#         system by setup_vfs_env (see common.sh).
# Output: the game's own stdout/stderr. The executable, the resolved ELF and
#         every tuning variable below are echoed as "[boot-config]" lines on
#         stderr first, so logs record the configuration that ran.
#
# Environment: PRESET and the VFS variables (see common.sh), plus these
# ps3recomp runtime switches. The values below are the port's defaults; a
# non-empty exported value always wins.
#   SPU_INTERP_UNLIFTED=1       interpret SPU code at addresses with no lifted
#                               function (code the policy module generates or
#                               copies at runtime); any value, even 0, enables it.
#   SPURS_PM_OWNS_READY=1       the policy module, not the SPURS kernel, owns
#                               the workload ready count; any value, even 0, enables it.
#   GCM_SEMA_ACQUIRE=1          semaphore ACQUIRE commands in the RSX FIFO
#                               really block until the label matches.
#   GCM_PARK_RESYNC_MS=2000000000
#                               milliseconds the FIFO may stay parked at one
#                               offset before it is resynchronised (runtime
#                               default 1000; this value effectively disables it).
#   GCM_FLIP_MIN_MS=250         minimum milliseconds between flips while flip
#                               pacing is active (0 disables pacing).
#   GCM_FLIP_PACE_WINDOW=3      number of flips of a pacing window to which
#                               GCM_FLIP_MIN_MS applies (0 = every flip).
#   SPURS_KERN_THREADS=6        host threads polling SPURS workloads.
#   SPURS_KERN_PARALLEL=7,13,14,15,16
#                               comma-separated workload IDs that each get a
#                               polling thread of their own; the rest share the
#                               first one. Unlike the others, an exported empty
#                               value is kept (all workloads on one thread).
#   SPURS_KERN_BUSY_RERUN=1     poll again immediately after a pass that found
#                               work instead of sleeping.
#   SPURS_KERN_IDLE_SHIFT=0     idle back-off shift of the polling threads
#                               (runtime default 4).
#   PS3RECOMP_VK_GUEST_PROGRAMS=1
#                               translate the game's own shader programs for
#                               Vulkan when the shader compiler is available.
#   PS3RECOMP_VK_WINDOW=1       open a window (otherwise run headless).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

exe="$(port_exe)"
setup_vfs_env
export SPU_INTERP_UNLIFTED="${SPU_INTERP_UNLIFTED:-1}"
export SPURS_PM_OWNS_READY="${SPURS_PM_OWNS_READY:-1}"
export GCM_SEMA_ACQUIRE="${GCM_SEMA_ACQUIRE:-1}"
export GCM_PARK_RESYNC_MS="${GCM_PARK_RESYNC_MS:-2000000000}"
export GCM_FLIP_MIN_MS="${GCM_FLIP_MIN_MS:-250}"
export GCM_FLIP_PACE_WINDOW="${GCM_FLIP_PACE_WINDOW:-3}"
export SPURS_KERN_THREADS="${SPURS_KERN_THREADS:-6}"
export SPURS_KERN_PARALLEL="${SPURS_KERN_PARALLEL-7,13,14,15,16}"
export SPURS_KERN_BUSY_RERUN="${SPURS_KERN_BUSY_RERUN:-1}"
export SPURS_KERN_IDLE_SHIFT="${SPURS_KERN_IDLE_SHIFT:-0}"
export PS3RECOMP_VK_GUEST_PROGRAMS="${PS3RECOMP_VK_GUEST_PROGRAMS:-1}"
export PS3RECOMP_VK_WINDOW="${PS3RECOMP_VK_WINDOW:-1}"
printf '[boot-config] executable=%s elf=%s\n' "$exe" "$(readlink -f "$ELF")" >&2
for name in SPU_INTERP_UNLIFTED SPURS_PM_OWNS_READY GCM_SEMA_ACQUIRE GCM_PARK_RESYNC_MS GCM_FLIP_MIN_MS GCM_FLIP_PACE_WINDOW SPURS_KERN_THREADS SPURS_KERN_PARALLEL SPURS_KERN_BUSY_RERUN SPURS_KERN_IDLE_SHIFT PS3RECOMP_VK_GUEST_PROGRAMS PS3RECOMP_VK_WINDOW; do
    printf '[boot-config] %s=%s\n' "$name" "${!name}" >&2
done
exec "$exe" "$ELF" "$@"
