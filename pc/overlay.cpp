#include "overlay.h"
#include "game_ini.h"
#include "input.h"
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
#include <vector>

extern "C" unsigned long long cellSpurs_kernel_busy_ns(unsigned wid);
extern "C" uint64_t ps3recomp_vdec_now_ns(void);
extern "C" uint32_t ps3recomp_vdec_pictures_in_last_second(uint64_t now_ns);
extern "C" uint64_t ps3recomp_vdec_last_picture_ns(void);

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
    rsx_vk_shader_stats shaders{};
    float shader_worst_ms = 0;
    float shader_window_ms = 0;
    uint64_t shader_sample_ns = 0;
    uint32_t video_fps = 0;
    bool video_playing = false;
};

struct PadKey {
    SDL_GameControllerButton button;
    ImGuiKey key;
};

enum WindowRequest : unsigned { request_display = 1u << 0, request_vsync = 1u << 1 };

constexpr PadKey pad_keys[] = {
    {SDL_CONTROLLER_BUTTON_A, ImGuiKey_GamepadFaceDown},
    {SDL_CONTROLLER_BUTTON_B, ImGuiKey_GamepadFaceRight},
    {SDL_CONTROLLER_BUTTON_X, ImGuiKey_GamepadFaceLeft},
    {SDL_CONTROLLER_BUTTON_Y, ImGuiKey_GamepadFaceUp},
    {SDL_CONTROLLER_BUTTON_DPAD_UP, ImGuiKey_GamepadDpadUp},
    {SDL_CONTROLLER_BUTTON_DPAD_DOWN, ImGuiKey_GamepadDpadDown},
    {SDL_CONTROLLER_BUTTON_DPAD_LEFT, ImGuiKey_GamepadDpadLeft},
    {SDL_CONTROLLER_BUTTON_DPAD_RIGHT, ImGuiKey_GamepadDpadRight},
    {SDL_CONTROLLER_BUTTON_LEFTSHOULDER, ImGuiKey_GamepadL1},
    {SDL_CONTROLLER_BUTTON_RIGHTSHOULDER, ImGuiKey_GamepadR1},
    {SDL_CONTROLLER_BUTTON_START, ImGuiKey_GamepadStart},
    {SDL_CONTROLLER_BUTTON_BACK, ImGuiKey_GamepadBack},
};
constexpr const char* corner_labels[] = {"Superior esquerdo", "Superior direito", "Inferior esquerdo",
                                         "Inferior direito"};
constexpr const char* mode_labels[] = {"Janela", "Janela sem bordas", "Tela cheia exclusiva"};
constexpr const char* size_labels[] = {"1280x720", "1600x900", "1920x1080", "Monitor"};
constexpr int size_presets[][2] = {{1280, 720}, {1600, 900}, {1920, 1080}};
constexpr const char* item_labels[] = {"FPS do jogo", "Tempo de quadro (média e pior)",
                                       "Gráfico do tempo de quadro", "FPS de apresentação",
                                       "Ocupação do SPU (EDGE)", "Compilação de shaders",
                                       "Vídeo (quadros do filme por segundo)"};
constexpr const char* button_labels[] = {"Select", "L3", "R3", "Start", "Direcional ↑", "Direcional →",
                                         "Direcional ↓", "Direcional ←", "L2", "R2", "L1", "R1",
                                         "Triângulo", "Círculo", "Cross (X)", "Quadrado"};
constexpr size_t flip_ring_size = 512;
constexpr unsigned edge_workloads[] = {13, 14, 15};
constexpr const char* font_paths[] = {
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
};
constexpr float base_font_px = 16.0f;
constexpr uint64_t video_idle_ns = 500000000ull;

