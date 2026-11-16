"""Entry point for NUP Studio GUI."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow


def _get_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        local_recompilers = exe_dir / "tools" / "recompilers"
        has_local_tools = (exe_dir / "tools" / "games_db.json").is_file() or (
            local_recompilers.is_dir() and any(local_recompilers.iterdir())
        )
        if has_local_tools and (exe_dir / "assets").is_dir():
            return exe_dir
        parent = exe_dir.parent
        if (parent / "tools").is_dir() or (parent / "assets").is_dir():
            return parent
        return exe_dir
    return Path(__file__).resolve().parent


def main() -> int:
    root_dir = _get_root_dir()
    if len(sys.argv) >= 2 and sys.argv[1] == "--nup-cli":
        pkg_root = root_dir / "tools" / "packagers"
        if str(pkg_root) not in sys.path:
            sys.path.insert(0, str(pkg_root))
        from nup import cli as nup_cli

        sys.argv = ["nup.cli", *sys.argv[2:]]
        return int(nup_cli.main() or 0)

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NUPStudio.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("NUP Studio")
    app.setApplicationDisplayName("NUP Studio")
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

