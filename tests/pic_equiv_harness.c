#include "spu_interp.c"
#include "spu_pic_match.h"
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

typedef struct {
    const spu_pic_entry* entries;
    unsigned count;
    const uint8_t* code;
    uint32_t size;
} pic_region;

typedef struct {
    uint32_t ch;
    uint32_t value;
    uint32_t write;
} chan_event;

typedef struct {
    chan_event events[256];
    unsigned count;
    unsigned overflow;
} chan_log;

typedef enum { RUN_DONE, RUN_STOP, RUN_CAP, RUN_FAULT, RUN_SMC } run_result;

enum { MAX_REGIONS = 32, STACK_TOP = 0x3FE00u, DATA_LO = 0x30000u, DATA_HI = 0x3C000u };

static const uint32_t SENTINEL = 0x3FFF0u;
static const uint64_t STEP_CAP = 4000000u;

static pic_region g_regions[MAX_REGIONS];
static unsigned g_region_count;
static const pic_region* g_region;
static chan_log* g_log;
static uint64_t g_aot_fallbacks;
static uint64_t g_aot_fallback_steps;
static uint64_t g_aot_indirect;
static int g_fault;
static uint64_t g_unwound;
static uint32_t g_code_lo, g_code_hi;
static jmp_buf g_escape;
static spu_context g_init, g_interp, g_aot;
static uint32_t g_skew;
static int g_truncate;
static const spu_pic_entry* g_trunc;

int g_spu_ls_watch_n, g_spu_ls_probe, g_wws_read_probe, g_wws_code_probe, g_spu_smc_watch;
SPU_THREAD_LOCAL void (*g_spu_trampoline_fn)(spu_context*) = 0;
SPU_THREAD_LOCAL uint32_t g_spu_pch[8];
SPU_THREAD_LOCAL unsigned g_spu_pch_n;

void spu_pic_register(const spu_pic_entry* entries, unsigned count, const uint8_t* code,
                      uint32_t code_size, int image_id)
{
    (void)image_id;
    if (g_region_count < MAX_REGIONS)
        g_regions[g_region_count++] = (pic_region){entries, count, code, code_size};
}

static uint32_t mix32(uint64_t x)
{
    x ^= x >> 33;
    x *= 0xFF51AFD7ED558CCDull;
    x ^= x >> 33;
    x *= 0xC4CEB9FE1A85EC53ull;
    x ^= x >> 33;
    return (uint32_t)x;
}

static void log_event(uint32_t ch, uint32_t value, uint32_t write)
{
    if (!g_log) return;
    if (g_log->count < 256) g_log->events[g_log->count++] = (chan_event){ch, value, write};
    else g_log->overflow++;
}

u128 spu_rdch(spu_context* c, uint32_t ch)
{
    (void)c;
    uint32_t v = mix32(((uint64_t)ch << 32) | (g_log ? g_log->count : 0)) & 0xFFFFu;
    log_event(ch, v, 0);
    return spu_pref_u32(v);
}

void spu_wrch(spu_context* c, uint32_t ch, u128 v)
{
    (void)c;
    log_event(ch, v._u32[0], 1);
}

uint32_t spu_rchcnt(spu_context* c, uint32_t ch)
{
    (void)c;
    log_event(ch, 1, 2);
    return 1;
}

spu_lifted_fn spu_lifted_lookup(const spu_context* c, uint32_t a)
{
    (void)c;
    (void)a;
    return 0;
}

void spu_spurs_taskset_syscall(spu_context* c)
{
    (void)c;
    g_fault = 1;
    longjmp(g_escape, 1);
}

void spu_ls_watch_slow(uint32_t l, int w, const uint8_t* p, uint32_t pc, uint32_t lr)
{
    (void)l; (void)w; (void)p; (void)pc; (void)lr;
}

