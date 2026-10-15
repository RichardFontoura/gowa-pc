import re
import sys
from pathlib import Path

THREAD_RE = re.compile(r'^Thread (\d+) \(.*?LWP (\d+)\)(?: "([^"]*)")?\)?:')
FRAME_RE = re.compile(r'^#(\d+)\s+(?:0x[0-9a-f]+ in )?([\w:~<>]+)\s*\(.*?\)(?: at ([^\s]+))?')
NOISE = ("libc.so", "libvulkan", "nvidia", "libGLX", "libSDL", "libpthread", "ld-linux")
NOISE_FUNCS = {"??", "start_thread", "clone3", "__GI___clone3"}


def keep(func: str, location: str | None) -> bool:
    if func in NOISE_FUNCS or func.startswith("__"):
        return False
    return not (location and any(n in location for n in NOISE))


def short_location(location: str | None) -> str:
    return Path(location).name if location else ""


def summarize(text: str) -> list[str]:
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
    text = Path(sys.argv[1]).read_text(errors="replace")
    print("\n".join(summarize(text)))


if __name__ == "__main__":
    main()
