"""Background QThread workers for app self-update and full recompiler backup."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import QThread, Signal

from .app_updater import AppUpdateInfo, apply_app_update, check_for_app_update
from .recompiler_downloader import DownloadCancelledError, download_recompiler
from .recompiler_manager import GameRecompiler


class AppUpdateCheckWorker(QThread):
    finished_check = Signal(bool, object, str)

    def __init__(self, root_dir: Path, recompilers: list[GameRecompiler], parent=None) -> None:
        super().__init__(parent)
        self.root_dir = root_dir
        self.recompilers = list(recompilers)

    def run(self) -> None:
        try:
            info = check_for_app_update(self.root_dir, self.recompilers)
            self.finished_check.emit(True, info, "")
        except Exception as exc:
            self.finished_check.emit(False, None, str(exc))


class AppUpdateApplyWorker(QThread):
    log_line = Signal(str)
    progress_percent = Signal(int)
    finished_result = Signal(bool, bool, str)

    def __init__(self, root_dir: Path, info: AppUpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.root_dir = root_dir
        self.info = info
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            external = apply_app_update(
                self.root_dir,
                self.info.repo_url,
                branch=self.info.branch,
                log_cb=self.log_line.emit,
                progress_cb=self.progress_percent.emit,
                cancel_check=lambda: self._cancelled,
            )
            self.finished_result.emit(
                True,
                external,
                f"Mise à jour v{self.info.remote_version} installée avec succès.",
            )
        except DownloadCancelledError:
            self.finished_result.emit(False, False, "Mise à jour annulée par l'utilisateur.")
        except Exception as exc:
            self.finished_result.emit(False, False, f"Échec de la mise à jour : {exc}")


class BackupRecompilersWorker(QThread):
    log_line = Signal(str)
    progress_percent = Signal(int)
    finished_result = Signal(bool, int, int, str)

    def __init__(self, recompilers: list[GameRecompiler], parent=None) -> None:
        super().__init__(parent)
        self.recompilers = [r for r in recompilers if r.repo_url]
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        total = len(self.recompilers)
        if total == 0:
            self.finished_result.emit(False, 0, 0, "Aucun recompileur avec dépôt GitHub configuré.")
            return

        ok_count = 0
        errors: list[str] = []
        for idx, rec in enumerate(self.recompilers):
            if self._cancelled:
                self.finished_result.emit(
                    False,
                    ok_count,
                    total,
                    f"Sauvegarde annulée ({ok_count}/{total} recompileurs téléchargés).",
                )
                return

            self.log_line.emit(f"[{idx + 1}/{total}] Sauvegarde de {rec.name} ({rec.id})…")

            def _per_repo_progress(pct: int, i: int = idx) -> None:
                overall = int(((i + (pct / 100.0)) / total) * 100)
                self.progress_percent.emit(min(99, overall))

            try:
                download_recompiler(
                    rec.repo_url,
                    rec.folder,
                    branch=rec.branch,
                    log_cb=self.log_line.emit,
                    progress_cb=_per_repo_progress,
                    cancel_check=lambda: self._cancelled,
                )
                ok_count += 1
            except DownloadCancelledError:
                self.finished_result.emit(
                    False,
                    ok_count,
                    total,
                    f"Sauvegarde annulée ({ok_count}/{total} recompileurs téléchargés).",
                )
                return
            except Exception as exc:
                errors.append(f"{rec.name} : {exc}")
                self.log_line.emit(f"[Erreur] {rec.name} : {exc}")

        self.progress_percent.emit(100)
        if errors:
            summary = (
                f"Sauvegarde partielle : {ok_count}/{total} recompileur(s) téléchargé(s).\n\n"
                + "\n".join(errors)
            )
            self.finished_result.emit(False, ok_count, total, summary)
        else:
            self.finished_result.emit(
                True,
                ok_count,
                total,
                f"Sauvegarde complète terminée : {ok_count}/{total} recompileurs enregistrés dans tools/recompilers/.",
            )
