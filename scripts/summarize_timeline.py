#!/usr/bin/env python3
import argparse
import collections
import struct
from pathlib import Path

RECORD = struct.Struct('<QIHHII')
NAMES = {1: 'pm+', 2: 'pm-', 3: 'drain+', 4: 'drain-', 5: 'present', 6: 'usleep+', 7: 'usleep-',
         8: 'task+', 9: 'task-', 10: 'wait+', 11: 'wait-', 12: 'gpu+', 13: 'gpu-', 14: 'flip', 15: 'report',
         16: 'vdec-decode', 17: 'vdec-picture'}
PAIRS = {1: 2, 3: 4, 6: 7, 8: 9, 10: 11, 12: 13}


def load(path):
    data = path.read_bytes()
    events = [RECORD.unpack_from(data, i) for i in range(0, len(data) - RECORD.size + 1, RECORD.size)]
    events.sort(key=lambda e: e[0])
    return events


def intervals(events):
    open_ = {}
    spans = collections.defaultdict(list)
    for t, tid, kind, _, arg, _ in events:
        if kind in PAIRS:
            open_[(tid, kind)] = (t, arg)
        else:
            for start, end in PAIRS.items():
                if end == kind and (tid, start) in open_:
                    t0, a0 = open_.pop((tid, start))
                    spans[tid].append((t0, t, start, a0, arg))
    return spans


def main():
    parser = argparse.ArgumentParser(description='Summarize a PS3_TIMELINE capture: per-thread activity per presented frame and an ASCII strip.')
    parser.add_argument('trace', type=Path)
    parser.add_argument('--frames', type=int, default=3, help='frames to draw as ASCII strips')
    parser.add_argument('--ms-per-char', type=float, default=2.0)
    args = parser.parse_args()
    events = load(args.trace)
    spans = intervals(events)
    presents = [e[0] for e in events if e[2] == 5]
    flips = [e[0] for e in events if e[2] == 14]
    print(f'{len(events)} events, {len(presents)} presents, {len(flips)} flip words, '
          f'{(events[-1][0] - events[0][0]) / 1e6:.0f} ms')
    if len(presents) >= 2:
        gaps = [(b - a) / 1e6 for a, b in zip(presents, presents[1:])]
        print('present gaps ms:', ' '.join(f'{g:.0f}' for g in gaps))
    window = (presents[0], presents[-1]) if len(presents) >= 2 else (events[0][0], events[-1][0])
    total = (window[1] - window[0]) / 1e6
    print(f'\nper-thread busy share over {total:.0f} ms (between first and last present):')
    labels = {}
    for tid, items in sorted(spans.items()):
        acc = collections.Counter()
        for t0, t1, kind, a0, a1 in items:
            lo, hi = max(t0, window[0]), min(t1, window[1])
            if hi <= lo:
                continue
            ms = (hi - lo) / 1e6
            if kind == 1:
                acc[f'w{a0 & 0xFF}{"*" if a1 & 0x100 else ""}'] += ms
            else:
                acc[NAMES[kind].rstrip('+')] += ms
        if not acc:
            continue
        top = acc.most_common(1)[0][0]
        labels[tid] = top
        summary = ', '.join(f'{k} {v / total:.0%}' for k, v in acc.most_common(6))
        print(f'  {tid}: {summary}')
    if len(presents) < 2:
        return
    print(f'\nASCII strips ({args.ms_per_char} ms/char): digits/letters = PM wid running (upper = found work), '
          'D drain, z guest usleep, w task signal wait, G gpu flush, | present, f flip word')
    symbols = '0123456789abcdefghijklmnopqrstuv'
    for k in range(min(args.frames, len(presents) - 1)):
        start, end = presents[k], presents[k + 1]
        width = int((end - start) / 1e6 / args.ms_per_char) + 1
        print(f'\nframe {k}: {(end - start) / 1e6:.0f} ms')
        for tid, items in sorted(spans.items()):
            row = [' '] * width
            for t0, t1, kind, a0, a1 in items:
                lo, hi = max(t0, start), min(t1, end)
                if hi <= lo:
                    continue
                c0 = int((lo - start) / 1e6 / args.ms_per_char)
                c1 = max(c0 + 1, int((hi - start) / 1e6 / args.ms_per_char))
                if kind == 1:
                    ch = symbols[a0 & 31]
                    ch = ch.upper() if a1 & 0x100 else ch
                else:
                    ch = {3: 'D', 6: 'z', 8: 'T', 10: 'w', 12: 'G'}[kind]
                for c in range(c0, min(c1, width)):
                    if row[c] in ' zT' or ch.isupper() or ch in 'Gw':
                        row[c] = ch
            for t, tid2, kind, _, arg, _ in events:
                if start <= t < end and tid2 == tid and kind in (5, 14):
                    row[min(width - 1, int((t - start) / 1e6 / args.ms_per_char))] = '|' if kind == 5 else 'f'
            line = ''.join(row)
            if line.strip():
                print(f'{tid:>8} {labels.get(tid, ""):>7} {line}')


if __name__ == '__main__':
    main()
