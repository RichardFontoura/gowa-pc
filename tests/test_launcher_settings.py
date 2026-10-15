import copy
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import game_ini
import gowa_launcher
import keynames
import save_backup
import settings

LEGACY = {'version': 1, 'display_mode': 'windowed', 'pad_style': 'playstation', 'keyboard': True,
          'rumble': True, 'stick_deadzone': 7849, 'flip_min_ms': 250,
          'controls': {'key': dict(settings.LEGACY_KEY_DEFAULTS), 'pad': {'cross': 'a'}}}


class MigrationTests(unittest.TestCase):
    def test_untouched_v1_keymap_gets_new_defaults(self):
        data = settings.validate(LEGACY)
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['controls']['key'], settings.KEY_DEFAULTS)
        self.assertEqual(data['controls']['key']['cross'], 'Space|K')
        self.assertEqual(data['controls']['key']['evade_mod'], 'Left Alt')
        self.assertEqual(data['controls']['pad'], {'cross': 'a'})
        self.assertNotIn('display_mode', data)
        self.assertNotIn('flip_min_ms', data)
        self.assertEqual(data['diagnostics']['flip_min_ms'], 250)

    def test_partial_and_empty_untouched_maps_migrate(self):
        for keys in ({}, {'cross': 'Space', 'start': 'Return'}):
            raw = {**LEGACY, 'controls': {'key': keys, 'pad': {}}}
            self.assertEqual(settings.validate(raw)['controls']['key'], settings.KEY_DEFAULTS)

    def test_customized_v1_keymap_is_kept(self):
        raw = copy.deepcopy(LEGACY)
        raw['controls']['key']['cross'] = 'V'
        raw['flip_min_ms'] = 500
        data = settings.validate(raw)
        self.assertEqual(data['controls']['key'], {**settings.LEGACY_KEY_DEFAULTS, 'cross': 'V'})
        self.assertEqual(data['diagnostics']['flip_min_ms'], 500)

    def test_v1_borderless_seeds_game_ini_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path, ini = Path(folder) / 'settings.json', Path(folder) / 'game.ini'
            path.write_text(json.dumps({**LEGACY, 'display_mode': 'borderless'}))
            settings.load(path, ini)
            self.assertEqual(game_ini.read(ini)['display_mode'], 'borderless')
            ini.write_text('display_mode=windowed\n')
            settings.load(path, ini)
            self.assertEqual(ini.read_text(), 'display_mode=windowed\n')

    def test_saved_file_is_v2_and_reloads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text(json.dumps(LEGACY))
            data = settings.load(path, Path(folder) / 'game.ini')
            settings.save(data, path)
            self.assertEqual(json.loads(path.read_text())['version'], 2)
            self.assertEqual(settings.load(path), data)


