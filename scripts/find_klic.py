import argparse
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Hash import CMAC

from ps3_keys import NP_KLIC_FREE, NP_OMAC_KEY_2

NPD_MAGIC = b"NPD\x00"
NPD_SIGNED_LEN = 0x60
NPD_DEV_HASH_LEN = 0x10


def read_npd(self_path: Path) -> bytes:
    data = self_path.read_bytes()
    offset = data.find(NPD_MAGIC)
    if offset < 0:
        sys.exit(f"{self_path}: sem header NPD, não é um SELF NPDRM")
    return data[offset:offset + NPD_SIGNED_LEN + NPD_DEV_HASH_LEN]


def klic_matches(klic: bytes, npd: bytes) -> bool:
    key = bytes(a ^ b for a, b in zip(klic, NP_OMAC_KEY_2))
    mac = CMAC.new(key, ciphermod=AES)
    mac.update(npd[:NPD_SIGNED_LEN])
    return mac.digest() == npd[NPD_SIGNED_LEN:NPD_SIGNED_LEN + NPD_DEV_HASH_LEN]


def candidates(sources: list[Path]):
    yield "NP_KLIC_FREE", NP_KLIC_FREE
    for source in sources:
        blob = source.read_bytes()
        for offset in range(len(blob) - 15):
            yield f"{source.name}+0x{offset:X}", blob[offset:offset + 16]


def main() -> None:
    parser = argparse.ArgumentParser(description="Acha a KLIC de um SELF NPDRM varrendo binários candidatos")
    parser.add_argument("self_file", type=Path)
    parser.add_argument("sources", type=Path, nargs="+")
    args = parser.parse_args()

    npd = read_npd(args.self_file)
    print(f"content_id: {npd[0x10:0x40].rstrip(bytes(1)).decode()}", file=sys.stderr)
    for origin, klic in candidates(args.sources):
        if klic_matches(klic, npd):
            print(f"{klic.hex().upper()}  ({origin})")
            return
    sys.exit("nenhuma KLIC válida nos candidatos")


if __name__ == "__main__":
    main()