std::array<std::atomic<uint64_t>, flip_ring_size> flip_ns;
std::atomic<uint32_t> flip_total{0};
std::atomic<uint32_t> present_total{0};
std::atomic<bool> overlay_visible{false};
std::atomic<bool> started{false};
std::atomic<bool> menu_wanted{false};
std::atomic<unsigned> window_requests{0};
std::atomic<int> window_width{1280};
std::atomic<int> window_height{720};
bool menu_applied = false;
bool menu_drawn = false;
bool combo_held = false;
int menu_tab = 0;
std::mutex input_mutex;
std::vector<SDL_Event> queued_events;
gowa::PadState queued_pad;
std::array<bool, std::size(pad_keys)> pad_down{};
std::vector<gowa::BindingInfo> bindings;
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

void update_shader_stats(uint64_t now) {
    rsx_vulkan_backend_shader_stats(&stats.shaders);
    stats.shader_window_ms = std::max(stats.shader_window_ms, float(stats.shaders.frame_ms));
    if (now - stats.shader_sample_ns >= 1000000000ull) {
        stats.shader_worst_ms = stats.shader_window_ms;
        stats.shader_window_ms = 0;
        stats.shader_sample_ns = now;
    }
}

void draw_shader_note() {
    if (stats.shaders.pending)
        ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.3f, 1.0f), "Compilando shaders: %u", stats.shaders.pending);
}

void update_video_stats() {
    const uint64_t now = ps3recomp_vdec_now_ns();
    const uint64_t last = ps3recomp_vdec_last_picture_ns();
    stats.video_playing = last && now - last <= video_idle_ns;
    stats.video_fps = stats.video_playing ? ps3recomp_vdec_pictures_in_last_second(now) : 0;
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
    update_shader_stats(now);
    update_video_stats();
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
        if (s.overlay_items & gowa::item_shaders)
            ImGui::Text("Shaders %s ms (%u pipelines)", decimal(stats.shader_worst_ms, 1).c_str(),
                        stats.shaders.pipelines);
        if ((s.overlay_items & gowa::item_video) && stats.video_playing)
            ImGui::Text("Vídeo: %u fps", stats.video_fps);
        draw_shader_note();
    }
    ImGui::End();
    ImGui::PopFont();
}

void draw_compiling_note(const gowa::GameSettings& s) {
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
    ImGui::PushFont(nullptr, base_font_px * display_scale());
    if (ImGui::Begin("##gowa_shader_note", nullptr, flags)) draw_shader_note();
    ImGui::End();
    ImGui::PopFont();
}

ImGuiKey imgui_key(SDL_Keycode key) {
    switch (key) {
    case SDLK_TAB: return ImGuiKey_Tab;
    case SDLK_LEFT: return ImGuiKey_LeftArrow;
    case SDLK_RIGHT: return ImGuiKey_RightArrow;
    case SDLK_UP: return ImGuiKey_UpArrow;
    case SDLK_DOWN: return ImGuiKey_DownArrow;
    case SDLK_PAGEUP: return ImGuiKey_PageUp;
    case SDLK_PAGEDOWN: return ImGuiKey_PageDown;
    case SDLK_HOME: return ImGuiKey_Home;
    case SDLK_END: return ImGuiKey_End;
    case SDLK_SPACE: return ImGuiKey_Space;
    case SDLK_RETURN: return ImGuiKey_Enter;
    case SDLK_KP_ENTER: return ImGuiKey_KeypadEnter;
    case SDLK_BACKSPACE: return ImGuiKey_Backspace;
    case SDLK_LCTRL: return ImGuiKey_LeftCtrl;
    case SDLK_RCTRL: return ImGuiKey_RightCtrl;
    case SDLK_LSHIFT: return ImGuiKey_LeftShift;
    case SDLK_RSHIFT: return ImGuiKey_RightShift;
    case SDLK_LALT: return ImGuiKey_LeftAlt;
    case SDLK_q: return ImGuiKey_Q;
    case SDLK_e: return ImGuiKey_E;
    case SDLK_RALT: return ImGuiKey_RightAlt;
    default: return ImGuiKey_None;
    }
}

