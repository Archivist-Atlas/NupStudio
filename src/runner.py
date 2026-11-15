"""Background worker that executes a game's recompiler script and streams logs."""

from __future__ import annotations

import codecs
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Optional

from PySide6.QtCore import QThread, Signal

from .recompiler_manager import GameRecompiler


# Patterns for interactive console prompts that typically do not end with '\n'.
_YES_NO_BRACKET_RE = re.compile(r"\[([OoYyNn])/([OoYyNn])\]\s*$")
_YES_NO_PAREN_RE = re.compile(r"\(([OoYyNn])/([OoYyNn])\)\s*$")
_PRESS_ENTER_RE = re.compile(
    r"(?:appuyez sur entr[ée]e|press enter|press any key)[^\r\n]*$",
    re.IGNORECASE,
)
_GENERIC_PROMPT_RE = re.compile(r"(?:\?\s*|:\s*)$")


def detect_prompt_choices(text: str) -> Optional[list[str]]:
    """Return suggested quick choices if text looks like an interactive prompt, else None."""
    stripped = text.strip()
    if not stripped:
        return None

    m = _YES_NO_BRACKET_RE.search(text) or _YES_NO_PAREN_RE.search(text)
    if m:
        return [m.group(1), m.group(2)]

    if _PRESS_ENTER_RE.search(stripped):
        return [""]

    if _GENERIC_PROMPT_RE.search(text):
        return []

    return None


class RecompileWorker(QThread):
    log_line = Signal(str)
    prompt_received = Signal(str, list)
    finished_status = Signal(bool, str)

    def __init__(
        self,
        recompiler: GameRecompiler,
        rom_path: Path,
        output_dir: Path,
        create_desktop_shortcut: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.recompiler = recompiler
        self.rom_path = rom_path
        self.output_dir = output_dir
        self.create_desktop_shortcut = create_desktop_shortcut
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._cancelled = False
        self._lock = threading.Lock()
        self._partial = ""
        self._last_output_at = 0.0
        self._last_prompt_sent = ""

    def cancel(self) -> None:
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass

    def send_input(self, text: str) -> None:
        """Write a line of user input to the running child process stdin."""
        proc = self._process
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return
        try:
            payload = (text + "\n").encode("utf-8", errors="replace")
            proc.stdin.write(payload)
            proc.stdin.flush()
            display = text if text else "<Entrée>"
            self.log_line.emit(f"> {display}")
            with self._lock:
                self._last_prompt_sent = ""
        except OSError as exc:
            self.log_line.emit(f"[Erreur d'envoi vers la console] {exc}")

    def _build_command(self) -> list[str]:
        if os.name == "nt":
            script = self.recompiler.folder / self.recompiler.windows_entrypoint
            if not script.is_file():
                raise FileNotFoundError(f"Script introuvable : {script}")
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Nup",
                str(self.rom_path),
                "-Dir",
                str(self.output_dir),
            ]
            if self.recompiler.shortcut_name:
                cmd.extend(["-ShortcutName", self.recompiler.shortcut_name])
            icon_path = self.recompiler.resolve_icon_path()
            if icon_path is not None:
                cmd.extend(["-IconPath", str(icon_path)])
            launcher_ui = Path(__file__).resolve().parent / "game_launcher.py"
            if launcher_ui.is_file():
                cmd.extend(["-LauncherUiPath", str(launcher_ui)])
            if not self.create_desktop_shortcut:
                cmd.append("-NoDesktopShortcut")
            return cmd
        script = self.recompiler.folder / self.recompiler.linux_entrypoint
        if not script.is_file():
            raise FileNotFoundError(f"Script introuvable : {script}")
        return [
            "bash",
            str(script),
            "--nup",
            str(self.rom_path),
            "--dir",
            str(self.output_dir),
        ]

    def _emit_partial_if_idle(self, stop_event: threading.Event) -> None:
        """Flush partial lines (like interactive prompts without '\\n') when output pauses."""
        while not stop_event.is_set():
            time.sleep(0.1)
            to_emit: Optional[str] = None
            choices: Optional[list[str]] = None
            with self._lock:
                if self._partial and (time.monotonic() - self._last_output_at) >= 0.2:
                    detected = detect_prompt_choices(self._partial)
                    if detected is not None or (time.monotonic() - self._last_output_at) >= 0.6:
                        to_emit = self._partial
                        self._partial = ""
                        if detected is not None and to_emit != self._last_prompt_sent:
                            choices = detected
                            self._last_prompt_sent = to_emit
            if to_emit is not None:
                self.log_line.emit(to_emit)
            if choices is not None and to_emit is not None:
                self.prompt_received.emit(to_emit, choices)

    def run(self) -> None:
        try:
            cmd = self._build_command()
        except Exception as exc:
            self.finished_status.emit(False, str(exc))
            return

        self.log_line.emit(f"$ {' '.join(cmd)}")
        self.log_line.emit(f"Dossier de travail : {self.recompiler.folder}")
        self.log_line.emit("-" * 60)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        stop_event = threading.Event()
        flusher = threading.Thread(
            target=self._emit_partial_if_idle, args=(stop_event,), daemon=True
        )
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.recompiler.folder),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=creationflags,
            )
            assert self._process.stdout is not None
            flusher.start()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

            while True:
                chunk = self._process.stdout.read(1)
                if not chunk:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        with self._lock:
                            self._partial += tail
                    break
                text = decoder.decode(chunk, final=False)
                if not text:
                    continue
                lines_to_emit: list[str] = []
                with self._lock:
                    self._last_output_at = time.monotonic()
                    self._partial += text
                    while "\n" in self._partial:
                        line, self._partial = self._partial.split("\n", 1)
                        lines_to_emit.append(line.rstrip("\r"))
                for line in lines_to_emit:
                    self.log_line.emit(line)

            stop_event.set()
            flusher.join(timeout=1.0)
            with self._lock:
                remaining = self._partial.rstrip("\r")
                self._partial = ""
            if remaining:
                self.log_line.emit(remaining)

            code = self._process.wait()
        except Exception as exc:
            stop_event.set()
            self.finished_status.emit(False, f"Erreur d'exécution : {exc}")
            return

        if self._cancelled:
            self.finished_status.emit(False, "Recompilation annulée par l'utilisateur.")
        elif code == 0:
            self.finished_status.emit(
                True, f"Recompilation terminée avec succès dans : {self.output_dir}"
            )
        else:
            self.finished_status.emit(
                False, f"Le recompileur s'est terminé avec le code d'erreur {code}."
            )
