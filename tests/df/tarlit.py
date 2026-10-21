"""Literal ustar generator for receive tests (no `tarfile`).

`tar_bytes` emits exactly the listed records in the given order: no implicit
root record, no implicit parents. Checksums are correct; two zero blocks end
the archive. Malformed archives are made by editing the returned bytes and
calling `fix_checksum`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

BLOCK = 512
REG = "0"
LNK = "1"
SYM = "2"
CHR = "3"
BLK = "4"
DIR = "5"
FIFO = "6"
CONT = "7"
PAX = "x"
GLOBAL = "g"

#: (name, type-or-mode, data-or-target). An `int` kind is a regular file with
#: that mode; a `str` kind is a type flag (payload is a link target for `SYM`
#: and `LNK`, body bytes otherwise).
Member = tuple[str, int | str] | tuple[str, int | str, bytes | str]

_DEFAULT_MODE = {DIR: 0o755, SYM: 0o777}


def tar_bytes(
    members: Sequence[Member], *, pax: Mapping[int, bytes] | None = None
) -> bytes:
    """Return a literal archive; `pax[i]` is a raw x-header body before member i."""
    pax = pax or {}
    out = bytearray()
    for index, member in enumerate(members):
        if index in pax:
            out += _pax_record(pax[index])
        out += _member(member)
    if len(members) in pax:
        out += _pax_record(pax[len(members)])
    out += bytes(2 * BLOCK)
    return bytes(out)


def _member(member: Member) -> bytes:
    name, kind = member[0], member[1]
    payload: bytes | str = member[2] if len(member) == 3 else b""  # type: ignore[misc]
    if isinstance(kind, int):
        typeflag, mode = REG, kind
    else:
        typeflag, mode = kind, _DEFAULT_MODE.get(kind, 0o644)
    if typeflag in (SYM, LNK):
        assert isinstance(payload, str)
        return header(name, typeflag, mode=mode, linkname=payload)
    data = payload.encode() if isinstance(payload, str) else payload
    return header(name, typeflag, mode=mode, size=len(data)) + _padded(data)


def _pax_record(body: bytes) -> bytes:
    return header("PaxHeader/x", PAX, mode=0o644, size=len(body)) + _padded(body)


def _padded(data: bytes) -> bytes:
    return data + bytes(-len(data) % BLOCK)


def header(
    name: str | bytes,
    typeflag: str,
    *,
    mode: int = 0o644,
    size: int = 0,
    linkname: str = "",
    size_field: bytes | None = None,
) -> bytes:
    """One ustar header block with a correct checksum."""
    raw_name = name.encode() if isinstance(name, str) else name
    prefix = b""
    if len(raw_name) > 100:
        prefix, _, raw_name = raw_name.rpartition(b"/")
    block = bytearray(BLOCK)
    _put(block, 0, 100, raw_name)
    _put(block, 100, 8, b"%07o\0" % mode)
    _put(block, 108, 8, b"%07o\0" % 1000)
    _put(block, 116, 8, b"%07o\0" % 1000)
    _put(block, 124, 12, size_field if size_field is not None else b"%011o\0" % size)
    _put(block, 136, 12, b"00000000000\0")
    block[156:157] = typeflag.encode()
    _put(block, 157, 100, linkname.encode())
    _put(block, 257, 8, b"ustar\x0000")
    _put(block, 265, 32, b"user")
    _put(block, 297, 32, b"group")
    _put(block, 329, 8, b"0000000\0")
    _put(block, 337, 8, b"0000000\0")
    _put(block, 345, 155, prefix)
    return fix_checksum(bytes(block))


def _put(block: bytearray, offset: int, width: int, value: bytes) -> None:
    if len(value) > width:
        raise ValueError(f"field too long: {value!r}")
    block[offset : offset + len(value)] = value


def fix_checksum(block: bytes) -> bytes:
    """Return `block` with its checksum field recomputed."""
    data = bytearray(block)
    data[148:156] = b" " * 8
    data[148:156] = b"%06o\0 " % sum(data)
    return bytes(data)
