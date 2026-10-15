"""Condense a gdb ``thread apply all bt`` dump into one short stack per thread.

Frames from system, graphics-driver and SDL libraries, unknown frames and
functions whose names start with "__" are dropped; source paths are reduced
to file names. Threads left with no frames are omitted. Used by hang_dump.sh.

Usage example:
    ../.venv/bin/python scripts/summarize_bt.py analysis/hang_bt.txt
"""
import re
import sys
from pathlib import Path

# gdb thread header: "Thread <n> (... LWP <lwp>) "<name>"):"; the name is optional.
THREAD_RE = re.compile(r'^Thread (\d+) \(.*?LWP (\d+)\)(?: "([^"]*)")?\)?:')
# gdb frame line: "#<n> [0x... in ]<function> (...) [at <location>]".
FRAME_RE = re.compile(r'^#(\d+)\s+(?:0x[0-9a-f]+ in )?([\w:~<>]+)\s*\(.*?\)(?: at ([^\s]+))?')
NOISE = ("libc.so", "libvulkan", "nvidia", "libGLX", "libSDL", "libpthread", "ld-linux")
NOISE_FUNCS = {"??", "start_thread", "clone3", "__GI___clone3"}


def keep(func: str, location: str | None) -> bool:
    """Tell whether a frame is worth showing (not library or runtime noise)."""
    if func in NOISE_FUNCS or func.startswith("__"):
        return False
    return not (location and any(n in location for n in NOISE))


def short_location(location: str | None) -> str:
    """Return the file-name part of a frame's source location, or ""."""
    return Path(location).name if location else ""


def summarize(text: str) -> list[str]:
    """Turn gdb backtrace output into summary lines.

    Args:
        text: full gdb output.

    Returns:
        For each thread with kept frames, a ``[<gdb id>] lwp=<lwp> <name>``
        line followed by one indented ``<function> <file>`` line per frame.
    """
    out: list[str] = []
    for block in re.split(r"\n(?=Thread \d+ )", text):
        lines = block.strip().splitlines()
        if not lines:
            continue
        header = THREAD_RE.match(lines[0])
        if not header:
            continue
        tid, lwp, name = header.groups()
        frames = []
        for line in lines[1:]:
            frame = FRAME_RE.match(line)
            if frame and keep(frame.group(2), frame.group(3)):
                frames.append(f"{frame.group(2)} {short_location(frame.group(3))}".strip())
        if frames:
            out.append(f"[{tid}] lwp={lwp} {name or ''}")
            out.extend(f"    {f}" for f in frames)
    return out


def main() -> None:
    """Print the summary of the gdb output file given as the only argument."""
    text = Path(sys.argv[1]).read_text(errors="replace")
    print("\n".join(summarize(text)))


if __name__ == "__main__":
    main()
