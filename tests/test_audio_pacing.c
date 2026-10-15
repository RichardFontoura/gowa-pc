/* Run the real POSIX mixer against a deterministic 48 kHz device and clock.
 * AUDIO_IMPLEMENTATION selects the baseline or candidate cellAudio.c.
 * No audio device, guest process, wall-clock sleep or GPU is used. */
#include <SDL2/SDL.h>
#include <time.h>
#include <unistd.h>
#include <assert.h>
#include <stdint.h>

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

static uint64_t now_ns, last_drain_ns, drain_remainder, events;
static uint64_t peak_frames, queued_frames, last_event_ns, burst, max_burst;
static int inject_pause, paused, frozen;
#define END_NS 20000000000ull

uint32_t sys_event_find_queue_by_key(uint64_t key) { (void)key; return 1; }
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

static void advance(uint64_t ns)
{
    now_ns += ns;
    if (inject_pause && !paused && now_ns >= 2000000000ull) {
        paused = 1;
        now_ns += 500000000ull; /* 500 ms debugger/scheduler pause */
    }
    if (now_ns >= END_NS) s_mix_thread_running = 0;
}

static int fake_clock_gettime(clockid_t id, struct timespec* ts)
{
    (void)id;
    ts->tv_sec = now_ns / 1000000000ull;
    ts->tv_nsec = now_ns % 1000000000ull;
    return 0;
}
static int fake_usleep(useconds_t us) { advance((uint64_t)us * 1000); return 0; }
static void fake_delay(Uint32 ms) { advance((uint64_t)ms * 1000000); }
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
static int fake_queue(SDL_AudioDeviceID dev, const void* data, Uint32 len)
{
    (void)data;
    fake_queued(dev);
    queued_frames += len / (2 * sizeof(float));
    if (queued_frames > peak_frames) peak_frames = queued_frames;
    return 0;
}

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