void feed_input() {
    std::vector<SDL_Event> events;
    gowa::PadState pad;
    {
        std::lock_guard<std::mutex> lock(input_mutex);
        events.swap(queued_events);
        pad = queued_pad;
    }
    ImGuiIO& io = ImGui::GetIO();
    const float sx = window_width.load() > 0 ? float(renderer.extent.width) / float(window_width.load()) : 1.0f;
    const float sy = window_height.load() > 0 ? float(renderer.extent.height) / float(window_height.load()) : 1.0f;
    for (const SDL_Event& e : events) {
        switch (e.type) {
        case SDL_MOUSEMOTION:
            io.AddMouseSourceEvent(ImGuiMouseSource_Mouse);
            io.AddMousePosEvent(float(e.motion.x) * sx, float(e.motion.y) * sy);
            break;
        case SDL_MOUSEBUTTONDOWN:
        case SDL_MOUSEBUTTONUP: {
            const int button = e.button.button == SDL_BUTTON_LEFT ? 0
                : e.button.button == SDL_BUTTON_RIGHT ? 1 : e.button.button == SDL_BUTTON_MIDDLE ? 2 : -1;
            io.AddMousePosEvent(float(e.button.x) * sx, float(e.button.y) * sy);
            if (button >= 0) io.AddMouseButtonEvent(button, e.type == SDL_MOUSEBUTTONDOWN);
            break;
        }
        case SDL_MOUSEWHEEL:
            io.AddMouseWheelEvent(float(e.wheel.x), float(e.wheel.y));
            break;
        case SDL_KEYDOWN:
        case SDL_KEYUP: {
            const SDL_Keymod mod = SDL_Keymod(e.key.keysym.mod);
            io.AddKeyEvent(ImGuiMod_Ctrl, mod & KMOD_CTRL);
            io.AddKeyEvent(ImGuiMod_Shift, mod & KMOD_SHIFT);
            io.AddKeyEvent(ImGuiMod_Alt, mod & KMOD_ALT);
            const ImGuiKey key = imgui_key(e.key.keysym.sym);
            if (key != ImGuiKey_None) io.AddKeyEvent(key, e.type == SDL_KEYDOWN);
            break;
        }
        default:
            break;
        }
    }
    for (size_t i = 0; i < std::size(pad_keys); ++i) {
        const bool down = pad.connected && pad.buttons[pad_keys[i].button];
        if (down != pad_down[i]) io.AddKeyEvent(pad_keys[i].key, down);
        pad_down[i] = down;
    }
    const float lx = pad.connected ? float(pad.axes[SDL_CONTROLLER_AXIS_LEFTX]) / 32767.0f : 0.0f;
    const float ly = pad.connected ? float(pad.axes[SDL_CONTROLLER_AXIS_LEFTY]) / 32767.0f : 0.0f;
    auto stick = [&](ImGuiKey key, float v) {
        const float a = std::clamp((v - 0.25f) / 0.75f, 0.0f, 1.0f);
        io.AddKeyAnalogEvent(key, a > 0.1f, a);
    };
    stick(ImGuiKey_GamepadLStickLeft, -lx);
    stick(ImGuiKey_GamepadLStickRight, lx);
    stick(ImGuiKey_GamepadLStickUp, -ly);
    stick(ImGuiKey_GamepadLStickDown, ly);
    if (pad.connected) io.BackendFlags |= ImGuiBackendFlags_HasGamepad;
    else io.BackendFlags &= ~ImGuiBackendFlags_HasGamepad;
}

