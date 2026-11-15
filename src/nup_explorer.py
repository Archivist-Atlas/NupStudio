"""NUP Explorer & in-place mutator backend (browse, extract, rename, delete, import, copy)."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import struct
import sys
from typing import Callable, Optional
import zstandard as zstd


def _ensure_nup_pkg(root_dir: Path) -> None:
    pkg_parent = str(root_dir / "tools" / "packagers")
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)


@dataclass(frozen=True)
class NupPackageSummary:
    index: int
    kind_name: str
    title_id: str
    version: int
    version_label: str
    root_dir: int


@dataclass(frozen=True)
class NupNodeEntry:
    pkg_index: int
    rel_parts: tuple[str, ...]
    name: str
    is_dir: bool
    size: int = 0
    codec: str = ""
    sha256: str = ""
    dir_index: int = -1
    file_index: int = -1


@dataclass(frozen=True)
class NupMutation:
    """One edit operation on a .nup container."""

    kind: str  # "rename" | "delete" | "import" | "copy" | "mod_import" | "mod_copy" | "delete_package"
    pkg_index: int
    rel_parts: tuple[str, ...]
    new_name: str = ""
    local_path: Optional[Path] = None
    dest_pkg_index: Optional[int] = None
    dest_dir_parts: Optional[tuple[str, ...]] = None


@dataclass
class _MutFile:
    name: bytes
    size: int
    sha256: bytes
    codec: int
    codec_flags: int
    # Either existing extent in source NupReader OR new local file path:
    src_extent: Optional[tuple[int, int]] = None
    local_path: Optional[Path] = None
    # Assigned during serialization:
    extent_offset: int = 0
    extent_size: int = 0
    id: int = -1


@dataclass
class _MutDir:
    name: bytes
    children: list[_MutDir | _MutFile] = field(default_factory=list)
    id: int = -1
    first_child: int = 0
    child_count: int = 0


def load_nup_tree(
    root_dir: Path, nup_path: Path
) -> tuple[list[NupPackageSummary], dict[int, list[NupNodeEntry]]]:
    """Read package summaries and per-package file/dir entries without keeping handles open."""
    _ensure_nup_pkg(root_dir)
    from nup import format as F
    from nup.access import app_meta, iter_children
    from nup.read import NupReader

    pkgs: list[NupPackageSummary] = []
    entries_by_pkg: dict[int, list[NupNodeEntry]] = {}

    with NupReader(str(nup_path)) as r:
        for p in r.packages:
            meta = app_meta(r, p)
            disp = meta.get(F.TLV_DISPLAY_VERSION, b"").decode("utf-8", "replace")
            ver_label = disp if disp else f"v{p.version}"
            pkgs.append(
                NupPackageSummary(
                    index=p.index,
                    kind_name=p.kind_name,
                    title_id=f"{p.title_id:016X}",
                    version=p.version,
                    version_label=ver_label,
                    root_dir=p.root_dir,
                )
            )
            pkg_entries: list[NupNodeEntry] = []

            def walk(dir_idx: int, prefix: tuple[str, ...]) -> None:
                for child in iter_children(r, dir_idx):
                    name = child.name.decode("utf-8", "surrogateescape")
                    parts = prefix + (name,)
                    if child.is_dir:
                        pkg_entries.append(
                            NupNodeEntry(
                                pkg_index=p.index,
                                rel_parts=parts,
                                name=name,
                                is_dir=True,
                                dir_index=child.index,
                            )
                        )
                        walk(child.index, parts)
                    else:
                        _, size, sha, _eoff, _esz, codec = r.file_info(child.index)
                        tag = (
                            "zstd"
                            if codec == F.CODEC_ZSTD
                            else ("raw" if size else "empty")
                        )
                        pkg_entries.append(
                            NupNodeEntry(
                                pkg_index=p.index,
                                rel_parts=parts,
                                name=name,
                                is_dir=False,
                                size=size,
                                codec=tag,
                                sha256=sha.hex(),
                                file_index=child.index,
                            )
                        )

            walk(p.root_dir, ())
            entries_by_pkg[p.index] = pkg_entries

    return pkgs, entries_by_pkg


def read_nup_file_bytes(
    root_dir: Path, nup_path: Path, pkg_index: int, rel_parts: tuple[str, ...]
) -> bytes:
    """Read a single file's decompressed payload from a .nup container."""
    _ensure_nup_pkg(root_dir)
    from nup.access import resolve_path
    from nup.read import NupReader

    with NupReader(str(nup_path)) as r:
        pkg = r.packages[pkg_index]
        ref = resolve_path(r, pkg, "/".join(rel_parts))
        if ref is None or ref.is_dir:
            raise FileNotFoundError("/".join(rel_parts))
        return r.read_file(ref.index)


