"""Generic pre-launch configuration popup for Switch2Recomp PC ports."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


STYLESHEET = """
QMainWindow, QWidget#Root {
    background-color: #0b0f17;
    color: #f1f5f9;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}
QLabel {
    background-color: transparent;
    color: #f1f5f9;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
QWidget#ScrollContent {
    background-color: transparent;
}
QFrame#OptionCard {
    background-color: #131b29;
    border: 1px solid #1e293b;
    border-radius: 10px;
}
QFrame#OptionCard:hover {
    border-color: #334155;
}
QPushButton {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 9px 16px;
    color: #f1f5f9;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #293548;
}
QPushButton#LaunchBtn {
    background-color: #e11d48;
    border: none;
    color: #ffffff;
    padding: 11px 26px;
    font-size: 14px;
    font-weight: 700;
    border-radius: 8px;
}
QPushButton#LaunchBtn:hover {
    background-color: #be123c;
}
QCheckBox#ToggleSwitch {
    spacing: 8px;
    background-color: transparent;
}
QCheckBox#ToggleSwitch::indicator {
    width: 44px;
    height: 24px;
    border-radius: 12px;
    background-color: #334155;
    border: 1px solid #475569;
}
QCheckBox#ToggleSwitch::indicator:checked {
    background-color: #e11d48;
    border-color: #fb7185;
}
QLineEdit {
    background-color: #090d14;
    border: 1px solid #1e293b;
    border-radius: 7px;
    padding: 8px 10px;
    color: #f1f5f9;
}
QLineEdit:focus {
    border-color: #e11d48;
}
QMessageBox {
    background-color: #0f1520;
    color: #f1f5f9;
}
QMessageBox QLabel {
    background-color: transparent;
    color: #f1f5f9;
}
"""


def diagnose_game_crash(output: str, exit_code: int) -> tuple[str, str]:
    """Inspect game stdout/stderr to provide an actionable crash diagnosis."""
    low = output.lower()
    if (
        ("vkqueuesubmit" in low and "-4" in low)
        or "vk_error_device_lost" in low
        or "gpu fault" in low
    ):
        return (
            "Crash GPU Vulkan (VK_ERROR_DEVICE_LOST)",
            "Le jeu s'est fermé à cause d'un timeout du pilote GPU Vulkan "
            "(vkQueueSubmit a renvoyé -4 / VK_ERROR_DEVICE_LOST).\n\n"
            "Solution recommandée :\n"
            "• Activez « Désactiver le culling meshlet (GPU) » (OOT_MESH_NOCULL)\n"
            "• Si le problème persiste, activez aussi « Accès mémoire GPU sécurisé » (OOT_ROBUST)",
        )
    if "missing" in low and "main.img" in low:
        return (
            "Fichier de jeu manquant (main.img)",
            "Le jeu n'a pas trouvé son image exécutable main.img.\n"
            "Vérifiez que l'installation est complète et lancez le jeu via son raccourci.",
        )
    if "romfs" in low and ("fail" in low or "error" in low or "missing" in low):
        return (
            "Erreur de lecture du fichier .nup",
            "Impossible de lire les données RomFS depuis le fichier .nup sélectionné.\n"
            "Vérifiez que le fichier .nup est valide et accessible.",
        )
    return (
        "Le jeu s'est arrêté de manière inattendue",
        f"Le processus s'est terminé avec le code d'erreur {exit_code}.",
    )


class _GameProcessMonitor(QThread):
    """Runs the game process and captures its trailing stdout/stderr for crash diagnosis."""

    finished_code = Signal(object, str)

    def __init__(self, cmd: list[str], cwd: str, env: dict[str, str]) -> None:
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.env = env

    def run(self) -> None:
        lines: deque[str] = deque(maxlen=120)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.finished_code.emit(1, str(exc))
            return

        assert proc.stdout is not None
        for raw_line in proc.stdout:
            lines.append(raw_line.decode("utf-8", errors="replace").rstrip())
        code = proc.wait()
        self.finished_code.emit(code, "\n".join(lines))


class GameLauncherWindow(QMainWindow):
    def __init__(self, config_path: Path, extra_args: list[str]) -> None:
        super().__init__()
        self.config_path = config_path
        self.base_dir = config_path.parent
        self.extra_args = extra_args
        self.config: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        self.settings_path = self.base_dir / "launch_settings.json"
        self.saved_settings, self._saved_rom_path = self._load_saved_settings()
        self.toggles: dict[str, tuple[QCheckBox, dict[str, Any]]] = {}
        self.rom_env_var = self._detect_rom_env_var()
        self.rom_input: Optional[QLineEdit] = None
        self._monitor: Optional[_GameProcessMonitor] = None

        game_name = self.config.get("game_name", "Switch 2 Game")
        self.setWindowTitle(f"{game_name} — Options de lancement")
        self.resize(680, 660)
        self.setMinimumSize(560, 520)
        self.setStyleSheet(STYLESHEET)

        icon_rel = self.config.get("icon", "")
        if icon_rel:
            icon_file = self.base_dir / icon_rel
            if icon_file.is_file():
                self.setWindowIcon(QIcon(str(icon_file)))

        self._build_ui()

    def _detect_rom_env_var(self) -> str:
        env_var = str(self.config.get("rom_env_var") or "").strip()
        if env_var:
            return env_var
        cfg_file = self.base_dir / "config.json"
        if cfg_file.is_file():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "nup" in data:
                    return "OOT_RECOMP_NUP"
            except (OSError, ValueError):
                pass
        return ""

    def _resolve_initial_rom_path(self) -> str:
        candidates: list[str] = []
        if self._saved_rom_path:
            candidates.append(self._saved_rom_path)
        if self.config.get("rom_path"):
            candidates.append(str(self.config["rom_path"]))
        cfg_file = self.base_dir / "config.json"
        if cfg_file.is_file():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("nup"):
                    candidates.append(str(data["nup"]))
            except (OSError, ValueError):
                pass
        for cand in candidates:
            p = Path(cand)
            if cand and p.is_file():
                return str(p)
        return ""

    def _load_saved_settings(self) -> tuple[dict[str, bool], str]:
        if not self.settings_path.is_file():
            return {}, ""
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rom_path = str(data.get("__rom_path__") or "")
                toggles = {
                    str(k): bool(v)
                    for k, v in data.items()
                    if k != "__rom_path__"
                }
                return toggles, rom_path
        except (OSError, ValueError):
            pass
        return {}, ""

    def _save_settings(self) -> None:
        state: dict[str, Any] = {
            opt_id: chk.isChecked() for opt_id, (chk, _) in self.toggles.items()
        }
        if self.rom_input is not None:
            rom_val = self.rom_input.text().strip()
            state["__rom_path__"] = rom_val
            cfg_file = self.base_dir / "config.json"
            if rom_val and cfg_file.is_file():
                try:
                    data = json.loads(cfg_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        data["nup"] = rom_val
                        cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except (OSError, ValueError):
                    pass
        self.settings_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _resolve_header_icon(self) -> Path | None:
        """Prefer a high-res PNG icon for the popup header, falling back to the .ico."""
        candidates: list[Path] = []
        icon_png = self.config.get("icon_png", "")
        if icon_png:
            candidates.append(self.base_dir / icon_png)
        icon_rel = self.config.get("icon", "")
        if icon_rel:
            icon_path = self.base_dir / icon_rel
            candidates.append(icon_path.with_suffix(".png"))
        candidates.append(self.base_dir / "game_icon.png")
        if icon_rel:
            candidates.append(self.base_dir / icon_rel)
        for cand in candidates:
            if cand.is_file():
                return cand
        return None

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Header with icon + game name + version
        header = QHBoxLayout()
        header.setSpacing(14)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(64, 64)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_file = self._resolve_header_icon()
        if icon_file is not None:
            pix = QPixmap(str(icon_file)).scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_lbl.setPixmap(pix)
        else:
            icon_lbl.setText("S2R")
            icon_lbl.setStyleSheet(
                "background-color: #e11d48; color: #ffffff; border-radius: 12px; "
                "font-size: 20px; font-weight: 800;"
            )
        header.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title_lbl = QLabel(self.config.get("game_name", "Jeu Switch 2"))
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("font-size: 20px; font-weight: 700; color: #ffffff;")
        title_col.addWidget(title_lbl)

        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        version = self.config.get("version", "")
        if version:
            ver_badge = QLabel(version)
            ver_badge.setStyleSheet(
                "background-color: #172554; color: #93c5fd; border: 1px solid #1e3a8a; "
                "padding: 2px 10px; border-radius: 10px; font-size: 11px; font-weight: 600;"
            )
            sub_row.addWidget(ver_badge)
        subtitle = QLabel("Paramètres de lancement avant démarrage")
        subtitle.setStyleSheet("color: #94a3b8; font-size: 12px;")
        sub_row.addWidget(subtitle)
        sub_row.addStretch()
        title_col.addLayout(sub_row)
        header.addLayout(title_col, stretch=1)
        root.addLayout(header)

        if self.rom_env_var:
            rom_card = QFrame()
            rom_card.setObjectName("OptionCard")
            rom_layout = QVBoxLayout(rom_card)
            rom_layout.setContentsMargins(14, 12, 14, 12)
            rom_layout.setSpacing(6)

            rom_lbl = QLabel("Fichier ROM (.nup)")
            rom_lbl.setStyleSheet("font-weight: 600; color: #f8fafc; font-size: 13px;")
            rom_desc = QLabel(
                "Le jeu lit ses ressources directement depuis ce fichier .nup au démarrage."
            )
            rom_desc.setWordWrap(True)
            rom_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
            rom_layout.addWidget(rom_lbl)
            rom_layout.addWidget(rom_desc)

            rom_row = QHBoxLayout()
            rom_row.setSpacing(8)
            self.rom_input = QLineEdit()
            self.rom_input.setPlaceholderText(
                "Aucun fichier .nup trouvé — sélectionnez votre ROM…"
            )
            self.rom_input.setText(self._resolve_initial_rom_path())
            btn_browse_rom = QPushButton("Parcourir…")
            btn_browse_rom.clicked.connect(self._browse_rom)
            rom_row.addWidget(self.rom_input, stretch=1)
            rom_row.addWidget(btn_browse_rom)
            rom_layout.addLayout(rom_row)

            root.addWidget(rom_card)

        # Scrollable options list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("ScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 8, 0)
        scroll_layout.setSpacing(10)

        current_category: Optional[str] = None
        for opt in self.config.get("options", []):
            cat = opt.get("category", "Général")
            if cat != current_category:
                current_category = cat
                cat_lbl = QLabel(cat)
                cat_lbl.setStyleSheet(
                    "color: #94a3b8; font-size: 12px; font-weight: 700; "
                    "text-transform: uppercase; padding-top: 6px;"
                )
                scroll_layout.addWidget(cat_lbl)

            card = QFrame()
            card.setObjectName("OptionCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(12)

            text_col = QVBoxLayout()
            text_col.setSpacing(3)
            name_lbl = QLabel(opt["label"])
            name_lbl.setWordWrap(True)
            name_lbl.setStyleSheet("font-weight: 600; color: #f8fafc; font-size: 13px;")
            desc_lbl = QLabel(opt.get("description", ""))
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
            text_col.addWidget(name_lbl)
            text_col.addWidget(desc_lbl)
            card_layout.addLayout(text_col, stretch=1)

            right_col = QHBoxLayout()
            right_col.setSpacing(8)
            state_lbl = QLabel()
            state_lbl.setMinimumWidth(28)
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            chk = QCheckBox()
            chk.setObjectName("ToggleSwitch")
            opt_id = opt["id"]
            default_val = bool(opt.get("default", False))
            initial = self.saved_settings.get(opt_id, default_val)
            chk.setChecked(initial)
            self._update_state_label(state_lbl, initial)
            chk.toggled.connect(
                lambda checked, lbl=state_lbl: self._update_state_label(lbl, checked)
            )

            right_col.addWidget(state_lbl)
            right_col.addWidget(chk)
            card_layout.addLayout(right_col)

            self.toggles[opt_id] = (chk, opt)
            scroll_layout.addWidget(card)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        root.addWidget(scroll, stretch=1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.setSpacing(10)
        btn_reset = QPushButton("Réinitialiser par défaut")
        btn_reset.clicked.connect(self._reset_defaults)
        btn_launch = QPushButton("Démarrer le jeu")
        btn_launch.setObjectName("LaunchBtn")
        btn_launch.clicked.connect(self._launch_game)

        footer.addWidget(btn_reset)
        footer.addStretch()
        footer.addWidget(btn_launch)
        root.addLayout(footer)

    @staticmethod
    def _update_state_label(lbl: QLabel, checked: bool) -> None:
        if checked:
            lbl.setText("ON")
            lbl.setStyleSheet("color: #fb7185; font-weight: 700; font-size: 12px;")
        else:
            lbl.setText("OFF")
            lbl.setStyleSheet("color: #64748b; font-weight: 700; font-size: 12px;")

    def _reset_defaults(self) -> None:
        for chk, opt in self.toggles.values():
            chk.setChecked(bool(opt.get("default", False)))

    def _browse_rom(self) -> None:
        start_dir = ""
        if self.rom_input is not None and self.rom_input.text().strip():
            start_dir = str(Path(self.rom_input.text().strip()).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner la ROM Switch 2 (.nup)",
            start_dir,
            "Fichiers NUP (*.nup);;Tous les fichiers (*)",
        )
        if path and self.rom_input is not None:
            self.rom_input.setText(path)

    def _launch_game(self) -> None:
        if self.rom_env_var and self.rom_input is not None:
            rom_path = self.rom_input.text().strip()
            if not rom_path or not Path(rom_path).is_file():
                QMessageBox.warning(
                    self,
                    "ROM manquante",
                    "Veuillez sélectionner un fichier .nup valide avant de démarrer le jeu.",
                )
                return

        self._save_settings()
        env = dict(os.environ)

        merged_env = dict(self.config.get("fixed_env", {}))
        merged_env.update(self.config.get("env", {}))
        for key, value in merged_env.items():
            if key.upper() == "PATH":
                env["PATH"] = f"{value}{os.pathsep}{env.get('PATH', '')}"
            else:
                env[key] = str(value)

        if self.rom_env_var and self.rom_input is not None:
            env[self.rom_env_var] = self.rom_input.text().strip()

        for _opt_id, (chk, opt) in self.toggles.items():
            var = opt.get("env_var") or opt.get("env")
            if not var:
                continue
            on_val = opt.get("on_value", "1") if "on_value" in opt or "env_var" not in opt else opt.get("on_value")
            val = on_val if chk.isChecked() else opt.get("off_value")
            if val is None:
                env.pop(var, None)
            else:
                env[var] = str(val)

        cmd = list(self.config.get("command", [])) + self.extra_args
        cwd = str(self.config.get("working_dir") or self.base_dir)
        env["PYTHONUNBUFFERED"] = "1"
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        self._monitor = _GameProcessMonitor(cmd, cwd, env)
        self._monitor.finished_code.connect(self._on_game_exited)
        self.hide()
        self._monitor.start()

    def _on_game_exited(self, exit_code: int, output: str) -> None:
        if self._monitor is not None:
            self._monitor.wait(2000)
            self._monitor = None
        app = QApplication.instance()
        if exit_code == 0:
            if app is not None:
                app.quit()
            else:
                self.close()
            return

        if app is not None:
            app.setQuitOnLastWindowClosed(True)
        self.show()
        self.raise_()
        self.activateWindow()
        title, advice = diagnose_game_crash(output, exit_code)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(advice)
        tail = "\n".join(output.splitlines()[-25:]).strip()
        if tail:
            box.setDetailedText(tail)
        box.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="Switch2Recomp Game Launcher")
    parser.add_argument("--config", required=True, help="Path to launch_options.json")
    args, extra = parser.parse_known_args()

    app = QApplication(sys.argv)
    win = GameLauncherWindow(Path(args.config).resolve(), extra)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

