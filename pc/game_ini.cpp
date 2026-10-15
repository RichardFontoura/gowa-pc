#include "game_ini.h"
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <map>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

namespace {
constexpr const char* item_names[] = {"fps", "frametime", "graph", "present", "spu", "shaders"};
constexpr const char* corner_names[] = {"tl", "tr", "bl", "br"};
constexpr const char* mode_names[] = {"windowed", "borderless", "fullscreen"};
constexpr const char* known_keys[] = {"show_fps", "overlay_corner", "overlay_scale",
    "overlay_opacity", "overlay_items", "display_mode", "vsync", "window_size"};

std::string trim(const std::string& s) {
    const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

bool split_line(const std::string& line, std::string& key, std::string& value) {
    const auto eq = line.find('=');
    if (eq == std::string::npos) return false;
    key = trim(line.substr(0, eq));
    value = trim(line.substr(eq + 1));
    if (key.empty()) return false;
    for (char c : key)
        if (!(isalnum(static_cast<unsigned char>(c)) || c == '_')) return false;
    return true;
}

bool parse_int(const std::string& text, int low, int high, int& out) {
    if (text.empty() || text.size() > 6) return false;
    for (char c : text)
        if (c < '0' || c > '9') return false;
    const int v = atoi(text.c_str());
    if (v < low || v > high) return false;
    out = v;
    return true;
}

template <size_t N>
int find_name(const char* const (&names)[N], const std::string& text) {
    for (size_t i = 0; i < N; ++i)
        if (text == names[i]) return int(i);
    return -1;
}

bool parse_size(const std::string& text, int& w, int& h) {
    const auto x = text.find('x');
    if (x == std::string::npos) return false;
    return parse_int(text.substr(0, x), 100, 99999, w) && parse_int(text.substr(x + 1), 100, 99999, h);
}

void apply(gowa::GameSettings& s, const std::string& key, const std::string& value) {
    int n = 0;
    if (key == "show_fps" && (value == "0" || value == "1")) s.show_fps = value == "1";
    else if (key == "vsync" && (value == "0" || value == "1")) s.vsync = value == "1";
    else if (key == "overlay_scale" && parse_int(value, 50, 300, n)) s.overlay_scale = n;
    else if (key == "overlay_opacity" && parse_int(value, 0, 100, n)) s.overlay_opacity = n;
    else if (key == "overlay_items") s.overlay_items = gowa::parse_overlay_items(value, &s.overlay_items_extra);
    else if (key == "overlay_corner" && (n = find_name(corner_names, value)) >= 0)
        s.corner = gowa::Corner(n);
    else if (key == "display_mode" && (n = find_name(mode_names, value)) >= 0)
        s.display_mode = gowa::DisplayMode(n);
    else if (key == "window_size") {
        int w = 0, h = 0;
        if (value == "monitor") s.window_monitor = true;
        else if (parse_size(value, w, h)) {
            s.window_monitor = false;
            s.window_width = w;
            s.window_height = h;
        } else fprintf(stderr, "[game.ini] ignored window_size=%s\n", value.c_str());
    } else if (find_name(known_keys, key) >= 0)
        fprintf(stderr, "[game.ini] ignored %s=%s\n", key.c_str(), value.c_str());
}

std::map<std::string, std::string> encode(const gowa::GameSettings& s) {
    std::map<std::string, std::string> v;
    v["show_fps"] = s.show_fps ? "1" : "0";
    v["overlay_corner"] = corner_names[int(s.corner)];
    v["overlay_scale"] = std::to_string(s.overlay_scale);
    v["overlay_opacity"] = std::to_string(s.overlay_opacity);
    v["overlay_items"] = gowa::format_overlay_items(s.overlay_items, s.overlay_items_extra);
    v["display_mode"] = mode_names[int(s.display_mode)];
    v["vsync"] = s.vsync ? "1" : "0";
    v["window_size"] = s.window_monitor ? "monitor"
        : std::to_string(s.window_width) + "x" + std::to_string(s.window_height);
    return v;
}

bool make_dirs(const std::string& dir) {
    if (dir.empty()) return true;
    struct stat st{};
    if (stat(dir.c_str(), &st) == 0) return S_ISDIR(st.st_mode);
    const auto slash = dir.find_last_of('/');
    if (slash != std::string::npos && slash > 0 && !make_dirs(dir.substr(0, slash))) return false;
    return mkdir(dir.c_str(), 0755) == 0 || errno == EEXIST;
}
}

unsigned gowa::parse_overlay_items(const std::string& text, std::string* extra) {
    unsigned items = 0;
    if (extra) extra->clear();
    std::istringstream stream(text);
    std::string part;
    while (std::getline(stream, part, ',')) {
        part = trim(part);
        const int i = find_name(item_names, part);
        if (i >= 0) items |= 1u << i;
        else if (extra && !part.empty()) *extra += (extra->empty() ? "" : ",") + part;
    }
    return items;
}

std::string gowa::format_overlay_items(unsigned items, const std::string& extra) {
    std::string out;
    for (size_t i = 0; i < std::size(item_names); ++i) {
        if (!(items & (1u << i))) continue;
        if (!out.empty()) out += ',';
        out += item_names[i];
    }
    if (!extra.empty()) out += (out.empty() ? "" : ",") + extra;
    return out;
}

std::string gowa::game_ini_path() {
    if (const char* p = getenv("GOWA_GAME_INI"); p && *p) return p;
    if (const char* d = getenv("GOWA_CONFIG_DIR"); d && *d) return std::string(d) + "/game.ini";
    if (const char* x = getenv("XDG_CONFIG_HOME"); x && *x) return std::string(x) + "/gowa-pc/game.ini";
    const char* home = getenv("HOME");
    return std::string(home && *home ? home : ".") + "/.config/gowa-pc/game.ini";
}

gowa::GameIni gowa::load_game_ini(const std::string& path) {
    GameIni ini;
    ini.path = path;
    std::ifstream in(path);
    if (!in) return ini;
    ini.existed = true;
    std::string line, key, value;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        ini.lines.push_back(line);
        if (split_line(line, key, value)) apply(ini.settings, key, value);
    }
    return ini;
}

