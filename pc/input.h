#pragma once
#include <SDL2/SDL.h>
#include "cellPad_host.h"
#include <array>
#include <string>
#include <vector>

namespace gowa {
struct PadState {
    bool connected = false;
    std::array<Uint8, SDL_CONTROLLER_BUTTON_MAX> buttons{};
    std::array<Sint16, SDL_CONTROLLER_AXIS_MAX> axes{};
};
struct BindingInfo {
    std::string button;
    std::string keys;
    std::string pad;
};
enum class BindingKind { none, key, mouse };
struct Binding {
    BindingKind kind = BindingKind::none;
    int code = 0;
};
using BindingPair = std::array<Binding, 2>;
constexpr size_t evade_mod_slot = 16;
struct InputMapping {
    std::array<BindingPair, 17> keys;
    std::array<SDL_GameControllerButton, 16> buttons;
    int deadzone = 7849;
    bool keyboard = true;
    InputMapping();
    void configure(const char* key_map, const char* pad_map);
    bool held(size_t slot, const Uint8* keyboard_state, Uint32 mouse_buttons) const;
    CellPadHostSample sample(const Uint8* keyboard_state, Uint32 mouse_buttons, const Uint8* pad_buttons,
                            const Sint16* axes, bool focused) const;
};
std::string binding_name(const Binding& binding);
}

void gowa_input_register();
void gowa_input_start();
void gowa_input_tick();
void gowa_input_stop();
void gowa_input_set_captured(bool captured);
gowa::PadState gowa_input_pad_state();
std::vector<gowa::BindingInfo> gowa_input_bindings();
