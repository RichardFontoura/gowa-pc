/// Unit test for the PC input mapping (pc/input.cpp) and the guest pad ABI.
///
/// Part 1 drives gowa::InputMapping directly with synthetic keyboard, mouse and
/// controller state: default bindings, two keys per button, mouse buttons, the
/// evade modifier, opposite directions, focus loss, remapping through the
/// key/pad map strings and the keyboard toggle. Part 2 feeds a sample through
/// the real cellPad implementation (ps3recomp libs/input/cellPad.c) into a small
/// guest memory buffer and checks the big-endian CellPadData layout, pressure
/// values, release, connection info and rumble forwarding. No game assets or
/// physical devices are used.
///
/// With GOWA_DEVICE_TEST set, part 3 also opens an SDL window and a virtual SDL
/// game controller and checks the full path from SDL events through the window
/// thread adapter to the guest port, including disconnect and reconnect. It
/// asserts that no physical joystick is present, so it needs a display and no
/// real controllers attached.
///
/// Built and run by scripts/test_pc.sh (from the repository root):
///
///     clang -std=c17 -D_GNU_SOURCE -I<ps3recomp>/include -c <ps3recomp>/libs/input/cellPad.c -o scratch/pc-tests/cellPad.o
///     clang++ -std=c++20 -Wall -Wextra -I<ps3recomp>/include -I<ps3recomp>/libs/input tests/test_pc_input.cpp pc/input.cpp scratch/pc-tests/cellPad.o -lSDL2 -lpthread -o scratch/pc-tests/test_pc_input
///     scratch/pc-tests/test_pc_input
#include "../pc/input.h"
#include "cellPad.h"
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
/// Sample returned for port 0 by the host adapter, and the number of rumble calls seen.
static CellPadHostSample supplied;
static int rumble_calls = 0;
/// Host adapter sample callback: port 0 gets supplied, other ports a neutral sample.
static void provide(unsigned port, CellPadHostSample* out) {
    *out = port == 0 ? supplied : CellPadHostSample{0, 0, 128, 128, 128, 128, 0, 0};
}
/// Host adapter rumble callback: expects port 0 with small=1, large=170 and counts calls.
static void rumble(unsigned port, uint8_t small, uint8_t large) {
    assert(port == 0 && small == 1 && large == 170);
    ++rumble_calls;
}
/// Reads a big-endian 16-bit value from guest memory at ea.
static unsigned be16(unsigned ea) { return (vm_base[ea] << 8) | vm_base[ea + 1]; }
/// Reads a big-endian 32-bit value from guest memory at ea.
static unsigned be32(unsigned ea) { return (be16(ea) << 16) | be16(ea + 2); }
/// Runs all input checks in sequence; any failure aborts through assert.
int main() {
    assert(SDL_Init(0) == 0);
    gowa::InputMapping m;
    Uint8 k[SDL_NUM_SCANCODES]{}, b[SDL_CONTROLLER_BUTTON_MAX]{};
    Sint16 a[SDL_CONTROLLER_AXIS_MAX]{};
    Uint32 mouse = 0;
    /// Samples the mapping with the current state and window focus.
    auto sample = [&] { return m.sample(k, mouse, b, a, true); };
    /// Returns the two bindings of a button slot as "first|second".
    auto names = [&](size_t slot) {
        return gowa::binding_name(m.keys[slot][0]) + "|" + gowa::binding_name(m.keys[slot][1]);
    };
    /// Default bindings; slots follow the cellPad button bit order.
    assert(names(14) == "Space|K" && names(13) == "F|L" && names(15) == "Mouse Left|J");
    assert(names(12) == "Mouse Right|I" && names(2) == "Mouse Middle|C" && names(3) == "Escape|Return");
    assert(names(10) == "Left Shift|" && names(11) == "E|" && names(8) == "Q|" && names(9) == "R|");
    assert(names(1) == "Left Ctrl|" && names(0) == "Tab|" && names(4) == "1|" && names(7) == "4|");
    assert(names(gowa::evade_mod_slot) == "Left Alt|");
    /// No input gives no buttons and centered sticks.
    auto s = sample();
    assert(s.buttons == 0 && s.lx == 128 && s.ry == 128);
    /// Space presses Cross, W pushes the left stick up, arrows drive the right
    /// stick; opposite keys cancel and losing focus releases everything.
    k[SDL_SCANCODE_SPACE] = k[SDL_SCANCODE_W] = k[SDL_SCANCODE_LEFT] = 1;
    s = sample();
    assert(s.buttons == CELL_PAD_CTRL_CROSS && s.ly == 0 && s.rx == 0);
    k[SDL_SCANCODE_S] = 1;
    assert(sample().ly == 128);  // opposite directions cancel
    s = m.sample(k, mouse, b, a, false);
    assert(s.buttons == 0 && s.ly == 128 && s.rx == 128);  // focus loss
    memset(k, 0, sizeof(k));
    /// Second keys and mouse buttons press the face buttons.
    k[SDL_SCANCODE_K] = k[SDL_SCANCODE_F] = 1;
    mouse = SDL_BUTTON(SDL_BUTTON_LEFT) | SDL_BUTTON(SDL_BUTTON_RIGHT);
    s = sample();
    assert(s.buttons == (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_CIRCLE | CELL_PAD_CTRL_SQUARE |
                         CELL_PAD_CTRL_TRIANGLE));
    memset(k, 0, sizeof(k));
    mouse = 0;
    /// Holding the evade modifier moves the movement keys to the right stick.
    k[SDL_SCANCODE_LALT] = k[SDL_SCANCODE_D] = k[SDL_SCANCODE_W] = 1;
    s = sample();
    assert(s.lx == 128 && s.ly == 128 && s.rx == 255 && s.ry == 0 && s.buttons == 0);
    k[SDL_SCANCODE_LALT] = 0;
    s = sample();
    assert(s.lx == 255 && s.ly == 0 && s.rx == 128);
    memset(k, 0, sizeof(k));
    /// A key bound to L2 reports the button and full trigger pressure.
    k[SDL_SCANCODE_Q] = 1;
    s = sample();
    assert(s.buttons == CELL_PAD_CTRL_L2 && s.l2 == 255);
    memset(k, 0, sizeof(k));
    /// Remapping: an empty map keeps the defaults; keys beyond two are dropped;
    /// unknown names and reserved keys (Insert, F10) are ignored, so a button
    /// given only invalid keys keeps its default while a pair keeps its valid
    /// half; unlisted buttons keep their defaults.
    m.configure("", nullptr);
    assert(names(14) == "Space|K");
    m.configure("cross=V|Mouse X1|B,circle=Escape,square=Not A Key,start=Insert,select=F10,"
                "r1=Insert|G,evade_mod=Right Alt", "cross=b,circle=a,l2=x");
    assert(names(14) == "V|Mouse X1" && names(3) == "Escape|Return" && names(0) == "Tab|");
    assert(names(11) == "G|" && names(gowa::evade_mod_slot) == "Right Alt|");
    /// The new bindings work and the replaced default no longer does.
    mouse = SDL_BUTTON(SDL_BUTTON_X1);
    assert(sample().buttons == CELL_PAD_CTRL_CROSS);
    mouse = 0;
    k[SDL_SCANCODE_SPACE] = 1;
    assert(!(sample().buttons & CELL_PAD_CTRL_CROSS));  // old binding removed
    k[SDL_SCANCODE_V] = k[SDL_SCANCODE_ESCAPE] = 1;
    assert((sample().buttons & (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_CIRCLE)) ==
           (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_CIRCLE));
    assert(names(15) == "Mouse Left|J");
    memset(k, 0, sizeof(k));
    /// Controller input: the pad map sends B to Cross, axes map to 0..255, a
    /// small deflection inside the dead zone stays centered, and the left
    /// trigger presses L2.
    b[SDL_CONTROLLER_BUTTON_B] = 1;
    a[SDL_CONTROLLER_AXIS_LEFTX] = -32768;
    a[SDL_CONTROLLER_AXIS_LEFTY] = 32767;
    a[SDL_CONTROLLER_AXIS_RIGHTX] = 5000;
    a[SDL_CONTROLLER_AXIS_TRIGGERLEFT] = 32767;
    s = sample();
    assert(s.buttons == (CELL_PAD_CTRL_CROSS | CELL_PAD_CTRL_L2));
    assert(s.lx == 0 && s.ly == 255 && s.rx == 128 && s.l2 == 255);
    /// Disabling the keyboard ignores key bindings.
    m.keyboard = false;
    k[SDL_SCANCODE_J] = 1;
    assert(!(sample().buttons & CELL_PAD_CTRL_SQUARE));

    /// Guest ABI through the real cellPad: the last sample is written as
    /// big-endian CellPadData with digital bits, pressure and stick values; a
    /// released sample clears them; only port 0 reports connected; and
    /// cellPadSetActDirect forwards the actuator values to the rumble callback.
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
    /// Optional device test: SDL virtual controller -> window-thread adapter ->
    /// guest port 0, through connect, button and axis input, disconnect and
    /// reconnect at a new device index.
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
    puts("PC input: defaults, dual and mouse bindings, evade modifier, reserved keys, focus, axes, remap, "
         "PS3 big-endian ABI, pressure, release and rumble OK");
}
