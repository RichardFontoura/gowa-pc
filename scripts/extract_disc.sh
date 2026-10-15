#!/usr/bin/env bash
# Extract the game disc image.
#
# Usage: extract_disc.sh [ISO]   (default: first *.iso in $GOWA_DUMPS, up to
#                                 two levels deep)
#
# Input:  disc ISO (left untouched).
# Output: game/disc/ containing PS3_GAME/ (extracted with 7z).
# Refuses an incomplete ISO: the ISO 9660 volume space size (logical block
# count at byte 0x8050, in 2048-byte blocks) must fit in the file, which
# catches images still being copied.
#
# Environment: GOWA_DUMPS (see common.sh).
# Requires: 7z, od, stat.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

iso="${1:-$(find_dump '*.iso')}"
[[ -f "$iso" ]] || die "informe o caminho do .iso ou defina GOWA_DUMPS (atual: ${DUMPS:-<vazio>})"

blocks=$(od -An -t u4 -j $((0x8050)) -N4 "$iso" | tr -d ' ')
expected=$((blocks * 2048))
actual=$(stat -c %s "$iso")
(( actual >= expected )) || die "ISO incompleto: $actual de $expected bytes (transferência ainda em andamento?)"

mkdir -p "$DISC"
7z x -tiso -y -bso0 -bsp1 -o"$DISC" "$iso"
[[ -f "$DISC/PS3_GAME/USRDIR/EBOOT.BIN" ]] || die "EBOOT.BIN não apareceu em $DISC/PS3_GAME/USRDIR"
echo "disco extraído em $DISC"
