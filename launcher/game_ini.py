"""Read and write ``game.ini``, the option file shared by the launcher and the game.

The game's in-game PC menu and the launcher's "Gráficos" page edit the same
plain ``key=value`` file. This module knows the supported keys, their default
values and their textual encoding, and rewrites the file in place while
keeping unknown lines, comments and the original order intact.
"""

import os
from pathlib import Path
import re
import tempfile

# Overlay anchor corners: top-left, top-right, bottom-left, bottom-right.
CORNERS = ('tl', 'tr', 'bl', 'br')
DISPLAY_MODES = ('windowed', 'borderless', 'fullscreen')
# Items the launcher knows how to toggle; other names in overlay_items are kept as-is.
OVERLAY_ITEMS = ('fps', 'frametime', 'graph', 'present', 'spu', 'shaders', 'video')
# Named window sizes; any WIDTHxHEIGHT string matching SIZE is accepted as well.
WINDOW_PRESETS = ('1280x720', '1600x900', '1920x1080', 'monitor')
# Every supported key with its default value; also defines the key set and value types.
DEFAULTS = {'show_fps': False, 'overlay_corner': 'tl', 'overlay_scale': 100, 'overlay_opacity': 80,
            'overlay_items': ['fps', 'frametime'], 'display_mode': 'windowed', 'vsync': True,
            'window_size': '1280x720'}
# One ``key=value`` line; group 1 is the key, group 2 the raw (unstripped) value.
LINE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=(.*)$')
# Custom window size: two numbers of 3 to 5 digits without a leading zero.
SIZE = re.compile(r'^[1-9][0-9]{2,4}x[1-9][0-9]{2,4}$')


def decode(key, text):
    """Parse the textual value of one ``game.ini`` key into its Python value.

    Booleans are ``0``/``1``, ``overlay_scale`` is an integer in 50..300,
    ``overlay_opacity`` an integer in 0..100, ``overlay_items`` a
    comma-separated list (empty entries dropped, names not checked), and the
    remaining keys must be one of their allowed strings (``window_size`` also
    accepts a custom ``WIDTHxHEIGHT`` matching ``SIZE``).

    Args:
        key: Name of a key from ``DEFAULTS``.
        text: Raw value as written in the file; surrounding whitespace is ignored.

    Returns:
        The decoded value (``bool``, ``int``, ``list`` of ``str`` or ``str``).

    Raises:
        ValueError: If the value is invalid for the key, or the key is unknown.
    """
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
    """Turn a Python value into the text stored in ``game.ini``.

    Booleans become ``0``/``1``, the ``overlay_items`` list is joined with
    commas and anything else goes through ``str``. This is the inverse of
    :func:`decode`.
    """
    if isinstance(value, bool):
        return str(int(value))
    if key == 'overlay_items':
        return ','.join(value)
    return str(value)


def validate(values):
    """Return a complete, normalized option dict built from ``values``.

    Each supported key is taken from ``values`` (or from ``DEFAULTS`` when
    missing), encoded to text and decoded back, so values coming from Tk
    variables such as ``'100'`` for an integer are accepted and normalized.
    Keys not listed in ``DEFAULTS`` are dropped.

    Raises:
        ValueError: If any value is invalid (see :func:`decode`).
    """
    result = {}
    for key, default in DEFAULTS.items():
        result[key] = decode(key, encode(key, values.get(key, default)))
    return result


def read(path):
    """Load the options from ``path``, falling back to defaults.

    A missing file yields a copy of ``DEFAULTS``. Unknown keys and lines with
    invalid values are silently ignored, so a damaged file never prevents the
    launcher from starting; when a key appears more than once the last valid
    line wins.

    Returns:
        A dict with every key from ``DEFAULTS``.
    """
    values ={key: list(value) if isinstance(value, list) else value for key, value in DEFAULTS.items()}
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
    """Produce the new file contents for ``values`` based on the existing ``text``.

    Every existing line of a supported key is rewritten with the new value;
    all other lines (comments, unknown keys, blank lines) are kept unchanged
    and in order. Keys that were absent from ``text`` are appended only when
    their value differs from the default, so the file stays minimal.

    Args:
        text: Current file contents (may be empty).
        values: Options to store; validated with :func:`validate`.

    Returns:
        The new contents ending with a newline, or ``''`` when there is nothing to write.
    """
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
    """Store ``values`` in the ``game.ini`` at ``path`` atomically.

    The new contents come from :func:`render` applied to the current file.
    If they are identical nothing is touched; otherwise the parent directory is
    created, the text is written and fsynced to a temporary ``.game-ini-*``
    file in the same directory and then moved over ``path`` with
    ``os.replace``. The temporary file is removed if anything fails.

    Returns:
        ``True`` if the file was written, ``False`` if it was already up to date.

    Raises:
        ValueError: If ``values`` is invalid.
        OSError: If the file cannot be read or written.
    """
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
