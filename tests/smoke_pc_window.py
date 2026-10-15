"""Window smoke test for the PC build under Xvfb.

Starts the game through ``scripts/run.sh`` (optionally inside gdb), finds its
X11 window by title, and sends real keyboard events through XTest so they reach
the guest pad through SDL, without any ``PAD_*`` input injection. A timeline of
steps takes screenshots and presses keys; Escape is pressed at the end to check
that the game is still alive. Game log, screenshots and frame dumps are written
to ``--out`` (default ``scratch/pc-window``).

Requires a built game, extracted game files, libX11, libXtst and ImageMagick
``import``. Example, from the repository root::

    xvfb-run -a python tests/smoke_pc_window.py --steps '60:shot,62:Return'

The X11 helpers here are also used by ``benchmark_pc_fps.py``.
"""
import argparse
import ctypes as C
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import time

PORT = Path(__file__).resolve().parents[1]
DESCRIPTION = ('Window smoke under Xvfb: real SDL keyboard events through XTest into the '
               'guest cellPad (no PAD_* injection). Artifacts stay in --out.')
STEPS_HELP = ('Comma-separated T:ACTION items, T in seconds since launch. ACTION is shot, '
              'shot=NAME or an X keysym (Return, space, Down, ...), optionally KEYSYM*HOLD '
              'in seconds. Replaces the default calibration sequence.')
DEFAULT_HOLD = 0.25
x = C.CDLL('libX11.so.6')
xt = C.CDLL('libXtst.so.6')
x.XOpenDisplay.argtypes = [C.c_char_p]
x.XOpenDisplay.restype = C.c_void_p
x.XDefaultRootWindow.argtypes = [C.c_void_p]
x.XDefaultRootWindow.restype = C.c_ulong
x.XQueryTree.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                        C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)]
x.XFetchName.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_void_p)]
x.XFree.argtypes = [C.c_void_p]
x.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
x.XRaiseWindow.argtypes = [C.c_void_p, C.c_ulong]
x.XStringToKeysym.argtypes = [C.c_char_p]
x.XStringToKeysym.restype = C.c_ulong
x.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
x.XKeysymToKeycode.restype = C.c_uint
x.XFlush.argtypes = [C.c_void_p]
x.XCloseDisplay.argtypes = [C.c_void_p]
xt.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]


