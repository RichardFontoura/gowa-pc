# God of War: Ascension for PC

A native Linux port of **God of War: Ascension** (PlayStation 3, BCUS98232), built by static
recompilation with [ps3recomp](https://github.com/sp00nznet/ps3recomp).

The game's PPU and SPU machine code is translated ahead of time into C/C++ and compiled together with
a runtime that reimplements the PS3 hardware and firmware libraries: LV2 system calls, SPURS, GCM,
the RSX graphics chip (on Vulkan), audio, video decoding, input and file systems. There is no
emulator and no interpreter in the main path. This repository holds the game-specific part of the
port: the pipeline that turns your dump into a native executable, the PC entry point, input, settings,
the in-game menu and the launcher.

> **No game files are included**: no executables, assets, translated code or keys. Everything is
> generated locally from your own legally obtained copy of the game.
> This project is not affiliated with Sony Interactive Entertainment or Santa Monica Studio.
> God of War is a trademark of Sony Interactive Entertainment.

## Status

**Work in progress, not playable yet.** The port boots, runs the menus and starts a new game, but the
first level runs far too slowly to play.

- Opening video, title screen, 3D menus, profile selection and the Single Player menu.
- A new game loads the first level, plays the opening cinematic in the engine and reaches player
  control: the first tutorials ("Dodge", "Attacks") respond to input.
- Performance (measured under Xvfb): 3D menu ~10 FPS, opening video ~28 FPS, new-game cinematic
  ~30 FPS, first level ~3 FPS. The current bottlenecks are SPU jobs (EDGE geometry) and the RSX
  thread.

**Known issues**
- The first level is not playable at its current frame rate.
- Rare SPURS kernel hangs when the kernel runs workloads on parallel threads (about 1 in 7 runs).
- Physical controllers are untested; keyboard and mouse are.
- No upscaling or adjustable internal resolution.
- Saving on exit is not guaranteed.

## What works

- **Static recompilation pipeline:** extraction of the disc ISO and the 1.12 patch, decryption of the
  NPDRM executable (the license key is found and validated automatically), PPU and SPU lifting,
  including the WWS job modules and the SPU code the game generates at run time.
- **Graphics:** the ps3recomp RSX backend on Vulkan, with a persistent shader cache and optional
  background shader compilation.
- **Input:** keyboard, mouse and SDL2 gamepads at the same time on port 0, with two remappable keys per
  PS3 button.
- **Launcher** (Tk): game status and save backups, display mode and window size, VSync, FPS counter,
  MangoHud and GameMode, controls, diagnostics switches and a live log with per-run history.
- **In-game menu** (Dear ImGui) and an FPS counter overlay.

## Requirements

- Linux x86-64 and a GPU with a Vulkan driver.
- clang, CMake, Ninja, 7-Zip, Vulkan headers, SDL2, FFmpeg and Python 3 with Tk.
- Your own copy of the game: the disc ISO (version 1.00) and the PKG of the 1.12 patch.
- An external SELF/NPDRM decrypter with a `--decrypt <file>` command line that reads the KLIC from
  standard input, and a C header with the public NPDRM constants `NP_OMAC_KEY_2` and `NP_KLIC_FREE`.
  Neither is distributed here.

**Build notes**
- The translated SPU code is compiled with `-march=x86-64-v3` (AVX2/FMA) by default. On a CPU without
  AVX2, configure with `-DPORT_SPU_MARCH=x86-64-v2`.
- The translated PPU sources are very large, so compiling them takes a lot of memory per job. Pick the
  number of parallel jobs to fit your RAM (`BUILD_JOBS` for `scripts/build.sh`, 6 by default;
  `-j` for `cmake --build`); fewer jobs use less memory but take longer.

## Building

### 1. Get the sources

The port builds against the `gowa-fixes` branch of
[RichardFontoura/ps3recomp](https://github.com/RichardFontoura/ps3recomp), which carries the runtime,
SPURS, GCM/RSX/Vulkan, SPU and lifter fixes this game needs. The CMake presets and the scripts expect
that checkout and a Python virtual environment next to this repository:

```bash
git clone https://github.com/RichardFontoura/gowa-pc.git
git clone -b gowa-fixes https://github.com/RichardFontoura/ps3recomp.git
python3 -m venv .venv
.venv/bin/pip install -r ps3recomp/requirements.txt ninja
cd gowa-pc
```

`PS3RECOMP=/path/to/ps3recomp` overrides the toolkit location for the scripts, and
`-DPS3RECOMP_DIR=...` does the same for CMake.

### 2. Prepare the game files

```bash
export SELF_DECRYPTER=/path/to/self-decrypter
export PS3_KEY_VAULT=/path/to/keys.h

scripts/extract_update.sh /path/to/patch-1.12.pkg   # -> game/update
scripts/extract_disc.sh /path/to/disc.iso           # -> game/disc (refuses incomplete ISOs)
scripts/decrypt_eboot.sh                            # update (default) or disc -> game/EBOOT.ELF
```

With the 1.12 patch, `USRDIR/EBOOT.BIN` is only a small loader that starts `GOWA.SELF`, the real
executable. `decrypt_eboot.sh` finds the license key of `GOWA.SELF` inside the decrypted loader with
`scripts/find_klic.py`, which validates every candidate against the CMAC of the NPD header. No key is
stored in the repository.

### 3. Translate and build

```bash
scripts/analyze.sh       # imports, ELF header, SPU images and the WWS job policy module -> analysis/
scripts/lift.sh          # PPU -> out/, recompiled/
../.venv/bin/python scripts/extract_jobmods_elf.py game/EBOOT.ELF --out analysis/jobmods
scripts/lift_spu.sh      # SPU images and the WWS policy module -> spu/
../.venv/bin/python ../ps3recomp/tools/lift_jobmods.py --jobmods analysis/jobmods \
    --out spu --register spu/jobmods_register.c
scripts/build.sh         # linux-vulkan preset by default (also linux-vulkan-debug, linux-null)
```

Incremental builds afterwards: `cmake --build build/linux-vulkan -j<jobs>`, with a job count that
fits your RAM.

**Optional: position-independent SPU fragments.** Some jobs copy SPU code to local-store addresses
chosen at run time. Without translated fragments the SPU interpreter runs that code, much more slowly.
To translate them, run the game once with `SPU_PIC_CAPTURE=<file>`, then
`../.venv/bin/python scripts/lift_spu_fragments.py <file> --overlay 210` and build again.

All generated files stay out of git: `game/` (extracted dumps and decrypted ELFs), `out/` and
`recompiled/` (translated PPU), `spu/` (translated SPU), `analysis/`, `build/`, `gamedata/` (saves
and the virtual HDD) and `scratch/` (temporary files).

## Playing

```bash
scripts/launcher.sh          # settings and the Play button
scripts/launcher.sh --play   # start straight away with the saved settings
scripts/run.sh               # diagnostic entry point (ignores the launcher settings)
```

Settings live in `${XDG_CONFIG_HOME:-~/.config}/gowa-pc/` (`GOWA_CONFIG_DIR` changes the folder):

| File | Contents |
|---|---|
| `settings.json` | launcher settings (schema 2; schema 1 is migrated on load) |
| `game.ini` | options shared with the in-game menu (`key=value`); the game gets its path in `GOWA_GAME_INI` |
| `last_run.log` | timestamped log of the last run started with Play |
| `logs/<UTC>-<id>/` | per-run history: raw output, JSONL events, metadata and an exit summary |
| `savedata_backups/` | save copies made before every run (the 10 most recent) |
| `frames.csv` | frame times, when enabled under Diagnostics |

### Launcher

- **Game:** installation status, save backup and restore (`gamedata/dev_hdd0/home/00000001/savedata`).
  Saves are copied before every run; a failed backup is logged and does not block the game. Restoring
  requires the game to be closed, asks for confirmation and backs up the current saves first.
- **Graphics:** display mode (windowed, borderless, fullscreen), window size (1280×720, 1600×900,
  1920×1080 or the monitor), VSync and the FPS counter (corner, scale, opacity and items), all stored
  in `game.ini`. MangoHud and GameMode prefix the command and are disabled when not installed.
- **Controls:** two keys per button, capture on click (key or mouse button), duplicate-key warning
  and a reset button.
- **Diagnostics:** `RSX_FRAME_CSV`, `RSX_VK_TEXTURE_COMPUTE`, `RSX_VK_RAW_TEXTURES`,
  `RSX_TEX_HASH_FULL`, `VDEC_PERF`, `SPURS_KERN_THREADS`, `SPURS_KERN_PARALLEL` and `GCM_FLIP_MIN_MS`.
  Only values that differ from the `scripts/run.sh` defaults are exported; a variable already set in
  the terminal wins.
- **Log:** the last 6000 lines with errors and gaps highlighted, counters, copy, clear and open
  folder. Clearing the view never deletes the per-run history.

### In game

**Insert** (or **R3 + L2** on a controller) opens the PC menu and **F10** toggles the FPS counter.
Both keys are reserved and cannot be remapped.

| PS3 button | Key 1 | Key 2 |
|---|---|---|
| Cross | Space | K |
| Square | Left mouse | J |
| Triangle | Right mouse | I |
| Circle | F | L |
| L1 / R1 | Left Shift / E | — |
| L2 / R2 | Q / R | — |
| L3 / R3 | Left Ctrl / Middle mouse | — / C |
| Start / Select | Escape / Tab | Enter / — |
| D-pad ↑ → ↓ ← | 1 / 2 / 3 / 4 | — |
| Evade (while held, WASD becomes the right stick) | Left Alt | — |

WASD is the left stick and the arrow keys the right stick. Keyboard, mouse and the first SDL gamepad
share port 0, and losing window focus releases all input. Remapping takes effect on the next run.

## How it works

| Part | Where | What it does |
|---|---|---|
| Pipeline | `scripts/` | Extracts the dumps, decrypts the executables, analyses the ELF and drives the PPU/SPU lifters |
| Translated code | `recompiled/`, `spu/` (generated) | The game's PPU functions and SPU programs as C/C++ |
| Runtime | [ps3recomp](https://github.com/RichardFontoura/ps3recomp) (`gowa-fixes`) | LV2 syscalls, HLE firmware libraries (SPURS, GCM, audio, video, file systems), SPU runtime and the RSX Vulkan backend |
| Entry point | `main.cpp`, `stubs.cpp` | Reserves guest memory, sets up the virtual file system, starts the runtime and the frame clock, and boots the game |
| PC layer | `pc/` | SDL2 input mapping (`input.cpp`), `game.ini` settings (`game_ini.cpp`) and the ImGui menu and FPS overlay (`overlay.cpp`) |
| Launcher | `launcher/` | Tk launcher: settings, controls, save backups, diagnostics and run logs |
| Tests | `tests/`, `scripts/test_pc.sh` | Input, settings and launcher unit tests that need no game assets, plus window smoke tests and benchmarks |

## Contributing

Bug reports, investigations and pull requests are welcome.

- **Report issues** with the log of the failing run (`last_run.log` or the folder under `logs/`), the
  build preset, your GPU and driver, and what was on screen.
- **Put fixes where they belong.** Hardware, syscall and firmware library behavior goes into the
  ps3recomp runtime (`gowa-fixes` branch); permanent fixes to translated code go into the lifters, never
  into generated files. This repository is for what is specific to this game and to the PC.
- **Do not skip work or hide failures**: no skipped jobs or draws, forced success codes, or sleeps and
  retries that mask a synchronisation bug. Preserve the game's logic, jobs and effects.
- **Document every function you add** with a comment that explains what it does, its parameters and
  any side effects.
- **Run the tests** that need no game assets before opening a pull request:
  ```bash
  scripts/test_pc.sh
  ../.venv/bin/python -m unittest discover -s tests -p 'test_launcher*.py'
  ```
  With `GOWA_UI_TEST=1` and `xvfb-run` the launcher tests also exercise the Tk pages.
- **Measure performance changes** on the same scene and settings, separating warm-up from steady state,
  and report FPS, frame times and the sample length. Menus and videos are not a substitute for gameplay.
- **Never commit game files**: executables, assets, keys or anything generated from them.

## Credits

- **sp00nznet** and the ps3recomp contributors:
  [ps3recomp](https://github.com/sp00nznet/ps3recomp), the static recompilation toolkit and runtime
  this port is built on.
- [gow3_pc](https://github.com/FelipePrado0/gow3_pc): the default input layout this port follows.

Also used: [SDL2](https://github.com/libsdl-org/SDL), [FFmpeg](https://ffmpeg.org),
[Dear ImGui](https://github.com/ocornut/imgui), [Vulkan](https://www.vulkan.org),
[PyCryptodome](https://www.pycryptodome.org), [Capstone](https://www.capstone-engine.org) and
[Tk](https://www.tcl.tk). Third-party components keep their own licenses.
