#pragma once

/// In-game Dear ImGui overlay for the Vulkan build: a performance readout
/// (F10) and a settings menu (Insert, or R3 + L2 on a controller) drawn on top
/// of each presented frame through the ps3recomp Vulkan backend's overlay hooks.

/// Loads game.ini, creates the ImGui context and registers the overlay hooks
/// with the Vulkan backend. Call once on the frame clock thread after the
/// backend window is open and after gowa_input_start() (it reads the binding
/// list). If registration fails the overlay stays disabled and the other
/// functions become no-ops. When game.ini existed, its display mode, window
/// size and VSync are applied on the next gowa_overlay_tick().
void gowa_overlay_start();
/// Per-iteration housekeeping on the frame clock thread: toggles the menu on
/// the R3 + L2 controller combo, hands controller state to the menu, captures
/// or releases guest input when the menu opens/closes (saving game.ini on
/// close) and applies pending window mode / size / VSync changes through SDL.
void gowa_overlay_tick();
/// Records the time of a guest flip (a frame the game actually finished) in a
/// lock-free ring used for the FPS and frame-time statistics. Called once per
/// presented guest frame; cheap and safe to call before gowa_overlay_start().
void gowa_overlay_note_flip();