def parse_args():
    """Parse the command line options for the smoke run."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--out', type=Path, default=PORT / 'scratch/pc-window')
    parser.add_argument('--flip-ms', type=int, default=None,
                        help='GCM_FLIP_MIN_MS for the run; default keeps run.sh default')
    parser.add_argument('--gdb', action='store_true')
    parser.add_argument('--first-key-at', type=int, default=75,
                        help='Seconds before first screenshot and Space press')
    parser.add_argument('--steps', default=None, help=STEPS_HELP)
    parser.add_argument('--shot-every', type=float, default=0,
                        help='Also take a screenshot every N seconds')
    parser.add_argument('--duration', type=float, default=None,
                        help='Seconds to keep running after the last step')
    return parser.parse_args()


def default_steps(first):
    """Return the default calibration timeline starting at ``first`` seconds.

    Screenshot, hold Space, screenshot, hold Return, screenshot, 25 s apart.
    """
    return [(first, 'shot=before-cross'), (first, 'space*1.2'),
            (first + 25, 'shot=after-cross'), (first + 25, 'Return*1.2'),
            (first + 50, 'shot=after-start')]


def parse_steps(text):
    """Parse comma-separated ``T:ACTION`` items into ``(seconds, action)`` tuples sorted by time."""
    steps = []
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue
        at, action = item.split(':', 1)
        steps.append((float(at), action.strip()))
    return sorted(steps, key=lambda s: s[0])


def find_window(d):
    """Return the top-level X11 window whose title contains "Ascension", or ``None``."""
    root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
    children = C.POINTER(C.c_ulong)()
    x.XQueryTree(d, x.XDefaultRootWindow(d), C.byref(root), C.byref(parent), C.byref(children),
                 C.byref(count))
    found = None
    for i in range(count.value):
        name = C.c_void_p()
        if x.XFetchName(d, children[i], C.byref(name)) and name.value:
            title = C.string_at(name).decode(errors='replace')
            x.XFree(name)
            if 'Ascension' in title:
                found = children[i]
                break
    if children:
        x.XFree(children)
    return found


def key(d, name, seconds=DEFAULT_HOLD):
    """Press the X keysym ``name`` through XTest, hold it for ``seconds`` and release it."""
    code = x.XKeysymToKeycode(d, x.XStringToKeysym(name.encode()))
    if not code:
        raise ValueError(f'Unknown keysym {name!r}')
    xt.XTestFakeKeyEvent(d, code, 1, 0)
    x.XFlush(d)
    time.sleep(seconds)
    xt.XTestFakeKeyEvent(d, code, 0, 0)
    x.XFlush(d)


def screenshot(out, label):
    """Save ``<label>.png`` of the root window, plus a copy of the latest frame dump as ``<label>.ppm``.

    The ``.ppm`` copy is made only when ``latest.ppm`` exists.
    """
    if (out / 'latest.ppm').exists():
        shutil.copyfile(out / 'latest.ppm', out / f'{label}.ppm')
    subprocess.run(['import', '-window', 'root', str(out / f'{label}.png')], check=True)


def game_env(out, flip_ms):
    """Build the game environment for a windowed run with input tracing and periodic frame dumps.

    Removes any ``PAD_*`` and ``YDKJ_INJECT_PAD`` variables so input only comes
    from real events, and sets ``GCM_FLIP_MIN_MS`` when ``flip_ms`` is given.
    """
    env = dict(os.environ, SDL_VIDEODRIVER='x11', PS3RECOMP_VK_WINDOW='1',
               GOWA_INPUT_TRACE='1', PS3RECOMP_VK_DUMP=str(out / 'latest.ppm'),
               PS3RECOMP_VK_DUMP_EVERY='10')
    if flip_ms is not None:
        env['GCM_FLIP_MIN_MS'] = str(flip_ms)
    for k in list(env):
        if k.startswith('PAD_') or k == 'YDKJ_INJECT_PAD':
            env.pop(k)
    env['PAD_REPORT_TRACE'] = '1'
    return env


def game_command(out, debug):
    """Return the command that runs the game.

    With ``debug``, writes a gdb script to ``out/gdb.cmd`` that runs the game,
    prints all thread backtraces when it stops and kills it. Extra gdb commands
    can be added through ``SMOKE_GDB_PRE`` and ``SMOKE_GDB_EXTRA``.
    """
    command = ['bash', str(PORT / 'scripts/run.sh')]
    if not debug:
        return command
    script = out / 'gdb.cmd'
    script.write_text('set pagination off\nset print thread-events off\n'
                      'set confirm off\nhandle SIGUSR1 nostop noprint pass\n'
                      'handle SIGUSR2 nostop noprint pass\n'
                      f'{os.environ.get("SMOKE_GDB_PRE", "")}\n'
                      f'run {shlex.quote(str(PORT / "scripts/run.sh"))} > '
                      f'{shlex.quote(str(out / "game.log"))} 2>&1\n'
                      'thread apply all bt 14\n'
                      f'{os.environ.get("SMOKE_GDB_EXTRA", "")}\nkill\nquit\n')
    return ['gdb', '-q', '-batch', '-x', str(script), '--args', *command]


def wait_until(p, start, deadline):
    """Sleep until ``deadline`` seconds after ``start`` or until process ``p`` exits."""
    while time.monotonic() - start < deadline and p.poll() is None:
        time.sleep(.2)


def run_steps(d, p, start, out, steps, shot_every, end_at):
    """Run the step timeline against the game window, then wait until ``end_at``.

    ``shot_every`` adds periodic screenshots. Stops early if the game exits.
    """
    timeline = list(steps)
    if shot_every:
        t = shot_every
        while t < end_at:
            timeline.append((t, f'shot=t{int(t):04d}'))
            t += shot_every
    timeline.sort(key=lambda s: s[0])
    for at, action in timeline:
        wait_until(p, start, at)
        if p.poll() is not None:
            break
        if action == 'shot' or action.startswith('shot='):
            label = action.split('=', 1)[1] if '=' in action else f's{int(at):04d}'
            screenshot(out, label)
            print(f'{at:6.1f}s shot {label}', flush=True)
            continue
        name, _, hold = action.partition('*')
        key(d, name, float(hold) if hold else DEFAULT_HOLD)
        print(f'{at:6.1f}s key {name}', flush=True)
    wait_until(p, start, end_at)


def main():
    """Run the smoke test: launch the game, focus its window, play the steps and press Escape.

    Raises if the window is not found within 25 s or the game exits early. The
    whole process group is always stopped (SIGINT first under gdb, then SIGKILL).
    """
    args = parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    d = x.XOpenDisplay(None)
    if not d:
        raise SystemExit('No X11 display; run with xvfb-run.')
    steps = parse_steps(args.steps) if args.steps else default_steps(args.first_key_at)
    last = steps[-1][0] if steps else 0
    end_at = last + (args.duration if args.duration is not None else 1)
    log = open(out / 'game.log', 'wb')
    debug_log = open(out / 'backtrace.log', 'wb') if args.gdb else log
    p = subprocess.Popen(game_command(out, args.gdb), cwd=PORT, env=game_env(out, args.flip_ms),
                         stdout=debug_log, stderr=subprocess.STDOUT, start_new_session=True)
    start = time.monotonic()
    try:
        window = None
        while time.monotonic() - start < 25 and p.poll() is None:
            window = find_window(d)
            if window:
                break
            time.sleep(.5)
        if not window:
            raise RuntimeError('Game window not found; inspect game.log')
        x.XRaiseWindow(d, window)
        x.XSetInputFocus(d, window, 1, 0)
        x.XFlush(d)
        print(f'Window found: {window}', flush=True)
        run_steps(d, p, start, out, steps, args.shot_every, end_at)
        if p.poll() is not None:
            raise RuntimeError('Game exited unexpectedly')
        key(d, 'Escape', .2)
        time.sleep(.5)
        if p.poll() is not None:
            raise RuntimeError('Game exited on Escape')
    finally:
        if args.gdb and p.poll() is None:
            os.killpg(p.pid, signal.SIGINT)
            try:
                p.wait(timeout=180)
            except subprocess.TimeoutExpired:
                pass
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        log.close()
        if args.gdb:
            debug_log.close()
        x.XCloseDisplay(d)
    print(f'Artifacts: {out}', flush=True)


if __name__ == '__main__':
    main()
