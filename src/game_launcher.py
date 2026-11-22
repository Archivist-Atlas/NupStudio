"""Generic pre-launch configuration popup for Switch2Recomp PC ports."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    from src.launcher_i18n import CATEGORY_I18N, OPTION_DESC_I18N, OPTION_LABEL_I18N
    from src.nup_parser import NupParseError, parse_nup
except ImportError:
    from launcher_i18n import CATEGORY_I18N, OPTION_DESC_I18N, OPTION_LABEL_I18N
    from nup_parser import NupParseError, parse_nup


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
QComboBox {
    background-color: #090d14;
    border: 1px solid #1e293b;
    border-radius: 7px;
    padding: 5px 28px 5px 10px;
    color: #f1f5f9;
    font-size: 12px;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #1e293b;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background-color: #0f1520;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #94a3b8;
}
QComboBox:focus {
    border-color: #e11d48;
}
QComboBox:focus::drop-down {
    border-left-color: #e11d48;
}
QComboBox QAbstractItemView {
    background-color: #0f1520;
    color: #f1f5f9;
    selection-background-color: #e11d48;
    border: 1px solid #1e293b;
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


SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("fr", "Français"),
    ("en", "English"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("es", "Español"),
    ("de", "Deutsch"),
]


def _language_config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Switch2Recomp" / "language_config.json"
    return Path.home() / ".local" / "share" / "switch2recomp" / "language_config.json"


LAUNCHER_I18N: dict[str, dict[str, str]] = {
    "fr": {
        "extracting_nup": "Extraction du package .nup compressé (une seule fois)…",
        "win_title": "{game} — Options de lancement",
        "subtitle": "Paramètres de lancement avant démarrage",
        "rom_lbl": "Source du jeu (.nup ou dossier exefs/romfs)",
        "rom_desc": "Le jeu lit ses ressources depuis ce fichier .nup ou ce dossier au démarrage.",
        "rom_placeholder": "Aucun fichier .nup ou dossier trouvé — sélectionnez votre ROM…",
        "btn_nup": "Fichier .nup…",
        "btn_dir": "Dossier…",
        "btn_reset": "Réinitialiser par défaut",
        "btn_launch": "Démarrer le jeu",
        "dlg_rom_title": "Sélectionner la ROM Switch 2 (.nup)",
        "dlg_rom_filter": "Fichiers NUP (*.nup);;Tous les fichiers (*)",
        "dlg_dir_title": "Sélectionner le dossier du jeu (contenant exefs / romfs)",
        "err_missing_title": "Source manquante",
        "err_missing_msg": "Veuillez sélectionner un fichier .nup valide ou un dossier contenant exefs/romfs avant de démarrer le jeu.",
        "err_invalid_rom_title": "ROM invalide",
        "err_invalid_rom_msg": "Le fichier sélectionné n'est pas un package .nup valide :\n{err}",
        "err_wrong_rom_title": "Mauvaise ROM (.nup)",
        "err_wrong_rom_msg": "Ce fichier .nup correspond au Title ID {found}, alors que ce jeu attend : {expected}.\n\nVeuillez sélectionner la ROM .nup correspondant au jeu.",
        "err_invalid_dir_title": "Dossier invalide",
        "err_invalid_dir_msg": "Le dossier sélectionné doit contenir un sous-dossier 'exefs' (avec 'main').",
        "crash_vk_title": "Crash GPU Vulkan (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "Le jeu s'est fermé à cause d'un timeout du pilote GPU Vulkan (vkQueueSubmit a renvoyé -4 / VK_ERROR_DEVICE_LOST).\n\nSolution recommandée :\n• Activez « Désactiver le culling meshlet (GPU) » (OOT_MESH_NOCULL)\n• Si le problème persiste, activez aussi « Accès mémoire GPU sécurisé » (OOT_ROBUST)",
        "crash_main_title": "Fichier de jeu manquant (main.img)",
        "crash_main_msg": "Le jeu n'a pas trouvé son image exécutable main.img.\nVérifiez que l'installation est complète et lancez le jeu via son raccourci.",
        "crash_romfs_title": "Erreur de lecture du fichier .nup",
        "crash_romfs_msg": "Impossible de lire les données RomFS depuis le fichier .nup sélectionné.\nVérifiez que le fichier .nup est valide et accessible.",
        "crash_generic_title": "Le jeu s'est arrêté de manière inattendue",
        "crash_generic_msg": "Le processus s'est terminé avec le code d'erreur {code}.",
        "lang_scope_title": "Appliquer la langue",
        "lang_scope_msg": "Voulez-vous appliquer cette langue à l'ensemble des jeux ou uniquement pour « {game} » ?",
        "lang_scope_all": "Tous les jeux",
        "lang_scope_game": "Ce jeu uniquement",
        "lang_scope_cancel": "Annuler",
    },
    "en": {
        "extracting_nup": "Extracting compressed .nup package (one-time)…",
        "win_title": "{game} — Launch Options",
        "subtitle": "Pre-launch configuration",
        "rom_lbl": "Game source (.nup or exefs/romfs folder)",
        "rom_desc": "The game reads its assets from this .nup file or folder on startup.",
        "rom_placeholder": "No .nup file or folder found — select your ROM…",
        "btn_nup": ".nup file…",
        "btn_dir": "Folder…",
        "btn_reset": "Reset to defaults",
        "btn_launch": "Launch game",
        "dlg_rom_title": "Select Switch 2 ROM (.nup)",
        "dlg_rom_filter": "NUP Files (*.nup);;All Files (*)",
        "dlg_dir_title": "Select game folder (containing exefs / romfs)",
        "err_missing_title": "Missing source",
        "err_missing_msg": "Please select a valid .nup file or a folder containing exefs/romfs before launching the game.",
        "err_invalid_rom_title": "Invalid ROM",
        "err_invalid_rom_msg": "The selected file is not a valid .nup package:\n{err}",
        "err_wrong_rom_title": "Wrong ROM (.nup)",
        "err_wrong_rom_msg": "This .nup file has Title ID {found}, while this game expects: {expected}.\n\nPlease select the matching .nup ROM.",
        "err_invalid_dir_title": "Invalid folder",
        "err_invalid_dir_msg": "The selected folder must contain an 'exefs' subfolder (with 'main').",
        "crash_vk_title": "Vulkan GPU Crash (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "The game closed due to a Vulkan GPU driver timeout (vkQueueSubmit returned -4 / VK_ERROR_DEVICE_LOST).\n\nRecommended solution:\n• Enable « Disable meshlet culling (GPU) » (OOT_MESH_NOCULL)\n• If it persists, also enable « Robust GPU buffer access » (OOT_ROBUST)",
        "crash_main_title": "Missing game file (main.img)",
        "crash_main_msg": "The game could not find its executable image main.img.\nVerify that the installation is complete and launch the game via its shortcut.",
        "crash_romfs_title": "Error reading .nup file",
        "crash_romfs_msg": "Unable to read RomFS data from the selected .nup file.\nVerify that the .nup file is valid and accessible.",
        "crash_generic_title": "The game exited unexpectedly",
        "crash_generic_msg": "The process exited with error code {code}.",
        "lang_scope_title": "Apply language",
        "lang_scope_msg": "Do you want to apply this language to all games or only to « {game} »?",
        "lang_scope_all": "All games",
        "lang_scope_game": "This game only",
        "lang_scope_cancel": "Cancel",
    },
    "it": {
        "extracting_nup": "Estrazione del pacchetto .nup compresso (una sola volta)…",
        "win_title": "{game} — Opzioni di avvio",
        "subtitle": "Parametri di pre-avvio",
        "rom_lbl": "Origine del gioco (.nup o cartella exefs/romfs)",
        "rom_desc": "Il gioco legge le proprie risorse da questo file .nup o cartella all'avvio.",
        "rom_placeholder": "Nessun file .nup o cartella trovato — seleziona la ROM…",
        "btn_nup": "File .nup…",
        "btn_dir": "Cartella…",
        "btn_reset": "Ripristina predefiniti",
        "btn_launch": "Avvia il gioco",
        "dlg_rom_title": "Seleziona ROM Switch 2 (.nup)",
        "dlg_rom_filter": "File NUP (*.nup);;Tutti i file (*)",
        "dlg_dir_title": "Seleziona cartella del gioco (con exefs / romfs)",
        "err_missing_title": "Origine mancante",
        "err_missing_msg": "Seleziona un file .nup valido o una cartella contenente exefs/romfs prima di avviare il gioco.",
        "err_invalid_rom_title": "ROM non valida",
        "err_invalid_rom_msg": "Il file selezionato non è un pacchetto .nup valido:\n{err}",
        "err_wrong_rom_title": "ROM errata (.nup)",
        "err_wrong_rom_msg": "Questo file .nup corrisponde al Title ID {found}, mentre questo gioco richiede: {expected}.\n\nSeleziona la ROM .nup corrispondente.",
        "err_invalid_dir_title": "Cartella non valida",
        "err_invalid_dir_msg": "La cartella selezionata deve contenere una sottocartella 'exefs' (con 'main').",
        "crash_vk_title": "Crash GPU Vulkan (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "Il gioco si è chiuso per un timeout del driver GPU Vulkan (vkQueueSubmit ha restituito -4 / VK_ERROR_DEVICE_LOST).\n\nSoluzione consigliata:\n• Attiva « Disattiva meshlet culling (GPU) » (OOT_MESH_NOCULL)\n• Se persiste, attiva anche « Accesso sicuro memoria GPU » (OOT_ROBUST)",
        "crash_main_title": "File di gioco mancante (main.img)",
        "crash_main_msg": "Il gioco non ha trovato l'immagine eseguibile main.img.\nVerifica che l'installazione sia completa e avvia il gioco dal collegamento.",
        "crash_romfs_title": "Errore di lettura del file .nup",
        "crash_romfs_msg": "Impossibile leggere i dati RomFS dal file .nup selezionato.\nVerifica che il file .nup sia valido e accessibile.",
        "crash_generic_title": "Il gioco si è chiuso in modo imprevisto",
        "crash_generic_msg": "Il processo è terminato con codice di errore {code}.",
        "lang_scope_title": "Applica lingua",
        "lang_scope_msg": "Vuoi applicare questa lingua a tutti i giochi o solo a « {game} »?",
        "lang_scope_all": "Tutti i giochi",
        "lang_scope_game": "Solo questo gioco",
        "lang_scope_cancel": "Annulla",
    },
    "pt": {
        "extracting_nup": "Extraindo pacote .nup comprimido (apenas uma vez)…",
        "win_title": "{game} — Opções de inicialização",
        "subtitle": "Parâmetros de pré-inicialização",
        "rom_lbl": "Origem do jogo (.nup ou pasta exefs/romfs)",
        "rom_desc": "O jogo lê seus recursos deste arquivo .nup ou pasta ao iniciar.",
        "rom_placeholder": "Nenhum arquivo .nup ou pasta encontrado — selecione sua ROM…",
        "btn_nup": "Arquivo .nup…",
        "btn_dir": "Pasta…",
        "btn_reset": "Restaurar padrões",
        "btn_launch": "Iniciar jogo",
        "dlg_rom_title": "Selecionar ROM Switch 2 (.nup)",
        "dlg_rom_filter": "Arquivos NUP (*.nup);;Todos os arquivos (*)",
        "dlg_dir_title": "Selecionar pasta do jogo (contendo exefs / romfs)",
        "err_missing_title": "Origem ausente",
        "err_missing_msg": "Selecione um arquivo .nup válido ou uma pasta contendo exefs/romfs antes de iniciar o jogo.",
        "err_invalid_rom_title": "ROM inválida",
        "err_invalid_rom_msg": "O arquivo selecionado não é um pacote .nup válido:\n{err}",
        "err_wrong_rom_title": "ROM incorreta (.nup)",
        "err_wrong_rom_msg": "Este arquivo .nup tem Title ID {found}, enquanto este jogo espera: {expected}.\n\nSelecione a ROM .nup correspondente.",
        "err_invalid_dir_title": "Pasta inválida",
        "err_invalid_dir_msg": "A pasta selecionada deve conter uma subpasta 'exefs' (com 'main').",
        "crash_vk_title": "Falha de GPU Vulkan (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "O jogo fechou devido a um timeout do driver GPU Vulkan (vkQueueSubmit retornou -4 / VK_ERROR_DEVICE_LOST).\n\nSolução recomendada:\n• Ative « Desativar meshlet culling (GPU) » (OOT_MESH_NOCULL)\n• Se persistir, ative também « Acesso seguro à memória GPU » (OOT_ROBUST)",
        "crash_main_title": "Arquivo de jogo ausente (main.img)",
        "crash_main_msg": "O jogo não encontrou a imagem executável main.img.\nVerifique se a instalação está completa e inicie pelo atalho.",
        "crash_romfs_title": "Erro ao ler arquivo .nup",
        "crash_romfs_msg": "Não foi possível ler os dados RomFS do arquivo .nup selecionado.\nVerifique se o arquivo .nup é válido e acessível.",
        "crash_generic_title": "O jogo encerrou inesperadamente",
        "crash_generic_msg": "O processo foi encerrado com o código de erro {code}.",
        "lang_scope_title": "Aplicar idioma",
        "lang_scope_msg": "Deseja aplicar este idioma a todos os jogos ou apenas a « {game} »?",
        "lang_scope_all": "Todos os jogos",
        "lang_scope_game": "Apenas este jogo",
        "lang_scope_cancel": "Cancelar",
    },
    "es": {
        "extracting_nup": "Extrayendo paquete .nup comprimido (solo una vez)…",
        "win_title": "{game} — Opciones de inicio",
        "subtitle": "Parámetros previos al inicio",
        "rom_lbl": "Origen del juego (.nup o carpeta exefs/romfs)",
        "rom_desc": "El juego lee sus recursos desde este archivo .nup o carpeta al iniciar.",
        "rom_placeholder": "No se encontró archivo .nup ni carpeta — selecciona tu ROM…",
        "btn_nup": "Archivo .nup…",
        "btn_dir": "Carpeta…",
        "btn_reset": "Restablecer valores",
        "btn_launch": "Iniciar juego",
        "dlg_rom_title": "Seleccionar ROM Switch 2 (.nup)",
        "dlg_rom_filter": "Archivos NUP (*.nup);;Todos los archivos (*)",
        "dlg_dir_title": "Seleccionar carpeta del juego (con exefs / romfs)",
        "err_missing_title": "Origen faltante",
        "err_missing_msg": "Selecciona un archivo .nup válido o una carpeta que contenga exefs/romfs antes de iniciar el juego.",
        "err_invalid_rom_title": "ROM no válida",
        "err_invalid_rom_msg": "El archivo seleccionado no es un paquete .nup válido:\n{err}",
        "err_wrong_rom_title": "ROM incorrecta (.nup)",
        "err_wrong_rom_msg": "Este archivo .nup tiene Title ID {found}, mientras que este juego espera: {expected}.\n\nSelecciona la ROM .nup correspondiente.",
        "err_invalid_dir_title": "Carpeta no válida",
        "err_invalid_dir_msg": "La carpeta seleccionada debe contener una subcarpeta 'exefs' (con 'main').",
        "crash_vk_title": "Error de GPU Vulkan (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "El juego se cerró por un tiempo de espera del controlador GPU Vulkan (vkQueueSubmit devolvió -4 / VK_ERROR_DEVICE_LOST).\n\nSolución recomendada:\n• Activa « Desactivar meshlet culling (GPU) » (OOT_MESH_NOCULL)\n• Si persiste, activa también « Acceso seguro a memoria GPU » (OOT_ROBUST)",
        "crash_main_title": "Falta archivo del juego (main.img)",
        "crash_main_msg": "El juego no encontró su imagen ejecutable main.img.\nVerifica que la instalación esté completa e inicia desde su acceso directo.",
        "crash_romfs_title": "Error al leer archivo .nup",
        "crash_romfs_msg": "No se pudieron leer los datos RomFS del archivo .nup seleccionado.\nVerifica que el archivo .nup sea válido y accesible.",
        "crash_generic_title": "El juego se cerró inesperadamente",
        "crash_generic_msg": "El proceso finalizó con el código de error {code}.",
        "lang_scope_title": "Aplicar idioma",
        "lang_scope_msg": "¿Quieres aplicar este idioma a todos los juegos o solo a « {game} »?",
        "lang_scope_all": "Todos los juegos",
        "lang_scope_game": "Solo este juego",
        "lang_scope_cancel": "Cancelar",
    },
    "de": {
        "extracting_nup": "Komprimiertes .nup-Paket wird entpackt (einmalig)…",
        "win_title": "{game} — Startoptionen",
        "subtitle": "Startkonfiguration vor dem Spielstart",
        "rom_lbl": "Spielquelle (.nup oder exefs/romfs-Ordner)",
        "rom_desc": "Das Spiel lädt seine Ressourcen beim Start aus dieser .nup-Datei oder diesem Ordner.",
        "rom_placeholder": "Keine .nup-Datei oder Ordner gefunden — ROM auswählen…",
        "btn_nup": ".nup-Datei…",
        "btn_dir": "Ordner…",
        "btn_reset": "Standardwerte wiederherstellen",
        "btn_launch": "Spiel starten",
        "dlg_rom_title": "Switch 2 ROM (.nup) auswählen",
        "dlg_rom_filter": "NUP-Dateien (*.nup);;Alle Dateien (*)",
        "dlg_dir_title": "Spielordner auswählen (mit exefs / romfs)",
        "err_missing_title": "Quelle fehlt",
        "err_missing_msg": "Bitte wähle eine gültige .nup-Datei oder einen Ordner mit exefs/romfs aus, bevor du das Spiel startest.",
        "err_invalid_rom_title": "Ungültige ROM",
        "err_invalid_rom_msg": "Die ausgewählte Datei ist kein gültiges .nup-Paket:\n{err}",
        "err_wrong_rom_title": "Falsche ROM (.nup)",
        "err_wrong_rom_msg": "Diese .nup-Datei hat die Title ID {found}, dieses Spiel erwartet jedoch: {expected}.\n\nBitte wähle die passende .nup-ROM aus.",
        "err_invalid_dir_title": "Ungültiger Ordner",
        "err_invalid_dir_msg": "Der ausgewählte Ordner muss einen Unterordner 'exefs' (mit 'main') enthalten.",
        "crash_vk_title": "Vulkan-GPU-Absturz (VK_ERROR_DEVICE_LOST)",
        "crash_vk_msg": "Das Spiel wurde wegen eines Vulkan-GPU-Treiber-Timeouts beendet (vkQueueSubmit gab -4 / VK_ERROR_DEVICE_LOST zurück).\n\nEmpfohlene Lösung:\n• Aktiviere « Meshlet-Culling deaktivieren (GPU) » (OOT_MESH_NOCULL)\n• Falls es weiterhin auftritt, aktiviere auch « Sicherer GPU-Speicherzugriff » (OOT_ROBUST)",
        "crash_main_title": "Spieldatei fehlt (main.img)",
        "crash_main_msg": "Das Spiel konnte sein ausführbares Image main.img nicht finden.\nPrüfe, ob die Installation vollständig ist, und starte das Spiel über die Verknüpfung.",
        "crash_romfs_title": "Fehler beim Lesen der .nup-Datei",
        "crash_romfs_msg": "RomFS-Daten konnten nicht aus der ausgewählten .nup-Datei gelesen werden.\nPrüfe, ob die .nup-Datei gültig und zugänglich ist.",
        "crash_generic_title": "Das Spiel wurde unerwartet beendet",
        "crash_generic_msg": "Der Prozess wurde mit Fehlercode {code} beendet.",
        "lang_scope_title": "Sprache anwenden",
        "lang_scope_msg": "Möchtest du diese Sprache auf alle Spiele oder nur auf « {game} » anwenden?",
        "lang_scope_all": "Alle Spiele",
        "lang_scope_game": "Nur dieses Spiel",
        "lang_scope_cancel": "Abbrechen",
    },
}


def _tr_lang(lang: str, key: str, **kwargs: Any) -> str:
    strings = LAUNCHER_I18N.get(lang) or LAUNCHER_I18N["fr"]
    text = strings.get(key) or LAUNCHER_I18N["fr"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def diagnose_game_crash(output: str, exit_code: int, lang: str = "fr") -> tuple[str, str]:
    """Inspect game stdout/stderr to provide an actionable crash diagnosis."""
    low = output.lower()
    if (
        ("vkqueuesubmit" in low and "-4" in low)
        or "vk_error_device_lost" in low
        or "gpu fault" in low
    ):
        return (
            _tr_lang(lang, "crash_vk_title"),
            _tr_lang(lang, "crash_vk_msg"),
        )
    if "missing" in low and "main.img" in low:
        return (
            _tr_lang(lang, "crash_main_title"),
            _tr_lang(lang, "crash_main_msg"),
        )
    if "romfs" in low and ("fail" in low or "error" in low or "missing" in low):
        return (
            _tr_lang(lang, "crash_romfs_title"),
            _tr_lang(lang, "crash_romfs_msg"),
        )
    return (
        _tr_lang(lang, "crash_generic_title"),
        _tr_lang(lang, "crash_generic_msg", code=exit_code),
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
        self._game_key = str(
            self.config.get("game_id")
            or self.config.get("game_name")
            or self.base_dir.name
        )
        self._lang = self._load_initial_language()
        self._cat_labels: list[tuple[QLabel, str, dict[str, str]]] = []
        self._opt_labels: list[tuple[QLabel, QLabel, dict[str, Any]]] = []

        self.resize(680, 660)
        self.setMinimumSize(560, 520)
        self.setStyleSheet(STYLESHEET)

        icon_rel = self.config.get("icon", "")
        if icon_rel:
            icon_file = self.base_dir / icon_rel
            if icon_file.is_file():
                self.setWindowIcon(QIcon(str(icon_file)))

        self._build_ui()
        self._apply_translations()

    def _tr(self, key: str, **kwargs: Any) -> str:
        return _tr_lang(self._lang, key, **kwargs)

    def _load_initial_language(self) -> str:
        valid = {code for code, _ in SUPPORTED_LANGUAGES}
        p = _language_config_path()
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    games = data.get("games")
                    if isinstance(games, dict):
                        game_lang = str(games.get(self._game_key) or "").lower()
                        if game_lang in valid:
                            return game_lang
                    def_lang = str(data.get("language_default") or "").lower()
                    if def_lang in valid:
                        return def_lang
            except (OSError, ValueError):
                pass
        return "fr"

    def _save_language_choice(self, new_lang: str, apply_all: bool) -> None:
        p = _language_config_path()
        data: dict[str, Any] = {"language_default": "fr", "games": {}}
        if p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError):
                pass
        if not isinstance(data.get("games"), dict):
            data["games"] = {}
        if apply_all:
            data["language_default"] = new_lang
            data["games"].pop(self._game_key, None)
        else:
            if not data.get("language_default"):
                data["language_default"] = self._lang
            data["games"][self._game_key] = new_lang
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _on_language_changed(self, _index: int) -> None:
        new_lang = str(self.lang_combo.currentData() or "fr")
        if new_lang == self._lang:
            return
        game_name = self.config.get("game_name", self._game_key)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_tr_lang(new_lang, "lang_scope_title"))
        box.setText(_tr_lang(new_lang, "lang_scope_msg", game=game_name))
        btn_all = box.addButton(
            _tr_lang(new_lang, "lang_scope_all"), QMessageBox.ButtonRole.AcceptRole
        )
        btn_game = box.addButton(
            _tr_lang(new_lang, "lang_scope_game"), QMessageBox.ButtonRole.ActionRole
        )
        btn_cancel = box.addButton(
            _tr_lang(new_lang, "lang_scope_cancel"), QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(btn_all)
        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_cancel or clicked is None:
            self.lang_combo.blockSignals(True)
            idx = self.lang_combo.findData(self._lang)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
            self.lang_combo.blockSignals(False)
            return
        apply_all = clicked == btn_all
        self._save_language_choice(new_lang, apply_all=apply_all)
        self._lang = new_lang
        self._apply_translations()

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
            if not cand:
                continue
            if p.is_file():
                return str(p)
            if p.is_dir() and ((p / "exefs").is_dir() or (p / "main").is_file()):
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
        self.subtitle_lbl = QLabel("Paramètres de lancement avant démarrage")
        self.subtitle_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
        sub_row.addWidget(self.subtitle_lbl)
        sub_row.addStretch()
        title_col.addLayout(sub_row)
        header.addLayout(title_col, stretch=1)

        self.lang_combo = QComboBox()
        for code, label in SUPPORTED_LANGUAGES:
            self.lang_combo.addItem(label, code)
        idx = self.lang_combo.findData(self._lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        header.addWidget(self.lang_combo, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        if self.rom_env_var:
            rom_card = QFrame()
            rom_card.setObjectName("OptionCard")
            rom_layout = QVBoxLayout(rom_card)
            rom_layout.setContentsMargins(14, 12, 14, 12)
            rom_layout.setSpacing(6)

            self.rom_lbl = QLabel("Source du jeu (.nup ou dossier exefs/romfs)")
            self.rom_lbl.setStyleSheet("font-weight: 600; color: #f8fafc; font-size: 13px;")
            self.rom_desc = QLabel(
                "Le jeu lit ses ressources depuis ce fichier .nup ou ce dossier au démarrage."
            )
            self.rom_desc.setWordWrap(True)
            self.rom_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
            rom_layout.addWidget(self.rom_lbl)
            rom_layout.addWidget(self.rom_desc)

            rom_row = QHBoxLayout()
            rom_row.setSpacing(8)
            self.rom_input = QLineEdit()
            self.rom_input.setPlaceholderText(
                "Aucun fichier .nup ou dossier trouvé — sélectionnez votre ROM…"
            )
            self.rom_input.setText(self._resolve_initial_rom_path())
            self.btn_browse_rom = QPushButton("Fichier .nup…")
            self.btn_browse_rom.clicked.connect(self._browse_rom)
            self.btn_browse_dir = QPushButton("Dossier…")
            self.btn_browse_dir.clicked.connect(self._browse_rom_dir)
            rom_row.addWidget(self.rom_input, stretch=1)
            rom_row.addWidget(self.btn_browse_rom)
            rom_row.addWidget(self.btn_browse_dir)
            rom_layout.addLayout(rom_row)

            root.addWidget(rom_card)
        else:
            self.rom_lbl = None
            self.rom_desc = None
            self.btn_browse_rom = None
            self.btn_browse_dir = None

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
                self._cat_labels.append(
                    (cat_lbl, cat, opt.get("category_i18n") or {})
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
            self._opt_labels.append((name_lbl, desc_lbl, opt))

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
        self.btn_reset = QPushButton("Réinitialiser par défaut")
        self.btn_reset.clicked.connect(self._reset_defaults)
        self.btn_launch = QPushButton("Démarrer le jeu")
        self.btn_launch.setObjectName("LaunchBtn")
        self.btn_launch.clicked.connect(self._launch_game)

        footer.addWidget(self.btn_reset)
        footer.addStretch()
        footer.addWidget(self.btn_launch)
        root.addLayout(footer)

    def _apply_translations(self) -> None:
        game_name = self.config.get("game_name", "Switch 2 Game")
        self.setWindowTitle(self._tr("win_title", game=game_name))
        self.subtitle_lbl.setText(self._tr("subtitle"))
        if self.rom_lbl is not None:
            self.rom_lbl.setText(self._tr("rom_lbl"))
        if self.rom_desc is not None:
            self.rom_desc.setText(self._tr("rom_desc"))
        if self.rom_input is not None:
            self.rom_input.setPlaceholderText(self._tr("rom_placeholder"))
        if self.btn_browse_rom is not None:
            self.btn_browse_rom.setText(self._tr("btn_nup"))
        if self.btn_browse_dir is not None:
            self.btn_browse_dir.setText(self._tr("btn_dir"))
        self.btn_reset.setText(self._tr("btn_reset"))
        self.btn_launch.setText(self._tr("btn_launch"))
        for cat_lbl, default_cat, cat_i18n in self._cat_labels:
            translated = (
                cat_i18n.get(self._lang)
                or CATEGORY_I18N.get(default_cat, {}).get(self._lang)
                or default_cat
            )
            cat_lbl.setText(translated)
        for name_lbl, desc_lbl, opt in self._opt_labels:
            lbl_i18n = opt.get("label_i18n") or {}
            desc_i18n = opt.get("description_i18n") or {}
            raw_lbl = opt.get("label", "")
            raw_desc = opt.get("description", "")
            name_lbl.setText(
                lbl_i18n.get(self._lang)
                or OPTION_LABEL_I18N.get(raw_lbl, {}).get(self._lang)
                or raw_lbl
            )
            desc_lbl.setText(
                desc_i18n.get(self._lang)
                or OPTION_DESC_I18N.get(raw_desc, {}).get(self._lang)
                or raw_desc
            )

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
            self._tr("dlg_rom_title"),
            start_dir,
            self._tr("dlg_rom_filter"),
        )
        if path and self.rom_input is not None:
            self.rom_input.setText(path)

    def _browse_rom_dir(self) -> None:
        current = self.rom_input.text().strip() if self.rom_input else ""
        start_dir = str(Path(current)) if current and Path(current).is_dir() else (
            str(Path(current).parent) if current and Path(current).parent.is_dir() else str(self.base_dir)
        )
        path = QFileDialog.getExistingDirectory(
            self,
            self._tr("dlg_dir_title"),
            start_dir,
        )
        if path and self.rom_input is not None:
            self.rom_input.setText(path)

    @staticmethod
    def _nup_has_compressed_entries(nup_path: Path) -> bool:
        try:
            with nup_path.open("rb") as f:
                sb = f.read(0x80)
                if len(sb) < 0x18 or sb[:4] != b"NUP\x01":
                    return False
                toc_off, toc_size = struct.unpack_from("<QQ", sb, 8)
                if toc_size == 0 or toc_size > (64 << 20):
                    return False
                f.seek(toc_off)
                toc = f.read(toc_size)
            packages_count = struct.unpack_from("<I", toc, 4)[0]
            dirs_count, files_count = struct.unpack_from("<QQ", toc, 8)
            file_table = 0x30 + packages_count * 0x40 + dirs_count * 0x18
            for i in range(files_count):
                off = file_table + i * 0x50
                if off + 0x41 > len(toc):
                    break
                size = struct.unpack_from("<Q", toc, off + 8)[0]
                codec = toc[off + 0x40]
                if size > 0 and codec != 0:
                    return True
        except OSError:
            pass
        return False

    def _ensure_extracted_nup_cache(self, nup_path: Path) -> Path:
        cache_dir = self.base_dir / "_nup_cache"
        meta_path = cache_dir / ".nup_meta.json"
        st = nup_path.stat()
        sig = {
            "path": str(nup_path.resolve()),
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
        if meta_path.is_file() and (cache_dir / "exefs" / "main").is_file():
            try:
                old = json.loads(meta_path.read_text(encoding="utf-8"))
                if old == sig:
                    return cache_dir
            except (OSError, ValueError):
                pass

        packagers_dir = Path(__file__).resolve().parent.parent / "tools" / "packagers"
        if packagers_dir.is_dir() and str(packagers_dir) not in sys.path:
            sys.path.insert(0, str(packagers_dir))
        from nup.access import extract_tree  # type: ignore
        from nup.read import NupReader  # type: ignore

        dlg = QProgressDialog(self._tr("extracting_nup"), "", 0, 0, self)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        QApplication.processEvents()

        try:
            tmp_dir = self.base_dir / "_nup_cache_tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            with NupReader(str(nup_path)) as r:
                pkgs = [p for p in r.packages if p.exefs_dir != 0xFFFFFFFF and p.kind in (0, 1)]
                best = max(pkgs, key=lambda p: p.version) if pkgs else (r.packages[0] if r.packages else None)
                if best is not None and best.exefs_dir != 0xFFFFFFFF:
                    extract_tree(r, str(tmp_dir / "exefs"), best.exefs_dir, progress=lambda *_: QApplication.processEvents())
                if best is not None and best.romfs_dir != 0xFFFFFFFF:
                    extract_tree(r, str(tmp_dir / "romfs"), best.romfs_dir, progress=lambda *_: QApplication.processEvents())
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            tmp_dir.rename(cache_dir)
            meta_path.write_text(json.dumps(sig, indent=2), encoding="utf-8")
        finally:
            dlg.close()
        return cache_dir

    def _launch_game(self) -> None:
        resolved_rom_arg: Optional[str] = None
        resolved_rom_env: Optional[str] = None
        resolved_romfs_dir: Optional[str] = None
        if self.rom_env_var and self.rom_input is not None:
            rom_path = self.rom_input.text().strip()
            p = Path(rom_path) if rom_path else Path()
            if not rom_path or (not p.is_file() and not p.is_dir()):
                QMessageBox.warning(
                    self,
                    self._tr("err_missing_title"),
                    self._tr("err_missing_msg"),
                )
                return
            if p.is_file():
                try:
                    nup_info = parse_nup(rom_path)
                except NupParseError as exc:
                    QMessageBox.warning(
                        self,
                        self._tr("err_invalid_rom_title"),
                        self._tr("err_invalid_rom_msg", err=exc),
                    )
                    return
                expected_ids = {
                    str(tid).upper()
                    for tid in (self.config.get("expected_title_ids") or [])
                    if tid
                }
                if expected_ids:
                    found_ids = {nup_info.title_id.upper()}
                    for pkg in nup_info.all_packages:
                        found_ids.add(pkg.title_id.upper())
                    expanded_found = set(found_ids)
                    for tid in found_ids:
                        if tid.endswith("800"):
                            expanded_found.add(tid[:-3] + "000")
                    if not (expanded_found & expected_ids):
                        QMessageBox.warning(
                            self,
                            self._tr("err_wrong_rom_title"),
                            self._tr(
                                "err_wrong_rom_msg",
                                found=nup_info.title_id,
                                expected=", ".join(sorted(expected_ids)),
                            ),
                        )
                        return
                cmd_list = [str(part) for part in self.config.get("command", [])]
                cmd_uses_exefs = any("--exefs" in part for part in cmd_list) and any(
                    "{rom}" in part for part in cmd_list
                )
                need_extract_nup = bool(self.config.get("extract_nup", False)) or (
                    (cmd_uses_exefs or self.config.get("extract_compressed_nup", False))
                    and self._nup_has_compressed_entries(p)
                )
                if need_extract_nup:
                    cache_dir = self._ensure_extracted_nup_cache(p)
                    resolved_rom_arg = (
                        str(cache_dir / "exefs")
                        if (cache_dir / "exefs" / "main").is_file()
                        else str(cache_dir)
                    )
                    resolved_rom_env = str(cache_dir)
                    if (cache_dir / "romfs").is_dir():
                        resolved_romfs_dir = str(cache_dir / "romfs")
                else:
                    resolved_rom_arg = rom_path
                    resolved_rom_env = rom_path
            else:
                active_dir = p
                updates_dir = p / "updates"
                if updates_dir.is_dir():
                    upd_candidates = [
                        d for d in updates_dir.iterdir()
                        if d.is_dir() and (d / "exefs" / "main").is_file()
                    ]
                    if upd_candidates:
                        active_dir = max(
                            upd_candidates,
                            key=lambda d: int(d.name) if d.name.isdigit() else -1,
                        )
                if (active_dir / "exefs" / "main").is_file():
                    resolved_rom_arg = str(active_dir / "exefs")
                elif (active_dir / "main").is_file():
                    resolved_rom_arg = str(active_dir)
                else:
                    QMessageBox.warning(
                        self,
                        self._tr("err_invalid_dir_title"),
                        self._tr("err_invalid_dir_msg"),
                    )
                    return
                resolved_rom_env = str(active_dir)
                if (active_dir / "romfs").is_dir():
                    resolved_romfs_dir = str(active_dir / "romfs")

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
            env[self.rom_env_var] = resolved_rom_env or self.rom_input.text().strip()
        if resolved_romfs_dir:
            env["NXRT_FS_ROMFS"] = resolved_romfs_dir

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
        if self.rom_env_var and self.rom_input is not None:
            rom_val = resolved_rom_arg or self.rom_input.text().strip()
            romfs_val = resolved_romfs_dir or str(self.base_dir / "romfs")
            cmd = [
                str(part).replace("{rom}", rom_val).replace("{romfs}", romfs_val)
                for part in cmd
            ]
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
        title, advice = diagnose_game_crash(output, exit_code, self._lang)
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