def extract_nup_entry(
    root_dir: Path,
    nup_path: Path,
    pkg_index: int,
    rel_parts: tuple[str, ...],
    is_dir: bool,
    dest: Path,
) -> int:
    """Extract a single file or directory subtree from a .nup container."""
    _ensure_nup_pkg(root_dir)
    from nup.access import extract_tree, resolve_path
    from nup.read import NupReader

    with NupReader(str(nup_path)) as r:
        pkg = r.packages[pkg_index]
        ref = resolve_path(r, pkg, "/".join(rel_parts)) if rel_parts else None
        if is_dir:
            dir_idx = ref.index if ref is not None else pkg.root_dir
            dest.mkdir(parents=True, exist_ok=True)
            return extract_tree(r, str(dest), dir_idx)
        if ref is None or ref.is_dir:
            raise FileNotFoundError("/".join(rel_parts))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.read_file(ref.index))
        return 1


def _find_dir(root: _MutDir, parts: tuple[str, ...]) -> _MutDir:
    cur = root
    for part in parts:
        target_b = part.encode("utf-8", "surrogateescape")
        nxt = next(
            (c for c in cur.children if isinstance(c, _MutDir) and c.name == target_b),
            None,
        )
        if nxt is None:
            nxt = _MutDir(name=target_b)
            cur.children.append(nxt)
        cur = nxt
    return cur


def _find_parent_and_node(
    root: _MutDir, parts: tuple[str, ...]
) -> tuple[_MutDir, _MutDir | _MutFile]:
    if not parts:
        raise ValueError("Chemin vide")
    parent = _find_dir(root, parts[:-1])
    target_b = parts[-1].encode("utf-8", "surrogateescape")
    node = next((c for c in parent.children if c.name == target_b), None)
    if node is None:
        raise FileNotFoundError("/".join(parts))
    return parent, node


