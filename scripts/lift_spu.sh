#!/usr/bin/env bash
# Lift the SPU images found by analyze.sh and generate their registration.
#
# Usage: lift_spu.sh [build_spu_workloads options...]
#        (extra arguments go to ps3recomp's build_spu_workloads tool)
#
# Input:  analysis/spu_images/ (analyze.sh), including the Wws_Job policy
#         module pm_wwsjob_at_007A7B00, which gets an extra function entry at
#         local-store address 0xA2C: its interrupt handler, which no branch
#         from the entry point reaches, so automatic discovery misses it.
# Output: spu/ (one subdirectory per image) and spu/spu_workloads.c, which
#         registers them all through gowa_spu_register_all(), called from a
#         constructor at startup. Images already lifted are reused unless
#         --relift is passed. Run build.sh afterwards so CMake configure picks
#         up the new files.
#
# Environment: PS3RECOMP (see common.sh).
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
