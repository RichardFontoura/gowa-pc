import argparse
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

from ps3_keys import NP_KLIC_FREE, NP_KLIC_KEY, SELF_KEYS


@dataclass
class MetaSection:
    data_offset: int
    data_size: int
    kind: int
    program_idx: int
    encrypted: int
    key_idx: int
    iv_idx: int
    compressed: int


SCE_MAGIC = b"SCE\x00"
SCE_HEADER = struct.Struct(">4sIHHIQQ")
EXT_HEADER = struct.Struct(">10Q")
APP_INFO = struct.Struct(">QIIQ")
CONTROL_ENTRY = struct.Struct(">IIQ")
META_INFO_LEN = 0x40
META_HEADER = struct.Struct(">QIIIIII")
META_SECTION = struct.Struct(">QQ8I")
ELF64_HEADER = struct.Struct(">16sHHIQQQIHHHHHH")
ELF64_PHDR = struct.Struct(">IIQQQQQQ")
DEBUG_FLAG = 0x8000
CONTROL_NPDRM = 3
NPD_OFFSET_IN_CONTROL = 0x10
NPD_LICENSE_FREE = 3
SECTION_PHDR = 2
SECTION_ENCRYPTED = 3
SECTION_COMPRESSED = 2


def fail(message: str) -> None:
    sys.exit(f"erro: {message}")


def npd_header(data: bytes, control_offset: int, control_size: int) -> bytes | None:
    offset = control_offset
    while offset < control_offset + control_size:
        kind, size, _ = CONTROL_ENTRY.unpack_from(data, offset)
        if size == 0:
            break
        if kind == CONTROL_NPDRM:
            return data[offset + NPD_OFFSET_IN_CONTROL:offset + size]
        offset += size
    return None


def strip_npdrm_layer(meta_info: bytes, npd: bytes, klic: bytes) -> bytes:
    license_type = struct.unpack_from(">I", npd, 8)[0]
    if license_type != NPD_LICENSE_FREE:
        fail(f"licença NPDRM {license_type} exige RAP, não suportada")
    npdrm_key = AES.new(NP_KLIC_KEY, AES.MODE_ECB).decrypt(klic)
    return AES.new(npdrm_key, AES.MODE_CBC, iv=bytes(16)).decrypt(meta_info)


def ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=iv).decrypt(data)


def decrypt_self(data: bytes, klic: bytes) -> bytes:
    magic, _, flags, _, meta_offset, header_len, _ = SCE_HEADER.unpack_from(data)
    if magic != SCE_MAGIC:
        fail("não é um SELF (magic SCE ausente)")
    ext = EXT_HEADER.unpack_from(data, SCE_HEADER.size)
    app_info_off, elf_off, phdr_off, shdr_off = ext[1], ext[2], ext[3], ext[4]
    control_off, control_size = ext[7], ext[8]
    _, _, program_type, _ = APP_INFO.unpack_from(data, app_info_off)

    info_off = SCE_HEADER.size + meta_offset
    meta_info = data[info_off:info_off + META_INFO_LEN]
    headers = data[info_off + META_INFO_LEN:header_len]

    if not flags & DEBUG_FLAG:
        keyset = SELF_KEYS.get((program_type, flags))
        if keyset is None:
            fail(f"sem chave para programa tipo {program_type} revisão 0x{flags:X} em ps3_keys.py")
        erk, riv = keyset
        npd = npd_header(data, control_off, control_size)
        if npd is not None:
            meta_info = strip_npdrm_layer(meta_info, npd, klic)
        meta_info = AES.new(erk, AES.MODE_CBC, iv=riv).decrypt(meta_info)

    if meta_info[0x10] != 0 or meta_info[0x30] != 0:
        fail("metadados não decriptaram; KLIC ou chave errada")
    headers = ctr(meta_info[0x00:0x10], meta_info[0x20:0x30], headers)

    _, _, section_count, key_count, _, _, _ = META_HEADER.unpack_from(headers)
    sections = []
    for index in range(section_count):
        fields = META_SECTION.unpack_from(headers, META_HEADER.size + index * META_SECTION.size)
        offset, size, kind, program_idx, _, _, encrypted, key_idx, iv_idx, compressed = fields
        sections.append(MetaSection(offset, size, kind, program_idx, encrypted, key_idx, iv_idx, compressed))
    keys_off = META_HEADER.size + section_count * META_SECTION.size
    data_keys = [headers[keys_off + i * 0x10:keys_off + (i + 1) * 0x10] for i in range(key_count)]

    ehdr = data[elf_off:elf_off + ELF64_HEADER.size]
    ident, *_, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum, _ = ELF64_HEADER.unpack(ehdr)
    if ident[4] != 2:
        fail("só ELF de 64 bits é suportado")
    phdrs = data[phdr_off:phdr_off + phnum * phentsize]
    shdrs = data[shdr_off:shdr_off + shnum * shentsize] if shdr_off else b""

    elf = bytearray()

    def put(offset: int, chunk: bytes) -> None:
        end = offset + len(chunk)
        if len(elf) < end:
            elf.extend(bytes(end - len(elf)))
        elf[offset:end] = chunk

    put(0, ehdr)
    put(phoff, phdrs)
    for section in sections:
        if section.kind != SECTION_PHDR:
            continue
        chunk = data[section.data_offset:section.data_offset + section.data_size]
        if section.encrypted == SECTION_ENCRYPTED:
            if section.key_idx >= key_count or section.iv_idx >= key_count:
                fail(f"seção do programa {section.program_idx} com índice de chave inválido")
            chunk = ctr(data_keys[section.key_idx], data_keys[section.iv_idx], chunk)
        _, _, p_offset, _, _, p_filesz, _, _ = ELF64_PHDR.unpack_from(phdrs, section.program_idx * phentsize)
        if section.compressed == SECTION_COMPRESSED:
            chunk = zlib.decompressobj().decompress(chunk, p_filesz)
        put(p_offset, chunk)
    if shdrs:
        put(shoff, shdrs)
    return bytes(elf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decripta um SELF/NPDRM do PS3 para ELF")
    parser.add_argument("self_file", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--klic", type=bytes.fromhex, default=NP_KLIC_FREE, help="KLIC em hex (padrão: NP_KLIC_FREE)")
    args = parser.parse_args()

    if len(args.klic) != 16:
        fail("a KLIC tem 16 bytes")
    args.output.write_bytes(decrypt_self(args.self_file.read_bytes(), args.klic))
    print(f"Decrypted {args.self_file.name} -> {args.output}")


if __name__ == "__main__":
    main()
