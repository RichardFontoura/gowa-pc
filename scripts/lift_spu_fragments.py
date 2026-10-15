#!/usr/bin/env python3
import argparse
from pathlib import Path
import subprocess
import sys

PORT = Path(__file__).resolve().parents[1]
WORKSPACE = PORT.parent
TOOLS = WORKSPACE / 'ps3recomp' / 'tools'


def locate(elf, blob):
    hits, start = [], 0
    while True:
        i = elf.find(blob, start)
        if i < 0:
            return hits
        hits.append(i)
        start = i + 1


def lift_region(elf, lo, hi, seeds, name, out):
    sys.path.insert(0, str(TOOLS))
    from wrap_spu_elf import wrap
    region = elf[lo:hi]
    work = out / name
    work.mkdir(parents=True, exist_ok=True)
    tmp_elf = work / '_region.elf'
    tmp_elf.write_bytes(wrap(region, base=0, entry=seeds[0]))
    prefix = f'{name}_'
    cmd = [sys.executable, str(TOOLS / 'spu_lifter.py'), '--auto-functions', str(tmp_elf), '--base', '0',
           '--pic', '--symbol-prefix', prefix, '-o', str(work), '--source-name', 'spu_recomp.c',
           '--header-name', 'spu_recomp.h', '--extra-funcs', ','.join(hex(s) for s in seeds)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    tmp_elf.unlink()
    if result.returncode:
        sys.exit(result.stderr)
    stats = [l.strip() for l in result.stdout.splitlines() if 'function(s)' in l or 'unsupported' in l]
    rows = [', '.join(f'0x{b:02X}' for b in region[i:i + 16]) for i in range(0, len(region), 16)]
    registration = [
        '',
        f'static const uint8_t {prefix}region[{len(region)}] = {{',
        *(f'    {row},' for row in rows),
        '};',
        '',
        f'__attribute__((constructor)) static void {prefix}register(void)',
        '{',
        f'    spu_pic_register({prefix}pic_functions, {prefix}pic_function_count, {prefix}region, '
        f'{len(region)}u, 0);',
        '}',
        '',
    ]
    with (work / 'spu_recomp.c').open('a') as f:
        f.write('\n'.join(registration))
    (work / 'region.txt').write_text(f'elf_offset=0x{lo:X} size=0x{hi - lo:X} seeds={len(seeds)}\n')
    print(f'{name}: 0x{lo:X}-0x{hi:X} ({hi - lo} bytes), {len(seeds)} seeds; {"; ".join(stats)}')


def main():
    parser = argparse.ArgumentParser(description='Lift the SPU code fragments jobs copy to runtime-chosen local-store addresses, from SPU_PIC_CAPTURE files, as position-independent images.')
    parser.add_argument('captures', type=Path, nargs='+')
    parser.add_argument('--elf', type=Path, default=PORT / 'game' / 'GOWA.update.ELF')
    parser.add_argument('--name', default='spu_pic')
    parser.add_argument('--out', type=Path, default=PORT / 'spu')
    parser.add_argument('--margin', type=lambda x: int(x, 0), default=0x800)
    parser.add_argument('--gap', type=lambda x: int(x, 0), default=0x8000)
    parser.add_argument('--min-entries', type=int, default=8)
    parser.add_argument('--overlay', action='append', default=[], help='only entries captured with this resident overlay')
    args = parser.parse_args()

    elf = args.elf.read_bytes()
    found, missing = set(), 0
    for capture in args.captures:
        for line in capture.read_text().splitlines():
            parts = line.split()
            if len(parts) != 3 or (args.overlay and parts[1] not in args.overlay):
                continue
            hits = locate(elf, bytes.fromhex(parts[2]))
            if not hits:
                missing += 1
            found.update(hits)
    if not found:
        sys.exit('no captured entry was found in the executable')
    offsets = sorted(found)
    clusters = [[offsets[0]]]
    for off in offsets[1:]:
        if off - clusters[-1][-1] > args.gap:
            clusters.append([off])
        else:
            clusters[-1].append(off)
    print(f'{len(found)} entries located, {missing} not in the executable, {len(clusters)} clusters')
    for cluster in clusters:
        if len(cluster) < args.min_entries:
            continue
        lo = max(0, (cluster[0] - args.margin) & ~0xF)
        hi = min(len(elf), (cluster[-1] + args.margin + 0xF) & ~0xF)
        if hi - lo > 0x3F000:
            print(f'skipping cluster 0x{lo:X}-0x{hi:X}: larger than a local store')
            continue
        lift_region(elf, lo, hi, [off - lo for off in cluster], f'{args.name}_{lo:06X}', args.out)


if __name__ == '__main__':
    main()
