#!/usr/bin/env bash
# Extract the game update package (patch 1.12).
#
# Usage: extract_update.sh [PKG [pkg_extract options...]]
#        (default PKG: first *.pkg in the game dumps directory, ../gowa, up to
#         two levels deep; extra arguments go to ps3recomp's pkg_extract tool)
#
# Input:  update PKG (left untouched).
# Output: game/update/ (PARAM.SFO, USRDIR/EBOOT.BIN, USRDIR/GOWA.SELF, ...).
#
# Environment: PS3RECOMP (see common.sh).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

pkg="${1:-$(find_dump '*.pkg')}"
[[ -f "$pkg" ]] || die "nenhum .pkg em $DUMPS"

mkdir -p "$UPDATE"
tool pkg_extract "$pkg" "$UPDATE" "${@:2}"
echo "patch extraído em $UPDATE"
