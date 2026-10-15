/// Timing and correctness test for hot SPU channel paths in spu_channels.c.
///
/// Runs the actual MFC and SPURS policy-module paths a fixed number of times,
/// prints how long each loop took, and asserts the semantic result of every
/// operation: list-stall acknowledgement and tag completion, 16 KiB PUT/GET DMA
/// between local store and guest memory, and the policy-module workload poll.
/// Intended for comparing a baseline and a candidate build of the same file.
///
/// SPU_IMPLEMENTATION is the quoted path of the spu_channels.c to test. It needs
/// the same runtime dependencies as ps3recomp's test_spu_mfc_slots.c. Built and
/// run by scripts/build_perf_candidate.py, roughly:
///
///     cc -std=gnu17 -O2 -pthread -I <ps3recomp>/include
///        -I <ps3recomp>/runtime/spu -I <ps3recomp>/runtime/platform
///        -DSPU_IMPLEMENTATION='"<ps3recomp>/runtime/spu/spu_channels.c"'
///        tests/test_spu_perf.c <spu runtime sources> win32_compat.c -lm
///     ./test_spu_perf
///
/// The cc command is a single line, wrapped here for readability; see the
/// script for the exact list of SPU runtime sources. With SPU_MFC_TRACE=1 and
/// SPURS_PM_TRACE=1 the script also counts the trace lines of each path.
#include <stdint.h>
#include <assert.h>
#include <time.h>
/// 128 KiB guest memory image used as vm_base for DMA transfers.
static uint8_t guest[131072];
uint8_t* vm_base = guest;
uint32_t ppu_vm_size = sizeof guest;
#include SPU_IMPLEMENTATION
extern _Thread_local volatile unsigned g_spurs_pm_polls;
/// Globals the SPU runtime references that are owned by other modules in the
/// full build; zero here.
uint32_t g_barrier_sync_watch, g_spu_image_src_ea, g_spu_image_ls_start, g_spu_image_span;
uint32_t g_ydkj_real_taskset_ea, g_ydkj_real_taskid, g_ydkj_real_spurs_ea;
/// Unused title/taskset hooks: fail if a fixture accidentally reaches them.
void spurs_ef_set_from_spu(uint32_t ea, uint16_t bits) { (void)ea; (void)bits; abort(); }
/// Unused hook; aborts if reached.
void ydkj_wake_all_event_flags(void) { abort(); }
/// Unused hook; aborts if reached.
uint64_t spurs_pm_build_context(uint8_t* ls, uint32_t ea, uint32_t task, uint32_t num, uint32_t tag)
{ (void)ls; (void)ea; (void)task; (void)num; (void)tag; abort(); }
/// Millisecond monotonic clock required by the SPU runtime.
unsigned long long ps3_ms_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/// Monotonic time in seconds, used to time each benchmark loop.
static double seconds(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/// Runs three timed loops of 20000 iterations on one SPU context, with the
/// first two entries of the guest page bitmap marked mapped:
/// - resume: acknowledging a stalled DMA list clears the stall and completes tag 0;
/// - put16k: a 16 KiB PUT copies local store to guest memory, and a GET back
///   into another local store range reproduces the data;
/// - poll: an indirect branch to the policy module's workload poll address
///   counts one poll, returns 0 in r3 and continues at the link register.
int main(void)
{
    const unsigned iterations = 20000;
    spu_context* ctx = calloc(1, sizeof(*ctx));
    assert(ctx);
    spu_context_init(ctx, 0);
    ctx->image_id = -1;
    extern uint8_t g_vm_page_bitmap[65536 / 8];
    g_vm_page_bitmap[0] = 3;
    mfc_engine mfc;
    mfc_engine_init(&mfc);

    double start = seconds();
    for (unsigned i = 0; i < iterations; i++) {
        ctx->list_stall_mask = 1;
        ctx->list_stall_remaining[0] = 0;
        mfc.tag_completed = 0;
        assert(mfc_list_stall_ack(&mfc, ctx, 0) == 0);
        assert(ctx->list_stall_mask == 0);
        assert(mfc.tag_completed == 1);
    }
    printf("resume %u operations %.6f seconds\n", iterations, seconds() - start);

    memset(ctx->ls + 0x1000, 0x5a, 16384);
    start = seconds();
    for (unsigned i = 0; i < iterations; i++) {
        ctx->ls[0x1000] = (uint8_t)i;
        assert(mfc_do_transfer(ctx, 0x1000, 0x12000, 16384, MFC_PUT_CMD) == 0);
        assert(guest[0x12000] == (uint8_t)i);
    }
    printf("put16k %u operations %.6f seconds\n", iterations, seconds() - start);
    assert(memcmp(ctx->ls + 0x1000, guest + 0x12000, 16384) == 0);
    assert(mfc_do_transfer(ctx, 0x6000, 0x12000, 16384, MFC_GET_CMD) == 0);
    assert(memcmp(ctx->ls + 0x1000, ctx->ls + 0x6000, 16384) == 0);

    ctx->policy_mode = 1;
    ctx->gpr[0]._u32[0] = 0x100;
    start = seconds();
    for (unsigned i = 0; i < iterations; i++) {
        g_spurs_pm_polls = 0; /* resets each run in the actual scheduler */
        ctx->pc = SPURS_PM_SELECT_WORKLOAD_LS;
        ctx->gpr[3]._u32[0] = 123;
        spu_indirect_branch(ctx);
        assert(g_spurs_pm_polls == 1);
        assert(ctx->gpr[3]._u32[0] == 0);
        assert(ctx->pc == 0x100);
    }
    printf("poll %u operations %.6f seconds\n", iterations, seconds() - start);
    free(ctx);
    puts("SPU performance paths: DMA payload, tag completion and poll results passed");
    return 0;
}
