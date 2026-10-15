import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import settings


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_environment(self):
        data = copy.deepcopy(settings.DEFAULTS)
        data['controls']['key'] = {'cross': 'V', 'circle': 'Escape'}
        data['controls']['pad'] = {'cross': 'b', 'circle': 'a'}
        data['keyboard'] = False
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            settings.save(data, path)
            self.assertEqual(settings.load(path), data)
        env = settings.game_environment(data, {'GOWA_KEY_MAP': 'cross=Q', 'GCM_FLIP_MIN_MS': '300'},
                                         ini={'display_mode': 'borderless'})
        self.assertEqual(env['GOWA_KEY_MAP'], 'cross=V,circle=Escape')
        self.assertEqual(env['GOWA_PAD_MAP'], 'cross=b,circle=a')
        self.assertEqual(env['PS3RECOMP_VK_FULLSCREEN'], '1')
        self.assertEqual(env['GOWA_KEYBOARD'], '0')
        self.assertEqual(env['GCM_FLIP_MIN_MS'], '300')
        self.assertEqual(settings.game_environment(settings.DEFAULTS, {}, ini={})['GOWA_KEY_MAP'], '')

    def test_interrupted_save_preserves_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            settings.save(settings.DEFAULTS, path)
            old = path.read_bytes()
            with patch.object(settings.os, 'replace', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    settings.save({**settings.DEFAULTS, 'rumble': False}, path)
            self.assertEqual(path.read_bytes(), old)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_rejects_invalid_settings_without_destroying_file(self):
        for change in ({'version': 3}, {'diagnostics': {'flip_min_ms': -1}}, {'keyboard': 'false'},
                       {'controls': {'pad': {'l2': 'x'}}}, {'controls': {'key': {'cross': 'V,start=Q'}}},
                       {'controls': {'key': {'cross': 'Insert'}}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settings.validate({**settings.DEFAULTS, **change})
        with self.assertRaises(ValueError):
            settings.game_environment({**settings.DEFAULTS, 'controls': {'key': {'cross': 'Not A Key'}}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                settings.load(path)
            self.assertEqual(path.read_text(), '{broken')

    def test_reads_legacy_config_until_first_save(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / 'gowa-pc' / 'settings.json'
            legacy = Path(folder) / 'gowa-recomp' / 'settings.json'
            settings.save({**settings.DEFAULTS, 'rumble': False}, legacy)
            with patch.object(settings, 'CONFIG_FILE', current), \
                    patch.object(settings, 'LEGACY_CONFIG_FILE', legacy):
                self.assertFalse(settings.load(current)['rumble'])
                settings.save(settings.DEFAULTS, current)
                self.assertTrue(settings.load(current)['rumble'])


if __name__ == '__main__':
    unittest.main()
