# Shared settings and helpers for the pipeline scripts in this directory.
#
# Not meant to be executed directly: every script does
#     source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
# which enables strict mode (errexit, nounset, pipefail) and defines the
# paths and functions below.
#
# Environment variables honoured:
#   PS3RECOMP       path to the ps3recomp checkout on the gowa-fixes branch
#                   (default: ../ps3recomp, next to this repository).
#   GOWA_PYTHON     Python interpreter with ps3recomp's requirements
#                   installed (default: python3 from PATH, e.g. an activated
#                   virtualenv).
#   GOWA_DUMPS      directory searched for the disc ISO and the update PKG
#                   when extract_disc.sh / extract_update.sh get no argument.
#   SELF_DECRYPTER  path to an external SELF/NPDRM decrypter executable that
#                   accepts `--decrypt <file>`, reads the KLIC (hex, one line,
#                   empty for none) from stdin and writes <file-stem>.elf next
#                   to the input. Required by decrypt_eboot.sh.
#   PS3_KEY_VAULT   path to a C header that defines the NP_OMAC_KEY_2 and
#                   NP_KLIC_FREE byte arrays. Required by find_klic.py.
#   PRESET          CMake preset whose build directory port_exe() looks in
#                   (default: linux-vulkan).
#   PS3_VFS_ROOT, PS3_HDD0_ROOT, PS3_HDD1_ROOT, PS3_BOOT_PATH, PS3_DEV_FLASH
#                   virtual file system overrides; see setup_vfs_env().

set -euo pipefail

# Root of this repository (the parent of scripts/).
PORT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ps3recomp checkout (gowa-fixes branch) providing tools/*.py, the runtime
# and the HLE libraries.
PS3RECOMP="${PS3RECOMP:-$(dirname "$PORT_DIR")/ps3recomp}"
# External SELF/NPDRM decrypter executable (see header above).
SELF_DECRYPTER="${SELF_DECRYPTER:-}"
# C header with the NP_OMAC_KEY_2 and NP_KLIC_FREE arrays (see header above).
PS3_KEY_VAULT="${PS3_KEY_VAULT:-}"
# Python interpreter with ps3recomp's requirements (see header above).
PY="${GOWA_PYTHON:-python3}"
# Optional directory with the game dumps (disc ISO and update PKG), never modified.
DUMPS="${GOWA_DUMPS:-}"
# Title ID of the game; names the update directory under /dev_hdd0/game.
TITLE_ID="BCUS98232"

# Everything extracted from or derived from the game lives under game/.
GAME="$PORT_DIR/game"
# Extracted disc contents (contains PS3_GAME/), mounted as /dev_bdvd.
DISC="$GAME/disc"
# Extracted update package contents (PARAM.SFO, USRDIR/...).
UPDATE="$GAME/update"
# Host directory backing /dev_hdd0 (game/<TITLE_ID> links to $UPDATE).
HDD0="$GAME/hdd0"
# Host directory backing /dev_hdd1.
HDD1="$GAME/hdd1"
# Symlink to the decrypted ELF that is analysed, lifted and booted.
ELF="$GAME/EBOOT.ELF"
# ppu_loader output: function list, imports and loader report.
OUT="$PORT_DIR/out"
# Generated PPU C/C++ sources compiled into the executable.
RECOMP="$PORT_DIR/recompiled"
# Analysis artefacts: ELF/import JSON, SPU images, hang dumps.
ANALYSIS="$PORT_DIR/analysis"

# Run a ps3recomp Python tool with $PY.
# Arguments: $1 = tool name (file stem in $PS3RECOMP/tools, without .py);
#            remaining arguments are passed to the tool unchanged.
tool() {
    "$PY" "$PS3RECOMP/tools/$1.py" "${@:2}"
}

# Print the path of the first file in $GOWA_DUMPS (searched at most two
# levels deep) whose name matches a glob; prints nothing if none matches or
# GOWA_DUMPS is unset.
# Arguments: $1 = file name glob, e.g. '*.iso'.
find_dump() {
    [[ -n "$DUMPS" ]] || return 0
    find "$DUMPS" -maxdepth 2 -type f -name "$1" -print -quit
}

# Print the path of the built game executable,
# build/<PRESET or linux-vulkan>/GowAscensionRecomp; exits with an error if
# it does not exist or is not executable.
port_exe() {
    local exe="$PORT_DIR/build/${PRESET:-linux-vulkan}/GowAscensionRecomp"
    [[ -x "$exe" ]] || die "executável não encontrado em $exe; rode build.sh antes"
    echo "$exe"
}

# Export the virtual file system variables the runtime reads, keeping any
# value already set by the caller:
#   PS3_VFS_ROOT   /dev_bdvd root, default $DISC (which must already be
#                  extracted, or the script exits).
#   PS3_HDD0_ROOT  /dev_hdd0 root; only when the update is extracted, set to
#                  $HDD0 after creating the link $HDD0/game/<TITLE_ID> -> $UPDATE
#                  so the update overlays the disc.
#   PS3_HDD1_ROOT  /dev_hdd1 root, default $HDD1 (created if missing).
#   PS3_BOOT_PATH  set to the update's USRDIR/GOWA.SELF when $ELF points to
#                  GOWA.update.ELF and an hdd0 root is in use.
# Prints the resulting mount summary (and PS3_DEV_FLASH, if set) on stderr.
setup_vfs_env() {
    [[ -d "$DISC/PS3_GAME" ]] || die "disco não extraído em $DISC; rode extract_disc.sh antes"
    export PS3_VFS_ROOT="${PS3_VFS_ROOT:-$DISC}"

    if [[ -z "${PS3_HDD0_ROOT:-}" && -f "$UPDATE/PARAM.SFO" ]]; then
        mkdir -p "$HDD0/game"
        ln -sfn "$UPDATE" "$HDD0/game/$TITLE_ID"
        export PS3_HDD0_ROOT="$HDD0"
    fi

    if [[ -z "${PS3_HDD1_ROOT:-}" ]]; then
        mkdir -p "$HDD1"
        export PS3_HDD1_ROOT="$HDD1"
    fi

    if [[ -z "${PS3_BOOT_PATH:-}" && -n "${PS3_HDD0_ROOT:-}" && "$(readlink -f "$ELF")" == "$GAME/GOWA.update.ELF" ]]; then
        export PS3_BOOT_PATH="/dev_hdd0/game/$TITLE_ID/USRDIR/GOWA.SELF"
    fi

    echo "bdvd=$PS3_VFS_ROOT hdd0=${PS3_HDD0_ROOT:-<nenhum>} hdd1=$PS3_HDD1_ROOT boot=${PS3_BOOT_PATH:-<padrão>} flash=${PS3_DEV_FLASH:-<nenhum>}" >&2
}

# Print "erro: <message>" on stderr and exit with status 1.
# Arguments: the message words.
die() {
    echo "erro: $*" >&2
    exit 1
}
