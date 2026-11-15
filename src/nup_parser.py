"""Parser for Nintendo Switch 2 .nup ROM packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct


NUP_MAGIC = b"NUP\x01"
MAX_TOC_SIZE = 64 << 20  # 64 MiB


class NupParseError(Exception):
    """Raised when a file is not a valid or readable Switch 2 .nup package."""


@dataclass(frozen=True)
class NupPackage:
    title_id: str
    exefs_dir_index: int
    version: int
    kind: int


@dataclass(frozen=True)
class NupInfo:
    file_path: Path
    file_size: int
    title_id: str
    version: int
    kind: int
    exefs_files: list[str] = field(default_factory=list)
    all_packages: list[NupPackage] = field(default_factory=list)


def _unpack_from(buf: bytes, offset: int, fmt: str) -> tuple:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(buf):
        raise NupParseError("Table des matières tronquée ou invalide.")
    return struct.unpack_from(fmt, buf, offset)


def parse_nup(path: str | Path) -> NupInfo:
    """Parse a Switch 2 .nup file header and table of contents."""
    file_path = Path(path)
    if not file_path.is_file():
        raise NupParseError(f"Fichier introuvable : {file_path}")

    file_size = file_path.stat().st_size
    if file_size < 0x80:
        raise NupParseError("Fichier trop petit pour être un package .nup valide.")

    with file_path.open("rb") as f:
        super_block = f.read(0x80)
        if super_block[:4] != NUP_MAGIC:
            raise NupParseError(
                "Ce fichier n'est pas un package .nup Switch 2 valide (en-tête NUP\\x01 absent)."
            )

        (toc_off,) = struct.unpack_from("<Q", super_block, 8)
        (toc_size,) = struct.unpack_from("<Q", super_block, 16)

        if toc_size == 0 or toc_size > MAX_TOC_SIZE:
            raise NupParseError("Taille de table des matières invalide.")
        if toc_off + toc_size > file_size:
            raise NupParseError("Table des matières hors limites (fichier incomplet ?).")

        f.seek(toc_off)
        toc = f.read(toc_size)
        if len(toc) != toc_size:
            raise NupParseError("Impossible de lire la table des matières complète.")

    (packages_count,) = _unpack_from(toc, 4, "<I")
    (dirs_count,) = _unpack_from(toc, 8, "<Q")
    (files_count,) = _unpack_from(toc, 16, "<Q")
    (pool_offset,) = _unpack_from(toc, 24, "<Q")

    packages: list[NupPackage] = []
    pos = 0x30
    for _ in range(packages_count):
        (title_raw,) = _unpack_from(toc, pos, "<Q")
        (exefs_idx,) = _unpack_from(toc, pos + 0x0C, "<I")
        (version,) = _unpack_from(toc, pos + 0x14, "<I")
        (kind,) = _unpack_from(toc, pos + 0x18, "<B")
        packages.append(
            NupPackage(
                title_id=f"{title_raw:016X}",
                exefs_dir_index=exefs_idx,
                version=version,
                kind=kind,
            )
        )
        pos += 0x40

    program_packages = [
        pkg
        for pkg in packages
        if pkg.exefs_dir_index != 0xFFFFFFFF and pkg.kind in (0, 1)
    ]
    if not program_packages:
        raise NupParseError("Aucun programme ExeFS trouvé dans ce package .nup.")

    best = max(program_packages, key=lambda pkg: pkg.version)

    dir_table = pos
    file_table = dir_table + dirs_count * 0x18
    children_table = file_table + files_count * 0x50

    exefs_files: list[str] = []
    dir_entry_off = dir_table + best.exefs_dir_index * 0x18
    first_child, child_count = _unpack_from(toc, dir_entry_off + 8, "<II")
    for idx in range(child_count):
        (child_id,) = _unpack_from(toc, children_table + 4 * (first_child + idx), "<I")
        if child_id < dirs_count:
            continue
        file_entry_off = file_table + (child_id - dirs_count) * 0x50
        name_off, name_len = _unpack_from(toc, file_entry_off, "<II")
        name_start = pool_offset + name_off
        name_end = name_start + name_len
        if name_end <= len(toc):
            exefs_files.append(toc[name_start:name_end].decode("utf-8", errors="replace"))

    return NupInfo(
        file_path=file_path,
        file_size=file_size,
        title_id=best.title_id,
        version=best.version,
        kind=best.kind,
        exefs_files=exefs_files,
        all_packages=packages,
    )