class KeymapTests(unittest.TestCase):
    def keys(self, **keys):
        return {**settings.DEFAULTS, 'controls': {'key': keys, 'pad': {}}}

    def test_accepts_pairs_mouse_and_single_keys(self):
        data = settings.validate(self.keys(cross='Space | K', square='mouse left|J', l1='Left Shift',
                                           evade_mod='Left Alt', r3='Mouse X2'))
        settings.validate_keys(data)
        self.assertEqual(data['controls']['key']['cross'], 'Space|K')
        self.assertEqual(data['controls']['key']['square'], 'Mouse Left|J')
        settings.validate_keys(settings.validate(self.keys(**settings.KEY_DEFAULTS)))

    def test_rejects_bad_bindings(self):
        for value in ('A|B|C', 'K|', '|K', 'K|k', 'Insert', 'J|F10', 'f10', 'V,start=Q', 'a=b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.validate(self.keys(cross=value))
        with self.assertRaises(ValueError):
            settings.validate(self.keys(touchpad='K'))
        for value in ('Mouse Wheel', 'Space|Not A Key'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.validate_keys(settings.validate(self.keys(cross=value)))

    def test_duplicates_and_stick_conflicts(self):
        keys = {'cross': 'Space|K', 'down': 'k', 'square': 'W', 'circle': 'F'}
        self.assertEqual(settings.duplicate_bindings(keys), {'K': ['cross', 'down']})
        self.assertEqual(settings.stick_conflicts(keys), {'square': 'W'})
        self.assertEqual(settings.duplicate_bindings(settings.KEY_DEFAULTS), {})
        self.assertEqual(settings.stick_conflicts(settings.KEY_DEFAULTS), {})

    def test_tk_names_map_to_sdl(self):
        for keysym, name in (('a', 'A'), ('7', '7'), ('space', 'Space'), ('Alt_L', 'Left Alt'),
                             ('Control_L', 'Left Ctrl'), ('Shift_R', 'Right Shift'), ('F12', 'F12'),
                             ('KP_5', 'Keypad 5'), ('Prior', 'PageUp'), ('comma', None), ('equal', None)):
            self.assertEqual(keynames.sdl_key_name(keysym), name)
        self.assertEqual(keynames.sdl_mouse_name(1), 'Mouse Left')
        self.assertEqual(keynames.sdl_mouse_name(3), 'Mouse Right')
        self.assertIsNone(keynames.sdl_mouse_name(4))
        for name in sorted(set(keynames.KEYSYMS.values()) | {f'F{n}' for n in range(1, 25)} - {'F10'}):
            with self.subTest(name=name):
                settings.validate_keys(settings.validate(self.keys(cross=name)))


class EnvironmentTests(unittest.TestCase):
    def env(self, diagnostics=None, inherited=None, ini=None, **extra):
        data = {**settings.DEFAULTS, **extra, 'diagnostics': {**settings.DIAGNOSTICS, **(diagnostics or {})}}
        return settings.game_environment(data, {} if inherited is None else inherited, ini=ini or {},
                                         ini_path=Path('/cfg/game.ini'))

    def test_defaults_set_no_diagnostic_variables(self):
        env = self.env()
        for var in ('RSX_FRAME_CSV', 'RSX_VK_TEXTURE_COMPUTE', 'RSX_VK_RAW_TEXTURES', 'RSX_TEX_HASH_FULL',
                    'VDEC_PERF', 'SPURS_KERN_THREADS', 'SPURS_KERN_PARALLEL', 'GCM_FLIP_MIN_MS'):
            self.assertNotIn(var, env)
        self.assertEqual(env['GOWA_GAME_INI'], '/cfg/game.ini')
        self.assertEqual(env['PS3RECOMP_VK_FULLSCREEN'], '0')

    def test_changed_diagnostics_become_variables(self):
        env = self.env({'frame_csv': True, 'texture_compute': False, 'raw_textures': False,
                        'tex_hash_full': True, 'vdec_perf': True, 'kern_threads': 2,
                        'kern_parallel': '', 'flip_min_ms': 500})
        self.assertEqual(env['RSX_FRAME_CSV'], '/cfg/frames.csv')
        self.assertEqual(env['RSX_VK_TEXTURE_COMPUTE'], '0')
        self.assertEqual(env['RSX_VK_RAW_TEXTURES'], '0')
        self.assertEqual(env['RSX_TEX_HASH_FULL'], '1')
        self.assertEqual(env['VDEC_PERF'], '1')
        self.assertEqual(env['SPURS_KERN_THREADS'], '2')
        self.assertEqual(env['SPURS_KERN_PARALLEL'], '')
        self.assertEqual(env['GCM_FLIP_MIN_MS'], '500')

    def test_explicit_environment_keeps_precedence(self):
        env = self.env({'kern_threads': 2}, inherited={'SPURS_KERN_THREADS': '4'})
        self.assertEqual(env['SPURS_KERN_THREADS'], '4')

    def test_display_mode_comes_from_game_ini(self):
        for mode, value in (('windowed', '0'), ('borderless', '1'), ('fullscreen', '1')):
            self.assertEqual(self.env(ini={'display_mode': mode})['PS3RECOMP_VK_FULLSCREEN'], value)

    def test_keymap_is_sent_with_pipes(self):
        env = self.env(controls={'key': {'cross': 'Space|K', 'square': 'Mouse Left'}, 'pad': {}})
        self.assertEqual(env['GOWA_KEY_MAP'], 'cross=Space|K,square=Mouse Left')

    def test_rejects_invalid_diagnostics(self):
        for change in ({'kern_threads': 0}, {'kern_threads': 9}, {'kern_parallel': '7;8'},
                       {'kern_parallel': '40'}, {'vdec_perf': 1}, {'unknown': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.env(change)
        self.assertEqual(settings.validate({**settings.DEFAULTS, 'diagnostics': {'kern_parallel': '7, 13'}})
                         ['diagnostics']['kern_parallel'], '7,13')

    def test_command_prefixes_only_installed_tools(self):
        data = {**settings.DEFAULTS, 'mangohud': True, 'gamemode': True}
        run = str(settings.PORT / 'scripts/run.sh')
        self.assertEqual(settings.game_command(data, which=lambda n: f'/usr/bin/{n}'),
                         ['gamemoderun', 'mangohud', 'bash', run])
        self.assertEqual(settings.game_command(data, which=lambda n: '/usr/bin/mangohud' if n == 'mangohud' else None),
                         ['mangohud', 'bash', run])
        self.assertEqual(settings.game_command(settings.DEFAULTS, which=lambda n: f'/usr/bin/{n}'), ['bash', run])
        self.assertEqual(settings.game_command(), ['bash', run])


class GameIniTests(unittest.TestCase):
    def test_roundtrip_preserves_unknown_keys_and_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game.ini'
            path.write_text('# gerado pelo jogo\nlanguage=pt\nshow_fps=0\n\nvsync = 1\nfuture_key=abc\n')
            values = game_ini.read(path)
            self.assertFalse(values['show_fps'])
            values.update(show_fps=True, overlay_corner='br', overlay_items=['fps', 'spu'],
                          window_size='1920x1080', display_mode='fullscreen', vsync=False)
            self.assertTrue(game_ini.write(path, values))
            self.assertEqual(path.read_text(), '# gerado pelo jogo\nlanguage=pt\nshow_fps=1\n\nvsync=0\n'
                                               'future_key=abc\noverlay_corner=br\noverlay_items=fps,spu\n'
                                               'display_mode=fullscreen\nwindow_size=1920x1080\n')
            self.assertEqual(game_ini.read(path), game_ini.validate(values))
            self.assertFalse(game_ini.write(path, game_ini.read(path)))

    def test_defaults_do_not_create_file_and_invalid_values_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game.ini'
            self.assertFalse(game_ini.write(path, game_ini.DEFAULTS))
            self.assertFalse(path.exists())
            path.write_text('overlay_scale=900\noverlay_corner=middle\nwindow_size=2560x1440\n'
                            'overlay_items=fps,newthing\n')
            values = game_ini.read(path)
            self.assertEqual(values['overlay_scale'], 100)
            self.assertEqual(values['overlay_corner'], 'tl')
            self.assertEqual(values['window_size'], '2560x1440')
            self.assertEqual(values['overlay_items'], ['fps', 'newthing'])
            game_ini.write(path, {**values, 'overlay_opacity': 40})
            self.assertIn('overlay_scale=100\n', path.read_text())
            self.assertIn('overlay_items=fps,newthing\n', path.read_text())
            self.assertIn('overlay_opacity=40\n', path.read_text())

    def test_validate_rejects_out_of_range(self):
        for change in ({'overlay_scale': 49}, {'overlay_opacity': 101}, {'window_size': 'huge'},
                       {'display_mode': 'kiosk'}, {'overlay_corner': 'center'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                game_ini.validate(change)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.saves = self.root / 'savedata'
        self.backups = self.root / 'backups'

    def tearDown(self):
        self.folder.cleanup()

    def write_save(self, text):
        (self.saves / 'BCUS98232-SYSDATA').mkdir(parents=True, exist_ok=True)
        (self.saves / 'BCUS98232-SYSDATA' / 'SAVEDATA').write_text(text)

    def test_empty_or_missing_saves_are_skipped(self):
        self.assertIsNone(save_backup.backup(self.saves, self.backups))
        self.saves.mkdir()
        self.assertIsNone(save_backup.backup(self.saves, self.backups))
        self.assertEqual(save_backup.list_backups(self.backups), [])

    def test_rotation_keeps_the_newest_ten(self):
        self.write_save('x')
        start = datetime.datetime(2026, 10, 15, 12, 0, 0)
        for i in range(13):
            save_backup.backup(self.saves, self.backups, now=start + datetime.timedelta(minutes=i))
        names = save_backup.list_backups(self.backups)
        self.assertEqual(len(names), save_backup.KEEP)
        self.assertEqual(names[0], '20261015-121200')
        self.assertEqual(names[-1], '20261015-120300')
        same = save_backup.backup(self.saves, self.backups, now=start + datetime.timedelta(minutes=12))
        self.assertEqual(same.name, '20261015-121200-2')
        self.assertEqual(save_backup.label('20261015-121200-2'), '15/10/2026 12:12:00-2')

    def test_restore_backs_up_current_saves_first(self):
        self.write_save('old')
        old = save_backup.backup(self.saves, self.backups, now=datetime.datetime(2026, 1, 1))
        self.write_save('new')
        safety = save_backup.restore(self.saves, self.backups, old.name, now=datetime.datetime(2026, 1, 2))
        self.assertEqual((self.saves / 'BCUS98232-SYSDATA' / 'SAVEDATA').read_text(), 'old')
        self.assertEqual((safety / 'BCUS98232-SYSDATA' / 'SAVEDATA').read_text(), 'new')
        self.assertFalse((self.root / 'savedata.restoring').exists())
        with self.assertRaises(ValueError):
            save_backup.restore(self.saves, self.backups, '../savedata')

    def test_launcher_backup_failure_does_not_raise(self):
        self.write_save('x')
        with patch.object(gowa_launcher, 'SAVEDATA_DIR', self.saves), \
                patch.object(gowa_launcher, 'BACKUP_DIR', self.backups), \
                patch.object(gowa_launcher, 'backup', side_effect=OSError('disco cheio')):
            self.assertIn('disco cheio', gowa_launcher.backup_saves())
        with patch.object(gowa_launcher, 'SAVEDATA_DIR', self.saves), \
                patch.object(gowa_launcher, 'BACKUP_DIR', self.backups):
            self.assertIn('backup dos saves', gowa_launcher.backup_saves())
        self.assertEqual(len(save_backup.list_backups(self.backups)), 1)


class StatusTests(unittest.TestCase):
    def test_marker_lines(self):
        self.assertEqual(gowa_launcher.status_for_line('[rsx] Vulkan backend init OK -- window open'), 'Jogo aberto')
        self.assertEqual(gowa_launcher.status_for_line('[rsx] Vulkan backend init FAILED'),
                         'Falha ao abrir a janela do jogo')
        self.assertEqual(gowa_launcher.status_for_line('[boot] dispatching entry OPD 0x008283F8'), 'Iniciando o jogo…')
        self.assertEqual(gowa_launcher.status_for_line('erro: disco não extraído'), 'Erro ao iniciar: disco não extraído')
        self.assertIsNone(gowa_launcher.status_for_line('[ppu] lv2_syscall 144 (stub)'))


if __name__ == '__main__':
    unittest.main()