void yz_lockstep_tick(spu_context* c) { (void)c; }
void spu_task_launch_check(spu_context* c, void* f) { (void)c; (void)f; }
void spu_depth_guard(spu_context* c) { (void)c; }
int spu_tailret_enabled(void) { return 0; }
void spu_img_restore(spu_context* c, int32_t saved) { c->image_id = saved; }
void spu_check_stack_reset(spu_context* c, void* f) { (void)c; (void)f; }
void spu_halt(spu_context* c) { c->status = SPU_STATUS_STOPPED_BY_HALT; g_fault = 1; longjmp(g_escape, 1); }
void spu_stop(spu_context* c) { (void)c; g_fault = 4; longjmp(g_escape, 1); }

void (*spu_take_interrupt(spu_context* c, void (*tf)(spu_context*)))(spu_context*)
{
    (void)c;
    return tf;
}

static run_result interp_until(spu_context* ctx, uint32_t stop, uint64_t* steps)
{
    ctx->status = SPU_STATUS_RUNNING;
    for (;;) {
        uint32_t pc = ctx->pc & SPU_LS_MASK;
        if (pc == SENTINEL || (stop && pc == stop)) return RUN_DONE;
        if (*steps >= STEP_CAP) return RUN_CAP;
        if (pc >= g_code_lo && pc < g_code_hi && memcmp(ctx->ls + pc, g_init.ls + pc, 4) != 0) return RUN_SMC;
        ++*steps;
        if (spu_step(ctx)) return RUN_STOP;
    }
}

static const spu_pic_entry* find_by_content(const spu_context* ctx, uint32_t pc, uint32_t* base)
{
    for (unsigned i = 0; i < g_region->count; i++) {
        const spu_pic_entry* e = &g_region->entries[i];
        if (!e->func || pc + 16u > SPU_LS_SIZE) continue;
        if (memcmp(ctx->ls + pc, g_region->code + e->addr, 16) != 0) continue;
        if (!spu_pic_entry_matches(ctx->ls, SPU_LS_SIZE, pc, g_region->code, e->addr, e->lo, e->hi,
                                   e->ranges, e->nranges))
            continue;
        *base = (pc - e->addr) & SPU_LS_MASK;
        return e;
    }
    return 0;
}

void spu_indirect_branch(spu_context* ctx)
{
    uint32_t pc = ctx->pc & SPU_LS_MASK & ~3u;
    if (pc == SENTINEL) return;
    uint32_t base;
    const spu_pic_entry* e = find_by_content(ctx, pc, &base);
    if (e) {
        g_aot_indirect++;
        ctx->pic_base = base;
        e->func(ctx);
        return;
    }
    g_aot_fallbacks++;
    uint64_t steps = 0;
    ctx->pc = pc;
    run_result r = interp_until(ctx, ctx->drain_ret_pc, &steps);
    g_aot_fallback_steps += steps;
    if (r != RUN_DONE) {
        g_fault = 2;
        longjmp(g_escape, 1);
    }
}

void spu_indirect_branch_mt(spu_context* ctx) { spu_indirect_branch(ctx); }

void spu_drain_call(spu_context* ctx, uint32_t return_pc)
{
    for (;;) {
        while (g_spu_trampoline_fn) {
            if (g_spu_trampoline_fn == spu_indirect_branch &&
                (ctx->pc & SPU_LS_MASK) == (return_pc & SPU_LS_MASK)) {
                g_spu_trampoline_fn = 0;
                ctx->drain_ret_pc = 0;
                return;
            }
            void (*fn)(spu_context*) = g_spu_trampoline_fn;
            g_spu_trampoline_fn = 0;
            ctx->drain_ret_pc = return_pc & SPU_LS_MASK;
            fn(ctx);
        }
        if ((ctx->pc & SPU_LS_MASK) == (return_pc & SPU_LS_MASK)) {
            ctx->drain_ret_pc = 0;
            return;
        }
        if ((ctx->pc & SPU_LS_MASK) == SENTINEL) {
            g_unwound++;
            longjmp(g_escape, 1);
        }
        g_spu_trampoline_fn = spu_indirect_branch;
    }
}

static void copy_ctx(spu_context* dst, const spu_context* src)
{
    memcpy(dst, src, sizeof *dst);
    dst->ls = dst->ls_store;
}

