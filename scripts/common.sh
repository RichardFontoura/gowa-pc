set -euo pipefail

PORT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(dirname "$PORT_DIR")"
PS3RECOMP="${PS3RECOMP:-$WORKSPACE/ps3recomp}"
SELF_DECRYPTER="${SELF_DECRYPTER:-}"
PS3_KEY_VAULT="${PS3_KEY_VAULT:-}"
PY="$WORKSPACE/.venv/bin/python"
DUMPS="$WORKSPACE/gowa"
TITLE_ID="BCUS98232"

GAME="$PORT_DIR/game"
DISC="$GAME/disc"
UPDATE="$GAME/update"
HDD0="$GAME/hdd0"
HDD1="$GAME/hdd1"
ELF="$GAME/EBOOT.ELF"
OUT="$PORT_DIR/out"
RECOMP="$PORT_DIR/recompiled"
ANALYSIS="$PORT_DIR/analysis"

tool() {
    "$PY" "$PS3RECOMP/tools/$1.py" "${@:2}"
}

find_dump() {
    find "$DUMPS" -maxdepth 2 -type f -name "$1" -print -quit
}

port_exe() {
    local exe="$PORT_DIR/build/${PRESET:-linux-vulkan}/GowAscensionRecomp"
    [[ -x "$exe" ]] || die "executável não encontrado em $exe; rode build.sh antes"
    echo "$exe"
}

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

die() {
    echo "erro: $*" >&2
    exit 1
}
