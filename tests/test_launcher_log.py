"""Tests for the launcher's run logger (``launcher/run_log.py``).

Each test starts a short Python child process through ``RunLog`` inside a
temporary directory that stands in for both the config directory and the port
directory, then checks the files a session leaves behind: ``raw.log``,
``session.log``, ``events.jsonl``, ``metadata.json``, ``summary.json`` and the
``last_run.log`` symlink. Line classification (``classify``) and the launcher
status text derived from timestamped lines (``status_for_line``) are covered
too. No game build or display is needed.

Run from the repository root::

    python -m unittest discover -s tests -p 'test_launcher*.py'
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from run_log import RunLog, classify
from gowa_launcher import status_for_line


class RunLogTests(unittest.TestCase):
    """Session lifecycle, persistence and robustness of ``RunLog``."""
    def setUp(self):
        """Create a temporary root directory and the list of sessions to clean up."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = []

    def tearDown(self):
        """Kill any child still running, close every session and remove the temporary root."""
        for session in self.sessions:
            if session.process and session.process.poll() is None:
                session.process.kill()
                session.process.wait()
            session.close()
        self.temp.cleanup()

    def start(self, code, env=None):
        """Start a ``RunLog`` session running ``python -c code`` and track it for cleanup.

        The git revision lookup is patched to a fixed value, and the launcher message
        ``'backup saved'`` is passed so tests can check that messages reach the log.
        """
        with patch('run_log.revision', return_value='test'):
            session = RunLog(self.root, self.root, [sys.executable, '-c', code],
                             env or os.environ, ['backup saved'])
        self.sessions.append(session)
        return session

    def finish(self, session):
        """Wait for the child to exit, then pump until the session reports it is finished."""
        session.process.wait(timeout=5)
        for _ in range(100):
            session.pump(8192)
            if session.finished:
                return
        self.fail('log did not drain')

    def test_preserves_raw_enriches_imports_and_records_failure(self):
        """Raw bytes are kept verbatim, NIDs are annotated and a non-zero exit is recorded.

        Checks that invalid UTF-8 and an unterminated last line survive in ``raw.log``,
        that ``last_run.log`` shows the import library from ``out/EBOOT.imports.json``,
        the launcher message, the exit code and the replacement character, and that
        ``events.jsonl`` and ``summary.json`` classify the host-loader and HLE lines.
        """
        (self.root / 'out').mkdir()
        (self.root / 'out/EBOOT.imports.json').write_text('[{"nid":"0x1234","library":"cellExample"}]')
        session = self.start("import os; os.write(1,b'[gap] hle unresolved NID=0x1234 lr=0x5678\\n'); os.write(2,b'error while loading shared libraries: libMissing.so\\npartial\\xff'); raise SystemExit(7)")
        self.finish(session)
        raw = (session.directory / 'raw.log').read_bytes()
        self.assertTrue(raw.endswith(b'partial\xff'))
        text = (self.root / 'last_run.log').read_text()
        self.assertIn('cellExample', text)
        self.assertIn('backup saved', text)
        self.assertIn('code=7', text)
        self.assertIn('partial\ufffd', text)
        events = [json.loads(line) for line in (session.directory / 'events.jsonl').read_text().splitlines()]
        self.assertIn('host-loader', [e['category'] for e in events])
        self.assertIn('hle', [e['category'] for e in events])
        self.assertEqual(json.loads((session.directory / 'summary.json').read_text())['exit_code'], 7)

    def test_frame_csv_and_scene_marks_live_in_the_session(self):
        (self.root / 'frames.csv').write_text('old run\n')
        (self.root / 'build/linux-vulkan').mkdir(parents=True)
        (self.root / 'build/linux-vulkan/GowAscensionRecomp').write_bytes(b'exe')
        env = dict(os.environ, RSX_FRAME_CSV=str(self.root / 'frames.csv'))
        env.pop('GOWA_SCENE_MARKS', None)
        code = ("import os; open(os.environ['RSX_FRAME_CSV'], 'w').write('frame\\n'); "
                "open(os.environ['GOWA_SCENE_MARKS'], 'w').write('mark\\n')")
        session = self.start(code, env)
        self.finish(session)
        self.assertEqual((session.directory / 'frames.csv').read_text(), 'frame\n')
        self.assertEqual((session.directory / 'marks.csv').read_text(), 'mark\n')
        self.assertEqual((session.directory / 'previous_frames.csv').read_text(), 'old run\n')
        self.assertTrue((self.root / 'frames.csv').is_symlink())
        self.assertEqual((self.root / 'frames.csv').read_text(), 'frame\n')
        meta = json.loads((session.directory / 'metadata.json').read_text())
        self.assertEqual(meta['artifacts']['executable']['sha256'], hashlib.sha256(b'exe').hexdigest())
        self.assertEqual(meta['environment_overrides']['GOWA_SCENE_MARKS'], str(session.directory / 'marks.csv'))

    def test_history_and_large_output_are_not_truncated(self):
        """An old ``last_run.log`` is archived and very long lines are stored in full.

        A regular-file ``last_run.log`` moves into the new session as
        ``previous_last_run.log``; earlier sessions keep their logs; a 150000-byte line
        is preserved in ``raw.log`` while the in-memory pending buffer stays bounded.
        """
        (self.root / 'last_run.log').write_text('legacy')
        first = self.start("print('first')")
        self.finish(first)
        self.assertEqual((first.directory / 'previous_last_run.log').read_text(), 'legacy')
        second = self.start("import os; os.write(1,b'x'*150000+b'\\nlast\\n')")
        self.finish(second)
        self.assertIn('first', (first.directory / 'session.log').read_text())
        self.assertEqual((second.directory / 'raw.log').stat().st_size, 150006)
        self.assertIn('last', (self.root / 'last_run.log').read_text())
        self.assertLessEqual(len(second.pending), 65536)

    def test_utf8_split_and_silence_are_not_false_hang(self):
        """Multi-byte characters split across reads decode correctly and silence is not a hang.

        After 30 s without output a notice says silence does not prove a hang; stopping
        with SIGTERM is then recorded in ``summary.json`` as a requested stop.
        """
        session = self.start('import time; time.sleep(30)')
        session.raw.write('é\n'.encode())
        session.pump(1)
        session.pump(1)
        session.pump(1)
        session.last_output = session.last_notice = time.monotonic() - 31
        session.pump()
        text = (session.directory / 'session.log').read_text()
        self.assertIn('é', text)
        self.assertIn('does not prove a hang', text)
        session.stop_requested = True
        session.process.send_signal(signal.SIGTERM)
        self.finish(session)
        summary = json.loads((session.directory / 'summary.json').read_text())
        self.assertEqual(summary['signal'], 'SIGTERM')
        self.assertTrue(summary['stop_requested'])

    def test_spawn_failure_is_persisted(self):
        """A command that cannot be started raises ``OSError`` and leaves "start failed" in ``last_run.log``."""
        with patch('run_log.revision', return_value='test'), self.assertRaises(OSError):
            RunLog(self.root, self.root, ['/nonexistent/gowa'], os.environ)
        self.assertIn('start failed', (self.root / 'last_run.log').read_text())

    def test_secrets_not_in_metadata_and_timestamped_status(self):
        """Unlisted environment variables stay out of metadata; timestamps do not break status parsing.

        Also checks ``classify`` on module, filesystem, harmless HLE and launcher
        summary lines.
        """
        session = self.start("print('[rsx] Vulkan backend init OK')", {**os.environ, 'API_TOKEN': 'secret-marker'})
        self.finish(session)
        self.assertNotIn('secret-marker', (session.directory / 'metadata.json').read_text())
        line = next(l for l in (session.directory / 'session.log').read_text().splitlines() if '] [rsx]' in l)
        self.assertEqual(status_for_line(line), 'Jogo aberto')
        self.assertEqual(classify('[cellSysmodule] LoadModule FAILED id=0xffff'), ('error', 'module'))
        self.assertEqual(classify('[fs] open FAIL guest=x errno=2'), ('warning', 'filesystem'))
        self.assertIsNone(classify('[cellSysmodule] result=CELL_OK mode=HLE'))
        self.assertIsNone(classify('[launcher:summary] {"error:runtime": 2}'))

    def test_logging_io_failure_does_not_lose_process_tracking(self):
        """A write error while pumping is stored in ``logging_error`` and the process exit is still detected."""
        session = self.start('import time; time.sleep(30)')
        session.raw.write(b'hello\n')
        with patch.object(session, 'write_line', side_effect=OSError('disk full')):
            session.pump()
        self.assertEqual(session.logging_error, 'disk full')
        self.assertFalse(session.finished)
        session.process.kill()
        session.process.wait(timeout=5)
        session.pump()
        self.assertTrue(session.finished)

    def test_stop_note_io_failure_does_not_raise(self):
        """A write error while noting a stop request is stored, not raised, and the session still finishes."""
        session = self.start('import time; time.sleep(30)')
        with patch.object(session, 'write_line', side_effect=OSError('disk full')):
            session.note('stop', 'SIGTERM requested')
        self.assertEqual(session.logging_error, 'disk full')
        session.process.terminate()
        session.process.wait(timeout=5)
        session.pump()
        self.assertTrue(session.finished)
