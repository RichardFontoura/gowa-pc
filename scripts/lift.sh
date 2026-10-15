#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

elf="${1:-$ELF}"
[[ -f "$elf" ]] || die "ELF decriptado não encontrado em $elf; rode decrypt_eboot.sh antes"
stem="$(basename "${elf%.*}")"

mkdir -p "$OUT" "$RECOMP"
tool ppu_loader "$elf" -o "$OUT" | tee "$OUT/loader.txt"

functions="$(ls "$OUT"/*.functions.json | head -1)"
imports="$(ls "$OUT"/*.imports.json | head -1)"
rm -f "$RECOMP"/ppu_recomp*.cpp "$RECOMP"/ppu_recomp*.c "$RECOMP"/ppu_recomp.h
tool ppu_lifter "$elf" \
    --functions "$functions" \
    --hle-stubs "$imports" \
    --jobs "$(nproc)" \
    --output "$RECOMP"

tool gen_hle_nids --all --out "$RECOMP/ppu_hle_nids.cpp"
echo "lift de $stem em $RECOMP"
