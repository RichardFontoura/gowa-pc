KEYSYMS = {
    'space': 'Space', 'Return': 'Return', 'Tab': 'Tab', 'ISO_Left_Tab': 'Tab', 'Escape': 'Escape',
    'BackSpace': 'Backspace', 'Delete': 'Delete', 'Home': 'Home', 'End': 'End',
    'Prior': 'PageUp', 'Next': 'PageDown', 'Up': 'Up', 'Down': 'Down', 'Left': 'Left', 'Right': 'Right',
    'Shift_L': 'Left Shift', 'Shift_R': 'Right Shift', 'Control_L': 'Left Ctrl', 'Control_R': 'Right Ctrl',
    'Alt_L': 'Left Alt', 'Alt_R': 'Right Alt', 'ISO_Level3_Shift': 'Right Alt', 'Meta_L': 'Left Alt',
    'Super_L': 'Left GUI', 'Super_R': 'Right GUI', 'Caps_Lock': 'CapsLock', 'Menu': 'Menu',
    'minus': '-', 'period': '.', 'slash': '/', 'backslash': '\\', 'semicolon': ';',
    'apostrophe': "'", 'grave': '`', 'bracketleft': '[', 'bracketright': ']',
    'KP_Enter': 'Keypad Enter', 'KP_Add': 'Keypad +', 'KP_Subtract': 'Keypad -',
    'KP_Multiply': 'Keypad *', 'KP_Divide': 'Keypad /', 'KP_Decimal': 'Keypad .', 'KP_Delete': 'Keypad .',
    'KP_Insert': 'Keypad 0', 'KP_End': 'Keypad 1', 'KP_Down': 'Keypad 2', 'KP_Next': 'Keypad 3',
    'KP_Left': 'Keypad 4', 'KP_Begin': 'Keypad 5', 'KP_Right': 'Keypad 6', 'KP_Home': 'Keypad 7',
    'KP_Up': 'Keypad 8', 'KP_Prior': 'Keypad 9',
}
MOUSE_BUTTONS = {1: 'Mouse Left', 2: 'Mouse Middle', 3: 'Mouse Right', 8: 'Mouse X1', 9: 'Mouse X2'}


def sdl_key_name(keysym):
    if keysym in KEYSYMS:
        return KEYSYMS[keysym]
    if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        return keysym.upper()
    if keysym.startswith('KP_') and keysym[3:].isdigit():
        return f'Keypad {keysym[3:]}'
    if keysym[:1] == 'F' and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24:
        return keysym
    return None


def sdl_mouse_name(button):
    return MOUSE_BUTTONS.get(button)