def mutate_nup(
    root_dir: Path,
    nup_path: Path,
    mutations: list[NupMutation],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Apply rename/delete/import/copy mutations to a .nup container and rewrite atomically."""
    _ensure_nup_pkg(root_dir)
    from nup import format as F
    from nup.access import iter_children
    from nup.read import NupReader

    tmp_path = nup_path.with_suffix(nup_path.suffix + ".tmp")
    chunk_size = 4 << 20

    try:
        with NupReader(str(nup_path)) as r:
            pkg_roots: list[_MutDir] = []
            pkg_meta_raw: list[bytes] = []
            pkg_records_meta: list[tuple[int, int, int, int]] = []

            def build_mut_dir(dir_idx: int, name_b: bytes) -> _MutDir:
                d = _MutDir(name=name_b)
                for child in iter_children(r, dir_idx):
                    if child.is_dir:
                        d.children.append(build_mut_dir(child.index, child.name))
                    else:
                        fname, size, sha, eoff, esz, codec = r.file_info(child.index)
                        cflags = r.files[child.index][5] if len(r.files[child.index]) > 5 else 0
                        d.children.append(
                            _MutFile(
                                name=fname,
                                size=size,
                                sha256=sha,
                                codec=codec,
                                codec_flags=0,
                                src_extent=(eoff, esz),
                            )
                        )
                return d

            for p in r.packages:
                root_mut = build_mut_dir(p.root_dir, b"")
                pkg_roots.append(root_mut)
                raw_meta = (
                    bytes(r._mm[p.meta_offset : p.meta_offset + p.meta_size])
                    if p.meta_size
                    else b""
                )
                pkg_meta_raw.append(raw_meta)
                pkg_records_meta.append((p.title_id, p.version, p.kind, p.flags))

            def _ensure_mod_update_pkg() -> int:
                from nup import meta as M
                for idx_p, (raw_m, (_tid, _ver, knd, _flg)) in enumerate(
                    zip(pkg_meta_raw, pkg_records_meta)
                ):
                    if knd == F.KIND_UPDATE and raw_m:
                        recs = M.parse_block(raw_m)
                        if recs.get(F.TLV_SOURCE_DUMP) == b"NUP Studio Mod":
                            return idx_p
                base_tid = next(
                    (tid for tid, _v, k, _f in pkg_records_meta if k == F.KIND_BASE),
                    pkg_records_meta[0][0],
                )
                max_ver = max((v for _t, v, _k, _f in pkg_records_meta), default=0)
                new_ver = max_ver + 0x10000
                mod_meta = M.serialize_block(
                    {
                        F.TLV_DISPLAY_VERSION: b"MOD",
                        F.TLV_SOURCE_DUMP: b"NUP Studio Mod",
                    }
                )
                pkg_roots.append(_MutDir(name=b""))
                pkg_meta_raw.append(mod_meta)
                pkg_records_meta.append((base_tid, new_ver, F.KIND_UPDATE, 0))
                return len(pkg_roots) - 1

            # Apply mutations
            for op in mutations:
                if op.kind == "delete_package":
                    if len(pkg_roots) <= 1:
                        raise ValueError("Impossible de supprimer le seul package du conteneur.")
                    pkg_roots.pop(op.pkg_index)
                    pkg_meta_raw.pop(op.pkg_index)
                    pkg_records_meta.pop(op.pkg_index)
                    continue
                if op.kind == "mod_import":
                    if op.local_path is None or not op.local_path.is_file():
                        raise FileNotFoundError(f"Fichier introuvable : {op.local_path}")
                    mod_idx = _ensure_mod_update_pkg()
                    dest_dir = _find_dir(pkg_roots[mod_idx], op.rel_parts)
                    fname = op.new_name or op.local_path.name
                    fname_b = fname.encode("utf-8", "surrogateescape")
                    dest_dir.children = [c for c in dest_dir.children if c.name != fname_b]
                    dest_dir.children.append(
                        _MutFile(
                            name=fname_b,
                            size=op.local_path.stat().st_size,
                            sha256=b"\0" * 32,
                            codec=F.CODEC_RAW,
                            codec_flags=0,
                            local_path=op.local_path,
                        )
                    )
                    continue
                if op.kind == "mod_copy":
                    _, src_node = _find_parent_and_node(pkg_roots[op.pkg_index], op.rel_parts)
                    if not isinstance(src_node, _MutFile):
                        raise IsADirectoryError("La copie de dossiers complets n'est pas prise en charge.")
                    mod_idx = _ensure_mod_update_pkg()
                    d_parts = op.dest_dir_parts if op.dest_dir_parts is not None else op.rel_parts[:-1]
                    dest_dir = _find_dir(pkg_roots[mod_idx], d_parts)
                    fname_b = (op.new_name or src_node.name.decode("utf-8", "surrogateescape")).encode(
                        "utf-8", "surrogateescape"
                    )
                    dest_dir.children = [c for c in dest_dir.children if c.name != fname_b]
                    dest_dir.children.append(
                        _MutFile(
                            name=fname_b,
                            size=src_node.size,
                            sha256=src_node.sha256,
                            codec=src_node.codec,
                            codec_flags=src_node.codec_flags,
                            src_extent=src_node.src_extent,
                            local_path=src_node.local_path,
                        )
                    )
                    continue

                root_mut = pkg_roots[op.pkg_index]
                if op.kind == "rename":
                    parent, node = _find_parent_and_node(root_mut, op.rel_parts)
                    new_b = op.new_name.encode("utf-8", "surrogateescape")
                    if any(c.name == new_b and c is not node for c in parent.children):
                        raise FileExistsError(f"'{op.new_name}' existe déjà dans ce dossier.")
                    node.name = new_b
                elif op.kind == "delete":
                    parent, node = _find_parent_and_node(root_mut, op.rel_parts)
                    parent.children.remove(node)
                elif op.kind == "import":
                    if op.local_path is None or not op.local_path.is_file():
                        raise FileNotFoundError(f"Fichier introuvable : {op.local_path}")
                    dest_dir = _find_dir(root_mut, op.rel_parts)
                    fname = op.new_name or op.local_path.name
                    fname_b = fname.encode("utf-8", "surrogateescape")
                    dest_dir.children = [
                        c for c in dest_dir.children if c.name != fname_b
                    ]
                    dest_dir.children.append(
                        _MutFile(
                            name=fname_b,
                            size=op.local_path.stat().st_size,
                            sha256=b"\0" * 32,
                            codec=F.CODEC_RAW,
                            codec_flags=0,
                            local_path=op.local_path,
                        )
                    )
                elif op.kind == "copy":
                    _, src_node = _find_parent_and_node(root_mut, op.rel_parts)
                    if not isinstance(src_node, _MutFile):
                        raise IsADirectoryError("La copie de dossiers complets n'est pas prise en charge.")
                    d_pkg = op.dest_pkg_index if op.dest_pkg_index is not None else op.pkg_index
                    d_parts = op.dest_dir_parts if op.dest_dir_parts is not None else op.rel_parts[:-1]
                    dest_dir = _find_dir(pkg_roots[d_pkg], d_parts)
                    fname_b = (op.new_name or src_node.name.decode("utf-8", "surrogateescape")).encode(
                        "utf-8", "surrogateescape"
                    )
                    dest_dir.children = [
                        c for c in dest_dir.children if c.name != fname_b
                    ]
                    dest_dir.children.append(
                        _MutFile(
                            name=fname_b,
                            size=src_node.size,
                            sha256=src_node.sha256,
                            codec=src_node.codec,
                            codec_flags=src_node.codec_flags,
                            src_extent=src_node.src_extent,
                            local_path=src_node.local_path,
                        )
                    )
                else:
                    raise ValueError(f"Opération inconnue : {op.kind}")

            # Flatten dirs & files in BFS/DFS order to assign IDs and string pool
            dirs: list[_MutDir] = []
            files: list[_MutFile] = []
            children_refs: list[int] = []
            pool_parts: list[bytes] = []
            pool_offsets: dict[bytes, int] = {}
            pool_len = 0

            def intern_name(b: bytes) -> int:
                nonlocal pool_len
                if b in pool_offsets:
                    return pool_offsets[b]
                off = pool_len
                pool_offsets[b] = off
                pool_parts.append(b + b"\0")
                pool_len += len(b) + 1
                return off

            def assign_tree(d: _MutDir) -> None:
                d.id = len(dirs)
                dirs.append(d)
                d.children.sort(key=lambda c: c.name)
                for c in d.children:
                    if isinstance(c, _MutDir):
                        assign_tree(c)
                    else:
                        c.id = len(files)
                        files.append(c)

            for root_mut in pkg_roots:
                assign_tree(root_mut)

            for d in dirs:
                d.first_child = len(children_refs)
                d.child_count = len(d.children)
                for c in d.children:
                    if isinstance(c, _MutDir):
                        children_refs.append(c.id)
                    else:
                        children_refs.append(len(dirs) + c.id)

            pkg_exefs_ids: list[int] = []
            pkg_romfs_ids: list[int] = []
            for root_mut in pkg_roots:
                ex_id = F.NO_DIR
                rm_id = F.NO_DIR
                for c in root_mut.children:
                    if isinstance(c, _MutDir):
                        if c.name == b"exefs":
                            ex_id = c.id
                        elif c.name == b"romfs":
                            rm_id = c.id
                pkg_exefs_ids.append(ex_id)
                pkg_romfs_ids.append(rm_id)

            dir_name_offs = [intern_name(d.name) for d in dirs]
            file_name_offs = [intern_name(f.name) for f in files]
            pool_bytes = b"".join(pool_parts)

            pkg_count = len(pkg_roots)
            dir_count = len(dirs)
            file_count = len(files)
            header_toc = F._toc_head_s.size
            pkg_table_off = header_toc
            dir_table_off = pkg_table_off + pkg_count * F.PKG_SIZE
            file_table_off = dir_table_off + dir_count * F.DIR_SIZE
            children_off = file_table_off + file_count * F.FILE_SIZE
            pool_off = children_off + len(children_refs) * 4
            toc_size = pool_off + len(pool_bytes)

            toc_offset = F.HEADER_SIZE
            blob_offset = (toc_offset + toc_size + F.EXTENT_ALIGN - 1) & ~(F.EXTENT_ALIGN - 1)

            with open(tmp_path, "wb") as out:
                out.write(b"\0" * blob_offset)
                blob_cursor = blob_offset
                seen: dict[bytes, tuple[int, int, int]] = {}
                zc = zstd.ZstdCompressor(level=3)
                total = len(files)

                for idx, f in enumerate(files):
                    if f.local_path is not None:
                        data = f.local_path.read_bytes()
                        f.size = len(data)
                        f.sha256 = hashlib.sha256(data).digest()
                        if f.size == 0:
                            f.extent_offset, f.extent_size, f.codec = 0, 0, F.CODEC_RAW
                        elif f.sha256 in seen:
                            f.extent_offset, f.extent_size, f.codec = seen[f.sha256]
                        else:
                            aligned = (blob_cursor + F.EXTENT_ALIGN - 1) & ~(F.EXTENT_ALIGN - 1)
                            if aligned > blob_cursor:
                                out.write(b"\0" * (aligned - blob_cursor))
                            comp = zc.compress(data)
                            if len(comp) < f.size:
                                payload, f.codec = comp, F.CODEC_ZSTD
                            else:
                                payload, f.codec = data, F.CODEC_RAW
                            out.write(payload)
                            f.extent_offset = aligned
                            f.extent_size = len(payload)
                            blob_cursor = aligned + len(payload)
                            seen[f.sha256] = (f.extent_offset, f.extent_size, f.codec)
                    else:
                        if f.size == 0:
                            f.extent_offset, f.extent_size, f.codec = 0, 0, F.CODEC_RAW
                        elif f.sha256 in seen:
                            f.extent_offset, f.extent_size, f.codec = seen[f.sha256]
                        else:
                            assert f.src_extent is not None
                            src_off, src_sz = f.src_extent
                            aligned = (blob_cursor + F.EXTENT_ALIGN - 1) & ~(F.EXTENT_ALIGN - 1)
                            if aligned > blob_cursor:
                                out.write(b"\0" * (aligned - blob_cursor))
                            pos_src = src_off
                            left = src_sz
                            while left:
                                take = min(chunk_size, left)
                                out.write(r._mm[pos_src : pos_src + take])
                                pos_src += take
                                left -= take
                            f.extent_offset = aligned
                            f.extent_size = src_sz
                            blob_cursor = aligned + src_sz
                            seen[f.sha256] = (f.extent_offset, f.extent_size, f.codec)
                    if progress_cb:
                        progress_cb(idx + 1, total)

                meta_offsets: list[tuple[int, int]] = []
                for raw_meta in pkg_meta_raw:
                    if raw_meta:
                        mo = blob_cursor
                        out.write(raw_meta)
                        blob_cursor += len(raw_meta)
                        meta_offsets.append((mo, len(raw_meta)))
                    else:
                        meta_offsets.append((0, 0))
                new_file_size = blob_cursor

                # Build TOC
                toc = bytearray(toc_size)
                F._toc_head_s.pack_into(
                    toc,
                    0,
                    F.TOC_MAGIC,
                    pkg_count,
                    dir_count,
                    file_count,
                    pool_off,
                    len(pool_bytes),
                    0,
                )
                pos = pkg_table_off
                for i, (tid, ver, kind, flags) in enumerate(pkg_records_meta):
                    mo, ms = meta_offsets[i]
                    F._pkg_s.pack_into(
                        toc,
                        pos,
                        tid,
                        pkg_roots[i].id,
                        pkg_exefs_ids[i],
                        pkg_romfs_ids[i],
                        ver,
                        kind,
                        flags,
                        0,
                        0,
                        mo,
                        ms,
                    )
                    pos += F.PKG_SIZE

                pos = dir_table_off
                for i, d in enumerate(dirs):
                    F._dir_s.pack_into(
                        toc, pos, dir_name_offs[i], len(d.name), d.first_child, d.child_count, 0
                    )
                    pos += F.DIR_SIZE

                pos = file_table_off
                for i, f in enumerate(files):
                    F._file_s.pack_into(
                        toc,
                        pos,
                        file_name_offs[i],
                        len(f.name),
                        f.size,
                        f.extent_offset,
                        f.extent_size,
                        f.sha256,
                        f.codec,
                        f.codec_flags,
                        0,
                    )
                    pos += F.FILE_SIZE

                if children_refs:
                    struct.pack_into(f"<{len(children_refs)}I", toc, children_off, *children_refs)
                if pool_bytes:
                    toc[pool_off : pool_off + len(pool_bytes)] = pool_bytes

                toc_sha = hashlib.sha256(toc).digest()
                s = r.super
                super_bytes = F._super_s.pack(
                    F.MAGIC,
                    F.HEADER_SIZE,
                    s["header_version"],
                    toc_offset,
                    toc_size,
                    toc_sha,
                    blob_offset,
                    new_file_size,
                    pkg_count,
                    s["flags"],
                    s["uuid"],
                    b"\0" * 32,
                )
                out.seek(0)
                out.write(super_bytes)
                out.write(toc)

        # Replace atomically once NupReader is closed
        for attempt in range(6):
            try:
                os.replace(tmp_path, nup_path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                import time
                time.sleep(0.25 * (attempt + 1))
    except BaseException:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
