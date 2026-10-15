#include "input.h"
#include "cellPad.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <sstream>
#include <string>

namespace {
constexpr const char* names[] = {"select", "l3", "r3", "start", "up", "right",
    "down", "left", "l2", "r2", "l1", "r1", "triangle", "circle", "cross", "square"};
std::string trim(std::string s) {
    const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
uint8_t axis(int raw, int deadzone) {
    if (std::abs(raw) <= deadzone) return 128;
    if (raw < 0) return uint8_t(128 - (-raw - deadzone) * 128 / (32768 - deadzone));
    return uint8_t(128 + (raw - deadzone) * 127 / (32767 - deadzone));
}
uint8_t trigger(int raw) { return uint8_t(std::clamp(raw, 0, 32767) * 255 / 32767); }
uint8_t key_axis(Uint8 neg, Uint8 pos, uint8_t previous) {
    if (!neg && !pos) return previous;
    return neg == pos ? 128 : (neg ? 0 : 255);
}
gowa::InputMapping mapping;
std::mutex state_mutex;
CellPadHostSample current = {1, 0, 128, 128, 128, 128, 0, 0};
SDL_GameController* controller = nullptr;
bool controller_subsystem = false;
bool rumble_pending = false;
bool rumble_enabled = true;
uint8_t rumble_small = 0, rumble_large = 0;

void read_sample(unsigned port, CellPadHostSample* out) {
    std::lock_guard<std::mutex> lock(state_mutex);
    *out = port == 0 ? current : CellPadHostSample{0, 0, 128, 128, 128, 128, 0, 0};
}
void set_rumble(unsigned port, uint8_t small, uint8_t large) {
    if (port != 0) return;
    std::lock_guard<std::mutex> lock(state_mutex);
    rumble_small = small; rumble_large = large; rumble_pending = true;
}
}

gowa::InputMapping::InputMapping()
    : keys{SDL_SCANCODE_TAB, SDL_SCANCODE_Z, SDL_SCANCODE_C, SDL_SCANCODE_RETURN,
           SDL_SCANCODE_I, SDL_SCANCODE_L, SDL_SCANCODE_K, SDL_SCANCODE_J,
           SDL_SCANCODE_R, SDL_SCANCODE_F, SDL_SCANCODE_1, SDL_SCANCODE_3,
           SDL_SCANCODE_Q, SDL_SCANCODE_LSHIFT, SDL_SCANCODE_SPACE, SDL_SCANCODE_E},
      buttons{SDL_CONTROLLER_BUTTON_BACK, SDL_CONTROLLER_BUTTON_LEFTSTICK,
              SDL_CONTROLLER_BUTTON_RIGHTSTICK, SDL_CONTROLLER_BUTTON_START,
              SDL_CONTROLLER_BUTTON_DPAD_UP, SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
              SDL_CONTROLLER_BUTTON_DPAD_DOWN, SDL_CONTROLLER_BUTTON_DPAD_LEFT,
              SDL_CONTROLLER_BUTTON_INVALID, SDL_CONTROLLER_BUTTON_INVALID,
              SDL_CONTROLLER_BUTTON_LEFTSHOULDER, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
              SDL_CONTROLLER_BUTTON_Y, SDL_CONTROLLER_BUTTON_B,
              SDL_CONTROLLER_BUTTON_A, SDL_CONTROLLER_BUTTON_X} {}

void gowa::InputMapping::configure(const char* key_map, const char* pad_map) {
    for (int kind = 0; kind < 2; ++kind) {
        const char* text = kind ? pad_map : key_map;
        std::istringstream stream(text ? text : "");
        std::string entry;
        while (std::getline(stream, entry, ',')) {
            auto eq = entry.find('=');
            std::string name = trim(entry.substr(0, eq));
            std::string value = eq == std::string::npos ? "" : trim(entry.substr(eq + 1));
            auto it = std::find(std::begin(names), std::end(names), name);
            bool valid = it != std::end(names) && !value.empty();
            if (valid) {
                size_t i = it - std::begin(names);
                if (!kind) {
                    auto code = SDL_GetScancodeFromName(value.c_str());
                    valid = code != SDL_SCANCODE_UNKNOWN && code != SDL_SCANCODE_INSERT;
                    if (valid) keys[i] = code;
                } else {
                    auto button = SDL_GameControllerGetButtonFromString(value.c_str());
                    valid = button != SDL_CONTROLLER_BUTTON_INVALID && i != 8 && i != 9;
                    if (valid) buttons[i] = button;
                }
            }
            if (!valid) fprintf(stderr, "[input] ignored invalid %s binding: %s\n",
                                kind ? "pad" : "key", entry.c_str());
        }
    }
}

CellPadHostSample gowa::InputMapping::sample(const Uint8* k, const Uint8* b,
                                            const Sint16* a, bool focused) const {
    CellPadHostSample s{1, 0, 128, 128, 128, 128, 0, 0};
    if (!focused) return s;
    for (size_t i = 0; i < keys.size(); ++i) {
        if ((keyboard && k && k[keys[i]]) || (b && buttons[i] >= 0 && b[buttons[i]]))
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
        s.lx = key_axis(k[SDL_SCANCODE_A], k[SDL_SCANCODE_D], s.lx);
        s.ly = key_axis(k[SDL_SCANCODE_W], k[SDL_SCANCODE_S], s.ly);
        s.rx = key_axis(k[SDL_SCANCODE_LEFT], k[SDL_SCANCODE_RIGHT], s.rx);
        s.ry = key_axis(k[SDL_SCANCODE_UP], k[SDL_SCANCODE_DOWN], s.ry);
        if (k[keys[8]]) s.l2 = 255;
        if (k[keys[9]]) s.r2 = 255;
    }
    return s;
}

void gowa_input_register() { cellPad_set_host_adapter(read_sample, set_rumble); }

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
    fprintf(stderr, "[input] PC adapter ready: WASD / arrows, Space=Cross, Enter=Start, Tab=Select\n");
}

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
    auto next = mapping.sample(SDL_GetKeyboardState(nullptr), buttons, axes, focused);
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
        pending = rumble_pending; rumble_pending = false;
        small = rumble_small; large = rumble_large;
    }
    if (controller && (pending || !focused)) {
        SDL_GameControllerRumble(controller,
            focused && rumble_enabled ? uint16_t(large * 257) : 0,
            focused && rumble_enabled && small ? 65535 : 0, 100);
    }
}

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
