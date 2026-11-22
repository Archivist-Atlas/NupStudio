"""Discovery and matching of per-game Switch 2 -> PC recompilers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Optional

from .nup_parser import NupInfo


@dataclass(frozen=True)
class GameRecompiler:
    id: str
    name: str
    recompiler_name: str
    title_ids: list[str]
    supported_versions: list[int]
    description: str
    folder: Path
    windows_entrypoint: str
    linux_entrypoint: str
    default_output_subdir: str
    shortcut_name: str = ""
    icon: str = ""
    repo_url: str = ""
    branch: str = "main"
    is_installed: bool = True
    expected_builds: list[dict[str, Any]] = field(default_factory=list)

    def supports_version(self, version: int) -> bool:
        return not self.supported_versions or version in self.supported_versions

    def verify_source(self, nup: NupInfo) -> tuple[bool, str, str]:
        """Verify that a .nup or extracted dump folder matches an expected version/build.

        Returns (is_valid, detected_label, mismatch_reason).
        """
        if self.expected_builds:
            for build in self.expected_builds:
                label = str(build.get("label") or f"v{build.get('version', '?')}")
                exp_bids = build.get("nso_build_ids") or {}
                exp_shas = build.get("exefs_sha256") or {}

                bid_match = False
                if exp_bids and nup.nso_build_ids:
                    bid_match = all(
                        mod in nup.nso_build_ids
                        and (
                            nup.nso_build_ids[mod].lower().startswith(str(val).lower())
                            or str(val).lower().startswith(nup.nso_build_ids[mod].lower())
                        )
                        for mod, val in exp_bids.items()
                    )

                sha_match = False
                if exp_shas and nup.exefs_sha256:
                    sha_match = all(
                        mod in nup.exefs_sha256
                        and nup.exefs_sha256[mod].lower() == str(val).lower()
                        for mod, val in exp_shas.items()
                    )

                if bid_match or sha_match:
                    return (True, label, "")

            expected_labels = ", ".join(
                str(b.get("label") or f"v{b.get('version', '?')}")
                for b in self.expected_builds
            )
            found_parts: list[str] = []
            if not nup.is_folder:
                found_parts.append(f"v{nup.version}")
            if "main" in nup.nso_build_ids:
                found_parts.append(f"main={nup.nso_build_ids['main'][:16]}...")
            elif "main" in nup.exefs_sha256:
                found_parts.append(f"main sha256={nup.exefs_sha256['main'][:16]}...")
            found_str = ", ".join(found_parts) if found_parts else "version inconnue"
            reason = (
                f"Version attendue : {expected_labels}. "
                f"Source détectée : {found_str}."
            )
            return (False, found_str, reason)

        if nup.is_folder:
            return (True, "Dossier ExeFS/RomFS", "")

        if self.supports_version(nup.version):
            return (True, f"v{nup.version}", "")

        expected = ", ".join(f"v{v}" for v in self.supported_versions)
        return (
            False,
            f"v{nup.version}",
            f"Version attendue : {expected}. Source détectée : v{nup.version}.",
        )

    def resolve_icon_path(self) -> Optional[Path]:
        if not self.icon:
            return None
        candidate = (self.folder / self.icon).resolve()
        return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class MatchResult:
    nup: NupInfo
    known_game_name: Optional[str]
    recompiler: Optional[GameRecompiler]
    version_supported: bool
    detected_version_label: str = ""
    version_mismatch_detail: str = ""


class RecompilerManager:
    """Scans the tools/recompilers/ directory for supported Switch 2 game recompilers."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.recompilers_dir = root_dir / "tools" / "recompilers"
        self.catalog_path = root_dir / "tools" / "recompilers_catalog.json"
        self.games_db_path = root_dir / "tools" / "games_db.json"
        self.recompilers_dir.mkdir(parents=True, exist_ok=True)
        self._games_db: dict[str, str] = self._load_games_db()
        self._catalog: list[dict] = self._load_catalog()
        self._recompilers: list[GameRecompiler] = []
        self.refresh()

    def _load_catalog(self) -> list[dict]:
        if not self.catalog_path.is_file():
            return []
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except (OSError, ValueError):
            pass
        return []

    def _load_games_db(self) -> dict[str, str]:
        if not self.games_db_path.is_file():
            return {}
        try:
            data = json.loads(self.games_db_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k).upper(): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            pass
        return {}

    def refresh(self) -> list[GameRecompiler]:
        """Reload local recompilers from tools/recompilers/ and merge remote catalog entries."""
        catalog_by_id = {
            str(item.get("id") or ""): item
            for item in self._catalog
            if item.get("id")
        }
        found: list[GameRecompiler] = []
        seen_ids: set[str] = set()

        if self.recompilers_dir.is_dir():
            for entry in sorted(self.recompilers_dir.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = entry / "recomp_manifest.json"
                if not manifest_path.is_file():
                    continue
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    rec_id = str(data.get("id") or entry.name)
                    cat = catalog_by_id.get(rec_id, {})
                    found.append(
                        GameRecompiler(
                            id=rec_id,
                            name=str(data.get("name") or entry.name),
                            recompiler_name=str(data.get("recompiler_name") or entry.name),
                            title_ids=[str(t).upper() for t in data.get("title_ids", [])],
                            supported_versions=[int(v) for v in data.get("supported_versions", [])],
                            description=str(data.get("description") or ""),
                            folder=entry,
                            windows_entrypoint=str(data.get("windows_entrypoint") or data.get("script") or "install.ps1"),
                            linux_entrypoint=str(data.get("linux_entrypoint") or "install.sh"),
                            default_output_subdir=str(data.get("default_output_subdir") or entry.name),
                            shortcut_name=str(data.get("shortcut_name") or data.get("name") or entry.name),
                            icon=str(data.get("icon") or ""),
                            repo_url=str(data.get("repo_url") or cat.get("repo_url") or ""),
                            branch=str(data.get("branch") or cat.get("branch") or "main"),
                            is_installed=True,
                            expected_builds=list(data.get("expected_builds") or cat.get("expected_builds") or []),
                        )
                    )
                    seen_ids.add(rec_id)
                except (OSError, ValueError, TypeError):
                    continue

        for cat in self._catalog:
            rec_id = str(cat.get("id") or "")
            if not rec_id or rec_id in seen_ids:
                continue
            found.append(
                GameRecompiler(
                    id=rec_id,
                    name=str(cat.get("name") or rec_id),
                    recompiler_name=str(cat.get("recompiler_name") or rec_id),
                    title_ids=[str(t).upper() for t in cat.get("title_ids", [])],
                    supported_versions=[int(v) for v in cat.get("supported_versions", [])],
                    description=str(cat.get("description") or ""),
                    folder=self.recompilers_dir / rec_id,
                    windows_entrypoint=str(cat.get("windows_entrypoint") or "install.ps1"),
                    linux_entrypoint=str(cat.get("linux_entrypoint") or "install.sh"),
                    default_output_subdir=str(cat.get("default_output_subdir") or rec_id),
                    shortcut_name=str(cat.get("shortcut_name") or cat.get("name") or rec_id),
                    icon=str(cat.get("icon") or ""),
                    repo_url=str(cat.get("repo_url") or ""),
                    branch=str(cat.get("branch") or "main"),
                    is_installed=False,
                    expected_builds=list(cat.get("expected_builds") or []),
                )
            )

        self._recompilers = found
        return found

    @property
    def recompilers(self) -> list[GameRecompiler]:
        return list(self._recompilers)

    def match_rom(self, nup: NupInfo) -> MatchResult:
        """Check whether a parsed .nup ROM has a matching subfolder in tools/recompilers/."""
        candidate_ids: list[str] = [nup.title_id.upper()]
        if nup.title_id.upper().endswith("800"):
            candidate_ids.append(nup.title_id.upper()[:-3] + "000")
        for pkg in nup.all_packages:
            tid = pkg.title_id.upper()
            if tid not in candidate_ids:
                candidate_ids.append(tid)

        matched: Optional[GameRecompiler] = None
        for tid in candidate_ids:
            for rec in self._recompilers:
                if tid in rec.title_ids:
                    matched = rec
                    break
            if matched is not None:
                break

        known_name = matched.name if matched else next(
            (self._games_db[tid] for tid in candidate_ids if tid in self._games_db),
            None,
        )
        if matched:
            version_ok, detected_label, mismatch_detail = matched.verify_source(nup)
        else:
            version_ok, detected_label, mismatch_detail = False, f"v{nup.version}", ""
        return MatchResult(
            nup=nup,
            known_game_name=known_name,
            recompiler=matched,
            version_supported=version_ok,
            detected_version_label=detected_label,
            version_mismatch_detail=mismatch_detail,
        )

