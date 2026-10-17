"""Launcher settings: ``settings.json`` schema, validation and game environment.

``settings.json`` holds the launcher-only options (controls, pad style,
rumble, dead zone, external tools and diagnostic switches). Display and
overlay options live in ``game.ini`` instead (see :mod:`game_ini`). This
module loads, migrates, validates and atomically saves the JSON file, and
turns the validated settings into the environment variables and command
line used to start the game through ``scripts/run.sh``.
"""

import copy
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import game_ini

# Root of the port repository (the parent of launcher/).
PORT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
# GOWA_CONFIG_DIR overrides the whole configuration directory; the game honors it too.
CONFIG_DIR = Path(os.environ.get('GOWA_CONFIG_DIR') or CONFIG_ROOT / 'gowa-pc')
CONFIG_FILE = CONFIG_DIR / 'settings.json'
GAME_INI = CONFIG_DIR / 'game.ini'
FRAME_CSV = CONFIG_DIR / 'frames.csv'
BACKUP_DIR = CONFIG_DIR / 'savedata_backups'
SHADER_CACHE_DIR = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'gowa-pc'
# Files inside SHADER_CACHE_DIR removed by clear_shader_cache(), besides spirv/*.spv.
SHADER_CACHE_FILES = ('pipelines.vkcache', 'pipelines.manifest')
# The game's save data inside the port's virtual dev_hdd0 tree.
SAVEDATA_DIR = PORT / 'gamedata/dev_hdd0/home/00000001/savedata'
# Current settings.json schema version; version 1 files are upgraded by migrate().
VERSION = 2
# One row per game button: (id, label shown in the UI, default keys joined by '|',
# default SDL game controller button or None when the pad mapping is fixed).
CONTROLS = [
    ('cross', 'Cross / X', 'Space|K', 'a'), ('circle', 'Circle / O', 'F|L', 'b'),
    ('square', 'Square / □', 'Mouse Left|J', 'x'), ('triangle', 'Triangle / △', 'Mouse Right|I', 'y'),
    ('l1', 'L1', 'Left Shift', 'leftshoulder'), ('r1', 'R1', 'E', 'rightshoulder'),
    ('l2', 'L2', 'Q', None), ('r2', 'R2', 'R', None),
    ('l3', 'L3', 'Left Ctrl', 'leftstick'), ('r3', 'R3', 'Mouse Middle|C', 'rightstick'),
    ('start', 'Start', 'Escape|Return', 'start'), ('select', 'Select', 'Tab', 'back'),
    ('up', 'Direcional ↑', '1', 'dpup'), ('right', 'Direcional →', '2', 'dpright'),
    ('down', 'Direcional ↓', '3', 'dpdown'), ('left', 'Direcional ←', '4', 'dpleft'),
    ('evade_mod', 'Esquiva (WASD vira analógico direito)', 'Left Alt', None),
]
KEY_DEFAULTS = {name: key for name, _, key, _ in CONTROLS}
# Default keyboard map of settings version 1, used by migrate() to detect untouched maps.
LEGACY_KEY_DEFAULTS = {
    'cross': 'Space', 'circle': 'Left Shift', 'square': 'E', 'triangle': 'Q', 'l1': '1', 'r1': '3',
    'l2': 'R', 'r2': 'F', 'l3': 'Z', 'r3': 'C', 'start': 'Return', 'select': 'Tab',
    'up': 'I', 'down': 'K', 'left': 'J', 'right': 'L',
}
# Mouse buttons accepted as bindings; they are not SDL key names.
MOUSE_NAMES = ('Mouse Left', 'Mouse Right', 'Mouse Middle', 'Mouse X1', 'Mouse X2')
RESERVED_KEYS = ('Insert', 'F10', 'F9')
# Keys hard-wired to the analog sticks (WASD left stick, arrows right stick).
STICK_KEYS = ('W', 'A', 'S', 'D', 'Up', 'Down', 'Left', 'Right')
# Maximum number of keys/mouse buttons bound to one game button.
MAX_BINDINGS = 2
# Display names of SDL game controller buttons, per pad style. The keys of the
# 'playstation' table are also the set of valid pad binding values.
PAD_NAMES = {
    'playstation': {'a': 'Cross', 'b': 'Circle', 'x': 'Square', 'y': 'Triangle',
                   'leftshoulder': 'L1', 'rightshoulder': 'R1', 'leftstick': 'L3', 'rightstick': 'R3',
                   'start': 'Start / Options', 'back': 'Select / Share', 'dpup': 'Direcional ↑',
                   'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
    'xbox': {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'leftshoulder': 'LB', 'rightshoulder': 'RB',
             'leftstick': 'LS', 'rightstick': 'RS', 'start': 'Menu', 'back': 'View',
             'dpup': 'Direcional ↑', 'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
}
# Named values for diagnostics.kern_parallel (SPURS workloads allowed to run in parallel).
PARALLEL_PRESETS = {'Padrão': '7,13,14,15,16', 'Seguro': ''}
# Diagnostic switches and their defaults; the defaults match scripts/run.sh, so a value
# only becomes an environment variable when it differs (see diagnostic_environment()).
DIAGNOSTICS = {'frame_csv': False, 'texture_compute': True, 'raw_textures': True,
               'tex_hash_full': False, 'vdec_perf': False, 'kern_threads': 6,
               'kern_parallel': PARALLEL_PRESETS['Padrão'], 'flip_min_ms': 250,
               'shader_cache': True, 'async_shaders': True, 'shader_warmup': True}
DEFAULTS = {'version': VERSION, 'pad_style': 'playstation', 'keyboard': True, 'rumble': True,
            'stick_deadzone': 7849, 'mangohud': False, 'gamemode': False,
            'diagnostics': copy.deepcopy(DIAGNOSTICS), 'controls': {'key': {}, 'pad': {}}}
# Comma-separated list of non-negative integers, or the empty string.
PARALLEL = re.compile(r'^([0-9]+(,[0-9]+)*)?$')


def migrate(data):
    """Upgrade a version 1 settings dict to the current schema.

    Data of any other version is returned unchanged (as a deep copy). For
    version 1: the version is bumped, the obsolete top-level ``display_mode``
    is dropped (it now lives in ``game.ini``, see :func:`load`), a top-level
    ``flip_min_ms`` is moved into ``diagnostics`` unless already set there,
    and a keyboard map that still equals the old defaults (or is empty) is
    replaced with the current ``KEY_DEFAULTS``.

    Returns:
        A new dict; ``data`` is not modified.
    """
    data = copy.deepcopy(data)
    if data.get('version') != 1:
        return data
    data['version'] = VERSION
    data.pop('display_mode', None)
    diagnostics = data.setdefault('diagnostics', {})
    if isinstance(diagnostics, dict) and 'flip_min_ms' in data:
        diagnostics.setdefault('flip_min_ms', data.pop('flip_min_ms'))
    controls = data.get('controls')
    keys = controls.get('key') if isinstance(controls, dict) else None
    if isinstance(keys, dict) and all(LEGACY_KEY_DEFAULTS.get(k) == v for k, v in keys.items()):
        controls['key'] = dict(KEY_DEFAULTS)
    return data


def canonical_key(value):
    """Return the canonical spelling of a mouse binding name, matched case-insensitively.

    Anything that is not one of ``MOUSE_NAMES`` is returned unchanged.
    """
    for name in MOUSE_NAMES:
        if value.casefold() == name.casefold():
            return name
    return value


def split_bindings(value):
    """Split a ``'Key1|Key2'`` binding into stripped, canonicalized parts (empty parts kept)."""
    return [canonical_key(part.strip()) for part in value.split('|')]


def normalize_binding(name, value):
    """Validate one keyboard binding and return it in normalized form.

    The binding must be a string with one or two non-empty parts separated
    by ``|``, without ``,``, ``=`` or newlines (they would break the
    ``GOWA_KEY_MAP`` encoding), without the same key twice and without a
    key from ``RESERVED_KEYS``. Whether each part is a real SDL key name is
    checked separately by :func:`validate_keys`.

    Args:
        name: Game button id, used in error messages.
        value: Binding as entered, e.g. ``'space | mouse left'``.

    Returns:
        The parts joined with ``|`` after stripping and mouse-name canonicalization.

    Raises:
        ValueError: If any rule above is violated.
    """
    if not isinstance(value, str) or any(c in value for c in ',=\n\r'):
        raise ValueError(f'Tecla inválida em {name}: {value!r}')
    parts = split_bindings(value)
    if not 1 <= len(parts) <= MAX_BINDINGS or not all(parts):
        raise ValueError(f'{name}: use uma ou duas teclas separadas por |')
    if len({p.casefold() for p in parts}) != len(parts):
        raise ValueError(f'{name}: a mesma tecla aparece duas vezes')
    for part in parts:
        if part.casefold() in (k.casefold() for k in RESERVED_KEYS):
            raise ValueError(f'{part} está reservada (Insert abre o menu do jogo, F10 liga o contador de FPS, F9 marca início/fim de cena).')
    return '|'.join(parts)


def validate(data):
    """Check a settings dict and return a complete, normalized copy.

    The input is migrated (:func:`migrate`) and laid over ``DEFAULTS`` (a
    shallow merge: a ``diagnostics`` or ``controls`` dict replaces the
    default one, then missing diagnostic keys are filled in). Types and
    ranges of every option are checked, ``kern_parallel`` has its spaces
    removed and must list workload numbers 0..31, keyboard bindings are
    normalized with :func:`normalize_binding`, and pad bindings must be SDL
    game controller button names (keys of ``PAD_NAMES['playstation']``).
    Only game buttons with a pad default in ``CONTROLS`` may have a pad
    binding. Unknown top-level keys are kept as-is.

    Returns:
        A new validated dict; ``data`` is not modified.

    Raises:
        ValueError: With a user-facing (Portuguese) message for the first problem found.
    """
    if not isinstance(data, dict):
        raise ValueError('A configuração deve ser um objeto JSON.')
    data = migrate(data)
    result = copy.deepcopy(DEFAULTS)
    result.update(copy.deepcopy(data))
    if type(result['version']) is not int or result['version'] != VERSION:
        raise ValueError('Versão de configuração incompatível; arquivo preservado.')
    if not isinstance(result['pad_style'], str) or result['pad_style'] not in PAD_NAMES:
        raise ValueError('Valor inválido: pad_style')
    for key in ('keyboard', 'rumble', 'mangohud', 'gamemode'):
        if type(result[key]) is not bool:
            raise ValueError(f'Valor inválido: {key}')
    if type(result['stick_deadzone']) is not int or not 0 <= result['stick_deadzone'] <= 30000:
        raise ValueError('stick_deadzone: use um inteiro entre 0 e 30000.')
    diagnostics = result['diagnostics']
    if not isinstance(diagnostics, dict) or set(diagnostics) - set(DIAGNOSTICS):
        raise ValueError('Opções de diagnóstico inválidas.')
    result['diagnostics'] = {**DIAGNOSTICS, **diagnostics}
    diagnostics = result['diagnostics']
    for key in ('frame_csv', 'texture_compute', 'raw_textures', 'tex_hash_full', 'vdec_perf',
                'shader_cache', 'async_shaders', 'shader_warmup'):
        if type(diagnostics[key]) is not bool:
            raise ValueError(f'Valor inválido: {key}')
    for key, low, high in (('kern_threads', 1, 8), ('flip_min_ms', 0, 5000)):
        if type(diagnostics[key]) is not int or not low <= diagnostics[key] <= high:
            raise ValueError(f'{key}: use um inteiro entre {low} e {high}.')
    parallel = diagnostics['kern_parallel']
    if not isinstance(parallel, str):
        raise ValueError('kern_parallel: use números separados por vírgula.')
    parallel = parallel.replace(' ', '')
    if not PARALLEL.match(parallel) or any(int(w) > 31 for w in filter(None, parallel.split(','))):
        raise ValueError('Workloads paralelos: use números de 0 a 31 separados por vírgula, ou deixe vazio.')
    diagnostics['kern_parallel'] = parallel
    controls = result['controls']
    if not isinstance(controls, dict) or set(controls) - {'key', 'pad'}:
        raise ValueError('Mapeamento de controles inválido.')
    for kind in ('key', 'pad'):
        choices = controls.setdefault(kind, {})
        allowed = {row[0] for row in CONTROLS if kind == 'key' or row[3]}
        if not isinstance(choices, dict) or set(choices) - allowed:
            raise ValueError(f'Mapeamento inválido: {kind}')
        for name, value in choices.items():
            if kind == 'key':
                choices[name] = normalize_binding(name, value)
                continue
            if not isinstance(value, str) or value.strip() not in PAD_NAMES['playstation']:
                raise ValueError(f'Botão desconhecido: {value}')
            choices[name] = value.strip()
    return result


def validate_keys(data):
    """Check that every keyboard binding names a key SDL recognizes.

    Loads the SDL2 shared library through ``ctypes`` and asks
    ``SDL_GetScancodeFromName`` for each bound key; a result of 0 (unknown
    scancode) is an error. Mouse button names are accepted without asking
    SDL. ``data`` must already be validated by :func:`validate`.

    Raises:
        ValueError: For the first unknown key name.
        OSError: If the SDL2 library cannot be loaded.
    """
    lib =ctypes.CDLL(ctypes.util.find_library('SDL2') or 'libSDL2-2.0.so.0')
    lib.SDL_GetScancodeFromName.argtypes = [ctypes.c_char_p]
    lib.SDL_GetScancodeFromName.restype = ctypes.c_int
    for name, value in data['controls']['key'].items():
        for part in split_bindings(value):
            if part not in MOUSE_NAMES and not lib.SDL_GetScancodeFromName(part.encode()):
                raise ValueError(f'Tecla desconhecida em {name}: {part}')


def duplicate_bindings(keys):
    """Find keys bound to more than one game button.

    Args:
        keys: Mapping of game button id to binding string (``'A|B'``).

    Returns:
        ``{key: [button ids]}`` for every key (compared case-insensitively)
        used by two or more buttons, using the spelling of its first occurrence.
    """
    owners = {}
    for name, value in keys.items():
        for part in split_bindings(value):
            if part:
                owners.setdefault(part.casefold(), (part, []))[1].append(name)
    return {part: names for part, names in owners.values() if len(names) > 1}


def stick_conflicts(keys):
    """Find bindings that use a key already wired to an analog stick.

    Returns:
        ``{button id: key}`` for each binding part found in ``STICK_KEYS``;
        if a button has two such keys only the last one is reported.
    """
    sticks = {k.casefold() for k in STICK_KEYS}
    return {name: part for name, value in keys.items() for part in split_bindings(value)
            if part.casefold() in sticks}


def load(path=CONFIG_FILE, ini_path=None):
    """Load and validate ``settings.json``.

    A missing file yields a copy of ``DEFAULTS``. A version 1 file that selected ``borderless`` display mode
    has that choice carried over into ``game.ini`` via
    :func:`seed_display_mode` (the file on disk is not rewritten here).

    Args:
        path: Settings file to read.
        ini_path: ``game.ini`` that receives a migrated display mode; defaults to ``GAME_INI``.

    Returns:
        The validated settings dict.

    Raises:
        ValueError: If the JSON is malformed or fails :func:`validate`.
        OSError: If a file cannot be read or ``game.ini`` cannot be written.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    data = validate(raw)
    if isinstance(raw, dict) and raw.get('version') == 1 and raw.get('display_mode') == 'borderless':
        seed_display_mode(ini_path or GAME_INI, 'borderless')
    return data


def seed_display_mode(ini_path, mode):
    """Write ``display_mode=mode`` to ``game.ini`` unless the file already sets it.

    Any existing ``display_mode`` line, even an invalid one, counts as a user
    choice and leaves the file untouched; otherwise the other options are
    preserved and the file is rewritten with :func:`game_ini.write`.
    """
    ini_path = Path(ini_path)
    text = ini_path.read_text(encoding='utf-8', errors='replace') if ini_path.exists() else ''
    if any((m := game_ini.LINE.match(line)) and m.group(1) == 'display_mode' for line in text.splitlines()):
        return
    game_ini.write(ini_path, {**game_ini.read(ini_path), 'display_mode': mode})


def save(data, path=CONFIG_FILE):
    """Validate ``data`` and write it to ``path`` as JSON atomically.

    Runs :func:`validate` and :func:`validate_keys`, creates the parent
    directory, writes indented UTF-8 JSON to a temporary ``.settings-*``
    file in the same directory, fsyncs it and moves it over ``path`` with
    ``os.replace``. The temporary file is removed if anything fails.

    Raises:
        ValueError: If the settings are invalid; nothing is written.
        OSError: If SDL2 cannot be loaded or the file cannot be written.
    """
    data = validate(data)
    validate_keys(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.settings-', delete=False) as f:
            name = f.name
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def diagnostic_environment(diagnostics, frame_csv=None):
    """Translate the diagnostic switches into environment variables.

    Only values that differ from the runtime's defaults produce a variable:
    ``frame_csv`` sets ``RSX_FRAME_CSV`` to the CSV path; disabled
    ``texture_compute``, ``raw_textures``, ``shader_cache``, ``async_shaders``
    or ``shader_warmup`` set their variable to ``0``; enabled
    ``tex_hash_full`` or ``vdec_perf`` set theirs to ``1``; and
    ``kern_threads``, ``kern_parallel`` and ``flip_min_ms`` are exported
    only when they differ from ``DIAGNOSTICS``.

    Args:
        diagnostics: The validated ``diagnostics`` dict.
        frame_csv: Path for the frame-time CSV; defaults to ``FRAME_CSV``.

    Returns:
        A dict of variable name to string value.
    """
    env = {}
    if diagnostics['frame_csv']:
        env['RSX_FRAME_CSV'] = str(frame_csv or FRAME_CSV)
    for key, var in (('texture_compute', 'RSX_VK_TEXTURE_COMPUTE'), ('raw_textures', 'RSX_VK_RAW_TEXTURES'),
                     ('shader_cache', 'GOWA_SHADER_CACHE'), ('async_shaders', 'GOWA_ASYNC_SHADERS'),
                     ('shader_warmup', 'GOWA_SHADER_WARMUP')):
        if not diagnostics[key]:
            env[var] = '0'
    for key, var in (('tex_hash_full', 'RSX_TEX_HASH_FULL'), ('vdec_perf', 'VDEC_PERF')):
        if diagnostics[key]:
            env[var] = '1'
    for key, var in (('kern_threads', 'SPURS_KERN_THREADS'), ('kern_parallel', 'SPURS_KERN_PARALLEL'),
                     ('flip_min_ms', 'GCM_FLIP_MIN_MS')):
        if diagnostics[key] != DIAGNOSTICS[key]:
            env[var] = str(diagnostics[key])
    return env


def clear_shader_cache(path=None):
    """Delete the game's on-disk shader cache.

    Removes the pipeline cache files listed in ``SHADER_CACHE_FILES`` and
    every ``spirv/*.spv`` under ``path`` (default ``SHADER_CACHE_DIR``).
    Files that are already gone are skipped; directories are left in place.

    Returns:
        The number of files actually removed.

    Raises:
        OSError: If a file exists but cannot be removed.
    """
    path =Path(path or SHADER_CACHE_DIR)
    victims = [path / name for name in SHADER_CACHE_FILES] + sorted((path / 'spirv').glob('*.spv'))
    removed = 0
    for victim in victims:
        try:
            victim.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def game_environment(data, inherited=None, ini=None, ini_path=None):
    """Build the full environment for starting the game.

    Starts from ``inherited`` (default: the launcher's own ``os.environ``)
    and sets the control maps (``GOWA_KEY_MAP``/``GOWA_PAD_MAP`` as
    ``id=binding`` pairs joined by commas), keyboard, rumble and dead zone
    switches, the ``game.ini`` path, the Vulkan window flags (fullscreen
    whenever ``display_mode`` is not ``windowed``) and the window title.
    Diagnostic variables from :func:`diagnostic_environment` are added with
    ``setdefault``, so a value already present in the inherited environment
    wins; the frame CSV is placed next to ``game.ini``.

    Args:
        data: Settings dict; validated with :func:`validate` and :func:`validate_keys`.
        inherited: Base environment mapping; it is copied, not modified.
        ini: ``game.ini`` options to use instead of reading ``ini_path``; validated.
        ini_path: ``game.ini`` location; defaults to ``GAME_INI``.

    Returns:
        A new environment dict.

    Raises:
        ValueError: If the settings or the ``game.ini`` options are invalid.
        OSError: If SDL2 cannot be loaded for key validation.
    """
    data = validate(data)
    validate_keys(data)
    ini_path = Path(ini_path or GAME_INI)
    ini = game_ini.read(ini_path) if ini is None else game_ini.validate(ini)
    env = dict(os.environ if inherited is None else inherited)
    for kind in ('key', 'pad'):
        env[f'GOWA_{kind.upper()}_MAP'] = ','.join(
            f'{name}={value}' for name, value in data['controls'][kind].items())
    env.update(GOWA_KEYBOARD=str(int(data['keyboard'])), GOWA_RUMBLE=str(int(data['rumble'])),
               GOWA_STICK_DEADZONE=str(data['stick_deadzone']), GOWA_GAME_INI=str(ini_path),
               PS3RECOMP_VK_WINDOW='1', PS3RECOMP_VK_KEEP_ESCAPE='1',
               PS3RECOMP_VK_FULLSCREEN=str(int(ini['display_mode'] != 'windowed')),
               PS3_TITLE='God of War: Ascension')
    for var, value in diagnostic_environment(data['diagnostics'],
                                             ini_path.parent / FRAME_CSV.name).items():
        env.setdefault(var, value)
    return env


def tool_available(name, which=shutil.which):
    """Return ``True`` if the executable ``name`` is found on ``PATH`` (``which`` is injectable for tests)."""
    return bool(which(name))


def game_command(data=None, which=shutil.which):
    """Return the argv that starts the game.

    The base command is ``bash scripts/run.sh``. When enabled in ``data`` and
    installed, it is wrapped by ``mangohud`` and then by ``gamemoderun``
    (so ``gamemoderun`` is outermost). Missing tools are skipped silently.

    Args:
        data: Settings dict (only ``mangohud`` and ``gamemode`` are read); ``None`` means no wrappers.
        which: ``shutil.which``-compatible lookup, injectable for tests.
    """
    command = ['bash', str(PORT / 'scripts/run.sh')]
    if data and data.get('mangohud') and tool_available('mangohud', which):
        command = ['mangohud'] + command
    if data and data.get('gamemode') and tool_available('gamemoderun', which):
        command = ['gamemoderun'] + command
    return command
