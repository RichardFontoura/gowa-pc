/* Opt-in probe for isolated Vulkan performance builds. One CSV row per
 * completed present call; the backend already serializes these operations. */
#include <time.h>
/// Not part of the normal build: scripts/relink_perf_snapshot.py --probe copies
/// this header next to the ps3recomp Vulkan backend and patches calls into it.
/// Expects <stdio.h>, <stdlib.h> and <stdint.h> to be included already.
/// Enabled by setting GOWA_FRAME_CSV to the output file path.

/// Open CSV output, or NULL when GOWA_FRAME_CSV is unset or cannot be opened.
static FILE* perf_csv;
/// Set after the first perf_init() call.
static int perf_initialized;
/// perf_start: time of perf_init(); perf_last: time of the previous row.
/// perf_wait_ns / perf_submits: fence wait time and submit count accumulated
/// by the patched backend since the last row, reset by perf_frame().
static uint64_t perf_start, perf_last, perf_wait_ns, perf_submits;
/// Guest flip requests so far (cellGcmSys HLE), written as the guest_flip column.
extern unsigned cellGcm_flip_request_count(void);
/// CLOCK_MONOTONIC time in nanoseconds.
static uint64_t perf_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}
/// One-time setup, safe to call repeatedly: opens GOWA_FRAME_CSV for writing,
/// writes the header row and starts the clock.
static void perf_init(void)
{
    if (perf_initialized) return;
    perf_initialized = 1;
    const char* path = getenv("GOWA_FRAME_CSV");
    if (path) perf_csv = fopen(path, "w");
    perf_start = perf_last = perf_now();
    if (perf_csv) fputs("frame,elapsed_ms,frame_ms,submit_wait_ms,submits,present_ms,guest_flip\n", perf_csv);
}
/// Appends one row for a completed present and flushes it, so the file is
/// usable even if the process is killed. Times are in milliseconds:
/// elapsed since perf_init, since the previous row, fence waits since the
/// previous row, and this present's duration from @p present_start (perf_now()
/// taken when the present began). @p frame is the backend's present counter.
static void perf_frame(uint64_t frame, uint64_t present_start)
{
    if (!perf_csv) return;
    uint64_t now = perf_now();
    fprintf(perf_csv, "%llu,%.6f,%.6f,%.6f,%llu,%.6f,%u\n",
            (unsigned long long)frame, (now - perf_start) / 1e6,
            (now - perf_last) / 1e6, perf_wait_ns / 1e6,
            (unsigned long long)perf_submits, (now - present_start) / 1e6,
            cellGcm_flip_request_count());
    fflush(perf_csv);
    perf_last = now;
    perf_wait_ns = perf_submits = 0;
}
