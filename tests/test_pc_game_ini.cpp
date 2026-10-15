/// Unit test for the game-side game.ini reader and writer (pc/game_ini.cpp).
///
/// Covers how the file path is chosen (GOWA_GAME_INI, then GOWA_CONFIG_DIR),
/// defaults for a missing file, parsing and clamping of values, preservation of
/// comments, unknown keys and unknown overlay items on save, and overlay item
/// list parsing and formatting. Writes only to a temporary folder under
/// scratch/pc-tests, which must exist (the test is run from the repository root).
///
/// Built and run by scripts/test_pc.sh:
///
///     clang++ -std=c++20 -Wall -Wextra tests/test_pc_game_ini.cpp pc/game_ini.cpp -o scratch/pc-tests/test_pc_game_ini
///     scratch/pc-tests/test_pc_game_ini
#include "../pc/game_ini.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <unistd.h>

/// Returns the whole content of the file at path (empty if it cannot be read).
static std::string read_file(const std::string& path) {
    std::ifstream in(path);
    std::stringstream text;
    text << in.rdbuf();
    return text.str();
}

/// Runs the game.ini checks in sequence; any failure aborts through assert.
int main() {
    char folder[] = "scratch/pc-tests/game-ini-XXXXXX";
    assert(mkdtemp(folder));
    const std::string path = std::string(folder) + "/sub/game.ini";

    /// Path: GOWA_GAME_INI wins; otherwise game.ini inside GOWA_CONFIG_DIR.
    setenv("GOWA_GAME_INI", path.c_str(), 1);
    assert(gowa::game_ini_path() == path);
    unsetenv("GOWA_GAME_INI");
    setenv("GOWA_CONFIG_DIR", folder, 1);
    assert(gowa::game_ini_path() == std::string(folder) + "/game.ini");
    unsetenv("GOWA_CONFIG_DIR");

    /// A missing file loads defaults, and defaults render to an empty file.
    gowa::GameIni missing = gowa::load_game_ini(path);
    assert(!missing.existed);
    assert(missing.settings == gowa::GameSettings{});
    assert(gowa::render_game_ini({}, gowa::GameSettings{}).empty());

    /// Saving creates the missing parent folder and writes only non-default keys.
    missing.settings.show_fps = true;
    assert(gowa::save_game_ini(missing));
    assert(read_file(path) == "show_fps=1\n");

    /// Loading keeps unknown overlay items, rejects an out-of-range scale (the
    /// value stays 100) and reads corner, monitor size, display mode and vsync.
    {
        std::ofstream out(path);
        out << "# launcher\nfuture_key=abc\noverlay_items=fps, hud ,spu\noverlay_scale=999\n"
               "overlay_corner=br\nwindow_size=monitor\ndisplay_mode=fullscreen\nvsync=0\n";
    }
    gowa::GameIni ini = gowa::load_game_ini(path);
    assert(ini.existed);
    const gowa::GameSettings& s = ini.settings;
    assert(s.overlay_items == (gowa::item_fps | gowa::item_spu) && s.overlay_items_extra == "hud");
    assert(s.overlay_scale == 100 && s.corner == gowa::Corner::bottom_right);
    assert(s.window_monitor && s.display_mode == gowa::DisplayMode::fullscreen && !s.vsync);

    /// Saving updates keys in place, keeps the comment and unknown key, orders
    /// overlay items with unknown ones last, appends new keys and reloads equal.
    ini.settings.overlay_items |= gowa::item_graph;
    ini.settings.window_monitor = false;
    ini.settings.window_width = 1600;
    ini.settings.window_height = 900;
    ini.settings.overlay_opacity = 40;
    assert(gowa::save_game_ini(ini));
    assert(read_file(path) ==
           "# launcher\nfuture_key=abc\noverlay_items=fps,graph,spu,hud\noverlay_scale=100\n"
           "overlay_corner=br\nwindow_size=1600x900\ndisplay_mode=fullscreen\nvsync=0\noverlay_opacity=40\n");
    assert(gowa::load_game_ini(path).settings == ini.settings);

    /// Overlay item lists parse to bit masks and format back in canonical order,
    /// with unknown names collected separately.
    assert(gowa::parse_overlay_items("fps,shaders") == (gowa::item_fps | gowa::item_shaders));
    assert(gowa::format_overlay_items(gowa::item_shaders) == "shaders");
    std::string extra;
    const unsigned with_video = gowa::parse_overlay_items("video,hud,fps", &extra);
    assert(with_video == (gowa::item_fps | gowa::item_video) && extra == "hud");
    assert(gowa::format_overlay_items(with_video, extra) == "fps,video,hud");
    assert(gowa::parse_overlay_items("fps,frametime") == (gowa::item_fps | gowa::item_frametime));
    unlink(path.c_str());
    rmdir((std::string(folder) + "/sub").c_str());
    rmdir(folder);
    puts("game.ini: path, defaults, parse, clamp rejection, unknown keys and items kept, atomic save OK");
}
