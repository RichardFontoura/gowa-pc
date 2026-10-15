#!/usr/bin/env bash
# Decrypt the game executable (SELF) into a plain ELF for analysis and lifting.
#
# Usage: decrypt_eboot.sh [update|disc]      (default: update)
#
#   update  Patch 1.12. USRDIR/EBOOT.BIN is a small loader that spawns
#           USRDIR/GOWA.SELF, the real game, which is NPDRM-protected. The
#           loader is decrypted first; find_klic.py then scans the decrypted
#           loader for the KLIC that unlocks GOWA.SELF, and GOWA.SELF is
#           decrypted with it. Writes game/LOADER.update.ELF and
#           game/GOWA.update.ELF and points game/EBOOT.ELF at the latter.
#   disc    Disc 1.00. PS3_GAME/USRDIR/EBOOT.BIN is the game itself and needs
#           no KLIC. Writes game/EBOOT.disc.ELF and points game/EBOOT.ELF at it
#           only if game/EBOOT.ELF does not exist yet.
#
# Input:  game/update/ (extract_update.sh) or game/disc/ (extract_disc.sh).
# Output: the ELFs above in game/, with their SHA-1 printed. Work happens on
#         copies in a temporary directory that is removed on exit.
#
# Environment:
#   SELF_DECRYPTER  required; external SELF/NPDRM decrypter (see common.sh).
#   PS3_KEY_VAULT   required for "update"; key header used by find_klic.py.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# Decrypt one SELF with $SELF_DECRYPTER, showing only its Decrypted/Failed
# status lines. The decrypter must leave <stem>.elf next to the input; exits
# with an error otherwise.
# Arguments: $1 = SELF file; $2 = KLIC as hex (optional, empty for none).
decrypt() {
    local self_file="$1" klic="${2:-}"
    printf '%s\n' "$klic" | "$SELF_DECRYPTER" --decrypt "$self_file" 2>/dev/null | grep -E '^(Decrypted|Failed)' || true
    local elf_out="${self_file%.*}.elf"
    [[ -f "$elf_out" ]] || die "$(basename "$SELF_DECRYPTER") não decriptou $(basename "$self_file")"
}

# Copy a decrypted ELF into game/ under a given name and print its SHA-1.
# Arguments: $1 = decrypted ELF; $2 = destination file name inside game/.
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
