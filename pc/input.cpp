#include "input.h"
#include "cellPad.h"
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <mutex>
#include <sstream>
#include <string>

/// Keyboard/mouse/SDL controller adapter behind the cellPad HLE library.
/// The frame clock thread polls SDL in gowa_input_tick() and publishes a
/// sample under state_mutex; guest threads read it through read_sample() and
/// request rumble through set_rumble(), so SDL is never touched off the
/// window thread.

namespace {
/// Slot names accepted in GOWA_KEY_MAP / GOWA_PAD_MAP; index = slot = cellPad button bit.
constexpr const char* names[] = {"select", "l3", "r3", "start", "up", "right",
    "down", "left", "l2", "r2", "l1", "r1", "triangle", "circle", "cross", "square", "evade_mod"};
/// Mouse binding names; index + 1 is the SDL mouse button number.
constexpr const char* mouse_names[] = {"Mouse Left", "Mouse Middle", "Mouse Right", "Mouse X1", "Mouse X2"};
/// Default keyboard/mouse binding per slot, in the same syntax as GOWA_KEY_MAP values.
constexpr const char* default_keys[] = {"Tab", "Left Ctrl", "Mouse Middle|C", "Escape|Return", "1", "2",
    "3", "4", "Q", "R", "Left Shift", "E", "Mouse Right|I", "F|L", "Space|K", "Mouse Left|J", "Left Alt"};
/// Returns @p s without leading/trailing whitespace.
std::string trim(std::string s) {
    const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
/// Maps a raw SDL stick axis (-32768..32767) to the DualShock 0..255 range
/// with 128 at rest. Values inside @p deadzone read as centre and the rest of
/// the travel is rescaled so the output still reaches 0 and 255.
uint8_t axis(int raw, int deadzone) {
    if (std::abs(raw) <= deadzone) return 128;
    if (raw < 0) return uint8_t(128 - (-raw - deadzone) * 128 / (32768 - deadzone));
    return uint8_t(128 + (raw - deadzone) * 127 / (32767 - deadzone));
}
/// Maps a raw SDL trigger axis (0..32767) to pressure 0..255.
uint8_t trigger(int raw) { return uint8_t(std::clamp(raw, 0, 32767) * 255 / 32767); }
/// Parses one binding: a `Mouse ...` name (case-insensitive) or an SDL scancode
/// name. Insert and F10 are refused because the overlay reserves them for the
/// menu and the overlay toggle. Returns false for unknown names.
bool parse_binding(const std::string& text, gowa::Binding& out) {
    for (size_t i = 0; i < std::size(mouse_names); ++i) {
        if (SDL_strcasecmp(text.c_str(), mouse_names[i]) == 0) {
            out = {gowa::BindingKind::mouse, int(i) + 1};
            return true;
        }
    }
    const SDL_Scancode code = SDL_GetScancodeFromName(text.c_str());
    if (code == SDL_SCANCODE_UNKNOWN || code == SDL_SCANCODE_INSERT || code == SDL_SCANCODE_F10) return false;
    out = {gowa::BindingKind::key, int(code)};
    return true;
}
/// Parses up to two `|`-separated bindings into @p out. Invalid or surplus
/// parts are reported on stderr (quoting @p entry) and skipped. @p out is
/// replaced only when at least one part was valid; returns whether it was.
bool parse_pair(const std::string& value, gowa::BindingPair& out, const std::string& entry) {
    gowa::BindingPair pair{};
    size_t count = 0;
    std::istringstream stream(value);
    std::string part;
    while (std::getline(stream, part, '|')) {
        part = trim(part);
        gowa::Binding binding;
        if (part.empty()) continue;
        if (count == pair.size() || !parse_binding(part, binding)) {
            fprintf(stderr, "[input] ignored key %s in binding: %s\n", part.c_str(), entry.c_str());
            continue;
        }
        pair[count++] = binding;
    }
    if (!count) return false;
    out = pair;
    return true;
}
/// Digital stick axis from two keys: full deflection toward the pressed key,
/// centre when both are pressed, and @p previous (the controller value) when
/// neither is, so keys only override the controller while in use.
uint8_t key_axis(Uint8 neg, Uint8 pos, uint8_t previous) {
    if (!neg && !pos) return previous;
    return neg == pos ? 128 : (neg ? 0 : 255);
}
/// Active mapping. Written by gowa_input_start() before polling begins, read afterwards.
gowa::InputMapping mapping;
/// Guards `current`, `pad_state` and the rumble request fields, which cross
/// between the window thread and guest threads.
std::mutex state_mutex;
/// Sample handed to the guest for port 0 (connected, no buttons, sticks centred).
CellPadHostSample current = {1, 0, 128, 128, 128, 128, 0, 0};
/// Open SDL controller used as port 0, or null. Window thread only.
SDL_GameController* controller = nullptr;
/// Whether SDL_INIT_GAMECONTROLLER was initialized by gowa_input_start().
bool controller_subsystem = false;
/// Set by set_rumble() when the guest changes motor values; consumed by gowa_input_tick().
bool rumble_pending = false;
/// False when GOWA_RUMBLE=0.
bool rumble_enabled = true;
/// Last motor values requested by the guest (small motor is on/off on a DualShock 3).
uint8_t rumble_small = 0, rumble_large = 0;
/// True while the overlay menu owns input; see gowa_input_set_captured().
std::atomic<bool> captured{false};
/// Buttons masked from the guest until physically released (after capture
/// ends or the menu combo is used). Window thread only.
uint16_t suppressed = 0;
/// Raw controller state for the overlay, published under state_mutex.
gowa::PadState pad_state;

/// cellPad host read callback (guest threads). Port 0 gets the latest sample;
/// every other port reports disconnected.
void read_sample(unsigned port, CellPadHostSample* out) {
    std::lock_guard<std::mutex> lock(state_mutex);
    *out = port == 0 ? current : CellPadHostSample{0, 0, 128, 128, 128, 128, 0, 0};
}
/// cellPad host rumble callback (guest threads, from cellPadSetActDirect).
/// Only records the request; the window thread applies it on its next tick.
void set_rumble(unsigned port, uint8_t small, uint8_t large) {
    if (port != 0) return;
    std::lock_guard<std::mutex> lock(state_mutex);
    rumble_small = small; rumble_large = large; rumble_pending = true;
}
}

/// Default controller layout (SDL names follow the Xbox layout: A = cross,
/// B = circle, X = square, Y = triangle) plus the default_keys bindings.
gowa::InputMapping::InputMapping()
    : buttons{SDL_CONTROLLER_BUTTON_BACK, SDL_CONTROLLER_BUTTON_LEFTSTICK,
              SDL_CONTROLLER_BUTTON_RIGHTSTICK, SDL_CONTROLLER_BUTTON_START,
              SDL_CONTROLLER_BUTTON_DPAD_UP, SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
              SDL_CONTROLLER_BUTTON_DPAD_DOWN, SDL_CONTROLLER_BUTTON_DPAD_LEFT,
              SDL_CONTROLLER_BUTTON_INVALID, SDL_CONTROLLER_BUTTON_INVALID,
              SDL_CONTROLLER_BUTTON_LEFTSHOULDER, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
              SDL_CONTROLLER_BUTTON_Y, SDL_CONTROLLER_BUTTON_B,
              SDL_CONTROLLER_BUTTON_A, SDL_CONTROLLER_BUTTON_X} {
    for (size_t i = 0; i < keys.size(); ++i) parse_pair(default_keys[i], keys[i], default_keys[i]);
}

/// Applies the keyboard list first, then the controller list; see input.h.
void gowa::InputMapping::configure(const char* key_map, const char* pad_map) {
    for (int kind = 0; kind < 2; ++kind) {
        const char* text = kind ? pad_map : key_map;
        std::istringstream stream(text ? text : "");
        std::string entry;
        while (std::getline(stream, entry, ',')) {
            if (trim(entry).empty()) continue;
            auto eq = entry.find('=');
            std::string name = trim(entry.substr(0, eq));
            std::string value = eq == std::string::npos ? "" : trim(entry.substr(eq + 1));
            auto it = std::find(std::begin(names), std::end(names), name);
            bool valid = it != std::end(names) && !value.empty();
            if (valid) {
                size_t i = it - std::begin(names);
                if (!kind) {
                    valid = parse_pair(value, keys[i], entry);
                } else {
                    auto button = SDL_GameControllerGetButtonFromString(value.c_str());
                    valid = button != SDL_CONTROLLER_BUTTON_INVALID && i < buttons.size() && i != 8 && i != 9;
                    if (valid) buttons[i] = button;
                }
            }
            if (!valid) fprintf(stderr, "[input] ignored invalid %s binding: %s\n",
                                kind ? "pad" : "key", entry.c_str());
        }
    }
}

/// True if either keyboard/mouse binding of @p slot is down; see input.h.
bool gowa::InputMapping::held(size_t slot, const Uint8* k, Uint32 mouse) const {
    for (const Binding& b : keys[slot]) {
        if (b.kind == BindingKind::key && k && k[b.code]) return true;
        if (b.kind == BindingKind::mouse && (mouse & SDL_BUTTON(b.code))) return true;
    }
    return false;
}

/// Builds the guest pad sample from keyboard, mouse and controller state; see input.h.
/// Without evade_mod, WASD drives the left stick and the arrows the right
/// stick; with evade_mod held, WASD drives the right stick and the left stick
/// keeps the controller value.
CellPadHostSample gowa::InputMapping::sample(const Uint8* k, Uint32 mouse, const Uint8* b,
                                            const Sint16* a, bool focused) const {
    CellPadHostSample s{1, 0, 128, 128, 128, 128, 0, 0};
    if (!focused) return s;
    for (size_t i = 0; i < buttons.size(); ++i) {
        if ((keyboard && held(i, k, mouse)) || (b && buttons[i] >= 0 && b[buttons[i]]))
            s.buttons |= uint16_t(1u << i);
    }
    if (a) {
        int dz = std::clamp(deadzone, 0, 30000);
        s.lx = axis(a[SDL_CONTROLLER_AXIS_LEFTX], dz);
        s.ly = axis(a[SDL_CONTROLLER_AXIS_LEFTY], dz);
        s.rx = axis(a[SDL_CONTROLLER_AXIS_RIGHTX], dz);
        s.ry = axis(a[SDL_CONTROLLER_AXIS_RIGHTY], dz);
        s.l2 = trigger(a[SDL_CONTROLLER_AXIS_TRIGGERLEFT]);
        s.r2 = trigger(a[SDL_CONTROLLER_AXIS_TRIGGERRIGHT]);
        if (s.l2 > 23) s.buttons |= CELL_PAD_CTRL_L2;
        if (s.r2 > 23) s.buttons |= CELL_PAD_CTRL_R2;
    }
    if (keyboard && k) {
        const bool evade = held(evade_mod_slot, k, mouse);
        if (!evade) {
            s.lx = key_axis(k[SDL_SCANCODE_A], k[SDL_SCANCODE_D], s.lx);
            s.ly = key_axis(k[SDL_SCANCODE_W], k[SDL_SCANCODE_S], s.ly);
        }
        s.rx = key_axis(k[SDL_SCANCODE_LEFT], k[SDL_SCANCODE_RIGHT], s.rx);
        s.ry = key_axis(k[SDL_SCANCODE_UP], k[SDL_SCANCODE_DOWN], s.ry);
        if (evade) {
            s.rx = key_axis(k[SDL_SCANCODE_A], k[SDL_SCANCODE_D], s.rx);
            s.ry = key_axis(k[SDL_SCANCODE_W], k[SDL_SCANCODE_S], s.ry);
        }
        if (held(8, k, mouse)) s.l2 = 255;
        if (held(9, k, mouse)) s.r2 = 255;
    }
    return s;
}

/// Display name of a binding; see input.h.
std::string gowa::binding_name(const Binding& b) {
    if (b.kind == BindingKind::mouse && b.code >= 1 && b.code <= int(std::size(mouse_names)))
        return mouse_names[b.code - 1];
    if (b.kind == BindingKind::key) return SDL_GetScancodeName(SDL_Scancode(b.code));
    return "";
}

/// Installs read_sample/set_rumble as the cellPad host adapter; see input.h.
void gowa_input_register() { cellPad_set_host_adapter(read_sample, set_rumble); }

/// Reads the input environment variables and opens the controller subsystem; see input.h.
void gowa_input_start() {
    mapping.configure(getenv("GOWA_KEY_MAP"), getenv("GOWA_PAD_MAP"));
    const char* dz = getenv("GOWA_STICK_DEADZONE");
    if (dz) mapping.deadzone = std::clamp(atoi(dz), 0, 30000);
    const char* kb = getenv("GOWA_KEYBOARD");
    mapping.keyboard = !kb || std::string(kb) != "0";
    const char* rumble = getenv("GOWA_RUMBLE");
    rumble_enabled = !rumble || std::string(rumble) != "0";
    controller_subsystem = SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) == 0;
    if (!controller_subsystem) fprintf(stderr, "[input] controller init: %s\n", SDL_GetError());
    fprintf(stderr, "[input] PC adapter ready: WASD / arrows, Left Alt+WASD = right stick, "
                    "Space=Cross, F=Circle, Escape/Enter=Start, Tab=Select\n");
}

