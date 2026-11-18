"""Pure-Python self-updater for NUP Studio (zero external dependencies, no git CLI)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Optional
import urllib.request
import zipfile

from .recompiler_downloader import (
    DownloadCancelledError,
    _download_file,
    _normalize_repo_base,
    fetch_remote_head_sha,
    get_local_recompiler_sha,
)
from .recompiler_manager import GameRecompiler


DEFAULT_VERSION = "1.2.0"
DEFAULT_REPO_URL = "https://github.com/Archivist-Atlas/NupStudio"
DEFAULT_BRANCH = "master"
_USER_AGENT = "NUP-Studio-Updater/1.0"


@dataclass(frozen=True)
class AppUpdateInfo:
    local_version: str
    remote_version: str
    repo_url: str
    branch: str
    update_available: bool
    checked_recompiler_ids: tuple[str, ...] = ()
    outdated_recompiler_ids: tuple[str, ...] = ()


def parse_version(version_str: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", version_str or "")]
    return tuple(nums) if nums else (0,)


def get_local_version_info(root_dir: Path) -> tuple[str, str, str]:
    """Read local version.json from root_dir (or frozen exe_dir)."""
    candidates = [root_dir / "version.json"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / "version.json")
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return (
                    str(data.get("version") or DEFAULT_VERSION),
                    str(data.get("repo_url") or DEFAULT_REPO_URL),
                    str(data.get("branch") or DEFAULT_BRANCH),
                )
            except (OSError, ValueError):
                pass
    return (DEFAULT_VERSION, DEFAULT_REPO_URL, DEFAULT_BRANCH)


def _raw_version_url(repo_url: str, branch: str) -> str:
    base = _normalize_repo_base(repo_url)
    prefix = "https://github.com/"
    if base.startswith(prefix):
        slug = base[len(prefix) :]
        return f"https://raw.githubusercontent.com/{slug}/{branch}/version.json"
    return f"{base}/raw/{branch}/version.json"


def check_recompiler_updates(
    recompilers: list[GameRecompiler], timeout: int = 10
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Check installed recompilers against remote GitHub HEADs without git CLI."""
    checked: list[str] = []
    outdated: list[str] = []
    for rec in recompilers:
        if not rec.is_installed or not rec.repo_url:
            continue
        remote_sha = fetch_remote_head_sha(rec.repo_url, rec.branch, timeout=timeout)
        if not remote_sha:
            continue
        checked.append(rec.id)
        local_sha = get_local_recompiler_sha(rec.folder)
        if local_sha != remote_sha.lower():
            outdated.append(rec.id)
    return (tuple(checked), tuple(outdated))


def check_for_app_update(
    root_dir: Path,
    recompilers: Optional[list[GameRecompiler]] = None,
    timeout: int = 10,
) -> AppUpdateInfo:
    """Fetch remote version.json and check installed recompilers without git."""
    local_ver, repo_url, branch = get_local_version_info(root_dir)
    url = _raw_version_url(repo_url, branch)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    remote_ver = str(data.get("version") or local_ver)
    remote_repo = str(data.get("repo_url") or repo_url)
    remote_branch = str(data.get("branch") or branch)
    available = parse_version(remote_ver) > parse_version(local_ver)
    checked_ids, outdated_ids = (
        check_recompiler_updates(recompilers, timeout=timeout)
        if recompilers
        else ((), ())
    )
    return AppUpdateInfo(
        local_version=local_ver,
        remote_version=remote_ver,
        repo_url=remote_repo,
        branch=remote_branch,
        update_available=available,
        checked_recompiler_ids=checked_ids,
        outdated_recompiler_ids=outdated_ids,
    )


def _copy_tree_merge(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copy_tree_merge(item, target)
        else:
            shutil.copy2(item, target)


def _sync_tools_preserving_recompilers(src_tools: Path, dst_tools: Path) -> None:
    """Update tools/ while preserving user-downloaded tools/recompilers/* subfolders."""
    dst_tools.mkdir(parents=True, exist_ok=True)
    for item in src_tools.iterdir():
        if item.name == "recompilers":
            continue
        target = dst_tools / item.name
        if item.is_dir():
            _copy_tree_merge(item, target)
        else:
            shutil.copy2(item, target)


def apply_app_update(
    root_dir: Path,
    repo_url: str,
    branch: str = DEFAULT_BRANCH,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Download repo archive and apply update to root_dir.

    Returns True if a detached helper script was launched to finish replacing a
    frozen executable after exit (caller should quit QApplication).
    """
    repo_base = _normalize_repo_base(repo_url)
    archive_url = f"{repo_base}/archive/refs/heads/{branch}.zip"
    stage_dir = Path(tempfile.mkdtemp(prefix="nupstudio_upd_"))
    cleanup_stage = True

    try:
        zip_path = stage_dir / "app.zip"
        if log_cb:
            log_cb(f"[Mise à jour] Téléchargement : {archive_url}")
        _download_file(
            archive_url,
            zip_path,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
            label="NUP Studio",
        )
        if cancel_check and cancel_check():
            raise DownloadCancelledError("Mise à jour annulée par l'utilisateur.")

        if log_cb:
            log_cb("[Mise à jour] Extraction de l'archive...")
        extract_root = stage_dir / "extracted"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_root)

        top_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
        payload = top_dirs[0] if len(top_dirs) == 1 else extract_root

        if log_cb:
            log_cb("[Mise à jour] Application des fichiers...")
        for fname in ("main.py", "version.json", "requirements.txt", "pyrefly.toml", "README.md"):
            src_f = payload / fname
            if src_f.is_file():
                shutil.copy2(src_f, root_dir / fname)
        for dname in ("src", "assets"):
            src_d = payload / dname
            if src_d.is_dir():
                _copy_tree_merge(src_d, root_dir / dname)
        if (payload / "tools").is_dir():
            _sync_tools_preserving_recompilers(payload / "tools", root_dir / "tools")

        if getattr(sys, "frozen", False) and (payload / "dist").is_dir():
            exe_path = Path(sys.executable).resolve()
            exe_dir = exe_path.parent
            if (payload / "version.json").is_file():
                shutil.copy2(payload / "version.json", exe_dir / "version.json")

            ps1_path = Path(tempfile.gettempdir()) / f"nupstudio_apply_{os.getpid()}.ps1"
            src_dist = str(payload / "dist").replace("'", "''")
            dst_dist = str(exe_dir).replace("'", "''")
            exe_str = str(exe_path).replace("'", "''")
            stage_str = str(stage_dir).replace("'", "''")
            ps1_str = str(ps1_path).replace("'", "''")
            pid = os.getpid()

            ps1_code = f"""$ErrorActionPreference = 'SilentlyContinue'
for ($i = 0; $i -lt 60; $i++) {{
    if (-not (Get-Process -Id {pid} -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 500
}}
Copy-Item -Path '{src_dist}\\*' -Destination '{dst_dist}' -Recurse -Force
Remove-Item -Path '{stage_str}' -Recurse -Force
Start-Process -FilePath '{exe_str}'
Remove-Item -Path '{ps1_str}' -Force
"""
            ps1_path.write_text(ps1_code, encoding="utf-8")
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(ps1_path),
                ],
                creationflags=creation_flags,
            )
            cleanup_stage = False
            return True

        return False
    finally:
        if cleanup_stage:
            shutil.rmtree(stage_dir, ignore_errors=True)
