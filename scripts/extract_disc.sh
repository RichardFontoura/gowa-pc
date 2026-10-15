#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

iso="${1:-$(find_dump '*.iso')}"
[[ -f "$iso" ]] || die "nenhum .iso em $DUMPS"

blocks=$(od -An -t u4 -j $((0x8050)) -N4 "$iso" | tr -d ' ')
expected=$((blocks * 2048))
actual=$(stat -c %s "$iso")
(( actual >= expected )) || die "ISO incompleto: $actual de $expected bytes (transferência ainda em andamento?)"

mkdir -p "$DISC"
7z x -tiso -y -bso0 -bsp1 -o"$DISC" "$iso"
[[ -f "$DISC/PS3_GAME/USRDIR/EBOOT.BIN" ]] || die "EBOOT.BIN não apareceu em $DISC/PS3_GAME/USRDIR"
echo "disco extraído em $DISC"
