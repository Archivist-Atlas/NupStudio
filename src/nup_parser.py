"""Parser for Nintendo Switch 2 .nup ROM packages."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import struct

try:
    import zstandard as _zstd
except ImportError:
    _zstd = None


NUP_MAGIC = b"NUP\x01"
NSO_MAGIC = b"NSO0"
MAX_TOC_SIZE = 64 << 20  # 64 MiB
_NSO_MODULE_NAMES = {"main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "sdk", "rtld"}


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
    exefs_sha256: dict[str, str] = field(default_factory=dict)
    nso_build_ids: dict[str, str] = field(default_factory=dict)
    is_folder: bool = False


def _extract_nso_build_id(header: bytes) -> str:
    """Extract the NSO0 ModuleId / Build ID (offset 0x40..0x60) as lowercase hex."""
    if len(header) < 0x60 or header[:4] != NSO_MAGIC:
        return ""
    raw = header[0x40:0x60]
    trimmed = raw.rstrip(b"\x00")
    if not trimmed:
        return ""
    # Preserve at least 16 bytes (32 hex chars) when zero-padded
    if len(trimmed) < 16:
        return raw[:16].hex()
    return trimmed.hex()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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

    active_root = folder
    detected_version = 0
    updates_dir = folder / "updates"
    if updates_dir.is_dir():
        upd_candidates = [
            d for d in updates_dir.iterdir()
            if d.is_dir() and (d / "exefs" / "main").is_file()
        ]
        if upd_candidates:
            best_upd = max(
                upd_candidates,
                key=lambda d: int(d.name) if d.name.isdigit() else -1,
            )
            active_root = best_upd
            if best_upd.name.isdigit():
                detected_version = int(best_upd.name)

    if (active_root / "exefs" / "main").is_file():
        exefs_dir = active_root / "exefs"
    elif (active_root / "main").is_file():
        exefs_dir = active_root
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
    exefs_sha256: dict[str, str] = {}
    nso_build_ids: dict[str, str] = {}
    for fn in exefs_files:
        fp = exefs_dir / fn
        try:
            exefs_sha256[fn] = _sha256_file(fp)
            if fn in _NSO_MODULE_NAMES:
                with fp.open("rb") as fh:
                    bid = _extract_nso_build_id(fh.read(0x60))
                if bid:
                    nso_build_ids[fn] = bid
        except OSError:
            pass

    total_size = sum(f.stat().st_size for f in exefs_dir.iterdir() if f.is_file())
    romfs_dir = active_root / "romfs"
    if romfs_dir.is_dir():
        for root, _, files in os.walk(romfs_dir):
            for fn in files:
                try:
                    total_size += (Path(root) / fn).stat().st_size
                except OSError:
                    pass

    pkg = NupPackage(title_id=title_id, exefs_dir_index=0, version=detected_version, kind=1 if detected_version else 0)
    return NupInfo(
        file_path=folder,
        file_size=total_size,
        title_id=title_id,
        version=detected_version,
        kind=1 if detected_version else 0,
        exefs_files=exefs_files,
        all_packages=[pkg],
        exefs_sha256=exefs_sha256,
        nso_build_ids=nso_build_ids,
        is_folder=True,
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
    exefs_sha256: dict[str, str] = {}
    nso_build_ids: dict[str, str] = {}
    nso_extents: list[tuple[str, int, int, int]] = []
    dir_entry_off = dir_table + best.exefs_dir_index * 0x18
    first_child, child_count = _unpack_from(toc, dir_entry_off + 8, "<II")
    for idx in range(child_count):
        (child_id,) = _unpack_from(toc, children_table + 4 * (first_child + idx), "<I")
        if child_id < dirs_count:
            continue
        file_entry_off = file_table + (child_id - dirs_count) * 0x50
        name_off, name_len, fsize, ext_off, ext_size, sha_bytes, codec = _unpack_from(
            toc, file_entry_off, "<IIQQQ32sB"
        )
        name_start = pool_offset + name_off
        name_end = name_start + name_len
        if name_end <= len(toc):
            fname = toc[name_start:name_end].decode("utf-8", errors="replace")
            exefs_files.append(fname)
            if any(sha_bytes):
                exefs_sha256[fname] = sha_bytes.hex()
            if fname in _NSO_MODULE_NAMES and fsize >= 0x60 and ext_off + ext_size <= file_size:
                nso_extents.append((fname, ext_off, ext_size, codec))

    if nso_extents:
        try:
            with file_path.open("rb") as f:
                dctx = _zstd.ZstdDecompressor() if _zstd is not None else None
                for fname, ext_off, ext_size, codec in nso_extents:
                    f.seek(ext_off)
                    if codec == 0:
                        hdr = f.read(0x60)
                    elif codec == 1 and dctx is not None:
                        try:
                            with dctx.stream_reader(f, closefd=False) as reader:
                                hdr = reader.read(0x60)
                        except Exception:
                            hdr = b""
                    else:
                        hdr = b""
                    bid = _extract_nso_build_id(hdr)
                    if bid:
                        nso_build_ids[fname] = bid
        except OSError:
            pass

    return NupInfo(
        file_path=file_path,
        file_size=file_size,
        title_id=best.title_id,
        version=best.version,
        kind=best.kind,
        exefs_files=exefs_files,
        all_packages=packages,
        exefs_sha256=exefs_sha256,
        nso_build_ids=nso_build_ids,
        is_folder=False,
    )