void draw_overlay_tab(gowa::GameSettings& s) {
    ImGui::Checkbox("Mostrar overlay (F10)", &s.show_fps);
    int corner = int(s.corner);
    if (ImGui::Combo("Canto", &corner, corner_labels, int(std::size(corner_labels)))) s.corner = gowa::Corner(corner);
    ImGui::SliderInt("Escala", &s.overlay_scale, 50, 300, "%d%%", ImGuiSliderFlags_AlwaysClamp);
    ImGui::SliderInt("Opacidade do fundo", &s.overlay_opacity, 0, 100, "%d%%", ImGuiSliderFlags_AlwaysClamp);
    ImGui::SeparatorText("Itens");
    for (size_t i = 0; i < std::size(item_labels); ++i) {
        bool on = s.overlay_items & (1u << i);
        if (ImGui::Checkbox(item_labels[i], &on)) s.overlay_items ^= 1u << i;
    }
    ImGui::Spacing();
    ImGui::TextDisabled("Com o menu aberto o overlay aparece como prévia.");
    ImGui::TextDisabled("O FPS conta os quadros que o jogo entrega (flips), não as apresentações.");
}

void draw_image_tab(gowa::GameSettings& s) {
    int mode = int(s.display_mode);
    if (ImGui::Combo("Modo de exibição", &mode, mode_labels, int(std::size(mode_labels))))
        s.display_mode = gowa::DisplayMode(mode);
    ImGui::Checkbox("VSync", &s.vsync);
    int size = 3;
    if (!s.window_monitor)
        for (int i = 0; i < 3; ++i)
            if (s.window_width == size_presets[i][0] && s.window_height == size_presets[i][1]) size = i;
    char custom[32];
    snprintf(custom, sizeof custom, "%dx%d", s.window_width, s.window_height);
    const bool preset = s.window_monitor || size < 3;
    if (ImGui::BeginCombo("Tamanho da janela", preset ? size_labels[size] : custom)) {
        for (int i = 0; i < 4; ++i) {
            if (ImGui::Selectable(size_labels[i], preset && size == i)) {
                s.window_monitor = i == 3;
                if (i < 3) {
                    s.window_width = size_presets[i][0];
                    s.window_height = size_presets[i][1];
                }
            }
        }
        ImGui::EndCombo();
    }
    ImGui::Spacing();
    ImGui::TextWrapped("Tudo aqui vale na hora. O VSync recria a swapchain no próximo quadro "
                       "(FIFO com VSync; MAILBOX ou IMMEDIATE sem). O tamanho só se aplica no modo janela; "
                       "em tela cheia vale a resolução do monitor.");
    ImGui::TextDisabled("Imagem interna do jogo: 1280x720, escalada para a janela.");
}

void draw_controls_tab() {
    ImGui::TextWrapped("Mapeamento atual (somente leitura). Para remapear, use o launcher.");
    ImGui::Spacing();
    if (ImGui::BeginTable("##bindings", 3, ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV |
                                              ImGuiTableFlags_ScrollY,
                          ImVec2(0, ImGui::GetContentRegionAvail().y - ImGui::GetFrameHeightWithSpacing() * 3))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("Botão");
        ImGui::TableSetupColumn("Teclado / mouse");
        ImGui::TableSetupColumn("Controle");
        ImGui::TableHeadersRow();
        for (const auto& b : bindings) {
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::TextUnformatted(b.button.c_str());
            ImGui::TableNextColumn();
            ImGui::TextUnformatted(b.keys.empty() ? "-" : b.keys.c_str());
            ImGui::TableNextColumn();
            ImGui::TextUnformatted(b.pad.empty() ? "-" : b.pad.c_str());
        }
        ImGui::EndTable();
    }
    ImGui::TextDisabled("Analógico esquerdo: WASD. Direito: setas. Reservadas: Insert (menu) e F10 (overlay).");
    ImGui::TextDisabled("Controle: R3 + L2 abre e fecha este menu.");
}

const char* device_type(VkPhysicalDeviceType type) {
    switch (type) {
    case VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU: return "dedicada";
    case VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU: return "integrada";
    case VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU: return "virtual";
    case VK_PHYSICAL_DEVICE_TYPE_CPU: return "CPU (software)";
    default: return "outra";
    }
}

