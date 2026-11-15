"""Background worker for NUP extraction and packaging via tools/extractors and tools/packagers."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QThread, Signal


_PACK_PROGRESS_RE = re.compile(r"pack:\s*(\d+)/(\d+)\s+files\s+\((\d+)%\)")


class NupCliWorker(QThread):
    """Runs a `nup.cli` subcommand in a background process and streams output."""

    log_line = Signal(str)
    progress_percent = Signal(int)
    finished_result = Signal(bool, str)

    def __init__(
        self,
        tool_root: Path,
        args: list[str],
        success_message: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.tool_root = tool_root
        self.args = args
        self.success_message = success_message
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def run(self) -> None:
        if not (self.tool_root / "nup" / "cli.py").is_file():
            self.finished_result.emit(
                False,
                f"Module nup introuvable dans : {self.tool_root}",
            )
            return

        env = dict(os.environ)
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{self.tool_root}{os.pathsep}{existing_pp}"
            if existing_pp
            else str(self.tool_root)
        )
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        cmd = [sys.executable, "-u", "-m", "nup.cli", *self.args]
        self.log_line.emit(f"$ {' '.join(cmd)}")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.tool_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.finished_result.emit(False, f"Impossible de lancer Python : {exc}")
            return

        assert self._proc.stdout is not None
        buf = bytearray()
        last_pct = -1
        while True:
            chunk = self._proc.stdout.read(256)
            if not chunk:
                break
            for b in chunk:
                if b in (10, 13):  # \n or \r
                    if buf:
                        line = buf.decode("utf-8", errors="replace").strip()
                        buf.clear()
                        if line:
                            m = _PACK_PROGRESS_RE.search(line)
                            if m:
                                pct = int(m.group(3))
                                if pct != last_pct:
                                    last_pct = pct
                                    self.progress_percent.emit(pct)
                                    self.log_line.emit(line)
                            else:
                                self.log_line.emit(line)
                else:
                    buf.append(b)
        if buf:
            line = buf.decode("utf-8", errors="replace").strip()
            if line:
                self.log_line.emit(line)

        code = self._proc.wait()
        if self._cancelled:
            self.finished_result.emit(False, "Opération annulée par l'utilisateur.")
        elif code == 0:
            self.progress_percent.emit(100)
            self.finished_result.emit(True, self.success_message)
        else:
            self.finished_result.emit(
                False, f"L'outil s'est terminé avec le code d'erreur {code}."
            )

