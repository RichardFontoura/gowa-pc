#pragma once
#include <SDL2/SDL.h>
#include "cellPad_host.h"
#include <array>

namespace gowa {
struct InputMapping {
    std::array<SDL_Scancode, 16> keys;
    std::array<SDL_GameControllerButton, 16> buttons;
    int deadzone = 7849;
    bool keyboard = true;
    InputMapping();
    void configure(const char* key_map, const char* pad_map);
    CellPadHostSample sample(const Uint8* keyboard_state, const Uint8* pad_buttons,
                            const Sint16* axes, bool focused) const;
};
}

void gowa_input_register();
void gowa_input_start();
void gowa_input_tick();
void gowa_input_stop();