const char* present_mode_name(VkPresentModeKHR mode) {
    switch (mode) {
    case VK_PRESENT_MODE_FIFO_KHR: return "FIFO (VSync)";
    case VK_PRESENT_MODE_MAILBOX_KHR: return "MAILBOX";
    case VK_PRESENT_MODE_IMMEDIATE_KHR: return "IMMEDIATE";
    default: return "outro";
    }
}

void draw_about_tab() {
    const VkPhysicalDeviceProperties& p = renderer.properties;
    ImGui::Text("God of War: Ascension (BCUS98232), port nativo");
    ImGui::Text("Build: %s", GOWA_COMMIT);
    ImGui::Text("ps3recomp: %s", GOWA_TOOLKIT_COMMIT);
    ImGui::SeparatorText("GPU");
    ImGui::Text("Placa: %s", p.deviceName);
    ImGui::Text("Tipo: %s", device_type(p.deviceType));
    ImGui::Text("Dispositivo Vulkan: %04x:%04x, API %u.%u.%u, driver 0x%08x", p.vendorID, p.deviceID,
                VK_API_VERSION_MAJOR(p.apiVersion), VK_API_VERSION_MINOR(p.apiVersion),
                VK_API_VERSION_PATCH(p.apiVersion), p.driverVersion);
    ImGui::Text("Swapchain: %ux%u, %u imagens, %s", renderer.extent.width, renderer.extent.height,
                renderer.image_count, present_mode_name(renderer.present_mode));
    ImGui::SeparatorText("Arquivos");
    ImGui::TextWrapped("Opções: %s", game_ini.path.c_str());
    ImGui::Text("Dear ImGui %s", IMGUI_VERSION);
}

void request_window_changes(const gowa::GameSettings& before, const gowa::GameSettings& after) {
    unsigned requests = 0;
    if (before.display_mode != after.display_mode || before.window_monitor != after.window_monitor ||
        before.window_width != after.window_width || before.window_height != after.window_height)
        requests |= request_display;
    if (before.vsync != after.vsync) requests |= request_vsync;
    if (requests) window_requests.fetch_or(requests);
}

