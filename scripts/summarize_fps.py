#!/usr/bin/env python3
"""Report guest flips separately from host presents; never count boot redraws as FPS.

Input: CSV files written by an executable built with the frame probe
(relink_perf_snapshot.py --probe) and run with GOWA_FRAME_CSV=<file>; one row
per host present with at least the columns elapsed_ms, guest_flip,
submit_wait_ms and present_ms. A guest frame is a row whose guest_flip counter
is positive and differs from the previous counted row.

For each time window (--window label:start_s:end_s, repeatable; default
intro:20:33 and menu:140:160) prints, as JSON: host presents, guest frames,
guest FPS, median/p95/p99 guest frame time, frames over 100 ms, GPU submit
wait and present time per guest frame, and whether the capture covered the
whole window.

Usage example:
    python3 scripts/summarize_fps.py scratch/frames.csv --window gameplay:60:120
"""
import argparse
import csv
import json
from pathlib import Path
import statistics


def percentile(values, p):
    """Return the ``p`` quantile (0..1) of ``values`` with linear interpolation."""
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def summarize(path, windows):
    """Compute the per-window statistics of one frame CSV.

    Args:
        path: Path of the CSV file.
        windows: list of (label, start seconds, end seconds) tuples.

    Returns:
        A dict with the CSV path, the last elapsed time in seconds and a
        ``stages`` list holding one statistics dict per window.

    Raises:
        ValueError: if the CSV is empty or has no guest_flip column.
    """
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
    """Parse arguments and print the summaries of all CSVs as a JSON list."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='+')
    parser.add_argument('--window', action='append', help='label:start_seconds:end_seconds')
    args = parser.parse_args()
    windows = [x.split(':') for x in (args.window or ['intro:20:33', 'menu:140:160'])]
    windows = [(name, float(start), float(end)) for name, start, end in windows]
    print(json.dumps([summarize(p, windows) for p in args.csv], indent=2))


if __name__ == '__main__':
    main()
