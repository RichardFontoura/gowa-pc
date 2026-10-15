/// Pacing test for the cellAudio mixer thread with a simulated clock and device.
///
/// Runs the real POSIX mixer loop of cellAudio.c against a deterministic 48 kHz
/// device and clock. The time, sleep and SDL queue functions the mixer calls are
/// redirected by macro to fakes in this file before the implementation is
/// #included, so no audio device, guest process, wall-clock sleep or GPU is used.
/// Each scenario simulates 20 s and prints the event rate, peak queue depth and
/// longest burst of events delivered at the same simulated instant.
///
/// AUDIO_IMPLEMENTATION is the quoted path of the cellAudio.c to test (a baseline
/// or a candidate tree). Built and run by scripts/build_perf_candidate.py, roughly:
///
///     cc -std=gnu17 -O2 -pthread -I <ps3recomp>/include
///        -DAUDIO_IMPLEMENTATION='"<ps3recomp>/libs/audio/cellAudio.c"'
///        tests/test_audio_pacing.c -lSDL2 -o test_audio_pacing
///     ./test_audio_pacing --check
///
/// The cc command is a single line, wrapped here for readability.
///
/// Without arguments only the statistics are printed; any argument (such as
/// --check) also enables the pacing assertions.
#include <SDL2/SDL.h>
#include <time.h>
#include <unistd.h>
#include <assert.h>
#include <stdint.h>

/// Fakes for the clock, sleep and SDL audio queue functions; defined below and
/// substituted into cellAudio.c by the macros that follow.
static int fake_clock_gettime(clockid_t id, struct timespec* ts);
static int fake_usleep(useconds_t usec);
static void fake_delay(Uint32 ms);
static Uint32 fake_queued(SDL_AudioDeviceID dev);
static int fake_queue(SDL_AudioDeviceID dev, const void* data, Uint32 len);
#define clock_gettime fake_clock_gettime
#define usleep fake_usleep
#define SDL_Delay fake_delay
#define SDL_GetQueuedAudioSize fake_queued
#define SDL_QueueAudio fake_queue
#include AUDIO_IMPLEMENTATION
#undef clock_gettime
#undef usleep
#undef SDL_Delay
#undef SDL_GetQueuedAudioSize
#undef SDL_QueueAudio

/// Runtime globals and hooks that cellAudio.c links against. Guest memory,
/// reservations and SPU coherency are not involved here, so they are no-ops.
uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t ea) { (void)ea; }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) { (void)a; (void)v; (void)w; }
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}

/// Simulation state: the fake clock, the fake device queue, event and burst
/// counters, the optional one-time pause and the frozen (non-draining) device
/// flag. END_NS is the simulated length of each scenario (20 s).
static uint64_t now_ns, last_drain_ns, drain_remainder, events;
static uint64_t peak_frames, queued_frames, last_event_ns, burst, max_burst;
static int inject_pause, paused, frozen;
#define END_NS 20000000000ull

/// Event queue lookup stub: every key resolves to queue id 1.
uint32_t sys_event_find_queue_by_key(uint64_t key) { (void)key; return 1; }
/// Counts each audio notification the mixer sends and tracks the longest run
/// delivered at the same simulated instant. Aborts after 20000 events, which
/// means the mixer is spinning without waiting on the clock or device.
int sys_event_queue_push_by_id(uint32_t id, uint64_t source, uint64_t a,
                              uint64_t b, uint64_t c)
{
    (void)id; (void)source; (void)a; (void)b; (void)c;
    events++;
    burst = events > 1 && now_ns == last_event_ns ? burst + 1 : 1;
    if (burst > max_burst) max_burst = burst;
    last_event_ns = now_ns;
    assert(events < 20000); /* detect a mixer spinning with no clock/device */
    return 0;
}

/// Moves the simulated clock forward by ns. When a pause is injected, the first
/// crossing of 2 s adds a single 500 ms jump; reaching END_NS stops the mixer.
static void advance(uint64_t ns)
{
    now_ns += ns;
    if (inject_pause && !paused && now_ns >= 2000000000ull) {
        paused = 1;
        now_ns += 500000000ull; /* 500 ms debugger/scheduler pause */
    }
    if (now_ns >= END_NS) s_mix_thread_running = 0;
}

