/// Live pacing test for the cellAudio mixer: real pthread and SDL dummy device.
///
/// Starts the real mixer thread of cellAudio.c for about two seconds, first
/// without an audio device and then with SDL's "dummy" driver, and checks the
/// block rate, that the device queue stays bounded and that the thread stops
/// promptly (including a repeated stop). Uses wall-clock time, so the accepted
/// rate ranges are wide.
///
/// AUDIO_IMPLEMENTATION is the quoted path of the cellAudio.c to test. Built and
/// run by scripts/build_perf_candidate.py, roughly:
///
///     cc -std=gnu17 -O2 -pthread -I <ps3recomp>/include
///        -DAUDIO_IMPLEMENTATION='"<ps3recomp>/libs/audio/cellAudio.c"'
///        tests/test_audio_pacing_live.c -lSDL2 -o test_audio_pacing_live
///     ./test_audio_pacing_live
///
/// The cc command is a single line, wrapped here for readability.
#include AUDIO_IMPLEMENTATION
#include <assert.h>

/// Runtime globals and hooks that cellAudio.c links against. Guest memory,
/// reservations, SPU coherency and event queues are not involved here, so they
/// are no-ops (no notify queue is found and pushes are ignored).
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
uint32_t sys_event_find_queue_by_key(uint64_t key) { (void)key; return 0; }
int sys_event_queue_push_by_id(uint32_t q, uint64_t s, uint64_t a, uint64_t b, uint64_t c)
{ (void)q; (void)s; (void)a; (void)b; (void)c; return 0; }

/// Runs the mixer thread for 100 polls of 20 ms, with the SDL device opened
/// when device is set. Asserts the block rate (about 187.5 blocks/s without a
/// device, a wider range for SDL's dummy driver), a peak queue of at most 1024
/// samples, a stop that takes under 100 ms, and that a second stop and the
/// backend shutdown are safe.
static void run(int device)
{
    if (device) assert(audio_backend_init() == 0);
    s_ports[0].read_index = 0;
    const uint64_t start = audio_monotonic_ns();
    assert(audio_start_mix_thread() == 0);
    u32 peak = 0;
    for (int i = 0; i < 100; i++) {
        usleep(20000);
        u32 queued = audio_backend_queued_samples();
        if (queued > peak) peak = queued;
    }
    const uint64_t stopping = audio_monotonic_ns();
    audio_stop_mix_thread();
    const uint64_t stopped = audio_monotonic_ns();
    double rate = s_ports[0].read_index * 1e9 / (stopped - start);
    printf("live %s rate=%.2f blocks/s peak=%u stop=%.3f ms\n",
           device ? "SDL-dummy" : "no-device", rate, peak, (stopped - stopping) / 1e6);
    /* Dummy SDL's integer-ms period is not an exact 48 kHz clock. */
    assert(device ? (rate > 175 && rate < 210) : (rate > 185 && rate < 190));
    assert(peak <= 1024);
    assert(stopped - stopping < 100000000ull);
    audio_stop_mix_thread(); /* repeated Quit guard */
    audio_backend_shutdown();
}

/// Selects SDL's dummy audio driver, sets up one running stereo port with eight
/// blocks, and runs the no-device case followed by the SDL dummy device case.
int main(void)
{
    SDL_setenv("SDL_AUDIODRIVER", "dummy", 1);
    mutex_init(&s_audio_mutex);
    AudioPortSlot* port = &s_ports[0];
    port->in_use = port->running = 1;
    port->param.nChannel = 2;
    port->param.nBlock = 8;
    port->param.level = 1;
    port->buffer = calloc(8 * CELL_AUDIO_BLOCK_SAMPLES * 2, sizeof(float));
    assert(port->buffer);
    run(0);
    run(1);
    free(port->buffer);
    mutex_destroy(&s_audio_mutex);
    SDL_QuitSubSystem(SDL_INIT_AUDIO);
    return 0;
}
