#!/usr/bin/env python3
"""Build/test an isolated performance A/B pair; never rebuild the shared game.

Copies existing link inputs, recompiles only audio/SPU channel objects, and
links two distinct executables. Does not launch either game. Run from anywhere
with the workspace .venv/bin/python. Output must be a new directory.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

PORT = Path(__file__).resolve().parents[1]
WORKSPACE = PORT.parent
SDK = WORKSPACE / "ps3recomp"
BUILD = PORT / "build/linux-vulkan"
PATCH = PORT / "patches/performance-2026-10-14.patch"
CHANGED = ("libs/audio/cellAudio.c", "runtime/spu/spu_channels.c", "runtime/spu/spu_dma.h")


def run(args, log, cwd=None, env=None):
    with log.open("w") as out:
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                                stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")


def fingerprint(path):
    stat = path.stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=PORT / "scratch" / datetime.now().strftime("perf-build-%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.nice(10)
    manifest = {str(SDK / name): hashlib.sha256((SDK / name).read_bytes()).hexdigest()
                for name in CHANGED}
    baseline = root / "baseline/ps3recomp"
    candidate = root / "candidate/ps3recomp"
    for part in ("include", "libs", "runtime"):
        shutil.copytree(SDK / part, baseline / part)
    for name in CHANGED:
        assert hashlib.sha256((baseline / name).read_bytes()).hexdigest() == manifest[str(SDK / name)]
    shutil.copytree(baseline, candidate)
    run(["patch", "-p1", "--batch", "--forward", "--dry-run", "-i", PATCH], root / "patch-check.log", cwd=candidate)
    run(["patch", "-p1", "--batch", "--forward", "-i", PATCH], root / "patch-apply.log", cwd=candidate)
    print(f"Source snapshot and candidate patch: {root}", flush=True)

    # Read Ninja's recipe only. -t commands does not execute build rules.
    ninja = shutil.which("ninja") or str(WORKSPACE / ".venv/bin/ninja")
    recipes = subprocess.check_output([ninja, "-C", str(BUILD), "-t", "commands",
                                       "GowAscensionRecomp"], text=True).splitlines()
    link = shlex.split(next(line for line in reversed(recipes) if " -o GowAscensionRecomp " in line))
    if link[:2] == [":", "&&"]:
        link = link[2:]
    if link[-2:] == ["&&", ":"]:
        link = link[:-2]
    assert not any(arg in ("&&", ";", "||") for arg in link)
    # The dependency-file option in the shared recipe is an output too.
    clean = []
    for arg in link:
        if arg.startswith("--dependency-file="):
            assert clean.pop() == "-Xlinker"
        else:
            clean.append(arg)
    link = clean
    inputs = {arg: (BUILD / arg).resolve() for arg in link if arg.endswith((".o", ".a"))}
    before = {arg: fingerprint(path) for arg, path in inputs.items()}
    copies = {}
    for index, (arg, path) in enumerate(inputs.items()):
        dst = root / "link-inputs" / f"{index:03d}-{path.name}"
        dst.parent.mkdir(exist_ok=True)
        shutil.copy2(path, dst)
        copies[arg] = str(dst)
    if before != {arg: fingerprint(path) for arg, path in inputs.items()}:
        raise RuntimeError("Another build changed link inputs during the snapshot; retry in a new output directory")
    manifest["link_inputs"] = {str(inputs[arg]): {
        "stat": before[arg], "sha256": hashlib.sha256(Path(dst).read_bytes()).hexdigest()
    } for arg, dst in copies.items()}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive_arg = next(arg for arg in inputs if arg.endswith("libps3recomp_runtime.a"))
    commands = json.loads((BUILD / "compile_commands.json").read_text())
    for variant, tree in (("baseline", baseline), ("candidate", candidate)):
        directory = root / variant
        objects = []
        for name in CHANGED[:2]:
            entry = next(c for c in commands if c["file"] == str(SDK / name))
            cmd = [arg.replace(str(SDK), str(tree)) for arg in shlex.split(entry["command"])]
            obj = directory / (Path(name).name + ".o")
            cmd[cmd.index("-o") + 1] = str(obj)
            run(cmd, directory / (obj.name + ".build.log"), cwd=directory)
            objects.append(obj)
        archive = directory / "libps3recomp_runtime.a"
        shutil.copy2(copies[archive_arg], archive)
        run(["ar", "r", archive, *objects], directory / "archive.log")
        cmd = [copies.get(arg, arg) for arg in link]
        cmd[cmd.index(copies[archive_arg])] = str(archive)
        exe = directory / ("GowaPerfBase" if variant == "baseline" else "GowaPerfCand")
        cmd[cmd.index("-o") + 1] = str(exe)
        run(cmd, directory / "link.log", cwd=directory)
        print(f"Linked {exe}", flush=True)
        audio_test = directory / "test_audio_pacing"
        flags = ["-std=gnu17", "-O2", "-ffunction-sections", "-fdata-sections",
                 "-I", tree / "include", "-Wl,--gc-sections", "-pthread"]
        define = '-DAUDIO_IMPLEMENTATION="' + str(tree / "libs/audio/cellAudio.c") + '"'
        run(["cc", *flags, define, PORT / "tests/test_audio_pacing.c", "-lSDL2", "-o", audio_test],
            directory / "audio-test-build.log")
        run([audio_test, *(["--check"] if variant == "candidate" else [])], directory / "audio-pacing.log")
        spu = tree / "runtime/spu"
        deps = "spu_drain.c spu_lockstep.c spu_coherency.c spu_workload.c spurs_policy.c spurs_job.c spu_tsp_weak.c spu_vm_pagemap.c spurs_policy_blob_weak.c spu_interp.c".split()
        spu_test = directory / "test_spu_perf"
        run(["cc", *flags, "-I", spu, "-I", tree / "runtime/platform",
             '-DSPU_IMPLEMENTATION="' + str(spu / "spu_channels.c") + '"', PORT / "tests/test_spu_perf.c",
             *(spu / d for d in deps), tree / "runtime/platform/win32_compat.c", "-lm", "-o", spu_test],
            directory / "spu-test-build.log")
        run([spu_test], directory / "spu-perf.log")
        if variant == "candidate":
            env = os.environ.copy()
            env.update(SPU_MFC_TRACE="1", SPURS_PM_TRACE="1", SPU_DMA_POISON_TRACE="1")
            trace_log = directory / "spu-trace.log"
            run([spu_test], trace_log, env=env)
            trace = trace_log.read_text()
            assert trace.count("RESUME-ELEMS") == 20000
            assert trace.count("poll #") == 20000
    flags = ["-std=gnu17", "-O2", "-ffunction-sections", "-fdata-sections",
             "-I", candidate / "include", "-Wl,--gc-sections", "-pthread"]
    for compiler in ("gcc", "clang"):
        for name in ("test_audio_ring", "test_audio_port_arena"):
            exe = root / (name + "-" + compiler)
            run([compiler, *flags, candidate / f"libs/audio/tests/{name}.c", "-lSDL2", "-o", exe],
                exe.with_suffix(".build.log"))
            run([exe], exe.with_suffix(".log"))
    exe = root / "test_audio_pacing_live"
    run(["cc", *flags, '-DAUDIO_IMPLEMENTATION="' + str(candidate / "libs/audio/cellAudio.c") + '"',
         PORT / "tests/test_audio_pacing_live.c", "-lSDL2", "-o", exe], root / "live-build.log")
    run([exe], root / "live.log")
    print("Audio/SPU checks passed. Both games are built; neither was launched.", flush=True)


if __name__ == "__main__":
    main()
