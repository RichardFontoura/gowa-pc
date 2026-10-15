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

PORT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
CONFIG_DIR = Path(os.environ.get('GOWA_CONFIG_DIR') or CONFIG_ROOT / 'gowa-pc')
CONFIG_FILE = CONFIG_DIR / 'settings.json'
GAME_INI = CONFIG_DIR / 'game.ini'
FRAME_CSV = CONFIG_DIR / 'frames.csv'
BACKUP_DIR = CONFIG_DIR / 'savedata_backups'
SAVEDATA_DIR = PORT / 'gamedata/dev_hdd0/home/00000001/savedata'
LEGACY_CONFIG_FILE = None if os.environ.get('GOWA_CONFIG_DIR') else CONFIG_ROOT / 'gowa-recomp' / 'settings.json'
VERSION = 2
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
LEGACY_KEY_DEFAULTS = {
    'cross': 'Space', 'circle': 'Left Shift', 'square': 'E', 'triangle': 'Q', 'l1': '1', 'r1': '3',
    'l2': 'R', 'r2': 'F', 'l3': 'Z', 'r3': 'C', 'start': 'Return', 'select': 'Tab',
    'up': 'I', 'down': 'K', 'left': 'J', 'right': 'L',
}
MOUSE_NAMES = ('Mouse Left', 'Mouse Right', 'Mouse Middle', 'Mouse X1', 'Mouse X2')
RESERVED_KEYS = ('Insert', 'F10')
STICK_KEYS = ('W', 'A', 'S', 'D', 'Up', 'Down', 'Left', 'Right')
MAX_BINDINGS = 2
PAD_NAMES = {
    'playstation': {'a': 'Cross', 'b': 'Circle', 'x': 'Square', 'y': 'Triangle',
                   'leftshoulder': 'L1', 'rightshoulder': 'R1', 'leftstick': 'L3', 'rightstick': 'R3',
                   'start': 'Start / Options', 'back': 'Select / Share', 'dpup': 'Direcional ↑',
                   'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
    'xbox': {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'leftshoulder': 'LB', 'rightshoulder': 'RB',
             'leftstick': 'LS', 'rightstick': 'RS', 'start': 'Menu', 'back': 'View',
             'dpup': 'Direcional ↑', 'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
}
PARALLEL_PRESETS = {'Padrão': '7,13,14,15,16', 'Seguro': ''}
DIAGNOSTICS = {'frame_csv': False, 'texture_compute': True, 'raw_textures': True,
               'tex_hash_full': False, 'vdec_perf': False, 'kern_threads': 6,
               'kern_parallel': PARALLEL_PRESETS['Padrão'], 'flip_min_ms': 250}
DEFAULTS = {'version': VERSION, 'pad_style': 'playstation', 'keyboard': True, 'rumble': True,
            'stick_deadzone': 7849, 'mangohud': False, 'gamemode': False,
            'diagnostics': copy.deepcopy(DIAGNOSTICS), 'controls': {'key': {}, 'pad': {}}}
PARALLEL = re.compile(r'^([0-9]+(,[0-9]+)*)?$')


def migrate(data):
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
    for name in MOUSE_NAMES:
        if value.casefold() == name.casefold():
            return name
    return value


def split_bindings(value):
    return [canonical_key(part.strip()) for part in value.split('|')]


def normalize_binding(name, value):
    if not isinstance(value, str) or any(c in value for c in ',=\n\r'):
        raise ValueError(f'Tecla inválida em {name}: {value!r}')
    parts = split_bindings(value)
    if not 1 <= len(parts) <= MAX_BINDINGS or not all(parts):
        raise ValueError(f'{name}: use uma ou duas teclas separadas por |')
    if len({p.casefold() for p in parts}) != len(parts):
        raise ValueError(f'{name}: a mesma tecla aparece duas vezes')
    for part in parts:
        if part.casefold() in (k.casefold() for k in RESERVED_KEYS):
            raise ValueError(f'{part} está reservada (Insert abre o menu do jogo, F10 liga o contador de FPS).')
    return '|'.join(parts)


def validate(data):
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
    for key in ('frame_csv', 'texture_compute', 'raw_textures', 'tex_hash_full', 'vdec_perf'):
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
    lib = ctypes.CDLL(ctypes.util.find_library('SDL2') or 'libSDL2-2.0.so.0')
    lib.SDL_GetScancodeFromName.argtypes = [ctypes.c_char_p]
    lib.SDL_GetScancodeFromName.restype = ctypes.c_int
    for name, value in data['controls']['key'].items():
        for part in split_bindings(value):
            if part not in MOUSE_NAMES and not lib.SDL_GetScancodeFromName(part.encode()):
                raise ValueError(f'Tecla desconhecida em {name}: {part}')


def duplicate_bindings(keys):
    owners = {}
    for name, value in keys.items():
        for part in split_bindings(value):
            if part:
                owners.setdefault(part.casefold(), (part, []))[1].append(name)
    return {part: names for part, names in owners.values() if len(names) > 1}


def stick_conflicts(keys):
    sticks = {k.casefold() for k in STICK_KEYS}
    return {name: part for name, value in keys.items() for part in split_bindings(value)
            if part.casefold() in sticks}


def load(path=CONFIG_FILE, ini_path=None):
    path = Path(path)
    if path == CONFIG_FILE and not path.exists() and LEGACY_CONFIG_FILE and LEGACY_CONFIG_FILE.exists():
        path = LEGACY_CONFIG_FILE
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    data = validate(raw)
    if isinstance(raw, dict) and raw.get('version') == 1 and raw.get('display_mode') == 'borderless':
        seed_display_mode(ini_path or GAME_INI, 'borderless')
    return data


def seed_display_mode(ini_path, mode):
    ini_path = Path(ini_path)
    text = ini_path.read_text(encoding='utf-8', errors='replace') if ini_path.exists() else ''
    if any((m := game_ini.LINE.match(line)) and m.group(1) == 'display_mode' for line in text.splitlines()):
        return
    game_ini.write(ini_path, {**game_ini.read(ini_path), 'display_mode': mode})


def save(data, path=CONFIG_FILE):
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
    env = {}
    if diagnostics['frame_csv']:
        env['RSX_FRAME_CSV'] = str(frame_csv or FRAME_CSV)
    for key, var in (('texture_compute', 'RSX_VK_TEXTURE_COMPUTE'), ('raw_textures', 'RSX_VK_RAW_TEXTURES')):
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


def game_environment(data, inherited=None, ini=None, ini_path=None):
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
    return bool(which(name))


def game_command(data=None, which=shutil.which):
    command = ['bash', str(PORT / 'scripts/run.sh')]
    if data and data.get('mangohud') and tool_available('mangohud', which):
        command = ['mangohud'] + command
    if data and data.get('gamemode') and tool_available('gamemoderun', which):
        command = ['gamemoderun'] + command
    return command
