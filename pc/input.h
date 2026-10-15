#pragma once
#include <SDL2/SDL.h>
#include "cellPad_host.h"
#include <array>
#include <string>
#include <vector>

/// PC input adapter for the cellPad HLE library: turns keyboard, mouse and an
/// SDL game controller into the single DualShock 3 sample the guest reads on
/// pad port 0. Slot indices used throughout match the cellPad digital button
/// bits (`CELL_PAD_CTRL_*`): 0 select, 1 L3, 2 R3, 3 start, 4 up, 5 right,
/// 6 down, 7 left, 8 L2, 9 R2, 10 L1, 11 R1, 12 triangle, 13 circle, 14 cross,
/// 15 square. Slot 16 is the keyboard-only `evade_mod` modifier.
namespace gowa {
/// Raw state of the physical SDL controller, captured each input tick for the
/// overlay menu's gamepad navigation (independent of the guest mapping).
struct PadState {
    bool connected = false;
    /// SDL_GameControllerGetButton() per SDL button index (0 or 1).
    std::array<Uint8, SDL_CONTROLLER_BUTTON_MAX> buttons{};
    /// SDL_GameControllerGetAxis() per SDL axis index (-32768..32767; triggers 0..32767).
    std::array<Sint16, SDL_CONTROLLER_AXIS_MAX> axes{};
};
/// Human-readable description of one slot's bindings, for display.
struct BindingInfo {
    /// Slot name (`cross`, `l2`, ...); the overlay replaces it with a localized label.
    std::string button;
    /// Keyboard/mouse bindings joined with ` | `, or `-` when keyboard input is disabled.
    std::string keys;
    /// SDL game controller button name, `lefttrigger`/`righttrigger` for L2/R2,
    /// or empty when the slot has no controller binding.
    std::string pad;
};
/// Source type of a keyboard/mouse binding.
enum class BindingKind { none, key, mouse };
/// One keyboard key or mouse button.
struct Binding {
    BindingKind kind = BindingKind::none;
    /// SDL_Scancode for `key`; SDL mouse button number (1 = left .. 5 = X2) for `mouse`.
    int code = 0;
};
/// Up to two alternative keyboard/mouse bindings per slot; either one activates it.
using BindingPair = std::array<Binding, 2>;
/// Slot of the evade modifier: while held, WASD drives the right stick instead
/// of the left one (the game's evade/roll is on the right stick).
constexpr size_t evade_mod_slot = 16;
/// Complete keyboard/mouse/controller mapping and the pure translation from
/// host input state to a CellPadHostSample. Holds no SDL resources, so it can
/// be unit-tested without a window.
struct InputMapping {
    /// Keyboard/mouse bindings for slots 0..16.
    std::array<BindingPair, 17> keys;
    /// SDL controller button for slots 0..15. Slots 8 and 9 (L2/R2) are
    /// SDL_CONTROLLER_BUTTON_INVALID because they come from the trigger axes.
    std::array<SDL_GameControllerButton, 16> buttons;
    /// Per-axis (not radial) stick deadzone in raw SDL units, clamped to 0..30000 when used.
    int deadzone = 7849;
    /// When false, keyboard and mouse are ignored entirely.
    bool keyboard = true;
    /// Builds the default layout (WASD/arrows, Space = cross, etc.) and the
    /// standard SDL controller to DualShock 3 button mapping.
    InputMapping();
    /// Overrides bindings from two comma-separated `name=value` lists.
    /// @param key_map  keyboard/mouse entries, e.g. `cross=Space|K`; values are
    ///                 SDL scancode names or `Mouse Left/Middle/Right/X1/X2`,
    ///                 up to two joined with `|`. Insert and F10 are rejected
    ///                 because the overlay reserves them.
    /// @param pad_map  controller entries, e.g. `cross=a`, using SDL game
    ///                 controller button names; L2/R2 cannot be remapped.
    /// Either pointer may be null. Invalid entries are reported on stderr and
    /// leave the previous binding in place.
    void configure(const char* key_map, const char* pad_map);
    /// True if any keyboard/mouse binding of @p slot is down.
    /// @param keyboard_state SDL_GetKeyboardState() array (may be null).
    /// @param mouse_buttons  SDL_GetMouseState() button mask.
    bool held(size_t slot, const Uint8* keyboard_state, Uint32 mouse_buttons) const;
    /// Combines all input sources into one pad sample for the guest.
    /// Buttons are ORed across keyboard/mouse and controller. Controller sticks
    /// and triggers are converted to 0..255 (128 = centre); a trigger past 23
    /// also sets the L2/R2 digital bit. Keyboard WASD/arrow keys then override
    /// the stick axes they touch, and a held L2/R2 key forces full pressure.
    /// When @p focused is false a neutral, connected sample is returned so the
    /// game does not see input meant for another window.
    /// @param pad_buttons per-SDL-button state (may be null).
    /// @param axes        per-SDL-axis state (may be null).
    CellPadHostSample sample(const Uint8* keyboard_state, Uint32 mouse_buttons, const Uint8* pad_buttons,
                            const Sint16* axes, bool focused) const;
};
/// Display name of @p binding: the SDL scancode name, a `Mouse ...` name, or
/// an empty string for an unset binding.
std::string binding_name(const Binding& binding);
}

/// Installs this adapter into cellPad (cellPad_set_host_adapter). Must run
/// before guest threads start; afterwards cellPad reads samples from here
/// instead of opening its own host backend, and forwards rumble requests.
void gowa_input_register();
/// Applies environment overrides and opens the SDL game controller subsystem.
/// Reads `GOWA_KEY_MAP`, `GOWA_PAD_MAP` (see InputMapping::configure),
/// `GOWA_STICK_DEADZONE`, `GOWA_KEYBOARD=0` (disable keyboard/mouse) and
/// `GOWA_RUMBLE=0` (disable vibration). Call once on the window thread after
/// the video backend has initialized SDL.
void gowa_input_start();
/// Polls host input and publishes the next guest sample. Call regularly from
/// the thread that owns the SDL window (the frame clock), never from a guest
/// thread. Handles controller hot-plug (first available controller becomes
/// port 0), applies capture/suppression, stores the raw PadState for the
/// overlay and drives pending rumble. `GOWA_INPUT_TRACE` logs button changes.
void gowa_input_tick();
/// Stops rumble, closes the controller and the SDL controller subsystem, and
/// resets the published sample to neutral. Called before process exit.
void gowa_input_stop();
/// While @p captured is true (the overlay menu is open) the guest receives a
/// neutral sample. Buttons held at release stay masked until they are let go,
/// so closing the menu does not leak a press into the game. Thread-safe.
void gowa_input_set_captured(bool captured);
/// Copy of the most recent raw controller state. Thread-safe.
gowa::PadState gowa_input_pad_state();
/// Describes the current mapping of slots 0..16 for display. Valid after
/// gowa_input_start() has applied the environment overrides.
std::vector<gowa::BindingInfo> gowa_input_bindings();
