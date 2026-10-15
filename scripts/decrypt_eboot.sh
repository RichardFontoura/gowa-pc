#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

decrypt() {
    local self_file="$1" klic="${2:-}"
    printf '%s\n' "$klic" | "$SELF_DECRYPTER" --decrypt "$self_file" 2>/dev/null | grep -E '^(Decrypted|Failed)' || true
    local elf_out="${self_file%.*}.elf"
    [[ -f "$elf_out" ]] || die "$(basename "$SELF_DECRYPTER") não decriptou $(basename "$self_file")"
}

publish() {
    local elf_in="$1" name="$2"
    cp "$elf_in" "$GAME/$name"
    sha1sum "$GAME/$name"
}

source_kind="${1:-update}"
[[ -x "$SELF_DECRYPTER" ]] || die "defina SELF_DECRYPTER com o caminho de um decriptador de SELF que aceite --decrypt"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

case "$source_kind" in
    update)
        usrdir="$UPDATE/USRDIR"
        [[ -f "$usrdir/EBOOT.BIN" && -f "$usrdir/GOWA.SELF" ]] || die "faltam EBOOT.BIN/GOWA.SELF em $usrdir; rode extract_update.sh antes"
        cp "$usrdir/EBOOT.BIN" "$usrdir/GOWA.SELF" "$work/"
        decrypt "$work/EBOOT.BIN"
        klic_line="$("$PY" "$PORT_DIR/scripts/find_klic.py" --keys "$PS3_KEY_VAULT" "$work/GOWA.SELF" "$work/EBOOT.elf")"
        echo "klic: $klic_line"
        decrypt "$work/GOWA.SELF" "${klic_line%% *}"
        publish "$work/EBOOT.elf" "LOADER.update.ELF"
        publish "$work/GOWA.elf" "GOWA.update.ELF"
        ln -sf "GOWA.update.ELF" "$ELF"
        ;;
    disc)
        eboot="$DISC/PS3_GAME/USRDIR/EBOOT.BIN"
        [[ -f "$eboot" ]] || die "EBOOT.BIN não encontrado em $eboot; rode extract_disc.sh antes"
        cp "$eboot" "$work/"
        decrypt "$work/EBOOT.BIN"
        publish "$work/EBOOT.elf" "EBOOT.disc.ELF"
        [[ -e "$ELF" ]] || ln -sf "EBOOT.disc.ELF" "$ELF"
        ;;
    *)
        die "uso: $0 [update|disc]"
        ;;
esac

echo "$ELF -> $(readlink "$ELF")"
