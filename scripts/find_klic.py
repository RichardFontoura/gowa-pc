import argparse
import os
import re
import sys
from pathlib import Path

from Crypto.Hash import CMAC
from Crypto.Cipher import AES

NPD_MAGIC = b"NPD\x00"
NPD_SIGNED_LEN = 0x60
NPD_DEV_HASH_LEN = 0x10


def vault_key(vault: str, name: str, origin: Path) -> bytes:
    match = re.search(rf"\b{name}\[0x10\]\s*=\s*\{{([^}}]*)\}}", vault)
    if not match:
        sys.exit(f"{name} não encontrada em {origin}")
    return bytes(int(byte, 16) for byte in re.findall(r"0x([0-9A-Fa-f]{2})", match.group(1)))


def read_npd(self_path: Path) -> bytes:
    data = self_path.read_bytes()
    offset = data.find(NPD_MAGIC)
    if offset < 0:
        sys.exit(f"{self_path}: sem header NPD, não é um SELF NPDRM")
    return data[offset:offset + NPD_SIGNED_LEN + NPD_DEV_HASH_LEN]


def klic_matches(klic: bytes, npd: bytes, omac_key: bytes) -> bool:
    key = bytes(a ^ b for a, b in zip(klic, omac_key))
    mac = CMAC.new(key, ciphermod=AES)
    mac.update(npd[:NPD_SIGNED_LEN])
    return mac.digest() == npd[NPD_SIGNED_LEN:NPD_SIGNED_LEN + NPD_DEV_HASH_LEN]


def candidates(sources: list[Path], klic_free: bytes):
    yield "NP_KLIC_FREE", klic_free
    for source in sources:
        blob = source.read_bytes()
        for offset in range(len(blob) - 15):
            yield f"{source.name}+0x{offset:X}", blob[offset:offset + 16]


def main() -> None:
    parser = argparse.ArgumentParser(description="Acha a KLIC de um SELF NPDRM varrendo binários candidatos")
    parser.add_argument("--keys", type=Path, default=os.environ.get("PS3_KEY_VAULT"))
    parser.add_argument("self_file", type=Path)
    parser.add_argument("sources", type=Path, nargs="+")
    args = parser.parse_args()

    if not args.keys or not args.keys.is_file():
        sys.exit("informe --keys (ou PS3_KEY_VAULT): header C com os arrays NP_OMAC_KEY_2 e NP_KLIC_FREE")
    vault = args.keys.read_text(encoding="utf-8", errors="replace")
    omac_key = vault_key(vault, "NP_OMAC_KEY_2", args.keys)
    klic_free = vault_key(vault, "NP_KLIC_FREE", args.keys)
    npd = read_npd(args.self_file)
    print(f"content_id: {npd[0x10:0x40].rstrip(bytes(1)).decode()}", file=sys.stderr)
    for origin, klic in candidates(args.sources, klic_free):
        if klic_matches(klic, npd, omac_key):
            print(f"{klic.hex().upper()}  ({origin})")
            return
    sys.exit("nenhuma KLIC válida nos candidatos")


if __name__ == "__main__":
    main()
