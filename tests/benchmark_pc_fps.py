"""Bounded, isolated FPS run under xvfb-run. Owns only its child process group.

Uses a frozen executable, private writable VFS/save paths, real SDL key events,
per-present CSV instrumentation and per-thread CPU sampling. No GPU frame dumps.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

import smoke_pc_window as ui

PORT = Path(__file__).resolve().parents[1]


def allow_parent_debuggers():
    # Yama permits this game's parent, but normally rejects a sibling gdb
    # spawned by that parent. Allow this parent AND its descendants only.
    # Changes only the child being exec'd; no global ptrace policy changes.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(0x59616D61, ctypes.c_ulong(os.getppid()), 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'PR_SET_PTRACER failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=175)
    parser.add_argument('--env', action='append', default=[], help='NAME=VALUE added to the game environment')
    parser.add_argument('--save', action='append', default=['BCUS98232-SYSDATA'],
                        help='save directory from scratch/saves copied into the private savedata')
    parser.add_argument('--steps', default='33:shot=intro,' + ','.join(f'{t}:space' for t in range(35, 126, 6)) +
                        ',138:shot=menu,165:shot=menu_end')
    parser.add_argument('--profile-at', type=float,
                        help='Sample all thread stacks with eu-stack starting at this second')
    parser.add_argument('--profile-samples', type=int, default=40)
    parser.add_argument('--profile-interval', type=float, default=0.05)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'hdd0/game').mkdir(parents=True)
    (out / 'hdd0/game/BCUS98232').symlink_to(PORT / 'game/update', target_is_directory=True)
    (out / 'hdd1').mkdir()
    saves = out / 'gamedata/dev_hdd0/home/00000001/savedata'
    for save in args.save:
        shutil.copytree(PORT / 'scratch/saves' / save, saves / save)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SPU_', 'SPURS_', 'RSX_', 'GCM_', 'PAD_', 'YDKJ_', 'PS3RECOMP_', 'GOWA_', 'AUDIO_'))}
    config = dict(SDL_VIDEODRIVER='x11', SDL_AUDIODRIVER='dummy',
                  PS3_VFS_ROOT=str(PORT / 'game/disc'), PS3_HDD0_ROOT='hdd0',
                  PS3_HDD1_ROOT='hdd1', PS3_BOOT_PATH='/dev_hdd0/game/BCUS98232/USRDIR/GOWA.SELF',
                  SPU_INTERP_UNLIFTED='1', SPURS_PM_OWNS_READY='1', GCM_SEMA_ACQUIRE='1',
                  GCM_PARK_RESYNC_MS='2000000000', GCM_FLIP_MIN_MS='250', GCM_FLIP_PACE_WINDOW='3',
                  SPURS_KERN_THREADS='6', SPURS_KERN_PARALLEL='7,13,14,15,16', SPURS_KERN_BUSY_RERUN='1', SPURS_KERN_IDLE_SHIFT='0',
                  PS3RECOMP_VK_GUEST_PROGRAMS='1', PS3RECOMP_VK_WINDOW='1', AUDIO_RATE='1',
                  RSX_FRAME_CSV=str(out / 'frames.csv'))
    config.update(item.split('=', 1) for item in args.env)
    env.update(config)
    (out / 'run.json').write_text(json.dumps(dict(exe=str(args.exe.resolve()), seconds=args.seconds,
                                                steps=args.steps, profile_at=args.profile_at, config=config), indent=2) + '\n')
    display = ui.x.XOpenDisplay(None)
    if not display:
        raise RuntimeError('No X11 display; use xvfb-run')
    with (out / 'game.log').open('wb') as log, (out / 'cpu.jsonl').open('w') as cpu:
        process = subprocess.Popen([str(args.exe.resolve()), str(PORT / 'game/EBOOT.ELF')],
                                   cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   preexec_fn=allow_parent_debuggers if args.profile_at is not None else None,
                                   start_new_session=True)
        start = time.monotonic()
        events = ui.parse_steps(args.steps)
        index, window, sampled = 0, None, -1
        profiled = False
        mount_checked = False
        try:
            while time.monotonic() - start < args.seconds:
                elapsed = time.monotonic() - start
                if process.poll() is not None:
                    raise RuntimeError(f'Game exited {process.returncode}; see {out}/game.log')
                if not mount_checked and elapsed >= 8:
                    with (out / 'game.log').open('rb') as boot:
                        prefix = boot.read(65536).decode(errors='replace')
                    if 'PatchCheck() -> NOTPATCH' in prefix or 'has no PARAM.SFO' in prefix:
                        raise RuntimeError('Patch mount failed; this run cannot measure the intended game version')
                    mount_checked = "attributes=0x1, titleId='BCUS98232'" in prefix
                if window is None:
                    window = ui.find_window(display)
                    if window:
                        ui.x.XRaiseWindow(display, window)
                        ui.x.XSetInputFocus(display, window, 1, 0)
                        ui.x.XFlush(display)
                    elif elapsed > 30:
                        raise RuntimeError('Game window not found')
                if int(elapsed) > sampled:
                    sampled = int(elapsed)
                    threads = []
                    for task in Path(f'/proc/{process.pid}/task').iterdir():
                        try:
                            stat = (task / 'stat').read_text()
                            fields = stat[stat.rindex(')') + 2:].split()
                            threads.append(dict(tid=int(task.name), name=stat[stat.index('(') + 1:stat.rindex(')')],
                                                user_ticks=int(fields[11]), system_ticks=int(fields[12])))
                        except FileNotFoundError:
                            pass
                    cpu.write(json.dumps(dict(seconds=elapsed, threads=threads)) + '\n')
                    cpu.flush()
                if args.profile_at is not None and not profiled and elapsed >= args.profile_at:
                    profiled = True
                    stacks = out / 'stacks'
                    stacks.mkdir()
                    shutil.copyfile(f'/proc/{process.pid}/maps', out / 'maps.txt')
                    for sample in range(args.profile_samples):
                        with (stacks / f'{sample:03d}.txt').open('w') as dump:
                            result = subprocess.run(['eu-stack', '-q', '-n', '48', '-p', str(process.pid)],
                                                    stdout=dump, stderr=subprocess.STDOUT, timeout=20)
                        if 'Operation not permitted' in (stacks / f'{sample:03d}.txt').read_text():
                            raise RuntimeError('Stack sampling not permitted; inspect stacks/')
                        time.sleep(args.profile_interval)
                    print(f'{time.monotonic() - start:.1f}s profiling complete', flush=True)
                while window and index < len(events) and elapsed >= events[index][0]:
                    at, action = events[index]
                    if action.startswith('shot='):
                        ui.screenshot(out, action.split('=', 1)[1])
                    else:
                        key, _, hold = action.partition('*')
                        ui.key(display, key, float(hold) if hold else .3)
                    print(f'{at:.0f}s {action}', flush=True)
                    index += 1
                time.sleep(.1)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            ui.x.XCloseDisplay(display)
    print(f'Finished: {out}', flush=True)


if __name__ == '__main__':
    main()