/// One input poll on the window thread; see input.h.
/// R3 + left trigger past half travel is the overlay menu combo, so the guest
/// never sees it. Rumble is (re)sent on a new guest request and every tick
/// while unfocused (which sends zero); each command lasts 100 ms, so a motor
/// stops on its own if requests stop. The small motor is on/off: any nonzero
/// value drives it at full strength.
void gowa_input_tick() {
    if (controller && !SDL_GameControllerGetAttached(controller)) {
        SDL_GameControllerClose(controller);
        controller = nullptr;
        fprintf(stderr, "[input] controller disconnected\n");
    }
    if (controller_subsystem && !controller) {
        for (int i = 0; i < SDL_NumJoysticks(); ++i) {
            if (SDL_IsGameController(i) && (controller = SDL_GameControllerOpen(i))) {
                fprintf(stderr, "[input] port 0: %s\n", SDL_GameControllerName(controller));
                break;
            }
        }
    }
    Uint8 buttons[SDL_CONTROLLER_BUTTON_MAX]{};
    Sint16 axes[SDL_CONTROLLER_AXIS_MAX]{};
    if (controller) {
        SDL_GameControllerUpdate();
        for (int i = 0; i < SDL_CONTROLLER_BUTTON_MAX; ++i)
            buttons[i] = SDL_GameControllerGetButton(controller, SDL_GameControllerButton(i));
        for (int i = 0; i < SDL_CONTROLLER_AXIS_MAX; ++i)
            axes[i] = SDL_GameControllerGetAxis(controller, SDL_GameControllerAxis(i));
    }
    bool focused = SDL_GetKeyboardFocus() != nullptr;
    const Uint32 mouse = focused ? SDL_GetMouseState(nullptr, nullptr) : 0;
    auto next = mapping.sample(SDL_GetKeyboardState(nullptr), mouse, buttons, axes, focused);
    const bool combo = buttons[SDL_CONTROLLER_BUTTON_RIGHTSTICK] &&
                       axes[SDL_CONTROLLER_AXIS_TRIGGERLEFT] > 16000;
    if (captured.load(std::memory_order_relaxed) || combo) {
        suppressed = next.buttons;
        next = {1, 0, 128, 128, 128, 128, 0, 0};
    } else {
        suppressed &= next.buttons;
        next.buttons &= uint16_t(~suppressed);
        if (suppressed & CELL_PAD_CTRL_L2) next.l2 = 0;
        if (suppressed & CELL_PAD_CTRL_R2) next.r2 = 0;
    }
    static const bool trace = getenv("GOWA_INPUT_TRACE") != nullptr;
    static uint16_t previous_buttons = 0;
    if (trace && next.buttons != previous_buttons) {
        fprintf(stderr, "[input] buttons=0x%04x focus=%d\n", next.buttons, focused);
        previous_buttons = next.buttons;
    }
    bool pending;
    uint8_t small, large;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        current = next;
        pad_state.connected = controller != nullptr;
        std::copy(std::begin(buttons), std::end(buttons), pad_state.buttons.begin());
        std::copy(std::begin(axes), std::end(axes), pad_state.axes.begin());
        pending = rumble_pending; rumble_pending = false;
        small = rumble_small; large = rumble_large;
    }
    if (controller && (pending || !focused)) {
        SDL_GameControllerRumble(controller,
            focused && rumble_enabled ? uint16_t(large * 257) : 0,
            focused && rumble_enabled && small ? 65535 : 0, 100);
    }
}

