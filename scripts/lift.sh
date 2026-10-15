#!/usr/bin/env bash
# Lift (statically recompile) the game's PPU code to C/C++.
#
# Usage: lift.sh [ELF]           (default: game/EBOOT.ELF)
#
# Steps, all with ps3recomp tools:
#   1. ppu_loader     discovers functions and imports -> out/*.functions.json,
#                     out/*.imports.json, report in out/loader.txt
#   2. ppu_lifter     deletes the previous recompiled/ppu_recomp* sources and
#                     regenerates them, emitting HLE stubs for the imports
#                     (one job per CPU core)
#   3. gen_hle_nids   writes recompiled/ppu_hle_nids.cpp, the NID table of
#                     every HLE module
#
# Input:  decrypted PPU ELF (decrypt_eboot.sh).
# Output: out/ and recompiled/. Rebuild afterwards with build.sh.
#
# Environment: PS3RECOMP (see common.sh).
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
