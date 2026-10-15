#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

images="$ANALYSIS/spu_images"
[[ -d "$images" ]] || die "sem imagens SPU em $images; rode analyze.sh antes"

mkdir -p "$PORT_DIR/spu"
tool build_spu_workloads \
    --images "$images" \
    --lifted "$PORT_DIR/spu" \
    --out "$PORT_DIR/spu/spu_workloads.c" \
    --register-fn gowa_spu_register_all \
    --constructor \
    --title gowa \
    --extra-funcs pm_wwsjob_at_007A7B00=0xA2C \
    "$@"
echo "SPU lifted em $PORT_DIR/spu; rode build.sh (o configure precisa ver os novos arquivos)"
