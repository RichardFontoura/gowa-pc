"""Opt-in real Tk smoke: GOWA_UI_TEST=1, DISPLAY and a usable Tk installation.

Opens a real ``Launcher`` window (``launcher/gowa_launcher.py``) with its
config, ``game.ini``, backup and savedata paths redirected to a temporary
directory. The game itself is never started; the process test runs a Python
child that ignores SIGTERM. Set ``GOWA_UI_SCREENSHOT=<file>`` to save a
screenshot of the Controls page with ImageMagick ``import``.

Run from the repository root::

    GOWA_UI_TEST=1 xvfb-run -a python -m unittest discover -s tests -p 'test_pc_*.py'
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))


@unittest.skipUnless(os.environ.get('GOWA_UI_TEST') == '1', 'requires a display and Tk')
class LauncherUITest(unittest.TestCase):
    """Process control and page handling of a real ``Launcher`` window."""
    def test_process_start_and_forced_stop(self):
        """Play starts the child and logs its output; Stop escalates to SIGKILL when SIGTERM is ignored.

        Checks that output reaches ``last_run.log`` and the log view, and that the
        launcher clears its process once the child dies with -9.
        """
        import tkinter as tk
        import time
        import gowa_launcher as ui
        import settings
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            root = tk.Tk()
            app = None
            try:
                with patch.object(ui, 'load', return_value=settings.load(path)), \
                     patch.object(ui, 'save', side_effect=lambda data: settings.save(data, path)), \
                     patch.object(ui, 'CONFIG_DIR', Path(folder)), \
                     patch.object(ui, 'GAME_INI', Path(folder) / 'game.ini'), \
                     patch.object(ui, 'BACKUP_DIR', Path(folder) / 'backups'), \
                     patch.object(ui, 'SAVEDATA_DIR', Path(folder) / 'savedata'), \
                     patch.object(ui, 'game_command', return_value=[sys.executable, '-u', '-c',
                         'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                         'print("ready",flush=True); time.sleep(30)']):
                    app = ui.Launcher(root)
                    app.play()
                    process = app.process
                    deadline = time.monotonic() + 3
                    while 'ready' not in (Path(folder) / 'last_run.log').read_text() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.02)
                    self.assertIsNone(process.poll())
                    self.assertIn('ready', (Path(folder) / 'last_run.log').read_text())
                    app.stop()
                    deadline = time.monotonic() + 4
                    while app.process and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.02)
                    self.assertIsNone(app.process)
                    self.assertEqual(process.returncode, -9)
                    self.assertIn('ready', app.log.get('1.0', 'end'))
            finally:
                if app and app.process and app.process.poll() is None:
                    app.process.kill()
                    app.process.wait()
                if app:
                    app.destroy()
                else:
                    root.destroy()

    def test_pages_and_saved_remap(self):
        """Every page can be shown, and a key and pad remap is saved and then reset to defaults.

        Also checks that the pad button labels follow the selected pad style.
        """
        import tkinter as tk
        import gowa_launcher as ui
        import settings
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            root = tk.Tk()
            app = None
            try:
                with patch.object(ui, 'load', return_value=settings.load(path)), \
                     patch.object(ui, 'save', side_effect=lambda data: settings.save(data, path)), \
                     patch.object(ui, 'GAME_INI', Path(folder) / 'game.ini'), \
                     patch.object(ui, 'BACKUP_DIR', Path(folder) / 'backups'), \
                     patch.object(ui, 'SAVEDATA_DIR', Path(folder) / 'savedata'):
                    app = ui.Launcher(root)
                    for name in app.pages:
                        app.show(name)
                        root.update()
                        self.assertTrue(app.pages[name].winfo_ismapped())
                    app.show('Controles')
                    app.key_vars['cross'][0].set('V')
                    app.key_vars['cross'][1].set('')
                    app.var('pad_style').set('xbox')
                    app.pad_vars['cross'] = 'b'
                    app.relabel()
                    root.update()
                    self.assertEqual(app.pad_boxes['cross'].get(), 'B')
                    self.assertTrue(app.save())
                    saved = settings.load(path)
                    self.assertEqual(saved['controls']['key']['cross'], 'V')
                    self.assertEqual(saved['controls']['pad']['cross'], 'b')
                    app.reset_controls()
                    self.assertEqual(app.key_vars['cross'][0].get(), 'Space')
                    self.assertEqual(app.key_vars['cross'][1].get(), 'K')
                    self.assertEqual(app.pad_boxes['cross'].get(), 'A')
                    root.update()
                    if os.environ.get('GOWA_UI_SCREENSHOT'):
                        import subprocess
                        subprocess.run(['import', '-window', 'root', os.environ['GOWA_UI_SCREENSHOT']], check=True)
            finally:
                if app:
                    app.destroy()
                else:
                    root.destroy()


if __name__ == '__main__':
    unittest.main()
