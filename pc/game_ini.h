#pragma once
#include <string>
#include <vector>

/// Port-side user settings stored in a small `key=value` file (game.ini).
/// The file is shared with the launcher; unknown lines and comments are kept
/// verbatim when the file is rewritten.
namespace gowa {
/// Screen corner the performance overlay is anchored to.
/// Serialized as `tl`, `tr`, `bl`, `br` (the enumerator order matters).
enum class Corner { top_left, top_right, bottom_left, bottom_right };
/// How the game window is presented.
/// Serialized as `windowed`, `borderless`, `fullscreen` (the enumerator order matters).
enum class DisplayMode { windowed, borderless, fullscreen };
/// Bit flags selecting which lines the performance overlay shows.
/// Bit N corresponds to the N-th name accepted by parse_overlay_items()
/// (`fps`, `frametime`, `graph`, `present`, `spu`, `shaders`, `video`).
enum OverlayItem : unsigned {
    item_fps = 1u << 0,
    item_frametime = 1u << 1,
    item_graph = 1u << 2,
    item_present = 1u << 3,
    item_spu = 1u << 4,
    item_shaders = 1u << 5,
    item_video = 1u << 6,
};

/// Parsed values of every setting the port understands. Default member
/// values are the defaults used when a key is missing or invalid; keys equal
/// to their default are not added to a file that did not already contain them.
struct GameSettings {
    /// Whether the performance overlay is visible (toggled with F10).
    bool show_fps = false;
    Corner corner = Corner::top_left;
    /// Overlay font scale in percent, accepted range 50..300.
    int overlay_scale = 100;
    /// Overlay background opacity in percent, accepted range 0..100.
    int overlay_opacity = 80;
    /// OR of OverlayItem flags.
    unsigned overlay_items = item_fps | item_frametime;
    /// Comma-separated `overlay_items` entries this build does not recognize,
    /// kept so that saving does not drop names written by a newer launcher.
    std::string overlay_items_extra;
    DisplayMode display_mode = DisplayMode::windowed;
    bool vsync = true;
    /// When true the window fills the usable area of its monitor and
    /// window_width/window_height are ignored (`window_size=monitor`).
    bool window_monitor = false;
    /// Windowed size in pixels, accepted range 100..99999 each.
    int window_width = 1280;
    int window_height = 720;
    bool operator==(const GameSettings&) const = default;
};

/// A loaded settings file: its location, the original lines (used to
/// preserve layout, comments and unknown keys on save) and the parsed values.
struct GameIni {
    /// Absolute or relative path of the file this object reads and writes.
    std::string path;
    /// True when the file could be opened at load time or has been saved since.
    bool existed = false;
    /// Raw file lines without trailing newline or `\r`.
    std::vector<std::string> lines;
    GameSettings settings;
};

/// Resolves where game.ini lives. First match wins: `$GOWA_GAME_INI` (full
/// path), `$GOWA_CONFIG_DIR/game.ini`, `$XDG_CONFIG_HOME/gowa-pc/game.ini`,
/// `$HOME/.config/gowa-pc/game.ini`, and `./.config/gowa-pc/game.ini` when
/// HOME is unset. Does not touch the filesystem.
std::string game_ini_path();
/// Reads and parses @p path. A missing or unreadable file is not an error: the
/// result has `existed == false`, no lines and default settings. Invalid values
/// for known keys are ignored (with a message on stderr) and keep the default.
GameIni load_game_ini(const std::string& path);
/// Produces the text of a settings file from the original @p lines and the
/// values in @p settings. Lines holding a known key are rewritten in place with
/// the current value; every other line is copied unchanged. Known keys absent
/// from @p lines are appended only when they differ from the default. Pure.
std::string render_game_ini(const std::vector<std::string>& lines, const GameSettings& settings);
/// Writes @p ini to `ini.path` atomically: creates missing parent directories,
/// writes a `<path>.tmp<pid>` file, fsyncs it and renames it over the target.
/// On success refreshes `ini.lines` with the written text and sets `existed`.
/// Returns false (and logs to stderr) on any failure, leaving the old file intact.
/// Not thread-safe for the same GameIni; callers serialize access.
bool save_game_ini(GameIni& ini);
/// Parses a comma-separated list of overlay item names into OverlayItem bits.
/// Whitespace around names is ignored. Unrecognized non-empty names are
/// appended to @p extra (comma-separated) when it is non-null; @p extra is
/// cleared first.
unsigned parse_overlay_items(const std::string& text, std::string* extra = nullptr);
/// Inverse of parse_overlay_items(): lists the names of the set bits in
/// canonical order, followed by @p extra, all comma-separated.
std::string format_overlay_items(unsigned items, const std::string& extra = {});
}
