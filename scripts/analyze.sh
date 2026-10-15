#!/usr/bin/env bash
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
