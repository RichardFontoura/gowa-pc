#!/usr/bin/env python3
"""Break guest frames of a PS3_TIMELINE capture into per-thread work, waits and candidate dependencies.

Inputs: the timeline file (see summarize_timeline.py), optionally the text of
``perf script --show-switch-events -F tid,time,ip,sym --ns`` recorded with ``-k CLOCK_MONOTONIC`` over
the same window (adds off-CPU intervals and the guest function being run), and optionally the
run's frames.csv (calibrates CSV elapsed time against process time with the PRESENT events).

Frames are the intervals between consecutive FLIP_WORD events (the RSX reaching the flip
command of a guest frame). For each thread role (rsx: drains, pm: SPURS kernel threads with
policy-module runs per workload id, ppu: threads calling sys_timer_usleep) it prints the median
per-frame time of each activity, then the PPU polling episodes (back-to-back usleeps with the
same return address) with the events on other threads that ended just before each episode ended.
Those are candidate releasers, not proof of causality. Intervals of different threads overlap;
never add them up as serial time.

Usage example:
    perf script -i perf.data --show-switch-events -F tid,time,ip,sym --ns > perf.txt
    python3 scripts/timeline_chain.py timeline.bin --perf perf.txt --csv frames.csv --at 760
"""
import argparse
import bisect
import collections
import csv
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_timeline import load, intervals

PM, DRAIN, PRESENT, USLEEP, TASK, WAIT, GPU, FLIP = 1, 3, 5, 6, 8, 10, 12, 14
SWITCH = re.compile(r'^\s*(?:\S+\s+)?(\d+)\s+(\d+\.\d+):\s+PERF_RECORD_SWITCH(?:_CPU_WIDE)?\s+(IN|OUT)\s*(preempt)?')
SAMPLE = re.compile(r'^\s*(?:\S+\s+)?(\d+)\s+(\d+\.\d+):')
FRAME = re.compile(r'^\s+[0-9a-f]+\s+(\S+)')
GUEST = ('func_', 'jobmod_', 'spu_pic_', 'pm_')


def parse_perf(path):
    """Return ``(switches, samples)``: per tid sorted ``(t_ns, 'IN'|'OUT'|'PRE')`` and ``(t_ns, symbol)``.

    ``PRE`` is a switch-out by preemption (the thread stayed runnable: CPU contention).

    The symbol of a sample is its first guest frame (``func_``/``jobmod_``/...) in the call chain,
    or the leaf symbol when there is none.
    """
    switches, samples = collections.defaultdict(list), collections.defaultdict(list)
    cur, chain = None, []

    def flush():
        if cur:
            sym = next((s for s in chain if s.startswith(GUEST)), chain[0] if chain else '?')
            samples[cur[0]].append((cur[1], sym))

    for line in Path(path).open(errors='replace'):
        m = SWITCH.match(line)
        if m:
            kind = 'PRE' if m.group(4) else m.group(3)
            switches[int(m.group(1))].append((int(round(float(m.group(2)) * 1e9)), kind))
            continue
        f = FRAME.match(line)
        if f and cur:
            chain.append(f.group(1).split('+')[0])
            continue
        m = SAMPLE.match(line)
        if m:
            flush()
            cur, chain = (int(m.group(1)), int(round(float(m.group(2)) * 1e9))), []
            rest = line[m.end():].split()
            if len(rest) >= 2 and not rest[0].endswith(':'):
                chain.append(rest[1].split('+')[0])
        elif not line.strip():
            flush()
            cur, chain = None, []
    flush()
    for v in switches.values():
        v.sort()
    for v in samples.values():
        v.sort()
    return switches, samples


