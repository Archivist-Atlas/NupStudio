"""Entry point for NUP Studio GUI."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow


def main() -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NUPStudio.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("NUP Studio")
    app.setApplicationDisplayName("NUP Studio")
    root_dir = Path(__file__).resolve().parent
    for icon_rel in ("assets/app_icon.ico", "assets/app_icon.png"):
        icon_file = root_dir / icon_rel
        if icon_file.is_file():
            app.setWindowIcon(QIcon(str(icon_file)))
            break
    window = MainWindow(root_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

