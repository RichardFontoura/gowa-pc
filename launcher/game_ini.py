import os
from pathlib import Path
import re
import tempfile

CORNERS = ('tl', 'tr', 'bl', 'br')
DISPLAY_MODES = ('windowed', 'borderless', 'fullscreen')
OVERLAY_ITEMS = ('fps', 'frametime', 'graph', 'present', 'spu', 'shaders')
WINDOW_PRESETS = ('1280x720', '1600x900', '1920x1080', 'monitor')
DEFAULTS = {'show_fps': False, 'overlay_corner': 'tl', 'overlay_scale': 100, 'overlay_opacity': 80,
            'overlay_items': ['fps', 'frametime'], 'display_mode': 'windowed', 'vsync': True,
            'window_size': '1280x720'}
LINE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=(.*)$')
SIZE = re.compile(r'^[1-9][0-9]{2,4}x[1-9][0-9]{2,4}$')


def decode(key, text):
    text = text.strip()
    if key in ('show_fps', 'vsync'):
        if text not in ('0', '1'):
            raise ValueError(f'{key}: use 0 ou 1')
        return text == '1'
    if key in ('overlay_scale', 'overlay_opacity'):
        low, high = (50, 300) if key == 'overlay_scale' else (0, 100)
        if not text.isdigit() or not low <= int(text) <= high:
            raise ValueError(f'{key}: use um inteiro entre {low} e {high}')
        return int(text)
    if key == 'overlay_items':
        return [item for item in (part.strip() for part in text.split(',')) if item]
    if key == 'overlay_corner' and text in CORNERS:
        return text
    if key == 'display_mode' and text in DISPLAY_MODES:
        return text
    if key == 'window_size' and (text in WINDOW_PRESETS or SIZE.match(text)):
        return text
    raise ValueError(f'{key}: valor inválido {text!r}')


def encode(key, value):
    if isinstance(value, bool):
        return str(int(value))
    if key == 'overlay_items':
        return ','.join(value)
    return str(value)


def validate(values):
    result = {}
    for key, default in DEFAULTS.items():
        result[key] = decode(key, encode(key, values.get(key, default)))
    return result


def read(path):
    values = {key: list(value) if isinstance(value, list) else value for key, value in DEFAULTS.items()}
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        match = LINE.match(line)
        if match and match.group(1) in DEFAULTS:
            try:
                values[match.group(1)] = decode(match.group(1), match.group(2))
            except ValueError:
                pass
    return values


def render(text, values):
    values = validate(values)
    lines, seen = text.splitlines(), set()
    for index, line in enumerate(lines):
        match = LINE.match(line)
        if match and match.group(1) in values:
            key = match.group(1)
            lines[index] = f'{key}={encode(key, values[key])}'
            seen.add(key)
    for key, value in values.items():
        if key not in seen and value != DEFAULTS[key]:
            lines.append(f'{key}={encode(key, value)}')
    return '\n'.join(lines) + '\n' if lines else ''


def write(path, values):
    path = Path(path)
    try:
        old = path.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        old = ''
    new = render(old, values)
    if new == old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.game-ini-', delete=False) as f:
            name = f.name
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return True
