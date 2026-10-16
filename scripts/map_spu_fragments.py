#!/usr/bin/env python3
import argparse
import collections
import json
import re
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
STAT = re.compile(r'\[img=(\d+) ovl=(\d+) pc=0x([0-9A-F]+) lr=0x([0-9A-F]+) n=(\d+) steps=(\d+) code=([0-9A-F]+)\]')
ENTRY = re.compile(r'\{ 0x([0-9A-F]+)u, 0x([0-9A-F]+)u, 0x([0-9A-F]+)u, (\w+) \}')
ELF_FILE_TO_EA = 0x10000
EPILOG = '''Map the SPU code that still falls into the interpreter to its origin in the game ELF.

Inputs are one run's ``SPU_PIC_CAPTURE`` file (``<LS pc> <resident overlay> <64 bytes>``) and its
game log with ``SPU_SMC_STATS=1`` lines. Every fallback target from the stats is joined with the
captured bytes by LS pc and the first eight code bytes, located in the ELF, and classified:

- ``generated``: the bytes are not in the ELF (runtime-written code, e.g. the PM stub at 0x3FEC0);
- ``jobmod-static``: the bytes sit at their own address in the resident jobmod (entry not found by
  the fixed-address lift);
- ``pic-registered``: a lifted PIC entry with the same offset exists, so the lookup rejected it
  (closure bytes differ in LS);
- ``pic-undiscovered``: inside a lifted PIC region, but no entry at that offset;
- ``pic-unlifted``: copied code outside every lifted PIC region.

Capture lines are deduplicated by content, so a target whose pc was not captured is joined by its\neight code bytes when exactly one captured content starts with them; such classes end in ``~``.

``--logpos`` (``<seconds> <log bytes> <flip>`` per line, written by the capture driver) restricts the
stats to a time window with ``--window``. Output is a tab-separated table on stdout, heaviest first.
'''


def load_regions(spu):
    regions = []
    for region in sorted(spu.glob('spu_pic_*/region.txt')):
        fields = dict(f.split('=') for f in region.read_text().split())
        lo, size = int(fields['elf_offset'], 16), int(fields['size'], 16)
        entries = {}
        for m in ENTRY.finditer((region.parent / 'spu_recomp.c').read_text()):
            entries[int(m.group(1), 16)] = (int(m.group(2), 16), int(m.group(3), 16), m.group(4))
        regions.append((region.parent.name, lo, size, entries))
    return regions


def load_jobmods(spu):
    mods = []
    for i, mod in enumerate(json.loads((spu / 'jobmods_manifest.json').read_text())):
        ea = int(mod['name'].split('_')[1], 16)
        mods.append((200 + i, mod['name'], ea, mod['size']))
    return mods


def window_offsets(logpos, window):
    lo_t, hi_t = window
    lo_b, hi_b = None, None
    for line in logpos.read_text().splitlines():
        t, size = float(line.split()[0]), int(line.split()[1])
        if lo_b is None and t >= lo_t:
            lo_b = size
        if t <= hi_t:
            hi_b = size
    return lo_b or 0, hi_b


def read_stats(log, byte_range):
    lo_b, hi_b = byte_range
    stats = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    lines = 0
    with log.open('rb') as f:
        f.seek(lo_b)
        while True:
            if hi_b is not None and f.tell() > hi_b:
                break
            raw = f.readline()
            if not raw:
                break
            if not raw.startswith(b'[smc-stats]'):
                continue
            lines += 1
            for m in STAT.finditer(raw.decode('latin-1')):
                img, ovl, pc, lr, n, steps, code = m.groups()
                key = (int(img), int(ovl), int(pc, 16), code)
                stats[key][0] += int(n)
                stats[key][1] += int(steps)
                stats[key][2][int(lr, 16)] += int(n)
    return stats, lines


def locate(elf, blob):
    hits, start = [], 0
    while True:
        i = elf.find(blob, start)
        if i < 0:
            return hits
        hits.append(i)
        start = i + 1


def classify(pc, ovl, hits, regions, mods):
    if not hits:
        return 'generated', '', ''
    for off in hits:
        ea = off + ELF_FILE_TO_EA
        for mid, name, mea, size in mods:
            if mid == ovl and mea <= ea < mea + size and 0x4000 + (ea - mea) == pc:
                return 'jobmod-static', name, hex(ea)
    for off in hits:
        for name, lo, size, entries in regions:
            if lo <= off < lo + size:
                rel = off - lo
                if rel in entries:
                    return 'pic-registered', name, hex(rel)
                return 'pic-undiscovered', name, hex(rel)
    owners = []
    for off in hits:
        ea = off + ELF_FILE_TO_EA
        owner = next((name for _, name, mea, size in mods if mea <= ea < mea + size), 'outside-jobmods')
        owners.append(f'{owner}@{hex(off)}')
    return 'pic-unlifted', ';'.join(owners[:3]), ''


def main():
    parser = argparse.ArgumentParser(description='Classify the SPU interpreter fallback targets of a run by their origin in the ELF.',
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('capture', type=Path)
    parser.add_argument('log', type=Path)
    parser.add_argument('--elf', type=Path, default=PORT / 'game' / 'GOWA.update.ELF')
    parser.add_argument('--spu', type=Path, default=PORT / 'spu')
    parser.add_argument('--logpos', type=Path)
    parser.add_argument('--window', type=lambda s: tuple(float(x) for x in s.split(':')))
    args = parser.parse_args()

    elf = args.elf.read_bytes()
    regions, mods = load_regions(args.spu), load_jobmods(args.spu)
    captured = collections.defaultdict(list)
    by_prefix = collections.defaultdict(set)
    for line in args.capture.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            captured[(int(parts[0], 16), parts[2][:16])].append((int(parts[1]), bytes.fromhex(parts[2])))
            by_prefix[parts[2][:16]].add(parts[2])
    byte_range = window_offsets(args.logpos, args.window) if args.logpos and args.window else (0, None)
    stats, lines = read_stats(args.log, byte_range)
    total_steps = sum(v[1] for v in stats.values()) or 1
    print(f'# smc-stats lines={lines} targets={len(stats)} steps={total_steps} entries={sum(v[0] for v in stats.values())}')
    print('class\timg\tovl\tpc\tn\tsteps\tsteps%\ttop_lr\twhere\tdetail\telf_off\tcode')
    for (img, ovl, pc, code), (n, steps, lrs) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
        blobs = captured.get((pc, code), [])
        joined = 'pc'
        if not blobs and len(by_prefix.get(code, ())) == 1:
            blobs = [(ovl, bytes.fromhex(next(iter(by_prefix[code]))))]
            joined = 'prefix'
        hits = locate(elf, blobs[0][1]) if blobs else None
        if hits is None:
            cls, where, detail, off = 'no-capture', '', '', ''
        else:
            cls, where, detail = classify(pc, ovl, hits, regions, mods)
            if joined == 'prefix' and cls == 'jobmod-static':
                cls, where, detail = 'no-capture', '', ''
            elif joined == 'prefix':
                cls += '~'
            off = ','.join(hex(h) for h in hits[:3])
        lr = ','.join(f'{k:05X}' for k, _ in lrs.most_common(2))
        print(f'{cls}\t{img}\t{ovl}\t0x{pc:05X}\t{n}\t{steps}\t{100.0 * steps / total_steps:.2f}\t{lr}\t{where}\t{detail}\t{off}\t{code}')


if __name__ == '__main__':
    main()