static uint32_t reg_value(uint64_t seed, unsigned reg, unsigned lane, unsigned style)
{
    uint32_t r = mix32(seed * 131 + reg * 4 + lane);
    switch (style) {
    case 0: return DATA_LO + (r % (DATA_HI - DATA_LO - 0x1000u)) & ~0xFu;
    case 1: return r & 0xFu;
    case 2: return r;
    default: return (r & 1u) ? 0x3F800000u + (r & 0x007FFFF0u) : 0xBF000000u + (r & 0x007FFFF0u);
    }
}

static void init_case(uint64_t seed, uint32_t base)
{
    spu_context_init(&g_init, 0);
    for (uint32_t i = 0; i < SPU_LS_SIZE; i += 4) {
        uint32_t v = mix32(seed ^ ((uint64_t)i << 20));
        uint32_t style = (i >> 4) % 4;
        if (style == 0) v &= 0xFu;
        if (style == 1) v = DATA_LO + (v % (DATA_HI - DATA_LO - 0x1000u)) & ~0xFu;
        if (style == 2) v = 0x3F000000u + (v & 0x00FFFFF0u);
        g_init.ls[i] = v >> 24; g_init.ls[i + 1] = v >> 16; g_init.ls[i + 2] = v >> 8; g_init.ls[i + 3] = v;
    }
    memcpy(g_init.ls + base, g_region->code, g_region->size);
    if (g_trunc && g_trunc->nranges)
        for (uint32_t k = 0; k < g_region->size && base + k < SPU_LS_SIZE; k++) {
            int kept = 0;
            for (uint32_t r = 0; r < g_trunc->nranges && !kept; r++)
                kept = k >= g_trunc->ranges[2 * r] && k < g_trunc->ranges[2 * r + 1];
            if (!kept) g_init.ls[base + k] = (uint8_t)mix32(seed ^ 0xA5A5A5A5ull ^ k);
        }
    for (unsigned r = 0; r < 128; r++) {
        unsigned style = mix32(seed * 7 + r) & 3u;
        for (unsigned l = 0; l < 4; l++) g_init.gpr[r]._u32[l] = reg_value(seed, r, l, style);
    }
    g_init.gpr[0] = spu_link(SENTINEL);
    g_init.gpr[1] = spu_splat_u32(STACK_TOP);
    g_init.gpr[1]._u32[1] = 0x1000u;
    g_init.image_id = -1;
    g_init.pic_base = base;
    g_init.ls[SENTINEL] = 0; g_init.ls[SENTINEL + 1] = 0; g_init.ls[SENTINEL + 2] = 0; g_init.ls[SENTINEL + 3] = 0;
}

static int touched_code(const spu_context* ctx, uint32_t base)
{
    return memcmp(ctx->ls + base, g_init.ls + base, g_region->size) != 0;
}

static int compare(const spu_context* a, const spu_context* b, const chan_log* la, const chan_log* lb,
                   uint32_t entry, uint32_t base, uint64_t seed)
{
    int bad = 0;
    for (unsigned r = 0; r < 128; r++)
        if (memcmp(&a->gpr[r], &b->gpr[r], 16) != 0) {
            if (bad < 4)
                printf("  DIFF entry=0x%05X base=0x%05X seed=%llu r%u interp=%08X%08X%08X%08X aot=%08X%08X%08X%08X\n",
                       entry, base, (unsigned long long)seed, r, a->gpr[r]._u32[0], a->gpr[r]._u32[1],
                       a->gpr[r]._u32[2], a->gpr[r]._u32[3], b->gpr[r]._u32[0], b->gpr[r]._u32[1],
                       b->gpr[r]._u32[2], b->gpr[r]._u32[3]);
            bad++;
        }
    for (uint32_t i = 0; i < SPU_LS_SIZE; i += 16)
        if (memcmp(a->ls + i, b->ls + i, 16) != 0) {
            if (bad < 8)
                printf("  DIFF entry=0x%05X base=0x%05X seed=%llu ls=0x%05X\n", entry, base,
                       (unsigned long long)seed, i);
            bad++;
        }
    if ((a->pc & SPU_LS_MASK) != (b->pc & SPU_LS_MASK)) {
        printf("  DIFF entry=0x%05X base=0x%05X seed=%llu pc interp=0x%05X aot=0x%05X\n", entry, base,
               (unsigned long long)seed, a->pc & SPU_LS_MASK, b->pc & SPU_LS_MASK);
        bad++;
    }
    if (la->count != lb->count || memcmp(la->events, lb->events, la->count * sizeof la->events[0]) != 0) {
        printf("  DIFF entry=0x%05X base=0x%05X seed=%llu channels interp=%u aot=%u\n", entry, base,
               (unsigned long long)seed, la->count, lb->count);
        bad++;
    }
    return bad;
}

