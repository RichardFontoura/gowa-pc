#!/usr/bin/env python3
import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
TOOLKIT = Path(os.environ.get('PS3RECOMP', PORT.parent / 'ps3recomp')).resolve()
FLAGS = ['-std=gnu17', '-O2', '-march=x86-64-v3', '-ffp-contract=off', '-DSPU_LS_NO_PROBES', '-w']
DATA_LO = 0x30000
SUMMARY = re.compile(r'SUMMARY region=\d+ entries_pass=(\d+) entries_fail=(\d+) entries_without_valid_case=(\d+) entries_crash=(\d+)')


def bases_for(size):
    top = (DATA_LO - size) & ~0xF
    return [0x400, (top // 2) & ~0xF | 0x90, top]


def build(region, work):
    work.mkdir(parents=True, exist_ok=True)
    inc = ['-I', str(TOOLKIT / 'include'), '-I', str(TOOLKIT / 'runtime/spu')]
    harness_o = work / 'pic_equiv_harness.o'
    harness_c = PORT / 'tests' / 'pic_equiv_harness.c'
    if not harness_o.exists() or harness_o.stat().st_mtime < harness_c.stat().st_mtime:
        subprocess.run(['clang', *FLAGS, *inc, '-c', str(harness_c), '-o', str(harness_o)], check=True)
    src = region / 'spu_recomp.c'
    obj = work / f'{region.name}.o'
    if not obj.exists() or obj.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(['clang', *FLAGS, *inc, '-c', str(src), '-o', str(obj)], check=True)
    exe = work / f'equiv_{region.name}'
    subprocess.run(['clang', str(harness_o), str(obj), '-lm', '-o', str(exe)], check=True)
    return exe


def main():
    parser = argparse.ArgumentParser(description='AOT x interpreter equivalence of lifted SPU PIC fragments at several local-store bases, with seeded random register and LS state.')
    parser.add_argument('regions', type=Path, nargs='*')
    parser.add_argument('--cases', type=int, default=6)
    parser.add_argument('--work', type=Path, default=PORT / 'build' / 'spu_pic_equiv')
    parser.add_argument('--entries', default='', help='comma-separated region offsets (default: every entry)')
    parser.add_argument('--log', type=Path)
    parser.add_argument('--snap', action='append', default=[], metavar='ENTRY=GLOB',
                        help='spu_context dumps taken at a lifted entry (gdb dump of *ctx); run them instead of random cases')
    parser.add_argument('--rebase', default='0', help='comma-separated LS shifts applied to the snapshot code (0 = as captured)')
    parser.add_argument('--truncate', action='store_true',
                        help='replace every region byte outside the tested entry\'s reachable instruction ranges with noise')
    args = parser.parse_args()
    regions = args.regions or sorted((PORT / 'spu').glob('spu_pic_*'))
    failed = 0
    log = args.log.open('w') if args.log else None
    if args.snap:
        exe = build(regions[0], args.work)
        for spec in args.snap:
            entry, pattern = spec.split('=', 1)
            for shift in args.rebase.split(','):
                env = dict(os.environ)
                if int(shift, 0):
                    env['PIC_EQUIV_REBASE'] = shift
                result = subprocess.run([str(exe), 'snap', entry, *sorted(glob.glob(pattern))], capture_output=True,
                                        text=True, timeout=3600, env=env)
                lines = [l for l in result.stdout.splitlines() if l.startswith('SNAP')]
                if log:
                    log.write(f'== snap {entry} rebase={shift}\n' + '\n'.join(lines) + '\n')
                passed = sum(l.endswith('PASS') for l in lines)
                bad = sum(l.endswith('FAIL') for l in lines)
                print(f'{regions[0].name} snap {entry} rebase={shift}: pass={passed} fail={bad} skipped={len(lines) - passed - bad}')
                failed += bad
        sys.exit(1 if failed else 0)
    for region in regions:
        size = int(dict(f.split('=') for f in (region / 'region.txt').read_text().split())['size'], 16)
        exe = build(region, args.work)
        bases = bases_for(size)
        cmd = [str(exe), '0', str(args.cases), ','.join(hex(b) for b in bases)]
        cmd += [e for e in args.entries.split(',') if e]
        env = dict(os.environ, PIC_EQUIV_TRUNCATE='1') if args.truncate else None
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
        if log:
            log.write(f'== {region.name} bases={",".join(hex(b) for b in bases)}\n{result.stdout}')
        m = SUMMARY.search(result.stdout)
        if not m:
            print(f'{region.name}: no summary (exit {result.returncode})\n{result.stdout[-2000:]}{result.stderr[-2000:]}')
            failed += 1
            continue
        ok, bad, novalid, crash = map(int, m.groups())
        print(f'{region.name}: bases={",".join(hex(b) for b in bases)} pass={ok} fail={bad} no_valid_case={novalid} crash={crash}')
        for line in result.stdout.splitlines():
            if line.startswith('  DIFF') or line.startswith('  FAULT') or 'CRASH' in line:
                print(line)
        failed += bad + crash
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
