#include "overlay.h"
#include "game_ini.h"
#include "rsx_vulkan_overlay.h"
#include "imgui.h"
#include "backends/imgui_impl_vulkan.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <mutex>
#include <string>
#include <sys/stat.h>

extern "C" unsigned long long cellSpurs_kernel_busy_ns(unsigned wid);

namespace {
struct VulkanFns {
    PFN_vkCreateRenderPass create_render_pass;
    PFN_vkDestroyRenderPass destroy_render_pass;
    PFN_vkCreateImageView create_image_view;
    PFN_vkDestroyImageView destroy_image_view;
    PFN_vkCreateFramebuffer create_framebuffer;
    PFN_vkDestroyFramebuffer destroy_framebuffer;
    PFN_vkCmdBeginRenderPass cmd_begin_render_pass;
    PFN_vkCmdEndRenderPass cmd_end_render_pass;
    PFN_vkGetPhysicalDeviceProperties get_physical_device_properties;
};

struct Renderer {
    rsx_vk_overlay_context context{};
    VulkanFns vk{};
    bool ready = false;
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkExtent2D extent{};
    VkPresentModeKHR present_mode = VK_PRESENT_MODE_FIFO_KHR;
    uint32_t image_count = 0;
    std::array<VkImageView, 8> views{};
    std::array<VkFramebuffer, 8> framebuffers{};
    VkPhysicalDeviceProperties properties{};
    uint64_t last_frame_ns = 0;
};

struct Stats {
    float fps = 0;
    float frame_avg_ms = 0;
    float frame_worst_ms = 0;
    float present_fps = 0;
    float spu_busy = -1;
    std::array<float, 120> graph{};
    int graph_count = 0;
    uint64_t present_sample_ns = 0;
    uint32_t present_sample_count = 0;
    uint64_t spu_sample_ns = 0;
    unsigned long long spu_sample_busy = 0;
};

constexpr size_t flip_ring_size = 512;
constexpr unsigned edge_workloads[] = {13, 14, 15};
constexpr const char* font_paths[] = {
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
};
constexpr float base_font_px = 16.0f;

std::array<std::atomic<uint64_t>, flip_ring_size> flip_ns;
std::atomic<uint32_t> flip_total{0};
std::atomic<uint32_t> present_total{0};
std::atomic<bool> overlay_visible{false};
std::atomic<bool> started{false};
std::mutex settings_mutex;
gowa::GameIni game_ini;
Renderer renderer;
Stats stats;

uint64_t now_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

std::string decimal(float value, int digits) {
    char text[32];
    snprintf(text, sizeof text, "%.*f", digits, value);
    for (char* p = text; *p; ++p)
        if (*p == '.') *p = ',';
    return text;
}

gowa::GameSettings settings_snapshot() {
    std::lock_guard<std::mutex> lock(settings_mutex);
    return game_ini.settings;
}

void save_settings() {
    std::lock_guard<std::mutex> lock(settings_mutex);
    gowa::save_game_ini(game_ini);
}

PFN_vkVoidFunction load_vulkan(const char* name, void* user) {
    const auto* ctx = static_cast<const rsx_vk_overlay_context*>(user);
    return ctx->get_instance_proc_addr(ctx->instance, name);
}

template <typename T>
void load(T& fn, const char* name) {
    fn = reinterpret_cast<T>(renderer.context.get_instance_proc_addr(renderer.context.instance, name));
}

bool load_functions() {
    VulkanFns& f = renderer.vk;
    load(f.create_render_pass, "vkCreateRenderPass");
    load(f.destroy_render_pass, "vkDestroyRenderPass");
    load(f.create_image_view, "vkCreateImageView");
    load(f.destroy_image_view, "vkDestroyImageView");
    load(f.create_framebuffer, "vkCreateFramebuffer");
    load(f.destroy_framebuffer, "vkDestroyFramebuffer");
    load(f.cmd_begin_render_pass, "vkCmdBeginRenderPass");
    load(f.cmd_end_render_pass, "vkCmdEndRenderPass");
    load(f.get_physical_device_properties, "vkGetPhysicalDeviceProperties");
    return f.create_render_pass && f.destroy_render_pass && f.create_image_view &&
           f.destroy_image_view && f.create_framebuffer && f.destroy_framebuffer &&
           f.cmd_begin_render_pass && f.cmd_end_render_pass && f.get_physical_device_properties;
}

bool create_render_pass(VkFormat format) {
    VkAttachmentDescription color{};
    color.format = format;
    color.samples = VK_SAMPLE_COUNT_1_BIT;
    color.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
    color.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    color.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    color.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    color.initialLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    color.finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    VkAttachmentReference ref{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription subpass{};
    subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    subpass.colorAttachmentCount = 1;
    subpass.pColorAttachments = &ref;
    VkSubpassDependency deps[2]{};
    deps[0].srcSubpass = VK_SUBPASS_EXTERNAL;
    deps[0].dstSubpass = 0;
    deps[0].srcStageMask = VK_PIPELINE_STAGE_TRANSFER_BIT;
    deps[0].dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    deps[0].srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    deps[0].dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    deps[1].srcSubpass = 0;
    deps[1].dstSubpass = VK_SUBPASS_EXTERNAL;
    deps[1].srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    deps[1].dstStageMask = VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT;
    deps[1].srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    VkRenderPassCreateInfo info{};
    info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO;
    info.attachmentCount = 1;
    info.pAttachments = &color;
    info.subpassCount = 1;
    info.pSubpasses = &subpass;
    info.dependencyCount = 2;
    info.pDependencies = deps;
    return renderer.vk.create_render_pass(renderer.context.device, &info, nullptr,
                                          &renderer.render_pass) == VK_SUCCESS;
}

void destroy_targets() {
    for (uint32_t i = 0; i < renderer.framebuffers.size(); ++i) {
        if (renderer.framebuffers[i])
            renderer.vk.destroy_framebuffer(renderer.context.device, renderer.framebuffers[i], nullptr);
        if (renderer.views[i])
            renderer.vk.destroy_image_view(renderer.context.device, renderer.views[i], nullptr);
        renderer.framebuffers[i] = VK_NULL_HANDLE;
        renderer.views[i] = VK_NULL_HANDLE;
    }
    renderer.image_count = 0;
}

bool create_targets(const rsx_vk_overlay_swapchain* sc) {
    renderer.image_count = std::min<uint32_t>(sc->image_count, renderer.views.size());
    for (uint32_t i = 0; i < renderer.image_count; ++i) {
        VkImageViewCreateInfo view{};
        view.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
        view.image = sc->images[i];
        view.viewType = VK_IMAGE_VIEW_TYPE_2D;
        view.format = sc->format;
        view.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        if (renderer.vk.create_image_view(renderer.context.device, &view, nullptr, &renderer.views[i]) != VK_SUCCESS)
            return false;
        VkFramebufferCreateInfo fb{};
        fb.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
        fb.renderPass = renderer.render_pass;
        fb.attachmentCount = 1;
        fb.pAttachments = &renderer.views[i];
        fb.width = sc->extent.width;
        fb.height = sc->extent.height;
        fb.layers = 1;
        if (renderer.vk.create_framebuffer(renderer.context.device, &fb, nullptr, &renderer.framebuffers[i]) != VK_SUCCESS)
            return false;
    }
    return true;
}

bool init_imgui_vulkan(const rsx_vk_overlay_swapchain* sc) {
    if (!ImGui_ImplVulkan_LoadFunctions(renderer.context.api_version, load_vulkan, &renderer.context))
        return false;
    ImGui_ImplVulkan_InitInfo info{};
    info.ApiVersion = renderer.context.api_version;
    info.Instance = renderer.context.instance;
    info.PhysicalDevice = renderer.context.physical_device;
    info.Device = renderer.context.device;
    info.QueueFamily = renderer.context.queue_family;
    info.Queue = renderer.context.queue;
    info.DescriptorPoolSize = 16;
    info.MinImageCount = std::max<uint32_t>(2, sc->min_image_count);
    info.ImageCount = std::max(info.MinImageCount, sc->image_count);
    info.PipelineInfoMain.RenderPass = renderer.render_pass;
    info.PipelineInfoMain.MSAASamples = VK_SAMPLE_COUNT_1_BIT;
    return ImGui_ImplVulkan_Init(&info);
}

void on_swapchain_lost(void*) {
    if (renderer.ready) destroy_targets();
}

void on_swapchain_ready(const rsx_vk_overlay_context* ctx, const rsx_vk_overlay_swapchain* sc, void*) {
    if (renderer.ready && sc->format != renderer.format) {
        destroy_targets();
        ImGui_ImplVulkan_Shutdown();
        renderer.vk.destroy_render_pass(renderer.context.device, renderer.render_pass, nullptr);
        renderer.render_pass = VK_NULL_HANDLE;
        renderer.ready = false;
    }
    if (!renderer.ready) {
        renderer.context = *ctx;
        if (!load_functions() || !create_render_pass(sc->format) || !init_imgui_vulkan(sc)) {
            fprintf(stderr, "[overlay] Vulkan setup failed; overlay disabled\n");
            return;
        }
        renderer.vk.get_physical_device_properties(ctx->physical_device, &renderer.properties);
        renderer.format = sc->format;
        renderer.ready = true;
        fprintf(stderr, "[overlay] ready on %s\n", ctx->device_name ? ctx->device_name : "?");
    }
    renderer.extent = sc->extent;
    renderer.present_mode = sc->present_mode;
    if (!create_targets(sc)) {
        destroy_targets();
        fprintf(stderr, "[overlay] swapchain targets failed\n");
    }
}

void update_stats(uint64_t now) {
    const uint32_t total = flip_total.load(std::memory_order_acquire);
    const uint32_t n = std::min<uint32_t>(total, flip_ring_size - 8);
    auto flip_at = [&](uint32_t k) { return flip_ns[(total - 1 - k) % flip_ring_size].load(std::memory_order_relaxed); };
    auto age = [&](uint64_t t) { return int64_t(now) - int64_t(t); };
    int in_window = 0, intervals = 0;
    double sum_ms = 0, worst_ms = 0;
    for (uint32_t k = 0; k < n && age(flip_at(k)) <= 1000000000ll; ++k) {
        ++in_window;
        if (k + 1 < n && age(flip_at(k + 1)) <= 1000000000ll) {
            const double ms = double(flip_at(k) - flip_at(k + 1)) / 1e6;
            sum_ms += ms;
            worst_ms = std::max(worst_ms, ms);
            ++intervals;
        }
    }
    stats.fps = float(in_window);
    stats.frame_avg_ms = intervals ? float(sum_ms / intervals) : 0.0f;
    stats.frame_worst_ms = float(worst_ms);
    const int graph_count = int(std::min<uint32_t>(n > 0 ? n - 1 : 0, stats.graph.size()));
    for (int i = 0; i < graph_count; ++i) {
        const uint32_t k = uint32_t(graph_count - 1 - i);
        stats.graph[i] = float(double(flip_at(k) - flip_at(k + 1)) / 1e6);
    }
    stats.graph_count = graph_count;

    const uint32_t presents = present_total.load(std::memory_order_relaxed);
    if (!stats.present_sample_ns) {
        stats.present_sample_ns = now;
        stats.present_sample_count = presents;
    } else if (now - stats.present_sample_ns >= 500000000ull) {
        stats.present_fps = float((presents - stats.present_sample_count) * 1e9 / double(now - stats.present_sample_ns));
        stats.present_sample_ns = now;
        stats.present_sample_count = presents;
    }

    unsigned long long busy = 0;
    for (unsigned wid : edge_workloads) busy += cellSpurs_kernel_busy_ns(wid);
    if (!stats.spu_sample_ns) {
        stats.spu_sample_ns = now;
        stats.spu_sample_busy = busy;
    } else if (now - stats.spu_sample_ns >= 1000000000ull) {
        stats.spu_busy = float(double(busy - stats.spu_sample_busy) * 100.0 / double(now - stats.spu_sample_ns));
        stats.spu_sample_ns = now;
        stats.spu_sample_busy = busy;
    }
}

float display_scale() {
    return std::max(1.0f, float(renderer.extent.height) / 720.0f);
}

void draw_overlay(const gowa::GameSettings& s) {
    const float scale = float(s.overlay_scale) / 100.0f;
    const float pad = 10.0f * display_scale();
    const ImVec2 size = ImGui::GetIO().DisplaySize;
    const bool right = s.corner == gowa::Corner::top_right || s.corner == gowa::Corner::bottom_right;
    const bool bottom = s.corner == gowa::Corner::bottom_left || s.corner == gowa::Corner::bottom_right;
    ImGui::SetNextWindowPos(ImVec2(right ? size.x - pad : pad, bottom ? size.y - pad : pad),
                            ImGuiCond_Always, ImVec2(right ? 1.0f : 0.0f, bottom ? 1.0f : 0.0f));
    ImGui::SetNextWindowBgAlpha(float(s.overlay_opacity) / 100.0f);
    const ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
        ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoNav |
        ImGuiWindowFlags_NoInputs;
    ImGui::PushFont(nullptr, base_font_px * scale * display_scale());
    if (ImGui::Begin("##gowa_overlay", nullptr, flags)) {
        if (s.overlay_items & gowa::item_fps) {
            ImGui::TextColored(stats.fps >= 29.0f ? ImVec4(0.45f, 1.0f, 0.45f, 1.0f)
                               : stats.fps >= 20.0f ? ImVec4(1.0f, 0.85f, 0.3f, 1.0f)
                                                    : ImVec4(1.0f, 0.4f, 0.35f, 1.0f),
                               "%s FPS", decimal(stats.fps, 0).c_str());
        }
        if (s.overlay_items & gowa::item_frametime) {
            if (stats.frame_avg_ms > 0)
                ImGui::Text("Quadro %s ms (pior %s)", decimal(stats.frame_avg_ms, 1).c_str(),
                            decimal(stats.frame_worst_ms, 1).c_str());
            else
                ImGui::TextUnformatted("Quadro -- ms");
        }
        if ((s.overlay_items & gowa::item_graph) && stats.graph_count > 1) {
            float top = 50.0f;
            for (int i = 0; i < stats.graph_count; ++i) top = std::max(top, stats.graph[i] * 1.1f);
            ImGui::PlotLines("##frametime", stats.graph.data(), stats.graph_count, 0, nullptr, 0.0f, top,
                             ImVec2(170.0f * scale * display_scale(), 38.0f * scale * display_scale()));
        }
        if (s.overlay_items & gowa::item_present)
            ImGui::Text("Apresentação %s FPS", decimal(stats.present_fps, 0).c_str());
        if (s.overlay_items & gowa::item_spu) {
            if (stats.spu_busy >= 0)
                ImGui::Text("EDGE (SPU) %s%%", decimal(stats.spu_busy, 0).c_str());
            else
                ImGui::TextUnformatted("EDGE (SPU) --");
        }
    }
    ImGui::End();
    ImGui::PopFont();
}

int on_record(VkCommandBuffer cmd, uint32_t image_index, void*) {
    present_total.fetch_add(1, std::memory_order_relaxed);
    if (!overlay_visible.load(std::memory_order_relaxed)) {
        renderer.last_frame_ns = 0;
        return 0;
    }
    if (!renderer.ready || image_index >= renderer.image_count || !renderer.framebuffers[image_index])
        return 0;
    const uint64_t now = now_ns();
    const gowa::GameSettings s = settings_snapshot();
    update_stats(now);
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(float(renderer.extent.width), float(renderer.extent.height));
    io.DeltaTime = renderer.last_frame_ns ? std::clamp(float(double(now - renderer.last_frame_ns) / 1e9), 1e-4f, 0.5f)
                                          : 1.0f / 60.0f;
    renderer.last_frame_ns = now;
    ImGui_ImplVulkan_NewFrame();
    ImGui::NewFrame();
    draw_overlay(s);
    ImGui::Render();
    VkRenderPassBeginInfo begin{};
    begin.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO;
    begin.renderPass = renderer.render_pass;
    begin.framebuffer = renderer.framebuffers[image_index];
    begin.renderArea.extent = renderer.extent;
    renderer.vk.cmd_begin_render_pass(cmd, &begin, VK_SUBPASS_CONTENTS_INLINE);
    ImGui_ImplVulkan_RenderDrawData(ImGui::GetDrawData(), cmd);
    renderer.vk.cmd_end_render_pass(cmd);
    return 1;
}

int on_event(const SDL_Event* e, void*) {
    if (e->type != SDL_KEYDOWN || e->key.repeat) return 0;
    if (e->key.keysym.sym == SDLK_F10) {
        bool show;
        {
            std::lock_guard<std::mutex> lock(settings_mutex);
            show = game_ini.settings.show_fps = !game_ini.settings.show_fps;
        }
        overlay_visible.store(show, std::memory_order_relaxed);
        save_settings();
        return 1;
    }
    return 0;
}

void setup_imgui() {
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    io.BackendPlatformName = "gowa-pc";
    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.WindowRounding = 6.0f;
    style.WindowBorderSize = 0.0f;
    for (const char* path : font_paths) {
        struct stat st{};
        if (stat(path, &st) == 0 && io.Fonts->AddFontFromFileTTF(path, base_font_px)) return;
    }
    io.Fonts->AddFontDefault();
}
}

void gowa_overlay_note_flip() {
    const uint32_t i = flip_total.load(std::memory_order_relaxed);
    flip_ns[i % flip_ring_size].store(now_ns(), std::memory_order_relaxed);
    flip_total.store(i + 1, std::memory_order_release);
}

void gowa_overlay_start() {
    {
        std::lock_guard<std::mutex> lock(settings_mutex);
        game_ini = gowa::load_game_ini(gowa::game_ini_path());
        overlay_visible.store(game_ini.settings.show_fps);
        fprintf(stderr, "[overlay] settings %s (%s)\n", game_ini.path.c_str(),
                game_ini.existed ? "loaded" : "defaults");
    }
    setup_imgui();
    rsx_vk_overlay_hooks hooks{};
    hooks.swapchain_ready = on_swapchain_ready;
    hooks.swapchain_lost = on_swapchain_lost;
    hooks.record = on_record;
    hooks.event = on_event;
    if (rsx_vulkan_overlay_register(&hooks) != 0) {
        fprintf(stderr, "[overlay] no window swapchain; overlay disabled\n");
        return;
    }
    started.store(true);
}

void gowa_overlay_tick() {}
