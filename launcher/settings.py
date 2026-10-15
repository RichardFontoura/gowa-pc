"""Versioned PC settings. No dependency on Tk or the gow3_pc checkout."""
import copy
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import tempfile

PORT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
CONFIG_DIR = Path(os.environ.get('GOWA_CONFIG_DIR') or CONFIG_ROOT / 'gowa-pc')
CONFIG_FILE = CONFIG_DIR / 'settings.json'
LEGACY_CONFIG_FILE = None if os.environ.get('GOWA_CONFIG_DIR') else CONFIG_ROOT / 'gowa-recomp' / 'settings.json'
CONTROLS = [
    ('cross', 'Cross / X', 'Space', 'a'), ('circle', 'Circle / O', 'Left Shift', 'b'),
    ('square', 'Square / □', 'E', 'x'), ('triangle', 'Triangle / △', 'Q', 'y'),
    ('l1', 'L1', '1', 'leftshoulder'), ('r1', 'R1', '3', 'rightshoulder'),
    ('l2', 'L2', 'R', None), ('r2', 'R2', 'F', None),
    ('l3', 'L3', 'Z', 'leftstick'), ('r3', 'R3', 'C', 'rightstick'),
    ('start', 'Start', 'Return', 'start'), ('select', 'Select', 'Tab', 'back'),
    ('up', 'Direcional ↑', 'I', 'dpup'), ('down', 'Direcional ↓', 'K', 'dpdown'),
    ('left', 'Direcional ←', 'J', 'dpleft'), ('right', 'Direcional →', 'L', 'dpright'),
]
PAD_NAMES = {
    'playstation': {'a': 'Cross', 'b': 'Circle', 'x': 'Square', 'y': 'Triangle',
                   'leftshoulder': 'L1', 'rightshoulder': 'R1', 'leftstick': 'L3', 'rightstick': 'R3',
                   'start': 'Start / Options', 'back': 'Select / Share', 'dpup': 'Direcional ↑',
                   'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
    'xbox': {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'leftshoulder': 'LB', 'rightshoulder': 'RB',
             'leftstick': 'LS', 'rightstick': 'RS', 'start': 'Menu', 'back': 'View',
             'dpup': 'Direcional ↑', 'dpdown': 'Direcional ↓', 'dpleft': 'Direcional ←', 'dpright': 'Direcional →'},
}
DEFAULTS = {'version': 1, 'display_mode': 'windowed', 'pad_style': 'playstation',
            'keyboard': True, 'rumble': True, 'stick_deadzone': 7849,
            'flip_min_ms': 250, 'controls': {'key': {}, 'pad': {}}}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('A configuração deve ser um objeto JSON.')
    result = copy.deepcopy(DEFAULTS)
    result.update(copy.deepcopy(data))
    if type(result['version']) is not int or result['version'] != 1:
        raise ValueError('Versão de configuração incompatível; arquivo preservado.')
    for key, values in (('display_mode', ('windowed', 'borderless')), ('pad_style', PAD_NAMES)):
        if not isinstance(result[key], str) or result[key] not in values:
            raise ValueError(f'Valor inválido: {key}')
    for key in ('keyboard', 'rumble'):
        if type(result[key]) is not bool:
            raise ValueError(f'Valor inválido: {key}')
    for key, low, high in (('stick_deadzone', 0, 30000), ('flip_min_ms', 0, 5000)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key}: use um inteiro entre {low} e {high}.')
    controls = result['controls']
    if not isinstance(controls, dict) or set(controls) - {'key', 'pad'}:
        raise ValueError('Mapeamento de controles inválido.')
    for kind in ('key', 'pad'):
        choices = controls.setdefault(kind, {})
        allowed = {row[0] for row in CONTROLS if kind == 'key' or row[3]}
        if not isinstance(choices, dict) or set(choices) - allowed:
            raise ValueError(f'Mapeamento inválido: {kind}')
        for name, value in choices.items():
            if not isinstance(value, str) or not value.strip() or any(c in value for c in ',=\n\r'):
                raise ValueError(f'Tecla ou botão inválido: {name}')
            choices[name] = value.strip()
            if kind == 'pad' and value not in PAD_NAMES['playstation']:
                raise ValueError(f'Botão desconhecido: {value}')
            if kind == 'key' and value.casefold() == 'insert':
                raise ValueError('Insert está reservado para o futuro menu de configuração PC.')
    return result


def validate_keys(data):
    """Use the same SDL2 key names as the native adapter, without opening a window."""
    lib = ctypes.CDLL(ctypes.util.find_library('SDL2') or 'libSDL2-2.0.so.0')
    lib.SDL_GetScancodeFromName.argtypes = [ctypes.c_char_p]
    lib.SDL_GetScancodeFromName.restype = ctypes.c_int
    for name, value in data['controls']['key'].items():
        if not lib.SDL_GetScancodeFromName(value.encode()):
            raise ValueError(f'Tecla desconhecida em {name}: {value}')


def load(path=CONFIG_FILE):
    path = Path(path)
    if path == CONFIG_FILE and not path.exists() and LEGACY_CONFIG_FILE and LEGACY_CONFIG_FILE.exists():
        path = LEGACY_CONFIG_FILE
    try:
        return validate(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)


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


def game_environment(data, inherited=None):
    data = validate(data)
    validate_keys(data)
    env = dict(os.environ if inherited is None else inherited)
    for kind in ('key', 'pad'):
        env[f'GOWA_{kind.upper()}_MAP'] = ','.join(
            f'{name}={value}' for name, value in data['controls'][kind].items())
    env.update(GOWA_KEYBOARD=str(int(data['keyboard'])), GOWA_RUMBLE=str(int(data['rumble'])),
               GOWA_STICK_DEADZONE=str(data['stick_deadzone']),
               PS3RECOMP_VK_WINDOW='1', PS3RECOMP_VK_KEEP_ESCAPE='1',
               PS3RECOMP_VK_FULLSCREEN=str(int(data['display_mode'] == 'borderless')),
               PS3_TITLE='God of War: Ascension')
    env.setdefault('GCM_FLIP_MIN_MS', str(data['flip_min_ms']))
    return env


def game_command():
    return ['bash', str(PORT / 'scripts/run.sh')]
