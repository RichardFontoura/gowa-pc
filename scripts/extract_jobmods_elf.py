import argparse
import struct
import sys
from pathlib import Path

PT_LOAD = 1
JOBMOD_MAGIC = 0xC0DEC0DE
MAGIC_OFFSET = 0x20
ILA_OPCODE7 = 0x21


def load_segments(elf: bytes) -> list[tuple[int, int, int]]:
    phoff, = struct.unpack_from(">Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from(">HH", elf, 0x36)
    segments = []
    for i in range(phnum):
        p_type, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from(">IIQQQQ", elf, phoff + i * phentsize)
        if p_type == PT_LOAD and filesz:
            segments.append((vaddr, offset, filesz))
    return segments


def is_module_header(data: bytes, pos: int) -> bool:
    if pos < 0 or pos + MAGIC_OFFSET + 4 > len(data):
        return False
    words = struct.unpack_from(">4I", data, pos)
    if any((w >> 25) != ILA_OPCODE7 for w in words):
        return False
    return struct.unpack_from(">I", data, pos + MAGIC_OFFSET)[0] == JOBMOD_MAGIC


def find_modules(elf: bytes) -> list[tuple[int, bytes]]:
    modules = []
    magic = struct.pack(">I", JOBMOD_MAGIC)
    for vaddr, offset, filesz in load_segments(elf):
        data = elf[offset:offset + filesz]
        starts = []
        hit = data.find(magic)
        while hit >= 0:
            start = hit - MAGIC_OFFSET
            if start % 16 == 0 and is_module_header(data, start):
                starts.append(start)
            hit = data.find(magic, hit + 1)
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(data)
            image_size = struct.unpack_from(">I", data, start + 0x14)[0]
            if 0 < image_size < end - start:
                end = start + ((image_size + 15) & ~15)
            modules.append((vaddr + start, data[start:end]))
    return modules


def main() -> None:
    parser = argparse.ArgumentParser(description="Extrai os módulos de job WWS (C0DEC0DE) embutidos num ELF PPU")
    parser.add_argument("elf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    modules = find_modules(args.elf.read_bytes())
    if not modules:
        sys.exit("nenhum módulo de job encontrado")
    args.out.mkdir(parents=True, exist_ok=True)
    for ea, blob in modules:
        entry = struct.unpack_from(">I", blob, 0x10)[0]
        path = args.out / f"jobmod_{ea:08X}_e{entry:X}.bin"
        path.write_bytes(blob)
        print(f"{path.name}  {len(blob):#x} bytes")
    print(f"{len(modules)} módulos em {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