static int run_entry(const spu_pic_entry* e, const uint32_t* bases, unsigned nbases, unsigned cases,
                     unsigned* valid_out, unsigned* skipped_out)
{
    unsigned valid = 0, skipped = 0, failed = 0;
    uint64_t steps_total = 0;
    static chan_log li, la;
    g_trunc = g_truncate ? e : 0;
    for (unsigned b = 0; b < nbases; b++) {
        uint32_t base = bases[b];
        if (base + g_region->size > DATA_LO) continue;
        for (unsigned c = 0; c < cases; c++) {
            uint64_t seed = ((uint64_t)e->addr << 16) ^ (c * 0x9E37u + 1);
            init_case(seed, base);
            uint32_t entry_pc = (base + e->addr) & SPU_LS_MASK;
            copy_ctx(&g_interp, &g_init);
            memset(&li, 0, sizeof li);
            g_log = &li;
            g_interp.pc = entry_pc;
            uint64_t steps = 0;
            g_fault = 0;
            g_code_lo = base;
            g_code_hi = base + g_region->size;
            run_result r = RUN_FAULT;
            if (!setjmp(g_escape)) r = interp_until(&g_interp, 0, &steps);
            g_code_lo = 0;
            g_code_hi = 0;
            if (r != RUN_DONE || g_fault || li.overflow) {
                if (getenv("PIC_EQUIV_WHY"))
                    printf("  SKIP entry=0x%05X base=0x%05X seed=%llu result=%d fault=%d code_touched=%d channels=%u overflow=%u pc=0x%05X steps=%llu\n",
                           e->addr, base, (unsigned long long)seed, (int)r, g_fault, touched_code(&g_interp, base),
                           li.count, li.overflow, g_interp.pc & SPU_LS_MASK, (unsigned long long)steps);
                skipped++;
                continue;
            }
            steps_total += steps;
            copy_ctx(&g_aot, &g_init);
            memset(&la, 0, sizeof la);
            g_log = &la;
            g_aot.pc = entry_pc;
            g_aot.pic_base = (base + g_skew) & SPU_LS_MASK;
            g_aot.host_depth = 1;
            g_aot.drain_ret_pc = SENTINEL;
            g_spu_trampoline_fn = 0;
            g_fault = 0;
            if (!setjmp(g_escape)) {
                e->func(&g_aot);
                spu_drain_call(&g_aot, SENTINEL);
            }
            g_log = 0;
            g_aot.host_depth = g_interp.host_depth;
            if (g_fault) {
                printf("  FAULT entry=0x%05X base=0x%05X seed=%llu code=%d\n", e->addr, base,
                       (unsigned long long)seed, g_fault);
                failed++;
                continue;
            }
            valid++;
            if (compare(&g_interp, &g_aot, &li, &la, e->addr, base, seed)) failed++;
        }
    }
    *valid_out = valid;
    *skipped_out = skipped;
    printf("entry 0x%05X valid=%u skipped=%u failed=%u interp_steps=%llu aot_fallbacks=%llu aot_fallback_steps=%llu indirect=%llu unwound=%llu\n",
           e->addr, valid, skipped, failed, (unsigned long long)steps_total,
           (unsigned long long)g_aot_fallbacks, (unsigned long long)g_aot_fallback_steps,
           (unsigned long long)g_aot_indirect, (unsigned long long)g_unwound);
    g_aot_fallbacks = g_aot_fallback_steps = g_aot_indirect = g_unwound = 0;
    return failed;
}

