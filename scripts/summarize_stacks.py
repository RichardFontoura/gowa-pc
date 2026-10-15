#!/usr/bin/env python3
"""Aggregate sampled thread stacks of a benchmark run into per-thread profiles.

The run directory must contain ``stacks/*.txt`` (one raw ``eu-stack`` sample
per file: ``TID <n>:`` lines followed by ``#<i> 0x<address>`` frames) and
``maps.txt`` (a copy of the process's /proc/<pid>/maps). Addresses are
symbolised offline with ``eu-addr2line`` (elfutils).

For every thread prints its total samples, the share of busy samples (top
frame not a wait/sleep/poll function), its root function and its most common
stacks (the innermost --depth frames, innermost first, joined by " < ").
--flat adds inclusive function counts over busy samples of all threads.

Usage example:
    python3 scripts/summarize_stacks.py scratch/bench-run --min-busy 0.2 --flat
"""
import argparse
import bisect
import collections
import re
import subprocess
from pathlib import Path

FRAME = re.compile(r'^#\d+\s+0x([0-9a-f]+)')
# Prefixes of top-of-stack functions that mean the thread is waiting, not busy.
WAIT_HINTS = ('__futex', 'futex', 'pthread_cond', '__GI___nanosleep', 'nanosleep', 'clock_nanosleep',
              '__GI___clock_nanosleep', 'poll', '__poll', 'epoll', 'sched_yield', '__sched_yield', 'usleep',
              'sem_', 'WaitForSingleObject', 'Sleep', 'select', '__GI___ioctl', 'ioctl', '__GI___poll',
              'syscall', '__syscall_cancel_arch', '__internal_syscall_cancel', '__syscall_cancel')


def parse(path):
    """Read one eu-stack sample file.

    Returns:
        A dict mapping thread ID to its list of frame addresses, innermost
        first.
    """
    threads, tid = {}, None
    for line in path.read_text(errors='replace').splitlines():
        if line.startswith('TID '):
            tid = int(line.split()[1].rstrip(':'))
            threads[tid] = []
        elif tid is not None:
            match = FRAME.match(line)
            if match:
                threads[tid].append(int(match.group(1), 16))
    return threads


def load_maps(path):
    """Read the file-backed mappings of a /proc/<pid>/maps copy.

    Returns:
        A list of (start, end, file offset, path) tuples sorted by start.
    """
    maps = []
    for line in path.read_text().splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[5].startswith('/'):
            continue
        lo, hi = (int(x, 16) for x in parts[0].split('-'))
        maps.append((lo, hi, int(parts[2], 16), parts[5]))
    maps.sort()
    return maps


def symbolize(addresses, maps):
    """Resolve absolute addresses to function names with eu-addr2line.

    Each address is converted to an offset from its module's load base and
    looked up in batches of 2000 per module.

    Args:
        addresses: iterable of absolute addresses.
        maps: mappings returned by load_maps().

    Returns:
        A dict mapping each address to its demangled function name,
        ``<module>+?`` when the module has no symbol for it, or ``??`` when
        no mapping contains it.
    """
    starts = [m[0] for m in maps]
    by_module = collections.defaultdict(list)
    names = {}
    for addr in addresses:
        i = bisect.bisect_right(starts, addr) - 1
        if i < 0 or addr >= maps[i][1]:
            names[addr] = '??'
            continue
        lo, _, off, path = maps[i]
        base = min(m[0] - m[2] for m in maps if m[3] == path)
        by_module[path].append((addr, addr - base))
    for path, items in by_module.items():
        short = Path(path).name
        for chunk in range(0, len(items), 2000):
            part = items[chunk:chunk + 2000]
            out = subprocess.run(['eu-addr2line', '-e', path, '-f', '-C'] + [hex(rel) for _, rel in part],
                                 capture_output=True, text=True).stdout.splitlines()
            for k, (addr, _) in enumerate(part):
                fn = out[2 * k] if 2 * k < len(out) else '??'
                names[addr] = fn if fn != '??' else f'{short}+?'
    return names


def main():
    """Parse arguments, aggregate all samples and print the per-thread report.

    Threads are listed by sample count, skipping those whose busy share is
    below --min-busy; each shows its --top most common stacks.
    """
    parser = argparse.ArgumentParser(description='Aggregate raw eu-stack samples per thread, symbolized offline from maps.txt.')
    parser.add_argument('run', type=Path, help='benchmark output directory with stacks/ and maps.txt')
    parser.add_argument('--depth', type=int, default=8)
    parser.add_argument('--top', type=int, default=6)
    parser.add_argument('--min-busy', type=float, default=0.0)
    parser.add_argument('--flat', action='store_true', help='also print inclusive function counts over busy samples')
    parser.add_argument('--only', help='restrict --flat to stacks containing this function')
    args = parser.parse_args()
    files = sorted((args.run / 'stacks').glob('*.txt'))
    samples = [parse(p) for p in files]
    names = symbolize({a for s in samples for frames in s.values() for a in frames}, load_maps(args.run / 'maps.txt'))
    per_thread = collections.defaultdict(collections.Counter)
    roots = collections.defaultdict(collections.Counter)
    flat = collections.Counter()
    for sample in samples:
        for tid, frames in sample.items():
            fns = [names.get(a, '??') for a in frames]
            fns = [f.split('(')[0] for f in fns]
            per_thread[tid][' < '.join(fns[:args.depth])] += 1
            tail = [f for f in fns if not f.startswith(('start_thread', 'clone', '__clone', '??'))]
            roots[tid][tail[-1] if tail else '??'] += 1
            if fns and not fns[0].startswith(WAIT_HINTS) and (not args.only or any(args.only in f for f in fns)):
                for f in set(fns):
                    flat[f] += 1
    print(f'{len(files)} samples')
    for tid, counter in sorted(per_thread.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(counter.values())
        busy = sum(n for stack, n in counter.items() if not stack.startswith(WAIT_HINTS))
        if busy / total < args.min_busy:
            continue
        print(f'\nTID {tid} root={roots[tid].most_common(1)[0][0]} samples={total} busy={busy / total:.0%}')
        for stack, n in counter.most_common(args.top):
            print(f'  {n:4d}  {stack}')
    if args.flat:
        print('\ninclusive (busy samples, all threads):')
        for fn, n in flat.most_common(60):
            print(f'  {n:5d}  {fn}')


if __name__ == '__main__':
    main()
