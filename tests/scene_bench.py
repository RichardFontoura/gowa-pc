#!/usr/bin/env python3
"""Run the fixed menu-3D + gameplay timeline once under xvfb and record comparable artifacts.

The key script is the one of ``scratch/opt-rea-ghidra-2026-10-16/gp.py`` (same times and keys), so
runs stay comparable with the 2026-10-16 baselines. It drives ``tests/benchmark_pc_fps.py`` of the
tree that contains this file, so ``game/`` and ``scratch/saves`` come from that tree. Writes the
harness artifacts to ``--out`` plus ``meta.json`` (executable hash, revisions, other games running,
load average) and ``notes.txt`` (stall detection, exit code). A run whose guest flip counter stops
for ``--stall`` seconds after 120 s is killed and reported as ``STALL``.

Example::

    flock $W/gowa-pc/scratch/bench.lock .venv/bin/python tests/scene_bench.py \\
        --exe build/linux-vulkan/GowAscensionRecomp --out scratch/runs/cand1 --gpu-timing
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
LOG_MARKERS = {'skip_get_fff0': b'ea=0xFFFFFFF0', 'batchjob': b'BATCHJOB', 'wait_signal': b'in WAIT_SIGNAL'}
INTRO_MENU = ('35:space,41:space,47:space,53:space,59:space,65:space,71:space,77:space,83:space,89:space,'
              '95:space,108:f,130:space,225:space,250:space,270:space,295:3,298:space,'
              '92:shot=menu,140:shot=s140,240:shot=s240,300:shot=s300,450:shot=s450,640:shot=s640')


def gameplay_steps(start, end):
    """Return the gameplay key steps of gp.py: approach, then a 6 s attack/move loop from ``start``."""
    steps = ['645:Right', '652:Right', '660:Left', '668:Right', '675:j', '678:j', '681:i', '685:j']
    t = start
    while t + 6 <= end:
        steps += [f'{t}:w*2', f'{t + 2.5}:Right', f'{t + 3.5}:j', f'{t + 4.5}:i', f'{t + 5.5}:j']
        t += 6
    return steps


def git_rev(path):
    """Return ``HEAD`` and whether the tree at ``path`` has local changes, or ``None``."""
    try:
        rev = subprocess.run(['git', '-C', str(path), 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'],
                               capture_output=True, text=True).stdout.strip()
        return f'{rev}{"+dirty" if dirty else ""}'
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def sha256(path):
    """Return the hex SHA-256 of ``path``."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def other_games(exe):
    """Return the ``pgrep -a`` lines of game processes that are not ``exe``."""
    r = subprocess.run(['pgrep', '-a', 'GowAscensionRec'], capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if exe not in line]


def last_flip(out):
    """Return the guest flip counter of the last row of ``out/frames.csv`` (0 if absent)."""
    p = out / 'frames.csv'
    if not p.exists():
        return 0
    with p.open() as f:
        rows = list(csv.DictReader(f))
    return int(float(rows[-1]['guest_flip'])) if rows else 0


def game_pid(exe):
    """Return the pid of the running ``exe`` or ``None``."""
    r = subprocess.run(['pgrep', '-f', '^' + exe], capture_output=True, text=True)
    pids = r.stdout.split()
    return int(pids[0]) if pids else None


def scan_log(out, state):
    """Count LOG_MARKERS in the bytes ``game.log`` gained since the last call; ``state`` keeps offset and totals."""
    p = out / 'game.log'
    if not p.exists():
        return
    with p.open('rb') as f:
        f.seek(state['offset'])
        data = state['tail'] + f.read()
    state['offset'] += len(data) - len(state['tail'])
    cut = data.rfind(b'\n') + 1
    for name, marker in LOG_MARKERS.items():
        state['counts'][name] = state['counts'].get(name, 0) + data[:cut].count(marker)
    state['tail'] = data[cut:]


def proc_ticks():
    """Return ``{pid: (comm, utime + stime ticks)}`` for every readable process."""
    res = {}
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / 'stat').read_text()
        except OSError:
            continue
        fields = stat[stat.rindex(')') + 2:].split()
        res[int(d.name)] = (stat[stat.index('(') + 1:stat.rindex(')')], int(fields[11]) + int(fields[12]))
    return res


def top_others(before, after, seconds, skip):
    """Return the three processes outside ``skip`` with most CPU between two ``proc_ticks`` snapshots."""
    tick = os.sysconf('SC_CLK_TCK')
    use = [(round((t - before[pid][1]) / tick / seconds, 2), comm) for pid, (comm, t) in after.items()
           if pid in before and pid not in skip]
    return [f'{comm}:{cores}' for cores, comm in sorted(use, reverse=True)[:3] if cores >= 0.05]


