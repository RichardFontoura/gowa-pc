#!/usr/bin/env bash
# Static analysis of the decrypted game ELF, run before lifting.
#
# Usage: analyze.sh [ELF]        (default: game/EBOOT.ELF)
#
# Input:  the decrypted PPU ELF produced by decrypt_eboot.sh.
# Output (analysis/):
#   elf.json             headers, sections and segments (ps3recomp elf_parser)
#   imports_named.json   imported library functions with resolved names
#   spu_images/          embedded SPU ELF images, plus the SPURS policy module
#                        below wrapped as pm_wwsjob_at_007A7B00.elf
#   spu_images.txt       extract_spu_images report
#   pm_wwsjob.bin        raw bytes of the Wws_Job policy module
#
# The policy module is stored raw (not as an SPU ELF) in the executable. It is
# cut from a fixed file offset of the patch 1.12 ELF (0x3000 bytes at 0x797B00,
# virtual address 0x007A7B00) and wrapped into an SPU ELF loaded and entered at
# local-store address 0xA00 so lift_spu.sh can lift it like the other images.
#
# Environment: PS3RECOMP (see common.sh).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

elf="${1:-$ELF}"
[[ -f "$elf" ]] || die "ELF decriptado não encontrado em $elf; rode decrypt_eboot.sh antes"

mkdir -p "$ANALYSIS"
tool elf_parser "$elf" --all > "$ANALYSIS/elf.json"
tool gen_imports "$elf" -o "$ANALYSIS/imports_named.json"
tool extract_spu_images "$elf" -o "$ANALYSIS/spu_images" | tee "$ANALYSIS/spu_images.txt"
pm_bin="$ANALYSIS/pm_wwsjob.bin"
pm_elf="$ANALYSIS/spu_images/pm_wwsjob_at_007A7B00.elf"
"$PY" -I -c 'import sys; d = open(sys.argv[1], "rb").read(); open(sys.argv[2], "wb").write(d[0x797B00:0x797B00 + 0x3000])' "$elf" "$pm_bin"
tool wrap_spu_elf "$pm_bin" --base 0xA00 --entry 0xA00 -o "$pm_elf"
echo "análise em $ANALYSIS"
