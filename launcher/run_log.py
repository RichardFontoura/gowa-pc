"""Start the game process and record a structured log of the run.

Each run gets its own directory ``<config>/logs/<UTC stamp>-<id>/`` with:

* ``raw.log`` - the exact stdout/stderr bytes of the game process;
* ``session.log`` - the same output as text, one line per output line, each
  prefixed with the UTC time and elapsed seconds at which the launcher read
  it, interleaved with ``[launcher:<category>]`` notes;
* ``events.jsonl`` - one JSON record per line matched by ``RULES``;
* ``metadata.json`` - command, revisions, platform, relevant environment
  variables and size/mtime of the executable and ELF;
* ``summary.json`` - exit code, signal, duration and event counts, written
  when the process ends.

``<config>/last_run.log`` is a symlink to the newest ``session.log``.
The launcher drives a :class:`RunLog` by calling :meth:`RunLog.pump` from
its Tk timer; nothing here uses threads.
"""

import codecs
import collections
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import subprocess
import time
import uuid

# Line classification rules as (level, category, pattern); the first match wins,
# so the specific categories must come before the generic 'runtime' ones.
RULES = (
    ('error', 'host-loader', re.compile(r'error while loading shared libraries|symbol lookup error|cannot open shared object', re.I)),
    ('gap', 'hle', re.compile(r'unresolved.*nid|unregistered import|faking CELL_OK', re.I)),
    ('gap', 'ppu', re.compile(r'unlifted function|unresolved indirect|\[gap\].*ppu', re.I)),
    ('gap', 'spu', re.compile(r'interpreting unlifted|spu.*(?:dispatch MISS|unimplemented|not in the microstep)|\[spu-smc\]|\[gap\].*spu', re.I)),
    ('warning', 'filesystem', re.compile(r'\[fs\].*(?:FAIL|ENOENT|errno=|failed)', re.I)),
    ('error', 'module', re.compile(r'(?:sysmodule|prx|library).*(?:FAIL|ERROR|not found)', re.I)),
    ('error', 'runtime', re.compile(r'\b(?:fatal|error|failed|segmentation fault|assertion failed|SIGSEGV|SIGABRT)\b', re.I)),
    ('warning', 'runtime', re.compile(r'\b(?:warning|unimplemented|unsupported|stub)\b', re.I)),
)
# Environment variables copied into metadata.json when present.
ENV_KEYS = ('PRESET', 'PS3_VFS_ROOT', 'PS3_HDD0_ROOT', 'PS3_HDD1_ROOT', 'PS3_BOOT_PATH',
            'PS3_DEV_FLASH', 'PS3_HLE_UNRESOLVED', 'SPU_INTERP_UNLIFTED', 'SPU_SMC_STATS',
            'SPURS_KERN_THREADS', 'SPURS_KERN_PARALLEL', 'SPURS_KERN_BUSY_RERUN',
            'GCM_FLIP_MIN_MS', 'GCM_FLIP_PACE_WINDOW', 'GCM_SEMA_ACQUIRE',
            'PS3RECOMP_VK_GUEST_PROGRAMS', 'RSX_FRAME_CSV', 'GOWA_GAME_INI',
            'GOWA_SHADER_CACHE', 'GOWA_ASYNC_SHADERS', 'GOWA_SHADER_WARMUP')
# The "[<UTC time>] [+<elapsed>s] " prefix that write_line() puts on session.log lines.
PREFIX = re.compile(r'^\[\d{4}-\d\d-\d\dT[^\]]+\] \[\+[^\]]+\] ')


def classify(line):
    """Classify one log line with ``RULES``.

    The launcher's own bookkeeping notes (``[launcher:session]``,
    ``summary``, ``exit``, ``stop`` and ``silence``) are never classified.

    Returns:
        A ``(level, category)`` tuple, e.g. ``('gap', 'spu')``, or ``None``.
    """
    if re.search(r'\[launcher:(?:session|summary|exit|stop|silence)\]', line):
        return None
    for level, category, pattern in RULES:
        if pattern.search(line):
            return level, category
    return None


