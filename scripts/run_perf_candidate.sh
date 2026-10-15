#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -ge 1 ]] || die "uso: run_perf_candidate.sh <diretório A/B> [candidate|baseline]"
snapshot="$(realpath -e "$1")"
shift
variant="${1:-candidate}"
if [[ $# -gt 0 ]]; then shift; fi
case "$variant" in
    candidate) exe="$snapshot/candidate/GowaPerfCand" ;;
    baseline) exe="$snapshot/baseline/GowaPerfBase" ;;
    fps) exe="$snapshot/candidate/GowaFpsFix" ;;
    fps-baseline) exe="$snapshot/baseline/GowaFpsBase2" ;;
    *) die "variante deve ser candidate, baseline, fps ou fps-baseline" ;;
esac
[[ -x "$exe" ]] || die "executável não encontrado: $exe"
[[ -d "$DISC/PS3_GAME" && -f "$UPDATE/PARAM.SFO" && -f "$ELF" ]] || die "assets do jogo não encontrados"
mkdir -p "$snapshot/sessions"
session="$(mktemp -d "$snapshot/sessions/$variant-XXXXXX")"
mkdir -p "$session/hdd0/game" "$session/hdd1"
ln -s "$UPDATE" "$session/hdd0/game/$TITLE_ID"
export PS3_VFS_ROOT="$DISC"
export PS3_HDD0_ROOT="hdd0"
export PS3_HDD1_ROOT="hdd1"
export PS3_BOOT_PATH="/dev_hdd0/game/$TITLE_ID/USRDIR/GOWA.SELF"
export SPU_INTERP_UNLIFTED="${SPU_INTERP_UNLIFTED:-1}"
export SPURS_PM_OWNS_READY="${SPURS_PM_OWNS_READY:-1}"
export GCM_SEMA_ACQUIRE="${GCM_SEMA_ACQUIRE:-1}"
export GCM_PARK_RESYNC_MS="${GCM_PARK_RESYNC_MS:-2000000000}"
export GCM_FLIP_MIN_MS="${GCM_FLIP_MIN_MS:-250}"
export GCM_FLIP_PACE_WINDOW="${GCM_FLIP_PACE_WINDOW:-3}"
export PS3RECOMP_VK_GUEST_PROGRAMS="${PS3RECOMP_VK_GUEST_PROGRAMS:-1}"
export PS3RECOMP_VK_WINDOW="${PS3RECOMP_VK_WINDOW:-1}"
export AUDIO_RATE="${AUDIO_RATE:-1}"
echo "Sessão isolada: $session"
echo "Log: $session/game.log — Ctrl+C encerra esta instância."
cd "$session"
exec "$exe" "$ELF" "$@" > "$session/game.log" 2>&1