def coarse_offset(begins, switches):
    """Histogram every usleep-begin minus nearby switch-OUT difference of the same thread; return the mode."""
    hist = collections.Counter()
    by_tid = collections.defaultdict(list)
    for t0, tid in begins:
        by_tid[tid].append(t0)
    for tid, ts in by_tid.items():
        outs = [t for t, k in switches.get(tid, []) if k == 'OUT']
        if not outs:
            continue
        sample = ts[::max(1, len(ts) // 300)]
        for t0 in sample:
            for t in outs[::max(1, len(outs) // 3000)]:
                hist[(t0 - t) // 20000] += 1
    if not hist:
        return None
    bucket = hist.most_common(1)[0][0]
    fine = collections.Counter()
    for tid, ts in by_tid.items():
        outs = [t for t, k in switches.get(tid, []) if k == 'OUT']
        for t0 in ts:
            lo = t0 - (bucket + 2) * 20000
            i = bisect.bisect_left(outs, lo)
            while i < len(outs) and outs[i] <= t0 - (bucket - 1) * 20000:
                fine[(t0 - outs[i]) // 1000] += 1
                i += 1
    return fine.most_common(1)[0][0] * 1000 if fine else bucket * 20000


def offcpu_intervals(switches, offset):
    """Return per tid ``(blocked, preempted)`` lists of ``(t0, t1)`` in timeline ns, switch-out -> next IN."""
    res = {}
    for tid, evs in switches.items():
        blocked, preempted, last = [], [], None
        for t, k in evs:
            t += offset
            if k in ('OUT', 'PRE'):
                last = (t, k)
            elif last is not None:
                (blocked if last[1] == 'OUT' else preempted).append((last[0], t))
                last = None
        res[tid] = (blocked, preempted)
    return res


def clip(items, lo, hi):
    """Return the total ns of ``(t0, t1, ...)`` intervals inside ``[lo, hi)``."""
    return sum(max(0, min(it[1], hi) - max(it[0], lo)) for it in items)


def subtract(a, b):
    """Return the intervals of ``a`` minus the union of ``b`` (both lists of ``(t0, t1)``)."""
    b = sorted(b)
    res = []
    for t0, t1 in sorted(a):
        cur = t0
        i = bisect.bisect_left(b, (t0, t0))
        i = max(0, i - 1)
        while i < len(b) and b[i][0] < t1:
            s, e = b[i]
            if e > cur and s < t1:
                if s > cur:
                    res.append((cur, s))
                cur = max(cur, e)
            i += 1
        if cur < t1:
            res.append((cur, t1))
    return res


def roles(spans, events):
    """Classify thread ids into rsx/pm/ppu/other from the event kinds they emit."""
    kinds = collections.defaultdict(set)
    for t, tid, kind, _, arg, _ in events:
        kinds[tid].add(kind)
    res = {}
    for tid, k in kinds.items():
        if DRAIN in k or FLIP in k:
            res[tid] = 'rsx'
        elif PM in k:
            res[tid] = 'pm'
        elif USLEEP in k:
            res[tid] = 'ppu'
        elif GPU in k or PRESENT in k:
            res[tid] = 'rsx'
        else:
            res[tid] = 'other'
    return res


def poll_episodes(items, gap_ns):
    """Merge back-to-back usleep intervals with the same return address into ``(t0, t1, lr, n)``."""
    eps = []
    for t0, t1, kind, a0, a1 in sorted(i for i in items if i[2] == USLEEP):
        if eps and eps[-1][2] == a1 and t0 - eps[-1][1] <= gap_ns:
            e = eps[-1]
            eps[-1] = (e[0], t1, e[2], e[3] + 1)
        else:
            eps.append((t0, t1, a1, 1))
    return eps


def busy_episodes(items, gap_ns, min_ns):
    """Merge found-work PM runs of one thread with gaps up to ``gap_ns`` into ``(t0, t1, wid)`` of at least ``min_ns``."""
    eps = []
    for t0, t1, kind, a0, a1 in sorted(i for i in items if i[2] == PM and i[4] & 0x100):
        if eps and t0 - eps[-1][1] <= gap_ns and eps[-1][2] == a0 & 0xFF:
            eps[-1][1] = max(eps[-1][1], t1)
        else:
            eps.append([t0, t1, a0 & 0xFF])
    return [tuple(e) for e in eps if e[1] - e[0] >= min_ns]


def end_index(spans, role):
    """Return a time-sorted list of ``(t_end, tid, label)`` for every non-usleep interval end."""
    ends = []
    for tid, items in spans.items():
        for t0, t1, kind, a0, a1 in items:
            if kind == PM:
                ends.append((t1, tid, f'pm-end w{a0 & 0xFF}{"*" if a1 & 0x100 else ""}'))
            elif kind != USLEEP:
                name = {DRAIN: 'drain', TASK: 'task', WAIT: 'sigwait', GPU: 'gpu'}.get(kind, kind)
                ends.append((t1, tid, f'{role.get(tid, "?")}:{name}-end'))
    ends.sort()
    return ends


def releasers(end, ends, tid_self, window_ns):
    """Return labels of interval ends on threads other than ``tid_self`` within ``window_ns`` before ``end``."""
    i = bisect.bisect_left(ends, (end - window_ns, -1, ''))
    labels = []
    while i < len(ends) and ends[i][0] <= end:
        if ends[i][1] != tid_self:
            labels.append(ends[i][2])
        i += 1
    return labels


def main():
    """Parse arguments and print the per-frame breakdown and the polling-episode table."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('timeline', type=Path)
    parser.add_argument('--perf', type=Path, help='perf script text with switch events')
    parser.add_argument('--csv', type=Path, help='frames.csv of the same run')
    parser.add_argument('--at', type=float, default=None, help='PS3_TIMELINE_AT of the capture (s)')
    parser.add_argument('--frames', type=int, default=8, help='frames printed one by one')
    parser.add_argument('--gap-us', type=float, default=200, help='max gap merging usleeps into one episode')
    parser.add_argument('--release-us', type=float, default=300, help='window for candidate releasers')
    parser.add_argument('--episode-ms', type=float, default=50,
                        help='minimum length of a busy episode (merged found-work PM runs, gaps < 2 ms)')
    parser.add_argument('--perf-freq', type=float, default=999, help='perf sampling frequency (Hz)')
    parser.add_argument('--label', action='append', default=[], metavar='SYMBOL=NAME',
                        help='name the on-CPU samples whose first guest frame is SYMBOL (e.g. a spin-wait loop)')
    args = parser.parse_args()
    events = load(args.timeline)
    spans = intervals(events)
    role = roles(spans, events)
    flips = [(t, arg) for t, tid, kind, _, arg, _ in events if kind == FLIP]
    presents = [(t, arg) for t, tid, kind, _, arg, _ in events if kind == PRESENT]
    span_s = (events[-1][0] - events[0][0]) / 1e9
    print(f'{len(events)} events over {span_s:.2f} s, {len(flips)} flip words, {len(presents)} presents')
    if args.csv and presents and args.at is not None:
        rows = {int(r['frame']): float(r['elapsed_ms']) for r in csv.DictReader(args.csv.open())}
        offs = [args.at + t / 1e9 - rows[a] / 1e3 for t, a in presents if a in rows]
        if offs:
            print(f'process time - CSV elapsed: median {statistics.median(offs) * 1e3:.1f} ms '
                  f'(min {min(offs) * 1e3:.1f}, max {max(offs) * 1e3:.1f}, n={len(offs)})')
    off = {}
    samples = {}
    if args.perf:
        switches, samples_raw = parse_perf(args.perf)
        offset = coarse_offset([(i[0], tid) for tid, it in spans.items() for i in it if i[2] == USLEEP], switches)
        if offset is None:
            print('perf: could not align (no usleep matched a switch)')
        else:
            print(f'perf aligned: timeline = perf + {offset / 1e9:.6f} s')
            off = offcpu_intervals(switches, offset)
            samples = {tid: [(t + offset, s) for t, s in v] for tid, v in samples_raw.items()}
    print('\nthreads:')
    for tid in sorted(role, key=lambda t: (role[t], t)):
        wids = sorted({a0 & 0xFF for t0, t1, k, a0, a1 in spans.get(tid, []) if k == PM})
        print(f'  {tid} {role[tid]}' + (f' wids={wids}' if wids else ''))
    if len(flips) < 2:
        return
    table = collections.defaultdict(lambda: collections.defaultdict(list))
    funcs = collections.defaultdict(collections.Counter)
    labels = dict(x.split('=', 1) for x in args.label)
    labelled = collections.defaultdict(lambda: collections.defaultdict(list))
    frame_ms = []
    for k in range(len(flips) - 1):
        lo, hi = flips[k][0], flips[k + 1][0]
        frame_ms.append((hi - lo) / 1e6)
        for tid, items in spans.items():
            acc = collections.Counter()
            for t0, t1, kind, a0, a1 in items:
                ns = max(0, min(t1, hi) - max(t0, lo))
                if not ns:
                    continue
                if kind == PM:
                    acc[f'w{a0 & 0xFF}{"*" if a1 & 0x100 else ""}'] += ns
                else:
                    acc[{DRAIN: 'drain', USLEEP: 'usleep', TASK: 'task', WAIT: 'sigwait', GPU: 'gpu'}.get(kind, str(kind))] += ns
            if tid in off:
                us = [(i[0], i[1]) for i in items if i[2] == USLEEP]
                acc['blocked-not-usleep'] += clip(subtract(off[tid][0], us), lo, hi)
                acc['preempted'] += clip(off[tid][1], lo, hi)
            for name, ns in acc.items():
                table[tid][name].append(ns / 1e6)
            for name in set(table[tid]) - set(acc):
                table[tid][name].append(0.0)
        for tid, v in samples.items():
            i = bisect.bisect_left(v, (lo, ''))
            per = collections.Counter()
            while i < len(v) and v[i][0] < hi:
                funcs[tid][v[i][1]] += 1
                per[labels.get(v[i][1], 'other')] += 1
                i += 1
            for name in set(labels.values()) | {'other'}:
                labelled[tid][name].append(per[name] * 1000 / args.perf_freq)
    n = len(frame_ms)
    print(f'\n{n} frames (flip to flip): median {statistics.median(frame_ms):.1f} ms, '
          f'min {min(frame_ms):.1f}, max {max(frame_ms):.1f}')
    print('per-frame ' + ' '.join(f'{x:.0f}' for x in frame_ms[:args.frames]))
    print('\nmedian ms per frame by thread and activity (overlapping, not additive across threads):')
    for tid in sorted(table, key=lambda t: (role.get(t, '?'), t)):
        if max((max(v) for v in table[tid].values() if v), default=0) < 0.5:
            continue
        cols = sorted(table[tid].items(), key=lambda kv: -statistics.median(kv[1] + [0] * (n - len(kv[1]))))
        shown = ', '.join(f'{k} {statistics.median(v + [0] * (n - len(v))):.1f}' for k, v in cols[:8])
        print(f'  {tid} {role.get(tid, "?")}: {shown}')
    if funcs:
        print('\non-CPU samples per thread (top guest functions in the frames):')
        for tid, c in sorted(funcs.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
            tot = sum(c.values())
            print(f'  {tid} {role.get(tid, "?")} {tot} samples: ' +
                  ', '.join(f'{s} {k / tot:.0%}' for s, k in c.most_common(5)))
    if labels and labelled:
        print('\non-CPU ms per frame by --label (samples x period; median over frames):')
        for tid, cols in sorted(labelled.items(), key=lambda kv: -sum(sum(v) for v in kv[1].values()))[:6]:
            print(f'  {tid} {role.get(tid, "?")}: ' + ', '.join(
                f'{k} {statistics.median(v):.1f}' for k, v in sorted(cols.items(), key=lambda kv: -sum(kv[1]))))
    episodes = []
    for tid, items in spans.items():
        if role.get(tid) == 'pm':
            episodes += [(t0, t1, w, tid) for t0, t1, w in busy_episodes(items, 2e6, args.episode_ms * 1e6)]
    episodes.sort()
    if episodes:
        print(f'\nbusy episodes >= {args.episode_ms:.0f} ms (found-work PM runs merged across gaps < 2 ms):')
        by = collections.defaultdict(list)
        for e in episodes:
            by[(e[3], e[2])].append(e)
        for (tid, w), es in sorted(by.items()):
            durs = [(e[1] - e[0]) / 1e6 for e in es]
            blk = sum(clip(off[tid][0], e[0], e[1]) for e in es) / 1e6 if tid in off else None
            print(f'  {tid} w{w}: {len(es)} episodes, ms ' + ' '.join(f'{d:.0f}' for d in durs) +
                  (f'; blocked inside {blk:.1f} ms' if blk is not None else ''))
        gaps = [(b[0] - a[1]) / 1e6 for a, b in zip(episodes, episodes[1:])]
        print('  gaps between consecutive episodes (any thread), ms: ' + ' '.join(f'{g:.1f}' for g in gaps[:30]))
        ep_ends = sorted(e[1] for e in episodes)
    print('\nPPU polling episodes (usleep loops by return address); "ended after" = interval ends on other'
          ' threads within --release-us before the episode ended, with the share of 1 ms-spaced random'
          ' instants that see the same label (base rate):')
    lo, hi = flips[0][0], flips[-1][0]
    ends = end_index(spans, role)
    base_n, base = 0, collections.Counter()
    for t in range(int(lo), int(hi), 1_000_000):
        base_n += 1
        base.update(set(releasers(t, ends, None, args.release_us * 1e3)))
    for tid, items in spans.items():
        if role.get(tid) != 'ppu':
            continue
        eps = [e for e in poll_episodes(items, args.gap_us * 1e3) if lo <= e[0] < hi]
        by = collections.defaultdict(list)
        for e in eps:
            by[e[2]].append(e)
        for lr, es in sorted(by.items(), key=lambda kv: -sum(e[1] - e[0] for e in kv[1])):
            tot = sum(e[1] - e[0] for e in es) / 1e6
            if tot / n < 1:
                continue
            durs = sorted((e[1] - e[0]) / 1e6 for e in es)
            rel = collections.Counter()
            for e in es:
                rel.update(set(releasers(e[1], ends, tid, args.release_us * 1e3)))
            lift = sorted(rel.items(), key=lambda kv: -(kv[1] / len(es) - base[kv[0]] / max(1, base_n)))
            top = ', '.join(f'{k} {v}/{len(es)} (base {base[k] / max(1, base_n):.0%})' for k, v in lift[:4])
            print(f'  {tid} lr=0x{lr:08X}: {len(es) / n:.1f}/frame, {tot / n:.1f} ms/frame, '
                  f'median {durs[len(durs) // 2]:.2f} ms, max {durs[-1]:.1f} ms; ended after: {top}')
            if episodes:
                hit = 0
                for e in es:
                    i = bisect.bisect_right(ep_ends, e[1]) - 1
                    hit += i >= 0 and e[1] - ep_ends[i] <= 1e5
                print(f'      ended <= 0.1 ms after a busy-episode end: {hit}/{len(es)}')


if __name__ == '__main__':
    main()