/// clock_gettime replacement: reports the simulated time for any clock id.
static int fake_clock_gettime(clockid_t id, struct timespec* ts)
{
    (void)id;
    ts->tv_sec = now_ns / 1000000000ull;
    ts->tv_nsec = now_ns % 1000000000ull;
    return 0;
}
/// usleep replacement: advances the simulated clock instead of sleeping.
static int fake_usleep(useconds_t us) { advance((uint64_t)us * 1000); return 0; }
/// SDL_Delay replacement: advances the simulated clock instead of sleeping.
static void fake_delay(Uint32 ms) { advance((uint64_t)ms * 1000000); }
/// SDL_GetQueuedAudioSize replacement. Drains the simulated queue by the frames a
/// 48 kHz device would have played since the last call (keeping the fractional
/// remainder; a frozen device plays nothing) and returns the queued size in bytes
/// of stereo float samples.
static Uint32 fake_queued(SDL_AudioDeviceID dev)
{
    (void)dev;
    uint64_t scaled = (now_ns - last_drain_ns) * CELL_AUDIO_SAMPLE_RATE + drain_remainder;
    uint64_t consumed = scaled / 1000000000ull;
    if (frozen) consumed = 0;
    drain_remainder = scaled % 1000000000ull;
    queued_frames = consumed < queued_frames ? queued_frames - consumed : 0;
    last_drain_ns = now_ns;
    return (Uint32)(queued_frames * 2 * sizeof(float));
}
/// SDL_QueueAudio replacement: drains the queue up to now, appends len bytes of
/// stereo float frames and records the peak queue depth.
static int fake_queue(SDL_AudioDeviceID dev, const void* data, Uint32 len)
{
    (void)data;
    fake_queued(dev);
    queued_frames += len / (2 * sizeof(float));
    if (queued_frames > peak_frames) peak_frames = queued_frames;
    return 0;
}

/// Runs the mixer thread function synchronously for one simulated scenario.
///
/// device selects the SDL device path (1) or the clock-only path without a
/// device (0); pause injects the 500 ms stall. Every notification must match one
/// port read-index advance. When check is set, the event count must stay within
/// a few blocks of the 48 kHz block rate over the active time (exactly the four
/// priming blocks for a frozen device), the device queue must never exceed four
/// blocks, and without a device no two events may share a simulated instant.
static void scenario(const char* name, int device, int pause, int check)
{
    now_ns = last_drain_ns = drain_remainder = events = 0;
    peak_frames = queued_frames = last_event_ns = burst = max_burst = 0;
    inject_pause = pause;
    paused = 0;
    s_sdl_audio_dev = device;
    s_ports[0].read_index = 0;
    s_mix_thread_running = 1;
    audio_mix_thread_func(NULL);
    assert(events == s_ports[0].read_index);
    double rate = events * 1e9 / now_ns;
    printf("%s: events=%llu rate=%.3f blocks/s queue_peak=%llu frames (%.3f ms) burst=%llu\n",
           name, (unsigned long long)events, rate, (unsigned long long)peak_frames,
           peak_frames * 1000.0 / CELL_AUDIO_SAMPLE_RATE, (unsigned long long)max_burst);
    if (check) {
        double active_seconds = (now_ns - (pause ? 500000000ull : 0)) / 1e9;
        double expected = active_seconds * CELL_AUDIO_SAMPLE_RATE / CELL_AUDIO_BLOCK_SAMPLES;
        /* A running device is primed with at most four blocks; after a long
         * pause its empty queue is primed again. Those are bounded lead, not
         * lost guest events or an unbounded catch-up burst. */
        if (frozen) assert(events == 4);
        else assert(events >= expected - 2 && events <= expected + (device && pause ? 8 : 4));
        if (device) assert(peak_frames <= 4 * CELL_AUDIO_BLOCK_SAMPLES);
        else assert(max_burst == 1);
    }
}

/// Sets up one running stereo port with eight blocks and a notify queue, then
/// runs the scenarios: device, no device, each with a pause, and a frozen
/// device whose queue never drains. Any command-line argument enables checks.
int main(int argc, char** argv)
{
    (void)argv;
    mutex_init(&s_audio_mutex);
    s_notify_queues[0].in_use = 1;
    s_notify_queues[0].key = 1;
    s_ports[0].in_use = s_ports[0].running = 1;
    s_ports[0].param.nChannel = 2;
    s_ports[0].param.nBlock = 8;
    s_ports[0].param.level = 1.0f;
    s_ports[0].buffer = calloc(8 * CELL_AUDIO_BLOCK_SAMPLES * 2, sizeof(float));
    assert(s_ports[0].buffer);
    scenario("device", 1, 0, argc > 1);
    scenario("no-device", 0, 0, argc > 1);
    scenario("device-pause", 1, 1, argc > 1);
    scenario("no-device-pause", 0, 1, argc > 1);
    frozen = 1;
    scenario("full-device", 1, 0, argc > 1);
    free(s_ports[0].buffer);
    mutex_destroy(&s_audio_mutex);
    return 0;
}