/// Releases the controller and resets the published sample; see input.h.
void gowa_input_stop() {
    if (controller) {
        SDL_GameControllerRumble(controller, 0, 0, 0);
        SDL_GameControllerClose(controller);
        controller = nullptr;
    }
    if (controller_subsystem) SDL_QuitSubSystem(SDL_INIT_GAMECONTROLLER);
    controller_subsystem = false;
    std::lock_guard<std::mutex> lock(state_mutex);
    current = {1, 0, 128, 128, 128, 128, 0, 0};
}

/// Sets whether the overlay menu owns input; see input.h.
void gowa_input_set_captured(bool value) { captured.store(value, std::memory_order_relaxed); }

/// Returns a copy of the raw controller state; see input.h.
gowa::PadState gowa_input_pad_state() {
    std::lock_guard<std::mutex> lock(state_mutex);
    return pad_state;
}

/// Lists every slot with its keyboard/mouse and controller bindings; see input.h.
std::vector<gowa::BindingInfo> gowa_input_bindings() {
    std::vector<gowa::BindingInfo> out;
    for (size_t i = 0; i < mapping.keys.size(); ++i) {
        std::string pad;
        if (i == 8) pad = "lefttrigger";
        else if (i == 9) pad = "righttrigger";
        else if (i < mapping.buttons.size() && mapping.buttons[i] != SDL_CONTROLLER_BUTTON_INVALID)
            pad = SDL_GameControllerGetStringForButton(mapping.buttons[i]);
        std::string keys;
        for (const auto& binding : mapping.keys[i]) {
            const std::string name = gowa::binding_name(binding);
            if (name.empty()) continue;
            if (!keys.empty()) keys += " | ";
            keys += name;
        }
        out.push_back({names[i], mapping.keyboard ? keys : "-", pad});
    }
    return out;
}
