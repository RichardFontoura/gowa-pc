/* Opt-in probe for isolated Vulkan performance builds. One CSV row per
 * completed present call; the backend already serializes these operations. */
#include <time.h>
static FILE* perf_csv;
static int perf_initialized;
static uint64_t perf_start, perf_last, perf_wait_ns, perf_submits;
extern unsigned cellGcm_flip_request_count(void);
static uint64_t perf_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}
static void perf_init(void)
{
    if (perf_initialized) return;
    perf_initialized = 1;
    const char* path = getenv("GOWA_FRAME_CSV");
    if (path) perf_csv = fopen(path, "w");
    perf_start = perf_last = perf_now();
    if (perf_csv) fputs("frame,elapsed_ms,frame_ms,submit_wait_ms,submits,present_ms,guest_flip\n", perf_csv);
}
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