def revision(path):
    """Return ``git describe --always --dirty`` for the repository at ``path``.

    Runs ``git`` as a subprocess with a 2 second timeout and returns
    ``'unknown'`` if git is missing, fails or times out.
    """
    try:
        result = subprocess.run(['git', '-C', str(path), 'describe', '--always', '--dirty'],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


class RunLog:
    """One run of the game: its process plus the log files described in the module docstring.

    Attributes of interest to callers:
        process: The ``subprocess.Popen`` of the game, or ``None`` if start failed.
        directory: ``Path`` of this run's log directory.
        counts: ``Counter`` of ``'level:category'`` occurrences seen so far.
        finished: ``True`` once the process has exited and all output was logged.
        logging_error: Message of the ``OSError`` that stopped logging, or ``None``.
        stop_requested: Set by the caller before terminating the process, so the
            exit is not reported as a failure.
    """

    def __init__(self, config, port, command, env, messages=()):
        """Create the run directory, open the log files and start the game.

        Side effects: creates ``<config>/logs/<stamp>-<id>/``; moves a
        regular-file ``<config>/last_run.log`` into it as
        ``previous_last_run.log`` and replaces ``last_run.log`` with a
        symlink to the new ``session.log``; writes ``metadata.json``; and
        starts ``command`` in a new session (its own process group) with
        ``cwd=port``, ``env`` and stdout/stderr redirected into ``raw.log``.

        NID lines of the output are annotated with the importing library
        taken from ``<port>/out/EBOOT.imports.json`` when that file exists.

        Args:
            config: Launcher configuration directory.
            port: Root of the port repository; also the working directory.
            command: argv of the game command.
            env: Complete environment for the game process.
            messages: Extra lines recorded as ``[launcher:launcher]`` notes
                before the process starts.

        Raises:
            Exception: Anything raised while setting up or starting the
                process is re-raised after the open files are closed.
        """
        self.started = self.last_output = self.last_notice = time.monotonic()
        self.counts = collections.Counter()
        self.imports = {}
        import_path = Path(port) / 'out' / 'EBOOT.imports.json'
        try:
            for entry in json.loads(import_path.read_text()):
                self.imports[int(entry['nid'], 0)] = entry.get('library', 'unknown')
        except (OSError, ValueError, KeyError, TypeError):
            self.imports = {}
        self.line_number = 0
        self.pending = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.finished = False
        self.logging_error = None
        self.process = None
        self.files = []
        self.stop_requested = False
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.directory = Path(config) / 'logs' / f'{stamp}-{uuid.uuid4().hex[:8]}'
        self.directory.mkdir(parents=True)
        try:
            self.log = self._open('session.log', 'w', encoding='utf-8', buffering=1)
            self.events = self._open('events.jsonl', 'w', encoding='utf-8', buffering=1)
            self.raw = self._open('raw.log', 'wb', buffering=0)
            self.reader = self._open('raw.log', 'rb')
            latest = Path(config) / 'last_run.log'
            if latest.exists() and not latest.is_symlink():
                latest.rename(self.directory / 'previous_last_run.log')
            link = self.directory / 'latest-link'
            link.symlink_to(self.directory.resolve() / 'session.log')
            os.replace(link, latest)
            self.metadata = {
                'started_utc': stamp, 'command': command, 'cwd': str(port),
                'platform': platform.platform(), 'cpu_count': os.cpu_count(),
                'port_revision': revision(port), 'runtime_revision': revision(Path(port).parent / 'ps3recomp'),
                'environment_overrides': {k: env[k] for k in ENV_KEYS if k in env},
                'timestamp_semantics': 'UTC and monotonic elapsed at launcher ingestion, not guest event time',
                'directory': str(self.directory),
                'artifacts': {},
            }
            for name, path in (('executable', Path(port) / 'build' / env.get('PRESET', 'linux-vulkan') / 'GowAscensionRecomp'),
                               ('elf', Path(port) / 'game/EBOOT.ELF')):
                try:
                    stat = path.stat()
                    self.metadata['artifacts'][name] = {'path': str(path.resolve()), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
                except OSError as exc:
                    self.metadata['artifacts'][name] = {'path': str(path), 'stat_error': str(exc)}
            (self.directory / 'metadata.json').write_text(json.dumps(self.metadata, indent=2) + '\n')
            self.note('session', f'command={shlex.join(command)} cwd={port}')
            self.note('session', f'logs={self.directory}; raw.log preserves stdout/stderr bytes')
            self.note('session', f'port={self.metadata["port_revision"]} runtime={self.metadata["runtime_revision"]} platform={self.metadata["platform"]}')
            self.note('session', 'environment overrides=' + json.dumps(self.metadata['environment_overrides'], sort_keys=True))
            for message in messages:
                self.note('launcher', message)
            self.process = subprocess.Popen(command, cwd=port, env=env, stdout=self.raw,
                                            stderr=subprocess.STDOUT, start_new_session=True)
            self.note('session', f'pid={self.process.pid}')
        except Exception as exc:
            try:
                if hasattr(self, 'log'):
                    self.note('error', f'start failed: {exc}')
            except OSError:
                pass
            finally:
                self.close()
            raise

    def _open(self, name, mode, **kwargs):
        """Open ``name`` inside the run directory and register it for :meth:`close`."""
        handle = (self.directory / name).open(mode, **kwargs)
        self.files.append(handle)
        return handle

    def write_line(self, line):
        """Append one line to ``session.log`` and, if it classifies, to ``events.jsonl``.

        A line that mentions ``NID=0x...`` of a known import gets an
        ``[import-library=...]`` suffix. The line is prefixed with the
        current UTC time and the seconds elapsed since the run started; a
        classified line also increments :attr:`counts`.

        Raises:
            OSError: If a log file cannot be written.
        """
        match = re.search(r'(?i)NID[= ]+(0x[0-9a-f]+)', line)
        if match and int(match[1], 16) in self.imports:
            line += ' [import-library=' + self.imports[int(match[1], 16)] + '; source=out/EBOOT.imports.json]'
        elapsed = time.monotonic() - self.started
        utc = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
        self.line_number += 1
        self.log.write(f'[{utc}] [+{elapsed:.3f}s] {line}\n')
        hit = classify(line)
        if hit:
            level, category = hit
            self.counts[f'{level}:{category}'] += 1
            self.events.write(json.dumps({'utc': utc, 'elapsed_s': round(elapsed, 3),
                                         'line': self.line_number, 'level': level,
                                         'category': category, 'message': line}, ensure_ascii=False) + '\n')

    def note(self, category, text):
        """Record a launcher note as ``[launcher:<category>] <text>``.

        Before the process exists a write error propagates (so the start
        fails). Once the process runs, a write error is stored in
        :attr:`logging_error`, the files are closed and further notes are
        dropped, so a full disk never interrupts the game.
        """
        if not self.logging_error:
            try:
                self.write_line(f'[launcher:{category}] {text}')
            except OSError as exc:
                if self.process is None:
                    raise
                self.logging_error = str(exc)
                self.close()

    def pump(self, limit=262144):
        """Process new output and detect the end of the run; call periodically.

        Reads at most ``limit`` bytes per call (see :meth:`_pump`). If logging
        has failed, only the process exit is tracked so that :attr:`finished`
        still becomes ``True``. ``OSError`` while logging is caught and
        stored in :attr:`logging_error`.
        """
        if self.logging_error:
            self.finished = self.process.poll() is not None
            return
        try:
            self._pump(limit)
        except OSError as exc:
            self.logging_error = str(exc)
            self.close()
            self.finished = self.process.poll() is not None

    def _pump(self, limit):
        """Read new bytes from ``raw.log``, log complete lines and handle the exit.

        Bytes are decoded as UTF-8 incrementally (invalid sequences replaced)
        and an incomplete last line is kept in :attr:`pending`; a pending line
        longer than 64 KiB is flushed in 64 KiB pieces marked
        ``[launcher:continued-line]``. If the read filled ``limit`` the call
        returns early, leaving the rest for the next call.

        The exit status is polled before reading, so once the process is seen
        as exited the read that follows contains all of its output. Then the
        tail is flushed, ``exit``/``error``/``summary`` notes are written,
        ``summary.json`` is created, :attr:`finished` is set and the files are
        closed. While the process runs silently, a ``silence`` note is written
        every 30 seconds.
        """
        if self.finished:
            return
        code = self.process.poll()
        data = self.reader.read(limit)
        if data:
            self.last_output = time.monotonic()
            text = self.pending + self.decoder.decode(data)
            lines = text.split('\n')
            self.pending = lines.pop()
            for line in lines:
                self.write_line(line)
            while len(self.pending) > 65536:
                self.write_line(self.pending[:65536] + ' [launcher:continued-line]')
                self.pending = self.pending[65536:]
            if len(data) == limit:
                return
        if code is not None:
            tail = self.pending + self.decoder.decode(b'', final=True)
            if tail:
                self.write_line(tail)
            self.pending = ''
            name = ''
            if code < 0:
                try:
                    name = signal.Signals(-code).name
                except ValueError:
                    name = str(-code)
            self.note('exit', f'code={code} signal={name or "none"} stop_requested={self.stop_requested}')
            if code != 0 and not self.stop_requested:
                self.note('error', f'process failed: exit={code} signal={name or "none"}; inspect raw.log and events.jsonl')
            summary = {'exit_code': code, 'signal': name or None, 'stop_requested': self.stop_requested,
                       'duration_s': round(time.monotonic() - self.started, 3),
                       'observed_log_events': dict(self.counts),
                       'counts_semantics': 'emitted log records, not all runtime calls; runtime may sample repeats'}
            self.note('summary', json.dumps(summary, ensure_ascii=False))
            (self.directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            self.finished = True
            self.close()
        elif time.monotonic() - max(self.last_output, self.last_notice) >= 30:
            self.note('silence', f'pid={self.process.pid} alive; no new output for {time.monotonic() - self.last_output:.0f}s; silence alone does not prove a hang')
            self.last_notice = time.monotonic()

    def close(self):
        """Close every file opened by this run, ignoring close errors; the process is not touched."""
        for handle in self.files:
            try:
                handle.close()
            except OSError:
                pass
