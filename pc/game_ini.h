#pragma once
#include <string>
#include <vector>

namespace gowa {
enum class Corner { top_left, top_right, bottom_left, bottom_right };
enum class DisplayMode { windowed, borderless, fullscreen };
enum OverlayItem : unsigned {
    item_fps = 1u << 0,
    item_frametime = 1u << 1,
    item_graph = 1u << 2,
    item_present = 1u << 3,
    item_spu = 1u << 4,
};

struct GameSettings {
    bool show_fps = false;
    Corner corner = Corner::top_left;
    int overlay_scale = 100;
    int overlay_opacity = 80;
    unsigned overlay_items = item_fps | item_frametime;
    std::string overlay_items_extra;
    DisplayMode display_mode = DisplayMode::windowed;
    bool vsync = true;
    bool window_monitor = false;
    int window_width = 1280;
    int window_height = 720;
    bool operator==(const GameSettings&) const = default;
};

struct GameIni {
    std::string path;
    bool existed = false;
    std::vector<std::string> lines;
    GameSettings settings;
};

std::string game_ini_path();
GameIni load_game_ini(const std::string& path);
std::string render_game_ini(const std::vector<std::string>& lines, const GameSettings& settings);
bool save_game_ini(GameIni& ini);
unsigned parse_overlay_items(const std::string& text, std::string* extra = nullptr);
std::string format_overlay_items(unsigned items, const std::string& extra = {});
}