def main():
    """Parse arguments, start the harness, watch for stalls and write ``meta.json``/``notes.txt``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--exe', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--seconds', type=float, default=810)
    parser.add_argument('--env', action='append', default=[], help='NAME=VALUE for the game')
    parser.add_argument('--gpu-timing', action='store_true', help='RSX_VK_GPU_TIMING=1 (gpu_ms column)')
    parser.add_argument('--timeline', metavar='AT:SECONDS',
                        help='PS3_TIMELINE capture to out/timeline.bin starting AT s after process start')
    parser.add_argument('--perf', metavar='AT:SECONDS',
                        help='perf record -g with switch events (CLOCK_MONOTONIC) of the game at AT s for SECONDS')
    parser.add_argument('--perf-freq', default='999')
    parser.add_argument('--perf-bin', default=os.environ.get('GOWA_PERF', 'perf'))
    parser.add_argument('--stall', type=float, default=90)
    parser.add_argument('--ps3recomp', default=os.environ.get('PS3RECOMP'))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    exe = str(Path(args.exe).resolve())
    env = list(args.env)
    if args.gpu_timing:
        env.append('RSX_VK_GPU_TIMING=1')
    if args.timeline:
        at, _, secs = args.timeline.partition(':')
        env += [f'PS3_TIMELINE={out / "timeline.bin"}', f'PS3_TIMELINE_AT={at}', f'PS3_TIMELINE_SECONDS={secs or 3}']
    steps = ','.join([INTRO_MENU] + gameplay_steps(690, args.seconds - 10) +
                     ['700:shot=g700', '760:shot=g760', f'{int(args.seconds) - 8}:shot=gend'])
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(perf=args.perf, exe=exe, exe_sha256=sha256(exe), port=str(PORT), port_rev=git_rev(PORT),
                ps3recomp=args.ps3recomp, ps3recomp_rev=git_rev(args.ps3recomp) if args.ps3recomp else None,
                elf_sha256=sha256(PORT / 'game/EBOOT.ELF'), xdg_cache_home=os.environ.get('XDG_CACHE_HOME'),
                env=env, seconds=args.seconds, stall_s=args.stall, others_at_start=other_games(exe),
                loadavg_start=Path('/proc/loadavg').read_text().split()[:3], started=time.strftime('%F %T'))
    bench = subprocess.Popen(['xvfb-run', '-a', '-s', '-screen 0 1440x900x24', sys.executable, '-W', 'ignore',
                              str(PORT / 'tests/benchmark_pc_fps.py'), '--exe', exe, '--out', str(out),
                              '--seconds', str(args.seconds), f'--steps={steps}'] + [f'--env={e}' for e in env],
                             stdout=open(str(out) + '.bench.log', 'w'), stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    notes, others = [], set()
    flip, flip_t = 0, t0
    perf_at, _, perf_secs = (args.perf or '').partition(':')
    perfed = not args.perf
    log_state, marks, next_mark = dict(offset=0, tail=b'', counts={}), [], 10.0
    ticks, ticks_t = proc_ticks(), time.monotonic()
    while bench.poll() is None:
        now = time.monotonic() - t0
        if now >= next_mark:
            next_mark += 10.0
            scan_log(out, log_state)
            pid = game_pid(exe)
            new_ticks, new_t = proc_ticks(), time.monotonic()
            busy = top_others(ticks, new_ticks, new_t - ticks_t, {pid, os.getpid()})
            ticks, ticks_t = new_ticks, new_t
            marks.append(dict(t=round(now, 1), flip=last_flip(out), load=Path('/proc/loadavg').read_text().split()[0],
                              others_cpu=busy, **log_state['counts']))
        if not perfed and now >= float(perf_at):
            perfed = True
            pid = game_pid(exe)
            notes.append(f'perf at {now:.1f}s pid={pid}')
            if pid:
                subprocess.run([args.perf_bin, 'record', '-q', '-k', 'CLOCK_MONOTONIC', '--switch-events', '-g',
                                '-F', args.perf_freq, '-p', str(pid), '-o', str(out / 'perf.data'),
                                '--', 'sleep', perf_secs or '5'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                notes.append(f'perf done {time.monotonic() - t0:.1f}s')
        others.update(other_games(exe))
        f = last_flip(out)
        if f != flip:
            flip, flip_t = f, time.monotonic()
        elif now > 120 and time.monotonic() - flip_t > args.stall:
            pid = game_pid(exe)
            notes.append(f'STALL at {now:.0f}s flip={flip} pid={pid}')
            if pid:
                subprocess.run(['kill', '-9', str(pid)])
            break
        time.sleep(2)
    bench.wait()
    scan_log(out, log_state)
    marks.append(dict(t=round(time.monotonic() - t0, 1), flip=flip, **log_state['counts']))
    (out / 'logmarks.jsonl').write_text(''.join(json.dumps(m) + '\n' for m in marks))
    notes.append(f'bench exit {bench.returncode} at {time.monotonic() - t0:.1f}s flip={flip}')
    meta.update(others_during=sorted(others), loadavg_end=Path('/proc/loadavg').read_text().split()[:3],
                finished=time.strftime('%F %T'), bench_exit=bench.returncode, notes=notes)
    (out / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    (out / 'notes.txt').write_text('\n'.join(notes) + '\n')
    print('\n'.join(notes))


if __name__ == '__main__':
    main()
