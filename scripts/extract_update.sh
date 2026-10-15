#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

pkg="${1:-$(find_dump '*.pkg')}"
[[ -f "$pkg" ]] || die "nenhum .pkg em $DUMPS"

mkdir -p "$UPDATE"
tool pkg_extract "$pkg" "$UPDATE" "${@:2}"
echo "patch extraído em $UPDATE"
