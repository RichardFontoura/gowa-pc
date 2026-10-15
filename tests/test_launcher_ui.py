import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT / 'launcher'))


@unittest.skipUnless(shutil.which('xvfb-run'), 'requires xvfb-run')
class LauncherSmokeTest(unittest.TestCase):
    def test_starts_under_xvfb_and_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'GOWA_CONFIG_DIR': folder}
            result = subprocess.run(['xvfb-run', '-a', 'bash', str(PORT / 'scripts/launcher.sh'),
                                     '--exit-after', '500'], env=env, capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), [])


@unittest.skipUnless(os.environ.get('GOWA_UI_TEST') == '1', 'requires a display and Tk')
class LauncherPagesTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import gowa_launcher as ui
        import settings
        self.ui, self.settings = ui, settings
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name)
        self.path = self.dir / 'settings.json'
        self.patches = [
            patch.object(ui, 'load', return_value=settings.load(self.path)),
            patch.object(ui, 'save', side_effect=lambda data: settings.save(data, self.path)),
            patch.object(ui, 'CONFIG_DIR', self.dir),
            patch.object(ui, 'GAME_INI', self.dir / 'game.ini'),
            patch.object(ui, 'BACKUP_DIR', self.dir / 'backups'),
            patch.object(ui, 'SAVEDATA_DIR', self.dir / 'savedata'),
        ]
        for p in self.patches:
            p.start()
        (self.dir / 'game.ini').write_text('language=pt\nshow_fps=1\nwindow_size=2560x1440\n')
        self.root = tk.Tk()
        self.app = ui.Launcher(self.root)

    def tearDown(self):
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
            self.app.process.wait()
        self.app.destroy()
        for p in self.patches:
            p.stop()
        self.folder.cleanup()

    def test_graphics_write_game_ini_and_reload(self):
        app = self.app
        self.assertTrue(app.ini_vars['show_fps'].get())
        self.assertEqual(app.size_box.get(), '2560 × 1440')
        app.ini_vars['display_mode'].set('borderless')
        app.ini_vars['overlay_scale'].set('150')
        app.item_vars['spu'].set(True)
        app.var('mangohud').set(True)
        self.assertTrue(app.save())
        text = (self.dir / 'game.ini').read_text()
        self.assertTrue(text.startswith('language=pt\nshow_fps=1\nwindow_size=2560x1440\n'))
        self.assertIn('display_mode=borderless\n', text)
        self.assertIn('overlay_scale=150\n', text)
        self.assertIn('overlay_items=fps,frametime,spu\n', text)
        self.assertTrue(self.settings.load(self.path)['mangohud'])
        (self.dir / 'game.ini').write_text('show_fps=0\ndisplay_mode=windowed\n')
        app.reload_ini()
        self.assertFalse(app.ini_vars['show_fps'].get())
        self.assertEqual(app.ini_vars['display_mode'].get(), 'windowed')
        app.ini_vars['overlay_scale'].set('abc')
        with patch.object(app.messagebox, 'showerror') as error:
            self.assertFalse(app.save())
            error.assert_called_once()

    def test_diagnostics_reset_and_save(self):
        app = self.app
        app.diag_vars['kern_threads'].set('3')
        app.diag_vars['frame_csv'].set(True)
        app.parallel_box.set(self.ui.PARALLEL_LABELS['Seguro'])
        app.select_parallel()
        self.assertEqual(app.diag_vars['kern_parallel'].get(), '')
        self.assertTrue(app.save())
        saved = self.settings.load(self.path)['diagnostics']
        self.assertEqual((saved['kern_threads'], saved['kern_parallel'], saved['frame_csv']), (3, '', True))
        app.diag_vars['kern_parallel'].set('7,13')
        self.assertEqual(app.parallel_box.get(), self.ui.PARALLEL_LABELS['custom'])
        app.reset_diagnostics()
        self.assertEqual(app.parallel_box.get(), self.ui.PARALLEL_LABELS['Padrão'])
        self.assertEqual(app.collect()['diagnostics'], self.settings.DIAGNOSTICS)

    def test_controls_two_columns_capture_and_duplicates(self):
        app = self.app
        app.show('Controles')
        self.root.update()
        first, second = app.key_vars['cross']
        self.assertEqual((first.get(), second.get()), ('Space', 'K'))
        self.assertEqual(app.key_vars['evade_mod'][0].get(), 'Left Alt')
        self.assertEqual(app.warning.cget('text'), '')
        app.key_vars['down'][1].set('K')
        self.assertIn('Tecla repetida: K', app.warning.cget('text'))
        app.key_vars['down'][1].set('')
        event = type('E', (), {})()
        event.widget = self.root.nametowidget(self.find_entry(first))
        event.num = 1
        self.assertEqual(app.capture_click(event, first, 'Cross'), 'break')
        self.assertIs(app.capturing, event.widget)
        event.keysym = 'Alt_L'
        self.assertEqual(app.capture_key(event, first), 'break')
        self.assertEqual(first.get(), 'Left Alt')
        self.assertIsNone(app.capturing)
        app.capture_click(event, second, 'Cross')
        event.num = 3
        app.capture_click(event, second, 'Cross')
        self.assertEqual(second.get(), 'Mouse Right')
        app.capture_click(event, second, 'Cross')
        event.keysym = 'Insert'
        app.capture_key(event, second)
        self.assertEqual(second.get(), 'Mouse Right')
        event.keysym = 'BackSpace'
        app.capture_key(event, second)
        self.assertEqual(second.get(), '')
        first.set('Space')
        second.set('Mouse X1')
        self.assertTrue(app.save())
        self.assertEqual(self.settings.load(self.path)['controls']['key']['cross'], 'Space|Mouse X1')
        app.reset_controls()
        self.assertEqual(second.get(), 'K')

    def find_entry(self, var):
        stack = [self.app.pages['Controles']]
        while stack:
            widget = stack.pop()
            if widget.winfo_class() == 'TEntry' and str(widget.cget('textvariable')) == str(var):
                return str(widget)
            stack.extend(widget.winfo_children())
        raise AssertionError('entry not found')

    def test_log_cap_copy_clear_and_status_markers(self):
        app = self.app
        app.follow(''.join(f'linha {i}\n' for i in range(self.ui.LOG_LIMIT + 50)))
        self.assertLessEqual(int(app.log.index('end-1c').split('.')[0]), self.ui.LOG_LIMIT + 1)
        app.follow('[rsx] Vulkan backend init ')
        self.assertNotEqual(app.status.get(), 'Jogo aberto')
        app.follow('OK -- window open\n')
        self.assertEqual(app.status.get(), 'Jogo aberto')
        app.copy_log()
        self.assertIn('window open', self.root.clipboard_get())
        app.clear_log()
        self.assertEqual(app.log.get('1.0', 'end-1c'), '')

    def test_play_backs_up_saves_and_restore_is_guarded(self):
        app, ui = self.app, self.ui
        saves = self.dir / 'savedata' / 'BCUS98232-SYSDATA'
        saves.mkdir(parents=True)
        (saves / 'SAVEDATA').write_text('v1')
        with patch.object(ui, 'game_command', return_value=[sys.executable, '-c', 'import time; time.sleep(30)']):
            app.play()
        self.assertEqual(len(app.backup_names), 1)
        self.assertIn('backup dos saves', app.log.get('1.0', 'end'))
        with patch.object(app.messagebox, 'showwarning') as warning, \
                patch.object(app.messagebox, 'askyesno') as ask:
            self.assertFalse(app.restore_saves())
            warning.assert_called_once()
            ask.assert_not_called()
        app.process.kill()
        app.process.wait()
        app.tick()
        self.assertIsNone(app.process)
        (saves / 'SAVEDATA').write_text('v2')
        with patch.object(app.messagebox, 'askyesno', return_value=False):
            self.assertFalse(app.restore_saves())
        self.assertEqual((saves / 'SAVEDATA').read_text(), 'v2')
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            self.assertTrue(app.restore_saves())
        self.assertEqual((saves / 'SAVEDATA').read_text(), 'v1')
        self.assertEqual(len(app.backup_names), 2)


if __name__ == '__main__':
    unittest.main()
