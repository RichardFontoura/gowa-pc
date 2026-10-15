#include "../pc/input.h"
#include "../../ps3recomp/libs/input/cellPad.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Minimal guest memory for the real cellPad implementation (no game assets).
extern "C" {
uint8_t* vm_base;
int g_resv_store_active = 0;
uint32_t g_ww_lo = 0, g_ww_hi = 0;
void ppu_resv_break_store(uint64_t) {}
void ps3_ww_report_inline(uint32_t, uint64_t, int) {}
int spu_coh_is_reserved(uint32_t) { return 0; }
void spu_lockline_lock() {}
void spu_lockline_unlock() {}
void spu_coh_notify_write(uint32_t) {}
}
static CellPadHostSample supplied;
static int rumble_calls = 0;
static void provide(unsigned port, CellPadHostSample* out) {
    *out = port == 0 ? supplied : CellPadHostSample{0, 0, 128, 128, 128, 128, 0, 0};
}
static void rumble(unsigned port, uint8_t small, uint8_t large) {
    assert(port == 0 && small == 1 && large == 170);
    ++rumble_calls;
}
static unsigned be16(unsigned ea) { return (vm_base[ea] << 8) | vm_base[ea + 1]; }
static unsigned be32(unsigned ea) { return (be16(ea) << 16) | be16(ea + 2); }
int main() {
    assert(SDL_Init(0) == 0);
    gowa::InputMapping m;
    Uint8 k[SDL_NUM_SCANCODES]{}, b[SDL_CONTROLLER_BUTTON_MAX]{};
    Sint16 a[SDL_CONTROLLER_AXIS_MAX]{};
    auto sample = [&] { return m.sample(k, b, a, true); };
    auto s = sample();
    assert(s.buttons == 0 && s.lx == 128 && s.ry == 128);
    k[SDL_SCANCODE_SPACE] = k[SDL_SCANCODE_W] = k[SDL_SCANCODE_LEFT] = 1;
    s = sample();
    assert(s.buttons == CELL_PAD_CTRL_CROSS && s.ly == 0 && s.rx == 0);
    k[SDL_SCANCODE_S] = 1;
    assert(sample().ly == 128);  // opposite directions cancel
    s = m.sample(k, b, a, false);
    assert(s.buttons == 0 && s.ly == 128 && s.rx == 128);  // focus loss
    memset(k, 0, sizeof(k));
    m.configure("cross=V,circle=Escape,square=Not A Key,start=Insert", "cross=b,circle=a,l2=x");
    k[SDL_SCANCODE_SPACE] = 1;
    assert(!(sample().buttons & CELL_PAD_CTRL_CROSS));  // old binding removed
    k[SDL_SCANCODE_V] = k[SDL_SCANCODE_ESCAPE] = 1;
    assert((sample().buttons & (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_CIRCLE)) ==
           (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_CIRCLE));
    assert(m.keys[15] == SDL_SCANCODE_E && m.keys[3] == SDL_SCANCODE_RETURN);
    memset(k, 0, sizeof(k));
    b[SDL_CONTROLLER_BUTTON_B] = 1;
    a[SDL_CONTROLLER_AXIS_LEFTX] = -32768;
    a[SDL_CONTROLLER_AXIS_LEFTY] = 32767;
    a[SDL_CONTROLLER_AXIS_RIGHTX] = 5000;
    a[SDL_CONTROLLER_AXIS_TRIGGERLEFT] = 32767;
    s = sample();
    assert(s.buttons == (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_L2));
    assert(s.lx == 0 && s.ly == 255 && s.rx == 128 && s.l2 == 255);
    m.keyboard = false;
    k[SDL_SCANCODE_E] = 1;
    assert(!(sample().buttons & CELL_PAD_CTRL_SQUARE));

    uint8_t memory[4096]{};
    vm_base = memory;
    supplied = s;
    cellPad_set_host_adapter(provide, rumble);
    assert(cellPadInit(7) == CELL_OK);
    assert(cellPadSetPortSetting(0, CELL_PAD_SETTING_PRESS_ON) == CELL_OK);
    auto* guest = reinterpret_cast<CellPadData*>(0x100);
    assert(cellPadGetData(0, guest) == CELL_OK);
    assert(be32(0x100) == CELL_PAD_LEN_CHANGE_PRESS_ON);
    auto word = [](unsigned n) { return be16(0x104 + n * 2); };
    assert(word(CELL_PAD_BTN_OFFSET_DIGITAL1) == 0);
    assert(word(CELL_PAD_BTN_OFFSET_DIGITAL2) == 0x41);
    assert(word(CELL_PAD_BTN_OFFSET_PRESS_CROSS) == 255);
    assert(word(CELL_PAD_BTN_OFFSET_PRESS_L2) == 255);
    assert(word(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X) == 0);
    supplied = {1, 0, 128, 128, 128, 128, 0, 0};
    SDL_Delay(5);
    assert(cellPadGetData(0, guest) == CELL_OK);
    assert(word(CELL_PAD_BTN_OFFSET_DIGITAL2) == 0 && word(CELL_PAD_BTN_OFFSET_PRESS_CROSS) == 0);
    assert(word(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X) == 128);
    assert(cellPadGetInfo2(reinterpret_cast<CellPadInfo2*>(0x300)) == CELL_OK);
    assert(be32(0x304) == 1);  // only port 0 connected
    memory[0x500 + CELL_PAD_ACTUATOR_PARAM_SMALL] = 1;
    memory[0x500 + CELL_PAD_ACTUATOR_PARAM_LARGE] = 170;
    assert(cellPadSetActDirect(0, reinterpret_cast<CellPadActParam*>(0x500)) == CELL_OK);
    assert(rumble_calls == 1);
    assert(cellPadEnd() == CELL_OK);
    if (getenv("GOWA_DEVICE_TEST")) {
        assert(SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) == 0);
        // No events or rumble should be sent to a user's physical device.
        assert(SDL_NumJoysticks() == 0);
        SDL_Window* window = SDL_CreateWindow("Ascension input test", 0, 0, 320, 200, SDL_WINDOW_SHOWN);
        assert(window && SDL_SetWindowInputFocus(window) == 0);
        SDL_PumpEvents();
        gowa_input_register();
        gowa_input_start();
        int index = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,
                                             SDL_CONTROLLER_AXIS_MAX, SDL_CONTROLLER_BUTTON_MAX, 0);
        assert(index >= 0);
        SDL_Joystick* joystick = SDL_JoystickOpen(index);
        assert(joystick);
        // SDL's virtual joystick defaults triggers to the stick center; set rest explicitly.
        SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_TRIGGERLEFT, -32768);
        SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_TRIGGERRIGHT, -32768);
        SDL_JoystickSetVirtualButton(joystick, SDL_CONTROLLER_BUTTON_A, 1);
        SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_LEFTX, 32767);
        SDL_PumpEvents();
        gowa_input_tick();
        assert(cellPadInit(1) == CELL_OK);
        SDL_Delay(5);
        cellPadGetData(0, guest);
        assert(word(CELL_PAD_BTN_OFFSET_DIGITAL2) & 0x40);
        assert(word(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X) == 255);
        SDL_JoystickClose(joystick);
        assert(SDL_JoystickDetachVirtual(index) == 0);
        SDL_PumpEvents();
        gowa_input_tick();
        SDL_Delay(5);
        cellPadGetData(0, guest);
        assert(word(CELL_PAD_BTN_OFFSET_DIGITAL2) == 0);
        assert(word(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X) == 128);
        // Reconnect at a new device index and verify the same guest port.
        index = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,
                                         SDL_CONTROLLER_AXIS_MAX, SDL_CONTROLLER_BUTTON_MAX, 0);
        joystick = SDL_JoystickOpen(index);
        assert(joystick);
        SDL_JoystickSetVirtualButton(joystick, SDL_CONTROLLER_BUTTON_B, 1);
        SDL_PumpEvents();
        gowa_input_tick();
        SDL_Delay(5);
        cellPadGetData(0, guest);
        assert(word(CELL_PAD_BTN_OFFSET_DIGITAL2) & 0x20);
        cellPadEnd();
        gowa_input_stop();
        SDL_JoystickClose(joystick);
        SDL_JoystickDetachVirtual(index);
        SDL_DestroyWindow(window);
        puts("SDL virtual controller -> window-thread adapter -> guest: connect, buttons, axes, disconnect, reconnect OK");
    }
    SDL_Quit();
    puts("PC input: mapping, focus, axes, remap, PS3 big-endian ABI, pressure, release and rumble OK");
}
