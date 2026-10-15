#!/usr/bin/env python3
"""Recompile named source(s) and relink only an isolated snapshot variant.

With --probe, instrument its Vulkan backend before compiling. The shared
source/build are only read for compiler/linker recipes; inputs come from the
immutable snapshot. No game is launched.

Usage example:
    ../.venv/bin/python scripts/relink_perf_snapshot.py scratch/perf-ab
        candidate --name GowaFpsFix --source libs/video/cellGcmSys.c --probe

SNAPSHOT is a directory made by build_perf_candidate.py. Each --source path is
relative to the ps3recomp checkout and is compiled from the variant's copy,
after which the object replaces its counterpart in the variant's
libps3recomp_runtime.a (the archive is updated in place). --port-main compiles
the given copy of main.cpp instead of the frozen main object. The executable
is written to SNAPSHOT/VARIANT/NAME and its path printed; with --probe and the
GOWA_FRAME_CSV variable set, it writes one CSV row per present (see
summarize_fps.py).
"""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from build_perf_candidate import BUILD, SDK, PORT, WORKSPACE, run


def instrument(tree):
    """Add the frame-timing probe to a snapshot tree's Vulkan backend.

    Copies pc/perf_probe.h next to libs/video/rsx_vulkan_backend.c and, unless
    the backend already includes it, inserts the probe calls at fixed text
    anchors (backend start, submit-and-wait, fence wait and present).

    Args:
        tree: Path of the variant's ps3recomp copy.

    Raises:
        ValueError: if an anchor does not occur exactly once in the backend.
    """
    source = tree / 'libs/video/rsx_vulkan_backend.c'
    text = source.read_text()
    probe = tree / 'libs/video/perf_probe.h'
    shutil.copyfile(PORT / 'pc/perf_probe.h', probe)
    if 'perf_probe.h' in text:
        return
    changes = [
        ('#include <string.h>', '#include <string.h>\n#include "perf_probe.h"'),
        ('static int vk_begin(void)\n{', 'static int vk_begin(void)\n{\n    perf_init();'),
        ('static int vk_submit_and_wait(void)\n{',
         'static int vk_submit_and_wait(void)\n{\n    uint64_t perf_t = perf_csv ? perf_now() : 0;'),
        ('VK_CHECK(pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX), "vkWaitForFences");',
         'VK_CHECK(pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX), "vkWaitForFences");\n'
         '    if (perf_csv) { perf_wait_ns += perf_now() - perf_t; perf_submits++; }'),
        ('static void vk_cb_present(void* ud, u32 buffer_id)\n{',
         'static void vk_cb_present(void* ud, u32 buffer_id)\n{\n    uint64_t perf_t = perf_csv ? perf_now() : 0;'),
        ('    vk_present_window();\n}', '    vk_present_window();\n    perf_frame(s_vk.presented, perf_t);\n}'),
    ]
    for old, new in changes:
        if text.count(old) != 1:
            raise ValueError(f'Probe anchor changed: {old}')
        text = text.replace(old, new)
    source.write_text(text)


def main():
    """Parse arguments, recompile the requested sources and relink the variant.

    The link command is read from the shared build's Ninja recipe (without
    running it) and rewritten so every object and archive comes from the
    snapshot's frozen link-inputs/, except the variant's runtime archive and,
    with --port-main, the freshly compiled main object.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('variant', choices=['baseline', 'candidate'])
    parser.add_argument('--name', required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--source', action='append', default=[])
    parser.add_argument('--port-main', type=Path,
                        help='Compile an isolated copy of the port main.cpp instead of its frozen object')
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        parser.error('--name must be a basename')
    root = args.snapshot.resolve()
    directory = root / args.variant
    tree = directory / 'ps3recomp'
    if args.probe:
        instrument(tree)
        args.source.append('libs/video/rsx_vulkan_backend.c')
    commands = json.loads((BUILD / 'compile_commands.json').read_text())
    objects = []
    for name in dict.fromkeys(args.source):
        entry = next(c for c in commands if c['file'] == str(SDK / name))
        cmd = [arg.replace(str(SDK), str(tree)) for arg in shlex.split(entry['command'])]
        obj = directory / (Path(name).name + '.o')
        cmd[cmd.index('-o') + 1] = str(obj)
        run(cmd, directory / (obj.name + '.build.log'), cwd=directory)
        objects.append(obj)
    main_object = None
    if args.port_main:
        entry = next(c for c in commands if c['file'] == str(PORT / 'main.cpp'))
        cmd = [arg.replace(str(SDK), str(tree)) for arg in shlex.split(entry['command'])]
        main_object = directory / 'perf_main.cpp.o'
        cmd[cmd.index('-o') + 1] = str(main_object)
        cmd[cmd.index('-c') + 1] = str(args.port_main.resolve())
        cmd.extend(['-I', str(PORT)])
        run(cmd, directory / 'perf_main.build.log', cwd=directory)
    archive = directory / 'libps3recomp_runtime.a'
    if objects:
        run(['ar', 'r', archive, *objects], directory / 'rearchive.log')
    ninja = shutil.which('ninja') or str(WORKSPACE / '.venv/bin/ninja')
    recipes = subprocess.check_output([ninja, '-C', str(BUILD), '-t', 'commands', 'GowAscensionRecomp'], text=True).splitlines()
    link = shlex.split(next(line for line in reversed(recipes) if ' -o GowAscensionRecomp ' in line))
    if link[:2] == [':', '&&']: link = link[2:]
    if link[-2:] == ['&&', ':']: link = link[:-2]
    assert not any(arg in ('&&', ';', '||') for arg in link)
    inputs = json.loads((root / 'manifest.json').read_text())['link_inputs']
    copies = {path: str(root / 'link-inputs' / f'{index:03d}-{Path(path).name}')
              for index, path in enumerate(inputs)}
    cmd = []
    for arg in link:
        if arg.startswith('--dependency-file='):
            assert cmd.pop() == '-Xlinker'
        elif arg.endswith('libps3recomp_runtime.a'):
            cmd.append(str(archive))
        elif arg.endswith(('.o', '.a')):
            if main_object and arg == 'CMakeFiles/GowAscensionRecomp.dir/main.cpp.o':
                cmd.append(str(main_object))
            else:
                cmd.append(copies[str((BUILD / arg).resolve())])
        else:
            cmd.append(arg)
    exe = directory / args.name
    cmd[cmd.index('-o') + 1] = str(exe)
    run(cmd, directory / (args.name + '.link.log'), cwd=directory)
    print(exe)


if __name__ == '__main__':
    main()
