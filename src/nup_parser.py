"""Parser for Nintendo Switch 2 .nup ROM packages."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
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


def parse_game_folder(path: str | Path) -> NupInfo:
    """Inspect an extracted Switch / Switch 2 game folder (containing exefs/ and romfs/)."""
    import re

    folder = Path(path)
    if not folder.is_dir():
        raise NupParseError(f"Dossier introuvable : {folder}")

    if (folder / "exefs" / "main").is_file():
        exefs_dir = folder / "exefs"
    elif (folder / "main").is_file():
        exefs_dir = folder
    else:
        raise NupParseError(
            "Dossier invalide : il doit contenir un sous-dossier 'exefs' avec le fichier 'main'."
        )

    title_id = ""
    npdm = exefs_dir / "main.npdm"
    if npdm.is_file():
        data = npdm.read_bytes()
        if len(data) >= 0x80 and data[0:4] == b"META":
            aci0_off = struct.unpack_from("<I", data, 0x70)[0]
            if aci0_off + 0x20 <= len(data) and data[aci0_off : aci0_off + 4] == b"ACI0":
                pid = struct.unpack_from("<Q", data, aci0_off + 0x10)[0]
                if pid:
                    title_id = f"{pid:016X}"

    if not title_id:
        m = re.search(r"([0-9A-Fa-f]{16})", folder.name)
        if m:
            title_id = m.group(1).upper()

    if not title_id:
        raise NupParseError(
            "Impossible de déterminer le Title ID depuis exefs/main.npdm."
        )

    exefs_files = sorted(f.name for f in exefs_dir.iterdir() if f.is_file())
    total_size = sum(f.stat().st_size for f in exefs_dir.iterdir() if f.is_file())
    romfs_dir = folder / "romfs"
    if romfs_dir.is_dir():
        for root, _, files in os.walk(romfs_dir):
            for fn in files:
                try:
                    total_size += (Path(root) / fn).stat().st_size
                except OSError:
                    pass

    pkg = NupPackage(title_id=title_id, exefs_dir_index=0, version=0, kind=0)
    return NupInfo(
        file_path=folder,
        file_size=total_size,
        title_id=title_id,
        version=0,
        kind=0,
        exefs_files=exefs_files,
        all_packages=[pkg],
    )


def parse_nup(path: str | Path) -> NupInfo:
    """Parse a Switch 2 .nup file header and table of contents (or an exefs/romfs folder)."""
    file_path = Path(path)
    if file_path.is_dir():
        return parse_game_folder(file_path)
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