void draw_menu(gowa::GameSettings& s) {
    const ImVec2 display = ImGui::GetIO().DisplaySize;
    const float ds = display_scale();
    const ImVec2 size(std::min(display.x * 0.9f, 640.0f * ds), std::min(display.y * 0.85f, 470.0f * ds));
    ImGui::SetNextWindowPos(ImVec2(display.x * 0.5f, display.y * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(size, ImGuiCond_Always);
    if (!menu_drawn) ImGui::SetNextWindowFocus();
    ImGui::SetNextWindowBgAlpha(0.94f);
    ImGui::PushFont(nullptr, 17.0f * ds);
    const ImGuiWindowFlags flags = ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
        ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoSavedSettings;
    if (ImGui::Begin("God of War: Ascension — Menu###gowa_menu", nullptr, flags)) {
        int step = 0;
        if (ImGui::IsKeyPressed(ImGuiKey_Q, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false)) step = -1;
        if (ImGui::IsKeyPressed(ImGuiKey_E, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false)) step = 1;
        const int forced = step ? (menu_tab + step + 4) % 4 : -1;
        if (ImGui::BeginTabBar("##tabs")) {
            auto tab = [&](int index, const char* label) {
                const bool open = ImGui::BeginTabItem(label, nullptr,
                                                      forced == index ? ImGuiTabItemFlags_SetSelected : 0);
                if (open) menu_tab = index;
                return open;
            };
            if (tab(0, "Overlay")) { draw_overlay_tab(s); ImGui::EndTabItem(); }
            if (tab(1, "Imagem")) { draw_image_tab(s); ImGui::EndTabItem(); }
            if (tab(2, "Controles")) { draw_controls_tab(); ImGui::EndTabItem(); }
            if (tab(3, "Sobre")) { draw_about_tab(); ImGui::EndTabItem(); }
            ImGui::EndTabBar();
        }
        ImGui::SetCursorPosY(ImGui::GetWindowHeight() - ImGui::GetFrameHeightWithSpacing() -
                             ImGui::GetStyle().WindowPadding.y);
        if (ImGui::Button("Fechar")) menu_wanted.store(false);
        ImGui::SameLine();
        ImGui::TextDisabled("Q/E ou L1/R1 trocam de aba. Esc, Insert ou R3 + L2 fecham.");
    }
    ImGui::End();
    ImGui::PopFont();
}

int on_record(VkCommandBuffer cmd, uint32_t image_index, void*) {
    present_total.fetch_add(1, std::memory_order_relaxed);
    const bool menu = menu_wanted.load(std::memory_order_relaxed);
    rsx_vk_shader_stats shader_now;
    rsx_vulkan_backend_shader_stats(&shader_now);
    const bool compiling = shader_now.pending > 0;
    if (!overlay_visible.load(std::memory_order_relaxed) && !menu && !compiling) {
        renderer.last_frame_ns = 0;
        menu_drawn = false;
        return 0;
    }
    if (!renderer.ready || image_index >= renderer.image_count || !renderer.framebuffers[image_index])
        return 0;
    const uint64_t now = now_ns();
    gowa::GameSettings s = settings_snapshot();
    const gowa::GameSettings before = s;
    update_stats(now);
    ImGuiIO& io = ImGui::GetIO();
    if (menu && !menu_drawn) {
        io.ClearInputKeys();
        io.ClearInputMouse();
        pad_down.fill(false);
    }
    if (menu) feed_input();
    io.DisplaySize = ImVec2(float(renderer.extent.width), float(renderer.extent.height));
    io.DeltaTime = renderer.last_frame_ns ? std::clamp(float(double(now - renderer.last_frame_ns) / 1e9), 1e-4f, 0.5f)
                                          : 1.0f / 60.0f;
    renderer.last_frame_ns = now;
    ImGui_ImplVulkan_NewFrame();
    ImGui::NewFrame();
    if (s.show_fps || menu) draw_overlay(s);
    else if (compiling) draw_compiling_note(s);
    if (menu) draw_menu(s);
    menu_drawn = menu;
    ImGui::Render();
    if (!(s == before)) {
        {
            std::lock_guard<std::mutex> lock(settings_mutex);
            game_ini.settings = s;
        }
        overlay_visible.store(s.show_fps, std::memory_order_relaxed);
        request_window_changes(before, s);
    }
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
    if (e->type == SDL_WINDOWEVENT && e->window.event == SDL_WINDOWEVENT_SIZE_CHANGED) {
        window_width.store(e->window.data1);
        window_height.store(e->window.data2);
        return 0;
    }
    const bool menu = menu_wanted.load(std::memory_order_relaxed);
    if (e->type == SDL_KEYDOWN && !e->key.repeat) {
        const SDL_Keycode key = e->key.keysym.sym;
        if (key == SDLK_F10) {
            bool show;
            {
                std::lock_guard<std::mutex> lock(settings_mutex);
                show = game_ini.settings.show_fps = !game_ini.settings.show_fps;
            }
            overlay_visible.store(show, std::memory_order_relaxed);
            if (!menu) save_settings();
            return 1;
        }
        if (key == SDLK_INSERT || (key == SDLK_ESCAPE && menu)) {
            menu_wanted.store(!menu);
            return 1;
        }
        if (key == SDLK_ESCAPE) return 1;
    }
    if (!menu) return 0;
    switch (e->type) {
    case SDL_KEYDOWN:
    case SDL_KEYUP:
    case SDL_MOUSEMOTION:
    case SDL_MOUSEBUTTONDOWN:
    case SDL_MOUSEBUTTONUP:
    case SDL_MOUSEWHEEL: {
        std::lock_guard<std::mutex> lock(input_mutex);
        if (queued_events.size() < 512) queued_events.push_back(*e);
        return e->type == SDL_KEYDOWN || e->type == SDL_KEYUP;
    }
    default:
        return 0;
    }
}

void apply_window(const gowa::GameSettings& s, unsigned requests) {
    if (requests & request_vsync) rsx_vulkan_backend_set_vsync(s.vsync);
    SDL_Window* window = rsx_vulkan_backend_window();
    if (!(requests & request_display) || !window) return;
    const int display = std::max(0, SDL_GetWindowDisplayIndex(window));
    switch (s.display_mode) {
    case gowa::DisplayMode::borderless:
        SDL_SetWindowFullscreen(window, SDL_WINDOW_FULLSCREEN_DESKTOP);
        break;
    case gowa::DisplayMode::fullscreen: {
        SDL_DisplayMode mode;
        if (SDL_GetDesktopDisplayMode(display, &mode) == 0) SDL_SetWindowDisplayMode(window, &mode);
        if (SDL_SetWindowFullscreen(window, SDL_WINDOW_FULLSCREEN) != 0)
            fprintf(stderr, "[overlay] exclusive fullscreen: %s\n", SDL_GetError());
        break;
    }
    case gowa::DisplayMode::windowed: {
        SDL_SetWindowFullscreen(window, 0);
        SDL_Rect bounds{0, 0, s.window_width, s.window_height};
        if (s.window_monitor && SDL_GetDisplayUsableBounds(display, &bounds) == 0) {
            SDL_SetWindowSize(window, bounds.w, bounds.h);
            SDL_SetWindowPosition(window, bounds.x, bounds.y);
        } else {
            SDL_SetWindowSize(window, s.window_width, s.window_height);
            SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED_DISPLAY(display),
                                  SDL_WINDOWPOS_CENTERED_DISPLAY(display));
        }
        break;
    }
    }
    int w = 0, h = 0;
    SDL_GetWindowSize(window, &w, &h);
    window_width.store(w);
    window_height.store(h);
}

void setup_imgui() {
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    io.BackendPlatformName = "gowa-pc";
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard | ImGuiConfigFlags_NavEnableGamepad;
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
    bindings = gowa_input_bindings();
    for (size_t i = 0; i < bindings.size() && i < std::size(button_labels); ++i) bindings[i].button = button_labels[i];
    if (SDL_Window* window = rsx_vulkan_backend_window()) {
        int w = 0, h = 0;
        SDL_GetWindowSize(window, &w, &h);
        window_width.store(w);
        window_height.store(h);
    }
    if (rsx_vulkan_overlay_register(&hooks) != 0) {
        fprintf(stderr, "[overlay] no window swapchain; overlay disabled\n");
        return;
    }
    if (game_ini.existed) window_requests.store(request_display | request_vsync);
    started.store(true);
}

void gowa_overlay_tick() {
    if (!started.load(std::memory_order_relaxed)) return;
    const gowa::PadState pad = gowa_input_pad_state();
    const bool combo = pad.connected && pad.buttons[SDL_CONTROLLER_BUTTON_RIGHTSTICK] &&
                       pad.axes[SDL_CONTROLLER_AXIS_TRIGGERLEFT] > 16000;
    if (combo && !combo_held) menu_wanted.store(!menu_wanted.load());
    combo_held = combo;
    const bool menu = menu_wanted.load();
    if (menu) {
        std::lock_guard<std::mutex> lock(input_mutex);
        queued_pad = pad;
    }
    if (menu != menu_applied) {
        menu_applied = menu;
        gowa_input_set_captured(menu);
        if (!menu) save_settings();
        fprintf(stderr, "[overlay] menu %s\n", menu ? "open" : "closed");
    }
    const unsigned requests = window_requests.exchange(0);
    if (requests) apply_window(settings_snapshot(), requests);
}
