"""Pure-Python downloader for GitHub-hosted game recompilers (no git CLI required)."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable, Optional
import urllib.error
import urllib.request
import zipfile


_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID_RE = re.compile(r"^oid sha256:([0-9a-fA-F]{64})$", re.MULTILINE)
_LFS_SIZE_RE = re.compile(r"^size (\d+)$", re.MULTILINE)
_USER_AGENT = "NUP-Studio-Recompiler-Downloader/1.0"


class DownloadCancelledError(RuntimeError):
    """Raised when the user cancels an active recompiler download."""


def _normalize_repo_base(repo_url: str) -> str:
    base = repo_url.strip().rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return base


def fetch_remote_head_sha(repo_url: str, branch: str = "main", timeout: int = 10) -> Optional[str]:
    """Fetch the remote HEAD commit SHA via GitHub smart-HTTP info/refs (no git CLI)."""
    repo_base = _normalize_repo_base(repo_url)
    url = f"{repo_base}.git/info/refs?service=git-upload-pack"
    req = urllib.request.Request(url, headers={"User-Agent": "git/2.40"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    for br in [branch] + (["master"] if branch == "main" else []):
        m = re.search(rf"([0-9a-f]{{40}})\s+refs/heads/{re.escape(br)}\b", data)
        if m:
            return m.group(1)
    return None


def get_local_recompiler_sha(dest_dir: Path) -> Optional[str]:
    """Read local commit SHA from .recomp_commit or .git metadata without git CLI."""
    marker = dest_dir / ".recomp_commit"
    if marker.is_file():
        try:
            sha = marker.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                return sha.lower()
        except OSError:
            pass

    git_dir = dest_dir / ".git"
    head_file = git_dir / "HEAD"
    if head_file.is_file():
        try:
            head = head_file.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", head):
                return head.lower()
            if head.startswith("ref:"):
                ref = head.split(":", 1)[1].strip()
                ref_file = git_dir / ref
                if ref_file.is_file():
                    sha = ref_file.read_text(encoding="utf-8").strip()
                    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                        return sha.lower()
                packed = git_dir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("#") or line.startswith("^"):
                            continue
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
                            return parts[0].lower()
        except OSError:
            pass
    return None


def _download_file(
    url: str,
    dest: Path,
    headers: Optional[dict[str, str]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    label: str = "archive",
) -> None:
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        total_hdr = resp.headers.get("Content-Length")
        total = int(total_hdr) if total_hdr and total_hdr.isdigit() else 0
        downloaded = 0
        next_log_mb = 5
        while True:
            if cancel_check and cancel_check():
                raise DownloadCancelledError("Téléchargement annulé par l'utilisateur.")
            chunk = resp.read(262144)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total > 0 and progress_cb:
                progress_cb(min(99, int((downloaded / total) * 100)))
            mb = downloaded // (1024 * 1024)
            if log_cb and mb >= next_log_mb:
                if total > 0:
                    total_mb = total / (1024 * 1024)
                    log_cb(f"[Téléchargement] {label} : {mb} / {total_mb:.1f} Mo")
                else:
                    log_cb(f"[Téléchargement] {label} : {mb} Mo téléchargés...")
                next_log_mb = mb + 10


def _resolve_lfs_pointers(
    root_dir: Path,
    repo_base: str,
    log_cb: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Replace Git LFS pointer files with actual binaries via GitHub's LFS Batch API."""
    pointers: list[tuple[Path, str, int]] = []
    for path in root_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 512:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if not raw.startswith(_LFS_HEADER):
            continue
        text = raw.decode("utf-8", errors="ignore")
        oid_m = _LFS_OID_RE.search(text)
        size_m = _LFS_SIZE_RE.search(text)
        if oid_m and size_m:
            pointers.append((path, oid_m.group(1), int(size_m.group(1))))

    if not pointers:
        return

    if log_cb:
        log_cb(f"[Téléchargement] Résolution de {len(pointers)} fichier(s) Git LFS...")

    batch_url = f"{repo_base}.git/info/lfs/objects/batch"
    payload = json.dumps(
        {
            "operation": "download",
            "transfers": ["basic"],
            "objects": [{"oid": oid, "size": size} for _, oid, size in pointers],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        batch_url,
        data=payload,
        method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.git-lfs+json",
            "Content-Type": "application/vnd.git-lfs+json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        batch_data = json.loads(resp.read().decode("utf-8"))

    by_oid = {obj.get("oid"): obj for obj in batch_data.get("objects", [])}
    for path, oid, _ in pointers:
        if cancel_check and cancel_check():
            raise DownloadCancelledError("Téléchargement annulé par l'utilisateur.")
        obj = by_oid.get(oid) or {}
        dl = (obj.get("actions") or {}).get("download")
        if not dl or not dl.get("href"):
            err = obj.get("error") or {}
            raise RuntimeError(f"Impossible de récupérer le fichier LFS {path.name}: {err}")
        rel = path.relative_to(root_dir)
        if log_cb:
            log_cb(f"[Téléchargement] Fichier LFS : {rel}")
        _download_file(
            dl["href"],
            path,
            headers=dl.get("header") or {},
            log_cb=log_cb,
            cancel_check=cancel_check,
            label=path.name,
        )


def download_recompiler(
    repo_url: str,
    dest_dir: Path,
    branch: str = "main",
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Download and extract a GitHub recompiler repository into dest_dir without git."""
    repo_base = _normalize_repo_base(repo_url)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f".dl_{dest_dir.name}_", dir=str(dest_dir.parent)) as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "repo.zip"
        branches = [branch] + (["master"] if branch == "main" else [])

        last_err: Optional[Exception] = None
        used_branch = branch
        for br in branches:
            archive_url = f"{repo_base}/archive/refs/heads/{br}.zip"
            if log_cb:
                log_cb(f"[Téléchargement] {archive_url}")
            try:
                _download_file(
                    archive_url,
                    zip_path,
                    log_cb=log_cb,
                    progress_cb=progress_cb,
                    cancel_check=cancel_check,
                    label=dest_dir.name,
                )
                used_branch = br
                last_err = None
                break
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code != 404:
                    raise
        if last_err is not None:
            raise last_err

        remote_sha = fetch_remote_head_sha(repo_base, used_branch)

        if cancel_check and cancel_check():
            raise DownloadCancelledError("Téléchargement annulé par l'utilisateur.")

        if log_cb:
            log_cb(f"[Extraction] Décompression dans {dest_dir}...")
        extract_root = tmp_path / "extracted"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_root)

        top_entries = [p for p in extract_root.iterdir() if p.is_dir()]
        payload_dir = top_entries[0] if len(top_entries) == 1 else extract_root

        _resolve_lfs_pointers(
            payload_dir,
            repo_base,
            log_cb=log_cb,
            cancel_check=cancel_check,
        )

        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(payload_dir), str(dest_dir))
        if remote_sha:
            try:
                (dest_dir / ".recomp_commit").write_text(remote_sha, encoding="utf-8")
            except OSError:
                pass

    if log_cb:
        log_cb(f"[Téléchargement] Recompileur prêt : {dest_dir}")