static int run_snapshot(const char* path, uint32_t addr)
{
    static spu_context snap;
    FILE* f = fopen(path, "rb");
    if (!f) {
        printf("SNAP %s unreadable\n", path);
        return 2;
    }
    size_t got = fread(&snap, 1, sizeof snap, f);
    fclose(f);
    if (got != sizeof snap) {
        printf("SNAP %s size %zu != %zu\n", path, got, sizeof snap);
        return 2;
    }
    const spu_pic_entry* e = 0;
    for (unsigned i = 0; i < g_region->count && !e; i++)
        if (g_region->entries[i].addr == addr) e = &g_region->entries[i];
    if (!e) {
        printf("SNAP entry 0x%05X not in region\n", addr);
        return 2;
    }
    uint32_t base = snap.pic_base & SPU_LS_MASK;
    spu_context_init(&g_init, 0);
    memcpy(g_init.gpr, snap.gpr, sizeof snap.gpr);
    memcpy(g_init.ls_store, snap.ls_store, sizeof snap.ls_store);
    g_init.pic_base = base;
    g_init.image_id = -1;
    if (getenv("PIC_EQUIV_REBASE")) {
        uint32_t lo = base + e->lo >= 0x400u ? base + e->lo - 0x400u : 0u;
        uint32_t hi = base + e->hi + 0x400u <= SPU_LS_SIZE ? base + e->hi + 0x400u : SPU_LS_SIZE;
        uint32_t moved = (base + (uint32_t)strtol(getenv("PIC_EQUIV_REBASE"), 0, 0)) & SPU_LS_MASK & ~0xFu;
        uint32_t dst = moved + (lo - base);
        if ((int64_t)moved + (int64_t)lo - (int64_t)base < 0 || dst + (hi - lo) > SPU_LS_SIZE) {
            printf("SNAP %s rebase 0x%05X out of range\n", path, moved);
            return 2;
        }
        memmove(g_init.ls + dst, snap.ls_store + lo, hi - lo);
        base = moved;
        g_init.pic_base = base;
    }
    uint32_t ret = g_init.gpr[0]._u32[0] & SPU_LS_MASK;
    int closure_ok = memcmp(g_init.ls + base + e->lo, g_region->code + e->lo, e->hi - e->lo) == 0;
    g_init.gpr[0]._u32[0] = SENTINEL;
    g_init.ls[SENTINEL] = 0; g_init.ls[SENTINEL + 1] = 0; g_init.ls[SENTINEL + 2] = 0; g_init.ls[SENTINEL + 3] = 0;
    static chan_log li, la;
    uint32_t entry_pc = (base + e->addr) & SPU_LS_MASK;
    copy_ctx(&g_interp, &g_init);
    memset(&li, 0, sizeof li);
    g_log = &li;
    g_interp.pc = entry_pc;
    uint64_t steps = 0;
    g_fault = 0;
    g_code_lo = base + e->lo;
    g_code_hi = base + e->hi;
    run_result r = RUN_FAULT;
    if (!setjmp(g_escape)) r = interp_until(&g_interp, 0, &steps);
    g_code_lo = g_code_hi = 0;
    if (r != RUN_DONE || g_fault) {
        printf("SNAP %s entry=0x%05X base=0x%05X link=0x%05X closure_ok=%d interp_result=%d fault=%d steps=%llu pc=0x%05X\n",
               path, addr, base, ret, closure_ok, (int)r, g_fault, (unsigned long long)steps, g_interp.pc & SPU_LS_MASK);
        return 3;
    }
    copy_ctx(&g_aot, &g_init);
    memset(&la, 0, sizeof la);
    g_log = &la;
    g_aot.pc = entry_pc;
    if (getenv("PIC_EQUIV_SKEW")) g_aot.pic_base = (base + (uint32_t)strtoul(getenv("PIC_EQUIV_SKEW"), 0, 0)) & SPU_LS_MASK;
    g_aot.host_depth = 1;
    g_aot.drain_ret_pc = SENTINEL;
    g_spu_trampoline_fn = 0;
    g_fault = 0;
    if (!setjmp(g_escape)) {
        e->func(&g_aot);
        spu_drain_call(&g_aot, SENTINEL);
    }
    g_log = 0;
    g_aot.host_depth = g_interp.host_depth;
    int bad = g_fault ? 1 : compare(&g_interp, &g_aot, &li, &la, e->addr, base, 0);
    printf("SNAP %s entry=0x%05X base=0x%05X link=0x%05X closure_ok=%d steps=%llu channels=%u aot_fallbacks=%llu %s\n",
           path, addr, base, ret, closure_ok, (unsigned long long)steps, li.count,
           (unsigned long long)g_aot_fallbacks, bad ? "FAIL" : "PASS");
    g_aot_fallbacks = g_aot_fallback_steps = g_aot_indirect = g_unwound = 0;
    return bad ? 1 : 0;
}