std::string gowa::render_game_ini(const std::vector<std::string>& lines, const GameSettings& settings) {
    const auto values = encode(settings);
    const auto defaults = encode(GameSettings{});
    std::map<std::string, bool> seen;
    std::string out, key, value;
    for (const auto& line : lines) {
        auto it = split_line(line, key, value) ? values.find(key) : values.end();
        if (it != values.end()) {
            out += key + "=" + it->second + "\n";
            seen[key] = true;
        } else out += line + "\n";
    }
    for (const char* k : known_keys)
        if (!seen.count(k) && values.at(k) != defaults.at(k)) out += std::string(k) + "=" + values.at(k) + "\n";
    return out;
}

bool gowa::save_game_ini(GameIni& ini) {
    const std::string text = render_game_ini(ini.lines, ini.settings);
    const auto slash = ini.path.find_last_of('/');
    if (slash != std::string::npos && !make_dirs(ini.path.substr(0, slash))) {
        fprintf(stderr, "[game.ini] cannot create the folder of %s\n", ini.path.c_str());
        return false;
    }
    const std::string tmp = ini.path + ".tmp" + std::to_string(getpid());
    FILE* f = fopen(tmp.c_str(), "w");
    if (!f) {
        fprintf(stderr, "[game.ini] cannot write %s: %s\n", tmp.c_str(), strerror(errno));
        return false;
    }
    const bool ok = fwrite(text.data(), 1, text.size(), f) == text.size() && fflush(f) == 0 &&
                    fsync(fileno(f)) == 0;
    fclose(f);
    if (!ok || rename(tmp.c_str(), ini.path.c_str()) != 0) {
        unlink(tmp.c_str());
        fprintf(stderr, "[game.ini] cannot save %s\n", ini.path.c_str());
        return false;
    }
    std::istringstream stream(text);
    ini.lines.clear();
    for (std::string line; std::getline(stream, line);) ini.lines.push_back(line);
    ini.existed = true;
    return true;
}
