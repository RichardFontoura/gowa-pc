"""Extract the WWS SPU job modules embedded in a big-endian 64-bit PPU ELF.

A job module is recognised by its header: 16-byte aligned, starting with four
SPU ``ila`` instructions and carrying the magic 0xC0DEC0DE at offset 0x20; the
word at 0x10 is the entry point and the word at 0x14 the image size. Each
module runs until the next module (or the end of its PT_LOAD segment),
trimmed to the image size rounded up to 16 bytes when that is smaller.

Each module is written as ``jobmod_<EA>_e<ENTRY>.bin`` (EA = guest virtual
address, hex) for ps3recomp's tools/lift_jobmods.py to lift.

Usage example:
    ../.venv/bin/python scripts/extract_jobmods_elf.py game/EBOOT.ELF --out analysis/jobmods
"""
import argparse
import struct
import sys
from pathlib import Path

PT_LOAD = 1
JOBMOD_MAGIC = 0xC0DEC0DE
# Offset of JOBMOD_MAGIC from the start of the module header.
MAGIC_OFFSET = 0x20
# Top 7 bits (bits 31..25) of the SPU "ila" (immediate load address) opcode.
ILA_OPCODE7 = 0x21


def load_segments(elf: bytes) -> list[tuple[int, int, int]]:
    """Return the non-empty PT_LOAD segments of an ELF64 big-endian image.

    Args:
        elf: whole ELF file contents.

    Returns:
        A list of (virtual address, file offset, file size) tuples.
    """
    phoff, = struct.unpack_from(">Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from(">HH", elf, 0x36)
    segments = []
    for i in range(phnum):
        p_type, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from(">IIQQQQ", elf, phoff + i * phentsize)
        if p_type == PT_LOAD and filesz:
            segments.append((vaddr, offset, filesz))
    return segments


def is_module_header(data: bytes, pos: int) -> bool:
    """Tell whether a job module header starts at ``pos`` in ``data``.

    True when the first four words are ``ila`` instructions and the magic is
    at ``pos + MAGIC_OFFSET``; False also when the header would not fit.
    """
    if pos < 0 or pos + MAGIC_OFFSET + 4 > len(data):
        return False
    words = struct.unpack_from(">4I", data, pos)
    if any((w >> 25) != ILA_OPCODE7 for w in words):
        return False
    return struct.unpack_from(">I", data, pos + MAGIC_OFFSET)[0] == JOBMOD_MAGIC


def find_modules(elf: bytes) -> list[tuple[int, bytes]]:
    """Find every job module in the loadable segments of an ELF.

    Args:
        elf: whole ELF file contents.

    Returns:
        A list of (guest virtual address, module bytes) tuples in segment and
        address order.
    """
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
    """Parse arguments, write each module found to --out and list them.

    Prints one line per module on stdout and the total on stderr; exits with
    an error if the ELF contains no module.
    """
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
