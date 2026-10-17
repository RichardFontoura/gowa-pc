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

Columns written by the Vulkan backend's RSX_FRAME_CSV (draws, submits,
shader_ms, gpu_ms) are summed over the window and reported per guest frame;
gpu_ms is only non-zero when the game ran with RSX_VK_GPU_TIMING=1. The
guest_flip counter delta over the window is reported too, so flips that never
reached a host present are visible (guest_flip_delta > guest_frames).

--scene NAME (repeatable) adds the fixed windows of tests/scene_bench.py, given
in harness seconds; --offset S is subtracted from them to obtain CSV seconds
(elapsed_ms starts when the backend initialises, 0.24-0.39 s after the process
in the measured runs).

Usage example:
    python3 scripts/summarize_fps.py scratch/frames.csv --window gameplay:60:120
    python3 scripts/summarize_fps.py runs/*/frames.csv --scene menu3d --scene gameplay --offset 0.39 --table
"""
import argparse
import csv
import json
from pathlib import Path
import statistics

SCENES = {'menu3d': (50, 90), 'gameplay': (700, 800), 'gameplay_early': (690, 720),
          'gameplay_late': (755, 800)}
PER_GUEST = ('submit_wait_ms', 'present_ms', 'draws', 'submits', 'shader_ms', 'gpu_ms')


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
                          frame_ms_p99=percentile(times, .99), frame_ms_max=max(times),
                          stutters_over_100ms=sum(t > 100 for t in times))
        if samples:
            result.update(guest_flip_delta=samples[-1]['guest_flip'] - samples[0]['guest_flip'] + 1)
            for key in PER_GUEST:
                if key in samples[0]:
                    result[f'{key}_per_guest'] = sum(r[key] for r in samples) / max(1, len(frames))
        results.append(result)
    return dict(csv=str(path), last_elapsed_s=rows[-1]['elapsed_ms'] / 1000, stages=results)


def mark_windows(csv_path, marks_path):
    rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(csv_path.open())]
    marks = [int(row['guest_flip']) for row in csv.DictReader(marks_path.open())]
    if not rows or not marks:
        return []
    def at(flip):
        for row in rows:
            if row['guest_flip'] >= flip:
                return row['elapsed_ms'] / 1000
        return rows[-1]['elapsed_ms'] / 1000
    windows = []
    for i in range(0, len(marks), 2):
        begin = at(marks[i])
        end = at(marks[i + 1]) if i + 1 < len(marks) else rows[-1]['elapsed_ms'] / 1000
        if end > begin:
            windows.append((f'marca{i // 2 + 1}', begin, end))
    return windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='+')
    parser.add_argument('--window', action='append', help='label:start_seconds:end_seconds')
    parser.add_argument('--scene', action='append', choices=sorted(SCENES), help='fixed harness window')
    parser.add_argument('--offset', type=float, default=0.0, help='harness seconds minus CSV seconds')
    parser.add_argument('--table', action='store_true', help='print a Markdown table instead of JSON')
    parser.add_argument('--marks', nargs='?', const='', default=None, metavar='MARKS_CSV',
                        help='add one window per pair of F9 scene marks (default: marks.csv next to each CSV)')
    args = parser.parse_args()
    defaults = [] if args.scene or args.marks is not None else ['intro:20:33', 'menu:140:160']
    windows = [x.split(':') for x in (args.window or defaults)]
    windows = [(name, float(start), float(end)) for name, start, end in windows]
    windows += [(name, SCENES[name][0] - args.offset, SCENES[name][1] - args.offset) for name in args.scene or []]
    results = []
    for p in args.csv:
        extra = []
        if args.marks is not None:
            marks = Path(args.marks) if args.marks else p.parent / 'marks.csv'
            extra = mark_windows(p, marks) if marks.exists() else []
        results.append(summarize(p, windows + extra))
    if not args.table:
        print(json.dumps(results, indent=2))
        return
    print('| run | window | s | guest frames | FPS | median | p95 | p99 | max | >100 ms | wait/frame | draws | gpu_ms | complete |')
    print('|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|')
    for r in results:
        run = Path(r['csv']).parent.name
        for st in r['stages']:
            g = st.get
            print(f"| {run} | {st['stage']} | {st['start_s']:.1f}-{st['end_s']:.1f} | {st['guest_frames']} | "
                  f"{st['guest_fps']:.2f} | {g('frame_ms_median', 0):.1f} | {g('frame_ms_p95', 0):.1f} | "
                  f"{g('frame_ms_p99', 0):.1f} | {g('frame_ms_max', 0):.0f} | {g('stutters_over_100ms', 0)} | "
                  f"{g('submit_wait_ms_per_guest', 0):.1f} | {g('draws_per_guest', 0):.0f} | "
                  f"{g('gpu_ms_per_guest', 0):.1f} | {'yes' if st['complete'] else 'NO'} |")


if __name__ == '__main__':
    main()