static int run_child(const spu_pic_entry* e, const uint32_t* bases, unsigned nbases, unsigned cases)
{
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
        alarm(60);
        unsigned v, s;
        int failed = run_entry(e, bases, nbases, cases, &v, &s);
        fflush(stdout);
        _exit(failed ? 1 : (v ? 0 : 3));
    }
    int status = 0;
    waitpid(pid, &status, 0);
    if (WIFSIGNALED(status)) {
        printf("entry 0x%05X CRASH signal=%d\n", e->addr, WTERMSIG(status));
        return 2;
    }
    return WEXITSTATUS(status);
}

int main(int argc, char** argv)
{
    if (argc >= 5 && strcmp(argv[1], "snap") == 0) {
        if (!g_region_count) return 2;
        g_region = &g_regions[0];
        int worst = 0;
        for (int a = 3; a < argc; a++) {
            int r = run_snapshot(argv[a], (uint32_t)strtoul(argv[2], 0, 0));
            if (r == 1 || (r > worst && worst != 1)) worst = r;
        }
        return worst;
    }
    if (argc < 4) {
        fprintf(stderr, "usage: %s <region index> <cases> <base,...> [entry ...] | snap <entry> <dump ...>\n", argv[0]);
        return 2;
    }
    unsigned ri = (unsigned)strtoul(argv[1], 0, 0);
    unsigned cases = (unsigned)strtoul(argv[2], 0, 0);
    if (ri >= g_region_count) {
        fprintf(stderr, "region %u not registered (%u regions)\n", ri, g_region_count);
        return 2;
    }
    g_region = &g_regions[ri];
    if (getenv("PIC_EQUIV_SKEW")) g_skew = (uint32_t)strtoul(getenv("PIC_EQUIV_SKEW"), 0, 0);
    g_truncate = getenv("PIC_EQUIV_TRUNCATE") != NULL;
    uint32_t bases[16];
    unsigned nbases = 0;
    for (char* tok = strtok(argv[3], ","); tok && nbases < 16; tok = strtok(0, ","))
        bases[nbases++] = (uint32_t)strtoul(tok, 0, 0) & ~0xFu;
    unsigned pass = 0, fail = 0, novalid = 0, crash = 0;
    for (unsigned i = 0; i < g_region->count; i++) {
        const spu_pic_entry* e = &g_region->entries[i];
        int wanted = argc == 4;
        for (int a = 4; a < argc && !wanted; a++)
            wanted = (uint32_t)strtoul(argv[a], 0, 0) == e->addr;
        if (!wanted) continue;
        int r = run_child(e, bases, nbases, cases);
        if (r == 0) pass++;
        else if (r == 3) novalid++;
        else if (r == 2) crash++;
        else fail++;
    }
    printf("SUMMARY region=%u entries_pass=%u entries_fail=%u entries_without_valid_case=%u entries_crash=%u\n",
           ri, pass, fail, novalid, crash);
    return (fail || crash) ? 1 : 0;
}
