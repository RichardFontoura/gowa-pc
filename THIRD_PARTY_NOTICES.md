# Third-party notices

Original GoWA-PC contributions are licensed under GPL-2.0-or-later; see [LICENSE](LICENSE).
That grant applies to contributions whose authors can grant those rights. It does not replace
the licenses of third-party code or grant rights to the game, its generated code, or assets.

## ps3recomp templates and runtime patches

- Project: [ps3recomp](https://github.com/sp00nznet/ps3recomp).
- Copyright (c) 2026 Ned Heller / sp00nznet.
- Original license: MIT; the complete notice is in [LICENSES/ps3recomp-MIT.txt](LICENSES/ps3recomp-MIT.txt).
- Included material: project templates adapted in `main.cpp`, `stubs.cpp`, `CMakeLists.txt`
  and `config.toml`, including bootstrap/frame-clock code, and toolkit portions included as
  context or adapted code in `patches/performance-2026-10-14.patch` and
  `patches/performance-present-2026-10-14.patch`.
- GoWA-PC modifications include Linux/Vulkan setup, SPU build integration, PC input/overlay
  integration, configuration and frame-clock/performance changes. The project history records
  individual modifications; the MIT notice continues to apply to the original portions.

The initial project used ps3recomp v0.14.0 (`a2c20108af45174dbb7d3e2f87f6acaaef09c6b4`).
The runtime itself is obtained and built separately from the `gowa-fixes` fork. Its own
third-party files and subsequent modifications must be reviewed under their respective licenses;
this notice does not assert that every file in that fork is MIT.

## Dear ImGui

- Project: [Dear ImGui](https://github.com/ocornut/imgui).
- Included version identifier: 1.92.9b, as recorded in `third_party/imgui/imgui.h`.
- Copyright (c) 2014-2026 Omar Cornut.
- License: MIT; see [third_party/imgui/LICENSE.txt](third_party/imgui/LICENSE.txt).
- Included paths: `third_party/imgui/`, including the Vulkan backend.

The bundled `imstb_rectpack.h`, `imstb_textedit.h` and `imstb_truetype.h` are ImGui variants
of stb components. Their authorship and MIT/public-domain alternative license texts remain
in those files. Preserve those notices when redistributing them.

`imgui_draw.cpp` includes compressed default font data with its own notices:

- ProggyClean: Copyright (c) 2004, 2005 Tristan Grimmer; MIT.
- ProggyForever-derived minimal font: Copyright (c) 2026 Disco Hello and
  Copyright (c) 2019, 2023 Tristan Grimmer; MIT.

Those font notices remain next to the corresponding data. They are third-party fonts,
not fonts extracted from God of War: Ascension. The MIT permission and warranty text
is provided in `third_party/imgui/LICENSE.txt` alongside these copyright notices.

## Historical decryption tools: RPCS3 provenance

Commit `63fee15` introduced `scripts/unself.py` and `scripts/ps3_keys.py`; these files and
the related `scripts/find_klic.py` / `scripts/decrypt_eboot.sh` helpers have been removed
from the tracked source tree. Local copies are ignored. Earlier commits still contain them.

The development record identifies `unself.py` as a Python adaptation of RPCS3's
`SELFDecrypter::LoadMetadata`, `DecryptNPDRM`, `DecryptData` and `WriteElf`, and identifies
the constants as taken from RPCS3's `key_vault.h` / `key_vault.cpp`. The reference checkout
was [RPCS3](https://github.com/RPCS3/rpcs3) at
`ce1077893a07011b42085a49ced82c0678828f66`; the adaptation was added on 2026-10-16.
Credit for the original implementation belongs to its RPCS3 contributors.

RPCS3 declares GPL-2.0-only for most files, with exceptions identified per file. Derived
RPCS3 portions retain their applicable original terms; GoWA-PC's GPL-2.0-or-later grant
does not grant a later-version option for GPL-2.0-only material. The GPL version 2 text
is included in [LICENSE](LICENSE). Removing these tools from the current tree does not
remove their historical copies or resolve separate questions about decryption technology.

## External dependencies and other credits

SDL2, FFmpeg, Vulkan headers/loader, Python/Tk and the Python packages used by the toolkit
are external dependencies rather than bundled source in this repository. Their licenses
and any binary distribution requirements must be checked for the versions actually shipped.

The default control layout follows [gow3_pc](https://github.com/FelipePrado0/gow3_pc).
This is a design credit, not a claim of authorship of that project or a license grant
for its source code.
