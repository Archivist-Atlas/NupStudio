"""Main PySide6 GUI window for Switch 2 -> PC Recompiler."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import time
from typing import Optional

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .app_updater import AppUpdateInfo, get_local_version_info
from .msys2_helper import is_msys2_ready
from .nup_parser import NupParseError, parse_nup
from .nup_explorer_tab import NupExplorerTab
from .nup_tools import NupCliWorker
from .recompiler_manager import GameRecompiler, MatchResult, RecompilerManager
from .runner import RecompileWorker
from .update_workers import (
    AppUpdateApplyWorker,
    AppUpdateCheckWorker,
    BackupRecompilersWorker,
)


DARK_STYLESHEET = """
QMainWindow, QWidget#RootCentral, QWidget#TabPage {
    background-color: #0b0f17;
    color: #f1f5f9;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}
QLabel {
    background-color: transparent;
    color: #f1f5f9;
}
QCheckBox {
    background-color: transparent;
    color: #cbd5e1;
    spacing: 8px;
    font-weight: 500;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid #334155;
    background-color: #090d14;
}
QCheckBox::indicator:checked {
    background-color: #e11d48;
    border-color: #e11d48;
}
QTabWidget::pane {
    border: 1px solid #1e293b;
    border-radius: 10px;
    background-color: #0f1520;
    padding: 10px;
    top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: #94a3b8;
    padding: 10px 20px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
    font-weight: 600;
    font-size: 13px;
}
QTabBar::tab:hover {
    color: #e2e8f0;
    background-color: #161f2e;
}
QTabBar::tab:selected {
    background-color: #0f1520;
    color: #ffffff;
    border: 1px solid #1e293b;
    border-bottom: 2px solid #e11d48;
}
QFrame#Card {
    background-color: #131b29;
    border: 1px solid #1e293b;
    border-radius: 12px;
}
QFrame#DropZone {
    background-color: #111827;
    border: 2px dashed #334155;
    border-radius: 12px;
}
QFrame#DropZone:hover {
    border-color: #e11d48;
    background-color: #161f30;
}
QFrame#PromptBar {
    background-color: #271a0c;
    border: 1px solid #d97706;
    border-radius: 10px;
}
QLineEdit {
    background-color: #090d14;
    border: 1px solid #283548;
    border-radius: 8px;
    padding: 8px 12px;
    min-height: 20px;
    color: #f1f5f9;
    selection-background-color: #e11d48;
}
QLineEdit:focus {
    border-color: #e11d48;
}
QLineEdit:disabled {
    background-color: #0d121b;
    color: #475569;
    border-color: #1e293b;
}
QPushButton {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 16px;
    min-height: 20px;
    color: #f1f5f9;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #293548;
    border-color: #475569;
}
QPushButton:pressed {
    background-color: #172030;
}
QPushButton:disabled {
    background-color: #131b29;
    color: #475569;
    border-color: #1e293b;
}
QPushButton#PrimaryBtn {
    background-color: #e11d48;
    border: none;
    color: #ffffff;
    padding: 10px 24px;
    font-size: 14px;
    border-radius: 8px;
}
QPushButton#PrimaryBtn:hover {
    background-color: #be123c;
}
QPushButton#PrimaryBtn:pressed {
    background-color: #9f1239;
}
QPushButton#PrimaryBtn:disabled {
    background-color: #3b1824;
    color: #884a5b;
}
QPushButton#DangerBtn {
    background-color: #991b1b;
    border: 1px solid #b91c1c;
    color: #ffffff;
    padding: 10px 18px;
}
QPushButton#DangerBtn:hover {
    background-color: #b91c1c;
}
QPlainTextEdit {
    background-color: #070a10;
    border: 1px solid #1e293b;
    border-radius: 10px;
    color: #cbd5e1;
    padding: 10px;
    selection-background-color: #e11d48;
}
QTableWidget {
    background-color: #090d14;
    color: #f1f5f9;
    border: 1px solid #1e293b;
    border-radius: 10px;
    gridline-color: #1e293b;
    alternate-background-color: #0f1623;
    outline: none;
}
QTableWidget::item {
    color: #f1f5f9;
    padding: 10px 12px;
    border: none;
}
QTableWidget::item:hover,
QTableWidget::item:selected {
    background-color: transparent;
    color: #f1f5f9;
}
QHeaderView::section {
    background-color: #131b29;
    color: #94a3b8;
    padding: 8px 12px;
    border: none;
    border-bottom: 1px solid #1e293b;
    font-weight: 600;
}
QProgressBar {
    background-color: #090d14;
    border: 1px solid #1e293b;
    border-radius: 5px;
    max-height: 10px;
    min-height: 10px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #e11d48;
    border-radius: 4px;
}
QComboBox, QSpinBox {
    background-color: #090d14;
    border: 1px solid #1e293b;
    border-radius: 7px;
    padding: 7px 10px;
    color: #f1f5f9;
}
QComboBox:focus, QSpinBox:focus {
    border-color: #e11d48;
}
QComboBox QAbstractItemView {
    background-color: #0f1520;
    color: #f1f5f9;
    selection-background-color: #e11d48;
    border: 1px solid #1e293b;
}
QMessageBox, QDialog {
    background-color: #0f1520;
    color: #f1f5f9;
}
QMessageBox QLabel, QDialog QLabel {
    background-color: transparent;
    color: #f1f5f9;
}
"""


def _format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} Po"


class DropZoneFrame(QFrame):
    """Drag-and-drop area for Switch 2 .nup ROM files."""

    def __init__(self, on_file_dropped, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self._on_file_dropped = on_file_dropped

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self._on_file_dropped(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.manager = RecompilerManager(root_dir)
        self.current_match: Optional[MatchResult] = None
        self.worker: Optional[RecompileWorker] = None
        self.extract_worker: Optional[NupCliWorker] = None
        self.pack_worker: Optional[NupCliWorker] = None
        self._latest_update_info: Optional[AppUpdateInfo] = None
        self._update_check_worker: Optional[AppUpdateCheckWorker] = None
        self._update_apply_worker: Optional[AppUpdateApplyWorker] = None
        self._backup_worker: Optional[BackupRecompilersWorker] = None
        self._compile_started_at: Optional[float] = None
        self._compile_timer = QTimer(self)
        self._compile_timer.setInterval(1000)
        self._compile_timer.timeout.connect(self._update_elapsed_label)

        self.setWindowTitle("NUP Studio — Suite Switch 2 (.nup & Recompilation)")
        self.resize(980, 820)
        self.setMinimumSize(820, 720)
        self.setStyleSheet(DARK_STYLESHEET)

        for icon_rel in ("assets/app_icon.ico", "assets/app_icon.png"):
            icon_file = self.root_dir / icon_rel
            if icon_file.is_file():
                self.setWindowIcon(QIcon(str(icon_file)))
                break

        self._build_ui()
        self._refresh_supported_games_table()
        QTimer.singleShot(1500, self._check_app_update_silent)

    def _default_output_base(self) -> Path:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return Path(base) / "Switch2Recomp"
        return Path.home() / ".local" / "share" / "switch2recomp"

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("RootCentral")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 18, 20, 18)
        root_layout.setSpacing(14)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(48, 48)
        logo_png = self.root_dir / "assets" / "app_icon.png"
        if logo_png.is_file():
            pix = QPixmap(str(logo_png)).scaled(
                48,
                48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo.setPixmap(pix)
            logo.setStyleSheet("background-color: transparent; border-radius: 10px;")
        else:
            logo.setText("S2R")
            logo.setStyleSheet(
                "background-color: #e11d48; color: #ffffff; border-radius: 10px; "
                "font-size: 16px; font-weight: 800;"
            )
        header_row.addWidget(logo)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("NUP Studio")
        title.setStyleSheet("font-size: 21px; font-weight: 700; color: #ffffff;")
        subtitle = QLabel(
            "Explorateur, modding, conversion et recompilation de jeux Switch 2 (.nup)"
        )
        subtitle.setStyleSheet("color: #94a3b8; font-size: 12px;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box)
        header_row.addStretch()

        local_ver, _, _ = get_local_version_info(self.root_dir)
        self.btn_app_update = QPushButton(f"v{local_ver} • Mise à jour")
        self.btn_app_update.setToolTip(
            "Vérifier et installer les mises à jour de NUP Studio depuis GitHub (sans git ni dépendance externe)."
        )
        self.btn_app_update.clicked.connect(self._on_click_app_update)
        header_row.addWidget(self.btn_app_update)

        self.badge_count = QLabel()
        self.badge_count.setStyleSheet(
            "background-color: #172554; color: #93c5fd; border: 1px solid #1e3a8a; "
            "padding: 6px 14px; border-radius: 15px; font-weight: 600; font-size: 12px;"
        )
        header_row.addWidget(self.badge_count)
        root_layout.addLayout(header_row)

        # Tabs
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, stretch=1)

        # Tab 1: Recompiler
        tab_recomp = QWidget()
        tab_recomp.setObjectName("TabPage")
        tab_layout = QVBoxLayout(tab_recomp)
        tab_layout.setContentsMargins(12, 12, 12, 12)
        tab_layout.setSpacing(12)

        # Drop zone + file picker + inline status
        drop_zone = DropZoneFrame(self._load_rom)
        dz_layout = QVBoxLayout(drop_zone)
        dz_layout.setContentsMargins(18, 16, 18, 16)
        dz_layout.setSpacing(10)

        dz_header = QHBoxLayout()
        dz_label = QLabel(
            "Glissez-déposez votre ROM Switch 2 (.nup) ici ou sélectionnez un fichier :"
        )
        dz_label.setStyleSheet("font-weight: 600; color: #e2e8f0; font-size: 13px;")
        self.status_badge = QLabel("En attente d'une ROM")
        self.status_badge.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; padding: 5px 12px; "
            "border-radius: 12px; font-weight: 600; font-size: 12px;"
        )
        dz_header.addWidget(dz_label)
        dz_header.addStretch()
        dz_header.addWidget(self.status_badge)
        dz_layout.addLayout(dz_header)

        rom_row = QHBoxLayout()
        rom_row.setSpacing(10)
        self.rom_input = QLineEdit()
        self.rom_input.setPlaceholderText("Chemin vers le fichier .nup (ex: ocarina_of_time.nup)")
        self.rom_input.setReadOnly(True)
        btn_browse_rom = QPushButton("Parcourir une ROM (.nup)…")
        btn_browse_rom.clicked.connect(self._browse_rom)
        rom_row.addWidget(self.rom_input, stretch=1)
        rom_row.addWidget(btn_browse_rom)
        dz_layout.addLayout(rom_row)

        self.lbl_status_detail = QLabel(
            "Sélectionnez un fichier .nup pour identifier le jeu et vérifier si un sous-dossier "
            "de recompilation existe dans tools/recompilers/."
        )
        self.lbl_status_detail.setWordWrap(True)
        self.lbl_status_detail.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.lbl_status_detail.setStyleSheet("color: #94a3b8; font-size: 12px;")
        dz_layout.addWidget(self.lbl_status_detail)

        tab_layout.addWidget(drop_zone)

        # Output dir + Action buttons
        out_card = QFrame()
        out_card.setObjectName("Card")
        out_layout = QVBoxLayout(out_card)
        out_layout.setContentsMargins(18, 14, 18, 14)
        out_layout.setSpacing(12)

        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        out_lbl = QLabel("Dossier d'installation PC")
        out_lbl.setStyleSheet("color: #94a3b8; font-weight: 600;")
        self.out_input = QLineEdit(str(self._default_output_base()))
        btn_browse_out = QPushButton("Choisir…")
        btn_browse_out.clicked.connect(self._browse_output)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self.out_input, stretch=1)
        out_row.addWidget(btn_browse_out)
        out_layout.addLayout(out_row)

        self.chk_desktop_shortcut = QCheckBox("Créer un raccourci avec icône sur le Bureau")
        self.chk_desktop_shortcut.setChecked(True)
        out_layout.addWidget(self.chk_desktop_shortcut)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        progress_col = QVBoxLayout()
        progress_col.setSpacing(4)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.lbl_compile_elapsed = QLabel("Temps écoulé : 00:00")
        self.lbl_compile_elapsed.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.lbl_compile_elapsed.setVisible(False)
        progress_col.addWidget(self.progress)
        progress_col.addWidget(self.lbl_compile_elapsed)

        self.btn_compile = QPushButton("Recompiler en version PC")
        self.btn_compile.setObjectName("PrimaryBtn")
        self.btn_compile.setEnabled(False)
        self.btn_compile.clicked.connect(self._start_compile)

        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.setObjectName("DangerBtn")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_compile)

        action_row.addLayout(progress_col, stretch=1)
        action_row.addStretch()
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_compile)
        out_layout.addLayout(action_row)

        tab_layout.addWidget(out_card)

        # Interactive prompt banner (hidden unless child process asks a question)
        self.prompt_bar = QFrame()
        self.prompt_bar.setObjectName("PromptBar")
        self.prompt_bar.setVisible(False)
        prompt_layout = QHBoxLayout(self.prompt_bar)
        prompt_layout.setContentsMargins(14, 10, 14, 10)
        prompt_layout.setSpacing(10)

        self.lbl_prompt_text = QLabel("")
        self.lbl_prompt_text.setWordWrap(True)
        self.lbl_prompt_text.setStyleSheet("color: #fde68a; font-weight: 600;")
        prompt_layout.addWidget(self.lbl_prompt_text, stretch=1)

        self.prompt_buttons_layout = QHBoxLayout()
        self.prompt_buttons_layout.setSpacing(8)
        prompt_layout.addLayout(self.prompt_buttons_layout)
        tab_layout.addWidget(self.prompt_bar)

        # Console Logs
        log_header = QHBoxLayout()
        log_title = QLabel("Console de recompilation")
        log_title.setStyleSheet("font-weight: 600; color: #94a3b8;")
        btn_clear_log = QPushButton("Effacer")
        btn_clear_log.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(btn_clear_log)
        tab_layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMinimumHeight(160)
        tab_layout.addWidget(self.log_view, stretch=1)

        # Manual console input row (only shown when a prompt is active)
        self.stdin_container = QWidget()
        self.stdin_container.setObjectName("TabPage")
        self.stdin_container.setVisible(False)
        stdin_row = QHBoxLayout(self.stdin_container)
        stdin_row.setContentsMargins(0, 0, 0, 0)
        stdin_row.setSpacing(10)
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText(
            "Répondre à la console (tapez votre réponse puis Entrée)…"
        )
        self.console_input.returnPressed.connect(self._send_console_input)
        self.btn_send_input = QPushButton("Envoyer")
        self.btn_send_input.clicked.connect(self._send_console_input)
        stdin_row.addWidget(self.console_input, stretch=1)
        stdin_row.addWidget(self.btn_send_input)
        tab_layout.addWidget(self.stdin_container)

        self.tabs.addTab(tab_recomp, "Recompilateur")

        # Tab 2: Supported Games (recompilers/ subfolders)
        tab_games = QWidget()
        tab_games.setObjectName("TabPage")
        games_layout = QVBoxLayout(tab_games)
        games_layout.setContentsMargins(12, 12, 12, 12)
        games_layout.setSpacing(12)

        games_top = QHBoxLayout()
        games_top.setSpacing(10)
        games_info = QLabel(
            f"Sous-dossiers détectés dans : {self.manager.recompilers_dir}"
        )
        games_info.setWordWrap(True)
        games_info.setStyleSheet("color: #94a3b8;")
        btn_refresh = QPushButton("Actualiser")
        btn_refresh.clicked.connect(self._refresh_supported_games_table)
        btn_open_dir = QPushButton("Ouvrir tools/recompilers/")
        btn_open_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.manager.recompilers_dir))
            )
        )
        games_top.addWidget(games_info, stretch=1)
        games_top.addWidget(btn_refresh)
        games_top.addWidget(btn_open_dir)
        games_layout.addLayout(games_top)

        backup_card = QFrame()
        backup_card.setObjectName("Card")
        backup_row = QHBoxLayout(backup_card)
        backup_row.setContentsMargins(14, 10, 14, 10)
        backup_row.setSpacing(12)
        backup_text = QLabel(
            "<b>Sauvegarde hors-ligne des recompileurs</b><br>"
            "<span style='color:#94a3b8; font-size:12px;'>"
            "Télécharge de force tous les dépôts de recompileurs dans tools/recompilers/ (sans avoir besoin d'un jeu .nup) "
            "afin de conserver une copie complète en cas de disparition des dépôts en ligne."
            "</span>"
        )
        backup_text.setWordWrap(True)
        self.btn_backup_recompilers = QPushButton("Sauvegarder tous les recompileurs")
        self.btn_backup_recompilers.setObjectName("PrimaryBtn")
        self.btn_backup_recompilers.clicked.connect(self._on_click_backup_recompilers)
        backup_row.addWidget(backup_text, stretch=1)
        backup_row.addWidget(self.btn_backup_recompilers)
        games_layout.addWidget(backup_card)

        self.games_table = QTableWidget(0, 4)
        self.games_table.setAlternatingRowColors(True)
        self.games_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.games_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.games_table.setWordWrap(True)
        self.games_table.verticalHeader().setVisible(False)
        self.games_table.verticalHeader().setMinimumSectionSize(44)
        self.games_table.setHorizontalHeaderLabels(
            ["Jeu", "Recompileur", "Sous-dossier", "Statut"]
        )
        header = self.games_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.games_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        games_layout.addWidget(self.games_table)

        self.tabs.addTab(tab_games, "Recompileurs")
        self._build_extract_tab()
        self._build_pack_tab()
        self._build_credits_tab()

    def _build_extract_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("TabPage")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        title = QLabel("EXTRAIRE UNE ROM .NUP")
        title.setObjectName("SectionTitle")
        grid.addWidget(title, 0, 0, 1, 3)

        grid.addWidget(QLabel("Fichier .nup :"), 1, 0)
        self.ext_nup_input = QLineEdit()
        self.ext_nup_input.setPlaceholderText("Sélectionnez le fichier .nup à extraire…")
        grid.addWidget(self.ext_nup_input, 1, 1)
        btn_browse_nup = QPushButton("Parcourir…")
        btn_browse_nup.clicked.connect(self._browse_extract_nup)
        grid.addWidget(btn_browse_nup, 1, 2)

        grid.addWidget(QLabel("Dossier de sortie :"), 2, 0)
        self.ext_dest_input = QLineEdit()
        self.ext_dest_input.setPlaceholderText("Dossier où extraire les fichiers…")
        grid.addWidget(self.ext_dest_input, 2, 1)
        btn_browse_dest = QPushButton("Parcourir…")
        btn_browse_dest.clicked.connect(self._browse_extract_dest)
        grid.addWidget(btn_browse_dest, 2, 2)

        grid.addWidget(QLabel("Package :"), 3, 0)
        self.ext_pkg_combo = QComboBox()
        self.ext_pkg_combo.addItem("Tous les packages (base + updates + dlc)", "all")
        self.ext_pkg_combo.addItem("1er package uniquement", "")
        self.ext_pkg_combo.addItem("base", "base")
        self.ext_pkg_combo.addItem("update", "update")
        self.ext_pkg_combo.addItem("addon", "addon")
        grid.addWidget(self.ext_pkg_combo, 3, 1, 1, 2)

        grid.addWidget(QLabel("Sous-dossier :"), 4, 0)
        self.ext_subtree_input = QLineEdit()
        self.ext_subtree_input.setPlaceholderText("Optionnel (ex: romfs ou exefs ; vide = tout)")
        grid.addWidget(self.ext_subtree_input, 4, 1, 1, 2)

        self.ext_flat_check = QCheckBox("Mode secours (--flat) : ignorer l'arborescence")
        grid.addWidget(self.ext_flat_check, 5, 1, 1, 2)

        layout.addWidget(card)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_ext_info = QPushButton("Infos .nup")
        self.btn_ext_info.clicked.connect(self._run_extract_info)
        self.btn_ext_run = QPushButton("Extraire la ROM")
        self.btn_ext_run.setObjectName("PrimaryBtn")
        self.btn_ext_run.clicked.connect(self._start_extract)
        self.btn_ext_cancel = QPushButton("Annuler")
        self.btn_ext_cancel.setVisible(False)
        self.btn_ext_cancel.clicked.connect(self._cancel_extract)
        actions.addWidget(self.btn_ext_info)
        actions.addStretch(1)
        actions.addWidget(self.btn_ext_cancel)
        actions.addWidget(self.btn_ext_run)
        layout.addLayout(actions)

        self.ext_progress = QProgressBar()
        self.ext_progress.setTextVisible(False)
        self.ext_progress.setVisible(False)
        layout.addWidget(self.ext_progress)

        self.ext_log = QPlainTextEdit()
        self.ext_log.setReadOnly(True)
        self.ext_log.setFont(QFont("Consolas", 10))
        self.ext_log.setPlaceholderText("Les logs d'extraction s'afficheront ici…")
        layout.addWidget(self.ext_log, stretch=1)

        self.tabs.addTab(tab, "Extraire (.nup)")

    def _build_pack_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("TabPage")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        title = QLabel("PACKAGER UN DOSSIER EN .NUP")
        title.setObjectName("SectionTitle")
        grid.addWidget(title, 0, 0, 1, 3)

        grid.addWidget(QLabel("Dossier source :"), 1, 0)
        self.pack_src_input = QLineEdit()
        self.pack_src_input.setPlaceholderText("Dossier du dump (base/updates/dlc ou exefs+romfs)…")
        grid.addWidget(self.pack_src_input, 1, 1)
        btn_browse_src = QPushButton("Parcourir…")
        btn_browse_src.clicked.connect(self._browse_pack_src)
        grid.addWidget(btn_browse_src, 1, 2)

        grid.addWidget(QLabel("Fichier .nup de sortie :"), 2, 0)
        self.pack_out_input = QLineEdit()
        self.pack_out_input.setPlaceholderText("Chemin du fichier .nup à créer…")
        grid.addWidget(self.pack_out_input, 2, 1)
        btn_browse_out = QPushButton("Enregistrer sous…")
        btn_browse_out.clicked.connect(self._browse_pack_out)
        grid.addWidget(btn_browse_out, 2, 2)

        grid.addWidget(QLabel("Title ID (optionnel) :"), 3, 0)
        self.pack_tid_input = QLineEdit()
        self.pack_tid_input.setPlaceholderText("16 caractères hex (ex: 010015100B514000)")
        grid.addWidget(self.pack_tid_input, 3, 1, 1, 2)

        grid.addWidget(QLabel("Nom du jeu (optionnel) :"), 4, 0)
        self.pack_name_input = QLineEdit()
        self.pack_name_input.setPlaceholderText("Titre affiché dans les métadonnées")
        grid.addWidget(self.pack_name_input, 4, 1, 1, 2)

        grid.addWidget(QLabel("Version / Éditeur :"), 5, 0)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.pack_ver_input = QLineEdit()
        self.pack_ver_input.setPlaceholderText("Version affichée (ex: 1.0.0)")
        self.pack_pub_input = QLineEdit()
        self.pack_pub_input.setPlaceholderText("Éditeur (ex: Nintendo)")
        meta_row.addWidget(self.pack_ver_input, stretch=1)
        meta_row.addWidget(self.pack_pub_input, stretch=1)
        grid.addLayout(meta_row, 5, 1, 1, 2)

        grid.addWidget(QLabel("Compression Zstd :"), 6, 0)
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        self.pack_level_spin = QSpinBox()
        self.pack_level_spin.setRange(1, 22)
        self.pack_level_spin.setValue(3)
        self.pack_split_check = QCheckBox("Séparer par package (--split)")
        opts_row.addWidget(self.pack_level_spin)
        opts_row.addWidget(self.pack_split_check)
        opts_row.addStretch(1)
        grid.addLayout(opts_row, 6, 1, 1, 2)

        layout.addWidget(card)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.btn_pack_cancel = QPushButton("Annuler")
        self.btn_pack_cancel.setVisible(False)
        self.btn_pack_cancel.clicked.connect(self._cancel_pack)
        self.btn_pack_run = QPushButton("Packager en .nup")
        self.btn_pack_run.setObjectName("PrimaryBtn")
        self.btn_pack_run.clicked.connect(self._start_pack)
        actions.addWidget(self.btn_pack_cancel)
        actions.addWidget(self.btn_pack_run)
        layout.addLayout(actions)

        self.pack_progress = QProgressBar()
        self.pack_progress.setTextVisible(False)
        self.pack_progress.setVisible(False)
        layout.addWidget(self.pack_progress)

        self.pack_log = QPlainTextEdit()
        self.pack_log.setReadOnly(True)
        self.pack_log.setFont(QFont("Consolas", 10))
        self.pack_log.setPlaceholderText("Les logs de packaging s'afficheront ici…")
        layout.addWidget(self.pack_log, stretch=1)

        self.tabs.addTab(tab, "Packager (.nup)")
        self.explorer_tab = NupExplorerTab(self.root_dir)
        self.tabs.addTab(self.explorer_tab, "NUP Explorer")

        # Status bar with logo icon
        sb = self.statusBar()
        sb.setStyleSheet(
            "QStatusBar { background-color: #090d14; color: #94a3b8; border-top: 1px solid #1e293b; }"
        )
        sb_icon = QLabel()
        logo_png = self.root_dir / "assets" / "app_icon.png"
        if logo_png.is_file():
            sb_icon.setPixmap(
                QPixmap(str(logo_png)).scaled(
                    18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
            sb.addPermanentWidget(sb_icon)
        sb.showMessage("NUP Studio prêt")

    @staticmethod
    def _make_value_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet("color: #f1f5f9;")
        return lbl

    def _refresh_supported_games_table(self) -> None:
        recs: list[GameRecompiler] = self.manager.refresh()
        self.badge_count.setText(
            f"{len(recs)} jeu{'x' if len(recs) > 1 else ''} supporté{'s' if len(recs) > 1 else ''}"
        )
        info = self._latest_update_info
        outdated_ids = set(info.outdated_recompiler_ids) if info else set()
        checked_ids = set(info.checked_recompiler_ids) if info else set()

        self.games_table.setRowCount(len(recs))
        for row, rec in enumerate(recs):
            self.games_table.setItem(row, 0, QTableWidgetItem(rec.name))
            self.games_table.setItem(row, 1, QTableWidgetItem(rec.recompiler_name))
            self.games_table.setItem(row, 2, QTableWidgetItem(rec.folder.name))

            if not rec.is_installed:
                status_text = "Non téléchargé"
                status_color = "#94a3b8"
            elif rec.id in outdated_ids:
                status_text = "Mise à jour dispo"
                status_color = "#fbbf24"
            elif rec.id in checked_ids:
                status_text = "Installé (à jour)"
                status_color = "#4ade80"
            else:
                status_text = "Installé"
                status_color = "#4ade80"

            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor(status_color))
            self.games_table.setItem(row, 3, status_item)

        self.games_table.resizeRowsToContents()

        if self.current_match is not None:
            self._load_rom(self.current_match.nup.file_path)

    def _browse_rom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner une ROM Switch 2 (.nup)",
            "",
            "Package Switch 2 (*.nup);;Tous les fichiers (*.*)",
        )
        if path:
            self._load_rom(Path(path))

    @staticmethod
    def _game_folder_name(rec: GameRecompiler) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]+', " -", rec.name)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or rec.default_output_subdir

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier d'installation PC",
            self.out_input.text().strip() or str(self._default_output_base()),
        )
        if path:
            chosen = Path(path)
            if self.current_match and self.current_match.recompiler:
                sub = self._game_folder_name(self.current_match.recompiler)
                if chosen.name.lower() != sub.lower():
                    chosen = chosen / sub
            self.out_input.setText(str(chosen))

    def _load_rom(self, path: Path) -> None:
        self.rom_input.setText(str(path))
        try:
            nup = parse_nup(path)
        except NupParseError as exc:
            self.current_match = None
            self.btn_compile.setEnabled(False)
            self._set_badge("ROM invalide", "#7f1d1d", "#fecaca")
            self.lbl_status_detail.setText(str(exc))
            self._append_log(f"[Erreur ROM] {exc}")
            return

        match = self.manager.match_rom(nup)
        self.current_match = match

        if match.recompiler is None:
            self._set_badge("Non supporté pour le moment", "#7f1d1d", "#fecaca")
            self.lbl_status_detail.setText(
                f"Aucun sous-dossier dans tools/recompilers/ ne correspond au Title ID {nup.title_id}. "
                "Il n'est donc pas encore possible de recompiler ce jeu."
            )
            self.btn_compile.setEnabled(False)
            self._append_log(
                f"[Analyse] ROM reconnue : Title ID={nup.title_id}, v{nup.version} "
                f"({_format_size(nup.file_size)}) — Aucun recompileur disponible dans tools/recompilers/."
            )
            return

        rec = match.recompiler
        suggested_out = self._default_output_base() / self._game_folder_name(rec)
        self.out_input.setText(str(suggested_out))

        if match.version_supported:
            self._set_badge("Recompilation disponible", "#064e3b", "#6ee7b7")
            dl_note = "" if rec.is_installed else " (sera téléchargé automatiquement au lancement)"
            self.lbl_status_detail.setText(
                f"Jeu supporté par {rec.recompiler_name}{dl_note}. {rec.description}"
            )
            self.btn_compile.setEnabled(self.worker is None)
        else:
            expected = ", ".join(f"v{v}" for v in rec.supported_versions)
            self._set_badge("Version non correspondante", "#78350f", "#fde68a")
            self.lbl_status_detail.setText(
                f"Le sous-dossier tools/recompilers/{rec.folder.name} attend la version {expected}, "
                f"mais votre ROM est en v{nup.version}. Le recompileur risque de refuser le dump."
            )
            self.btn_compile.setEnabled(self.worker is None)

        self._append_log(
            f"[Analyse] {rec.name} ({nup.title_id} v{nup.version}, {_format_size(nup.file_size)}) "
            f"-> tools/recompilers/{rec.folder.name}"
        )

    def _set_badge(self, text: str, bg: str, fg: str) -> None:
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; padding: 5px 12px; "
            "border-radius: 12px; font-weight: 600; font-size: 12px;"
        )

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _start_compile(self) -> None:
        if not self.current_match or not self.current_match.recompiler:
            return
        if self.worker is not None:
            return

        out_text = self.out_input.text().strip()
        if not out_text:
            QMessageBox.warning(
                self,
                "Dossier manquant",
                "Veuillez choisir un dossier d'installation PC.",
            )
            return

        rec = self.current_match.recompiler
        rom_path = self.current_match.nup.file_path
        out_dir = Path(out_text)
        game_sub = self._game_folder_name(rec)
        if out_dir.name.lower() != game_sub.lower():
            out_dir = out_dir / game_sub
            self.out_input.setText(str(out_dir))

        if not is_msys2_ready():
            reply = QMessageBox.question(
                self,
                "Installation requise : MSYS2",
                "La recompilation nécessite l'environnement MSYS2 (Clang64), "
                "qui va être installé sur votre lecteur C (C:\\msys64).\n\n"
                "Souhaitez-vous installer MSYS2 et les outils de compilation maintenant ?\n"
                "(Si vous refusez, la recompilation sera arrêtée.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._append_log(
                    "[Annulé] Installation de MSYS2 refusée par l'utilisateur — recompilation arrêtée."
                )
                return

        self._append_log("")
        self._append_log(f"=== Démarrage de la recompilation : {rec.name} ===")

        self.worker = RecompileWorker(
            rec,
            rom_path,
            out_dir,
            create_desktop_shortcut=self.chk_desktop_shortcut.isChecked(),
            parent=self,
        )
        self.worker.log_line.connect(self._on_compile_log)
        self.worker.prompt_received.connect(self._on_prompt_received)
        self.worker.finished_status.connect(self._on_compile_finished)

        self.btn_compile.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(2)
        self.progress.setVisible(True)
        self._compile_started_at = time.monotonic()
        self._update_elapsed_label()
        self.lbl_compile_elapsed.setVisible(True)
        self._compile_timer.start()
        self.worker.start()

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        secs = max(0, int(seconds))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _update_elapsed_label(self) -> None:
        if self._compile_started_at is None:
            return
        elapsed = self._format_elapsed(time.monotonic() - self._compile_started_at)
        pct = self.progress.value()
        self.lbl_compile_elapsed.setText(f"Temps écoulé : {elapsed} • {pct}%")

    def _on_compile_log(self, line: str) -> None:
        self._append_log(line)
        self._update_compile_progress(line)

    def _update_compile_progress(self, line: str) -> None:
        pct: Optional[int] = None
        m_prog = re.match(r"^\s*progress\s+([0-9]+(?:\.[0-9]+)?)", line)
        if m_prog:
            pct = max(2, min(99, int(float(m_prog.group(1)) * 100)))
        else:
            m_ninja = re.search(r"\[(\d+)/(\d+)\]", line)
            if m_ninja:
                done, total = int(m_ninja.group(1)), int(m_ninja.group(2))
                if total > 0:
                    pct = 55 + int((done / total) * 40)
            else:
                m_emit = re.search(r"emitted\s+(\d+)/(\d+)\s+functions", line)
                if m_emit:
                    done, total = int(m_emit.group(1)), int(m_emit.group(2))
                    if total > 0:
                        pct = 25 + int((done / total) * 28)
                else:
                    low = line.lower()
                    if "extracting exefs" in low or "[extract_nup]" in low:
                        pct = 12
                    elif "generating analysis" in low or "recompiling" in low:
                        pct = 25
                    elif "configuring" in low or "cmake" in low:
                        pct = 50
                    elif "shortcuts" in low or "cleaning" in low or "nettoyage" in low:
                        pct = 96

        if pct is not None and pct > self.progress.value():
            self.progress.setValue(min(99, pct))
            self._update_elapsed_label()

    def _clear_prompt_buttons(self) -> None:
        while self.prompt_buttons_layout.count():
            item = self.prompt_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_prompt_received(self, prompt_text: str, choices: list) -> None:
        self._clear_prompt_buttons()
        self.lbl_prompt_text.setText(f"Question console : {prompt_text.strip()}")

        for choice in choices:
            if choice == "":
                label = "Entrée"
            elif choice.lower() in ("o", "y"):
                label = f"Oui ({choice})"
            elif choice.lower() == "n":
                label = f"Non ({choice})"
            else:
                label = choice
            btn = QPushButton(label)
            btn.setStyleSheet(
                "background-color: #d97706; color: #ffffff; border: none; padding: 6px 14px;"
                if choice in ("O", "Y", "o", "y", "")
                else "background-color: #334155; color: #ffffff; border: none; padding: 6px 14px;"
            )
            btn.clicked.connect(lambda _checked=False, c=choice: self._submit_prompt_answer(c))
            self.prompt_buttons_layout.addWidget(btn)

        self.prompt_bar.setVisible(True)
        self.stdin_container.setVisible(True)
        self.console_input.setFocus()

    def _submit_prompt_answer(self, answer: str) -> None:
        if self.worker is not None:
            self.worker.send_input(answer)
        self.prompt_bar.setVisible(False)
        self.stdin_container.setVisible(False)
        self._clear_prompt_buttons()

    def _send_console_input(self) -> None:
        if self.worker is None:
            return
        text = self.console_input.text()
        self.console_input.clear()
        self._submit_prompt_answer(text)

    def _cancel_compile(self) -> None:
        if self.worker is not None:
            self._append_log("[Annulation] Demande d'arrêt du processus…")
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)

    def _on_compile_finished(self, success: bool, message: str) -> None:
        self._compile_timer.stop()
        elapsed_str = ""
        if self._compile_started_at is not None:
            elapsed_str = self._format_elapsed(time.monotonic() - self._compile_started_at)
            self._compile_started_at = None

        if success:
            self.progress.setValue(100)
        if elapsed_str:
            self.lbl_compile_elapsed.setText(
                f"Temps écoulé : {elapsed_str} • {'100%' if success else 'Interrompu'}"
            )
            self._append_log(f"=== {message} (Temps écoulé : {elapsed_str}) ===")
        else:
            self._append_log(f"=== {message} ===")

        self.worker = None
        self.prompt_bar.setVisible(False)
        self.stdin_container.setVisible(False)
        self._clear_prompt_buttons()
        self.progress.setVisible(False)
        self.progress.setValue(0)
        self.lbl_compile_elapsed.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self._refresh_supported_games_table()
        self.btn_compile.setEnabled(
            self.current_match is not None and self.current_match.recompiler is not None
        )

        popup_msg = f"{message}\n\nTemps écoulé : {elapsed_str}" if elapsed_str else message
        if success:
            QMessageBox.information(self, "Recompilation terminée", popup_msg)
        else:
            QMessageBox.warning(self, "Recompilation interrompue", popup_msg)

    # ---- Extract (.nup) tab handlers ------------------------------------------------

    def _browse_extract_nup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner une ROM Switch 2 (.nup)",
            "",
            "Fichiers NUP (*.nup);;Tous les fichiers (*)",
        )
        if path:
            self.ext_nup_input.setText(path)
            if not self.ext_dest_input.text().strip():
                p = Path(path)
                self.ext_dest_input.setText(str(p.parent / f"{p.stem}_extracted"))

    def _browse_extract_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Dossier de destination pour l'extraction",
            self.ext_dest_input.text().strip() or "",
        )
        if path:
            self.ext_dest_input.setText(path)

    def _run_extract_info(self) -> None:
        if self.extract_worker is not None:
            return
        nup_path = self.ext_nup_input.text().strip()
        if not nup_path or not Path(nup_path).is_file():
            QMessageBox.warning(self, "Fichier manquant", "Sélectionnez un fichier .nup valide.")
            return
        self.ext_log.clear()
        self._launch_extract_worker(
            ["info", nup_path, "--tree"],
            "Analyse du fichier .nup terminée.",
            show_popup=False,
        )

    def _start_extract(self) -> None:
        if self.extract_worker is not None:
            return
        nup_path = self.ext_nup_input.text().strip()
        dest_path = self.ext_dest_input.text().strip()
        if not nup_path or not Path(nup_path).is_file():
            QMessageBox.warning(self, "Fichier manquant", "Sélectionnez un fichier .nup valide.")
            return
        if not dest_path:
            QMessageBox.warning(self, "Destination manquante", "Choisissez un dossier de sortie.")
            return

        args = ["extract", nup_path, dest_path]
        pkg = self.ext_pkg_combo.currentData()
        if pkg:
            args.extend(["--package", str(pkg)])
        subtree = self.ext_subtree_input.text().strip()
        if subtree:
            args.extend(["--subtree", subtree])
        if self.ext_flat_check.isChecked():
            args.append("--flat")

        self.ext_log.clear()
        self._launch_extract_worker(
            args,
            f"Extraction terminée dans : {dest_path}",
            show_popup=True,
        )

    def _launch_extract_worker(
        self, args: list[str], success_message: str, show_popup: bool
    ) -> None:
        extractor_root = self.root_dir / "tools" / "extractors"
        self.btn_ext_run.setEnabled(False)
        self.btn_ext_info.setEnabled(False)
        self.btn_ext_cancel.setVisible(True)
        self.btn_ext_cancel.setEnabled(True)
        self.ext_progress.setVisible(True)
        self.ext_progress.setRange(0, 0)

        worker = NupCliWorker(extractor_root, args, success_message, self)
        worker.log_line.connect(self.ext_log.appendPlainText)
        worker.finished_result.connect(
            lambda ok, msg: self._on_extract_finished(ok, msg, show_popup)
        )
        self.extract_worker = worker
        worker.start()

    def _cancel_extract(self) -> None:
        if self.extract_worker is not None:
            self.ext_log.appendPlainText("[Annulation] Arrêt de l'extraction…")
            self.extract_worker.cancel()
            self.btn_ext_cancel.setEnabled(False)

    def _on_extract_finished(self, success: bool, message: str, show_popup: bool) -> None:
        self.ext_log.appendPlainText(f"=== {message} ===")
        self.extract_worker = None
        self.ext_progress.setVisible(False)
        self.ext_progress.setRange(0, 1)
        self.btn_ext_cancel.setVisible(False)
        self.btn_ext_run.setEnabled(True)
        self.btn_ext_info.setEnabled(True)
        if show_popup:
            if success:
                QMessageBox.information(self, "Extraction terminée", message)
            else:
                QMessageBox.warning(self, "Extraction interrompue", message)

    # ---- Pack (.nup) tab handlers ---------------------------------------------------

    def _browse_pack_src(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Sélectionner le dossier source à packager",
            self.pack_src_input.text().strip() or "",
        )
        if path:
            self.pack_src_input.setText(path)
            if not self.pack_out_input.text().strip():
                p = Path(path)
                self.pack_out_input.setText(str(p.parent / f"{p.name}.nup"))

    def _browse_pack_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le fichier .nup",
            self.pack_out_input.text().strip() or "",
            "Fichiers NUP (*.nup);;Tous les fichiers (*)",
        )
        if path:
            if not path.lower().endswith(".nup"):
                path += ".nup"
            self.pack_out_input.setText(path)

    def _start_pack(self) -> None:
        if self.pack_worker is not None:
            return
        src_path = self.pack_src_input.text().strip()
        out_path = self.pack_out_input.text().strip()
        if not src_path or not Path(src_path).is_dir():
            QMessageBox.warning(self, "Dossier manquant", "Sélectionnez un dossier source valide.")
            return
        if not out_path:
            QMessageBox.warning(self, "Sortie manquante", "Choisissez le fichier .nup de sortie.")
            return

        tid = self.pack_tid_input.text().strip()
        if tid and not re.fullmatch(r"[0-9a-fA-F]{16}", tid):
            QMessageBox.warning(
                self,
                "Title ID invalide",
                "Le Title ID doit contenir exactement 16 caractères hexadécimaux.",
            )
            return

        args = ["pack", src_path, out_path, "--level", str(self.pack_level_spin.value())]
        if tid:
            args.extend(["--title-id", tid])
        name = self.pack_name_input.text().strip()
        if name:
            args.extend(["--name", name])
        ver = self.pack_ver_input.text().strip()
        if ver:
            args.extend(["--display-version", ver])
        pub = self.pack_pub_input.text().strip()
        if pub:
            args.extend(["--publisher", pub])
        if self.pack_split_check.isChecked():
            args.append("--split")

        packager_root = self.root_dir / "tools" / "packagers"
        self.pack_log.clear()
        self.btn_pack_run.setEnabled(False)
        self.btn_pack_cancel.setVisible(True)
        self.btn_pack_cancel.setEnabled(True)
        self.pack_progress.setVisible(True)
        self.pack_progress.setRange(0, 100)
        self.pack_progress.setValue(0)

        worker = NupCliWorker(
            packager_root,
            args,
            f"Packaging terminé : {out_path}",
            self,
        )
        worker.log_line.connect(self.pack_log.appendPlainText)
        worker.progress_percent.connect(self.pack_progress.setValue)
        worker.finished_result.connect(self._on_pack_finished)
        self.pack_worker = worker
        worker.start()

    def _cancel_pack(self) -> None:
        if self.pack_worker is not None:
            self.pack_log.appendPlainText("[Annulation] Arrêt du packaging…")
            self.pack_worker.cancel()
            self.btn_pack_cancel.setEnabled(False)

    def _on_pack_finished(self, success: bool, message: str) -> None:
        self.pack_log.appendPlainText(f"=== {message} ===")
        self.pack_worker = None
        self.pack_progress.setVisible(False)
        self.pack_progress.setValue(0)
        self.btn_pack_cancel.setVisible(False)
        self.btn_pack_run.setEnabled(True)
        if success:
            QMessageBox.information(self, "Packaging terminé", message)
        else:
            QMessageBox.warning(self, "Packaging interrompu", message)

    # ---- App Self-Updater & Recompilers Backup ----

    def _check_app_update_silent(self) -> None:
        if self._update_check_worker is not None:
            return
        w = AppUpdateCheckWorker(self.root_dir, self.manager.recompilers, self)
        w.finished_check.connect(self._on_app_update_checked_silent)
        self._update_check_worker = w
        w.start()

    def _on_app_update_checked_silent(
        self, ok: bool, info: Optional[AppUpdateInfo], _err: str
    ) -> None:
        self._update_check_worker = None
        if not ok or info is None:
            return
        self._latest_update_info = info
        self._refresh_supported_games_table()
        n_recomp = len(info.outdated_recompiler_ids)
        if info.update_available and n_recomp:
            self.btn_app_update.setText(
                f"MàJ v{info.remote_version} + {n_recomp} recompileur(s)"
            )
        elif info.update_available:
            self.btn_app_update.setText(f"Mise à jour v{info.remote_version} disponible")
        elif n_recomp:
            self.btn_app_update.setText(f"MàJ {n_recomp} recompileur(s)")
        else:
            local_ver, _, _ = get_local_version_info(self.root_dir)
            self.btn_app_update.setText(f"v{local_ver} • À jour")
            self.btn_app_update.setStyleSheet("")
            return
        self.btn_app_update.setStyleSheet(
            "background-color: #065f46; border: 1px solid #10b981; color: #ecfdf5; font-weight: 700;"
        )

    def _on_click_app_update(self) -> None:
        if (
            self._update_apply_worker is not None
            or self._update_check_worker is not None
            or self._backup_worker is not None
        ):
            return
        if self._latest_update_info and (
            self._latest_update_info.update_available
            or self._latest_update_info.outdated_recompiler_ids
        ):
            self._prompt_and_apply_update(self._latest_update_info)
            return

        self.btn_app_update.setEnabled(False)
        self.btn_app_update.setText("Vérification…")
        self.manager.refresh()
        w = AppUpdateCheckWorker(self.root_dir, self.manager.recompilers, self)
        w.finished_check.connect(self._on_app_update_checked_manual)
        self._update_check_worker = w
        w.start()

    def _on_app_update_checked_manual(
        self, ok: bool, info: Optional[AppUpdateInfo], err: str
    ) -> None:
        self._update_check_worker = None
        self.btn_app_update.setEnabled(True)
        local_ver, _, _ = get_local_version_info(self.root_dir)
        self.btn_app_update.setText(f"v{local_ver} • Mise à jour")
        if not ok or info is None:
            QMessageBox.warning(
                self,
                "Vérification impossible",
                f"Impossible de vérifier les mises à jour :\n{err}",
            )
            return
        self._on_app_update_checked_silent(True, info, "")
        if info.update_available or info.outdated_recompiler_ids:
            self._prompt_and_apply_update(info)
        else:
            QMessageBox.information(
                self,
                "Tout est à jour",
                f"NUP Studio (v{info.local_version}) et tous les recompileurs installés sont à jour.",
            )

    def _prompt_and_apply_update(self, info: AppUpdateInfo) -> None:
        outdated_recs = [
            r for r in self.manager.recompilers if r.id in set(info.outdated_recompiler_ids)
        ]
        lines: list[str] = []
        if info.update_available:
            lines.append(f"• NUP Studio : {info.local_version} → {info.remote_version}")
        for rec in outdated_recs:
            lines.append(f"• Recompileur : {rec.name} ({rec.id})")

        ans = QMessageBox.question(
            self,
            "Mises à jour disponibles",
            "Les éléments suivants peuvent être mis à jour :\n\n"
            + "\n".join(lines)
            + "\n\nTélécharger et installer ces mises à jour maintenant ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        if outdated_recs:
            self._run_recompiler_updates(outdated_recs, then_app_info=info if info.update_available else None)
        elif info.update_available:
            self._run_app_update_only(info)

    def _run_recompiler_updates(
        self, targets: list[GameRecompiler], then_app_info: Optional[AppUpdateInfo] = None
    ) -> None:
        dlg = QProgressDialog(
            "Mise à jour des recompileurs…",
            "Annuler",
            0,
            100,
            self,
        )
        dlg.setWindowTitle("Mise à jour des recompileurs")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        w = BackupRecompilersWorker(targets, self)
        dlg.canceled.connect(w.cancel)
        w.log_line.connect(dlg.setLabelText)
        w.progress_percent.connect(dlg.setValue)

        def _done(success: bool, _ok: int, _total: int, summary: str) -> None:
            self._backup_worker = None
            dlg.close()
            if success and self._latest_update_info is not None:
                updated_ids = {r.id for r in targets}
                self._latest_update_info = AppUpdateInfo(
                    local_version=self._latest_update_info.local_version,
                    remote_version=self._latest_update_info.remote_version,
                    repo_url=self._latest_update_info.repo_url,
                    branch=self._latest_update_info.branch,
                    update_available=self._latest_update_info.update_available,
                    checked_recompiler_ids=self._latest_update_info.checked_recompiler_ids,
                    outdated_recompiler_ids=tuple(
                        rid for rid in self._latest_update_info.outdated_recompiler_ids if rid not in updated_ids
                    ),
                )
            self._refresh_supported_games_table()
            if not success:
                QMessageBox.warning(self, "Mise à jour des recompileurs", summary)
                return
            if then_app_info is not None:
                self._run_app_update_only(then_app_info)
            else:
                local_ver, _, _ = get_local_version_info(self.root_dir)
                self.btn_app_update.setText(f"v{local_ver} • À jour")
                self.btn_app_update.setStyleSheet("")
                QMessageBox.information(self, "Mises à jour terminées", summary)

        w.finished_result.connect(_done)
        self._backup_worker = w
        w.start()

    def _run_app_update_only(self, info: AppUpdateInfo) -> None:
        dlg = QProgressDialog(
            f"Téléchargement de NUP Studio v{info.remote_version}…",
            "Annuler",
            0,
            100,
            self,
        )
        dlg.setWindowTitle("Mise à jour de NUP Studio")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        w = AppUpdateApplyWorker(self.root_dir, info, self)
        dlg.canceled.connect(w.cancel)
        w.log_line.connect(dlg.setLabelText)
        w.progress_percent.connect(dlg.setValue)

        def _done(success: bool, external_restart: bool, message: str) -> None:
            self._update_apply_worker = None
            dlg.close()
            if not success:
                QMessageBox.warning(self, "Mise à jour", message)
                return
            if external_restart:
                QMessageBox.information(
                    self,
                    "Mise à jour prête",
                    f"{message}\n\nNUP Studio va redémarrer pour finaliser la mise à jour.",
                )
                QApplication.quit()
                return
            ans_restart = QMessageBox.question(
                self,
                "Mise à jour installée",
                f"{message}\n\nRedémarrer NUP Studio maintenant ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans_restart == QMessageBox.StandardButton.Yes:
                QProcess.startDetached(sys.executable, sys.argv)
                QApplication.quit()

        w.finished_result.connect(_done)
        self._update_apply_worker = w
        w.start()

    def _build_credits_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("TabPage")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        intro = QLabel(
            "<b>Crédits — Contributeurs des recompileurs initiaux</b><br>"
            "<span style='color:#94a3b8;'>"
            "Remerciements aux développeurs et contributeurs des 6 recompileurs de base sur lesquels s'appuie NUP Studio :"
            "</span>"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            "background-color: #111827; border: 1px solid #1f2937; border-radius: 10px; "
            "padding: 12px; color: #e2e8f0; font-size: 13px;"
        )
        browser.setHtml(
            """
            <style>
              h3 { color: #60a5fa; margin-top: 14px; margin-bottom: 4px; }
              p { margin-top: 0px; margin-bottom: 10px; line-height: 1.45; color: #cbd5e1; }
            </style>
            <h3>The Legend of Zelda: Ocarina of Time (TOoT:R)</h3>
            <p>Unkn0wnAli4n</p>

            <h3>Super Mario Bros. Wonder (SMW PC Recomp)</h3>
            <p>BelmanteGu (Belmante / SMW-Maker)</p>

            <h3>Mario Kart World (mkw-recomp)</h3>
            <p>Adrià (adriadam10), Andrade_DEV, Andre Rinas, EdouardSence, Elenedeath Olawir, Flávio Alves,
            guillo1990, Igor Lomba, innateessence, mitch030504, riyadhHDA, Robert-Hoffman04, SavageCore,
            tibor-stick, titooo7, Vinicius Stanula</p>

            <h3>Donkey Kong Bananza (DKB Recomp / dkb-pc-recomp)</h3>
            <p>applezra, arthurhfcardoso, Custerok8814, devadobe11-pixel, dwk001, Esa, Felitendo, Kvinkxerli,
            lcd1232, Liam Valty, Lucas Daniel Duarte Cabral, Lucas-blip-png, Luis Hansen, lunar-me,
            MamamaNanana-dot, Marjaperse, Martin Grahn, MatyMeatBoy, monsterlag, mrtink125792, NotShiny,
            Osama Shoubber, Osohow, Pasha Kagan, philneitz, PorlocK-BR, Seth, shadowlink1174, shenjack,
            thatbananaman08, tuwary, Valiantsin Dzerakh, Vicente Mediano, Wellington Dias, wrothgaril, zswxp59</p>

            <h3>Pokémon Legends: Z-A (PokeZ-ARecomp / LASRecomp)</h3>
            <p>aqali1, Atterax, AzleKayn, DieMitchell, dvilela, Felipe Dias, gabrielsem16-bot, German Torres,
            gitarre1234rr5, Hexveil404, huangjianqin, Krijn kok, ky1vstar, lucabravi, Maganra, Man Sarip,
            NevesHenry, Oscar Andrén, Peiikai, Primož Ocepek, Tensorlong, Tristan, we11adam, wimw85</p>

            <h3>Mario Tennis Fever (MTF Recomp)</h3>
            <p>Kayo Chiaradia et les contributeurs des briques de base utilisées
            (Valiantsin Dzerakh / TotK Recomp, Peiikai / LASRecomp, MacNativeGames, jhaizhou / d2r-mobile,
            seedds / nxnative, a55uka / nup, Revivedx / SavageWindRecomp, NotRexed / Hookshot)</p>
            """
        )
        layout.addWidget(browser, stretch=1)
        self.tabs.addTab(tab, "Crédits")

    def _on_click_backup_recompilers(self) -> None:
        if self._backup_worker is not None:
            return
        self.manager.refresh()
        targets = [r for r in self.manager.recompilers if r.repo_url]
        if not targets:
            QMessageBox.information(
                self,
                "Sauvegarde",
                "Aucun recompileur avec dépôt GitHub configuré n'a été trouvé dans le catalogue.",
            )
            return

        ans = QMessageBox.question(
            self,
            "Sauvegarder tous les recompileurs",
            f"Cette opération va télécharger de force les {len(targets)} recompileurs dans :\n"
            f"{self.manager.recompilers_dir}\n\n"
            "Même les recompileurs déjà présents seront retéléchargés et mis à jour "
            "(sauvegarde hors-ligne complète en cas de perte des dépôts).\n\n"
            "Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.btn_backup_recompilers.setEnabled(False)
        dlg = QProgressDialog(
            "Préparation de la sauvegarde des recompileurs…",
            "Annuler",
            0,
            100,
            self,
        )
        dlg.setWindowTitle("Sauvegarde des recompileurs")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        w = BackupRecompilersWorker(targets, self)
        dlg.canceled.connect(w.cancel)
        w.log_line.connect(dlg.setLabelText)
        w.progress_percent.connect(dlg.setValue)

        def _done(success: bool, _ok: int, _total: int, summary: str) -> None:
            self._backup_worker = None
            dlg.close()
            self.btn_backup_recompilers.setEnabled(True)
            self._refresh_supported_games_table()
            if success:
                QMessageBox.information(self, "Sauvegarde terminée", summary)
            else:
                QMessageBox.warning(self, "Sauvegarde", summary)

        w.finished_result.connect(_done)
        self._backup_worker = w
        w.start()
