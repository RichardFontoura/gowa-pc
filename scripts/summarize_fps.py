#!/usr/bin/env python3
"""Report guest flips separately from host presents; never count boot redraws as FPS."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def percentile(values, p):
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def summarize(path, windows):
    rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(path.open())]
    if not rows or 'guest_flip' not in rows[0]:
        raise ValueError('Need an instrumented CSV with guest_flip counter')
    unique = []
    last_id, last_time = 0, None
    for row in rows:
        if row['guest_flip'] <= 0 or row['guest_flip'] == last_id:
            continue
        unique.append(dict(row, guest_frame_ms=(row['elapsed_ms'] - last_time if last_time is not None else 0)))
        last_id, last_time = row['guest_flip'], row['elapsed_ms']
    results = []
    for label, start, end in windows:
        samples = [r for r in rows if start * 1000 <= r['elapsed_ms'] < end * 1000]
        frames = [r for r in unique if start * 1000 <= r['elapsed_ms'] < end * 1000]
        times = [r['guest_frame_ms'] for r in frames if r['guest_frame_ms'] > 0]
        result = dict(stage=label, start_s=start, end_s=end, host_presents=len(samples),
                      guest_frames=len(frames), guest_fps=len(frames) / (end - start),
                      complete=rows[-1]['elapsed_ms'] >= end * 1000)
        if times:
            result.update(frame_ms_median=statistics.median(times), frame_ms_p95=percentile(times, .95),
                          frame_ms_p99=percentile(times, .99), stutters_over_100ms=sum(t > 100 for t in times))
        if samples:
            result.update(submit_wait_ms_per_guest=sum(r['submit_wait_ms'] for r in samples) / max(1, len(frames)),
                          present_ms_per_guest=sum(r['present_ms'] for r in samples) / max(1, len(frames)))
        results.append(result)
    return dict(csv=str(path), last_elapsed_s=rows[-1]['elapsed_ms'] / 1000, stages=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='+')
    parser.add_argument('--window', action='append', help='label:start_seconds:end_seconds')
    args = parser.parse_args()
    windows = [x.split(':') for x in (args.window or ['intro:20:33', 'menu:140:160'])]
    windows = [(name, float(start), float(end)) for name, start, end in windows]
    print(json.dumps([summarize(p, windows) for p in args.csv], indent=2))


if __name__ == '__main__':
    main()
