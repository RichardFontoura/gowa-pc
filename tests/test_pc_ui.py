"""Opt-in real Tk smoke: GOWA_UI_TEST=1, DISPLAY and a usable Tk installation."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))


@unittest.skipUnless(os.environ.get('GOWA_UI_TEST') == '1', 'requires a display and Tk')
class LauncherUITest(unittest.TestCase):
    def test_process_start_and_forced_stop(self):
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
        import tkinter as tk
        import gowa_launcher as ui
        import settings
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            root = tk.Tk()
            app = None
            try:
                with patch.object(ui, 'load', return_value=settings.load(path)), \
                     patch.object(ui, 'save', side_effect=lambda data: settings.save(data, path)):
                    app = ui.Launcher(root)
                    for name in app.pages:
                        app.show(name)
                        root.update()
                        self.assertTrue(app.pages[name].winfo_ismapped())
                    app.show('Controles')
                    app.key_vars['cross'].set('V')
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
                    self.assertEqual(app.key_vars['cross'].get(), 'Space')
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
