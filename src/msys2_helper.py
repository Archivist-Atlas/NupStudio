"""Helpers for checking and installing the global MSYS2 (Clang64) toolchain."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MSYS2_DIR = Path("C:/msys64")


def is_msys2_ready(msys_dir: Path = DEFAULT_MSYS2_DIR) -> bool:
    """Return True if MSYS2 Clang64 and required build tools/headers are installed."""
    if os.name != "nt":
        return True
    required = [
        msys_dir / "usr" / "bin" / "bash.exe",
        msys_dir / "usr" / "bin" / "make.exe",
        msys_dir / "clang64" / "bin" / "clang.exe",
        msys_dir / "clang64" / "bin" / "clang++.exe",
        msys_dir / "clang64" / "bin" / "lld.exe",
        msys_dir / "clang64" / "bin" / "cmake.exe",
        msys_dir / "clang64" / "bin" / "ninja.exe",
        msys_dir / "clang64" / "bin" / "python.exe",
        msys_dir / "clang64" / "include" / "SDL3" / "SDL.h",
        msys_dir / "clang64" / "include" / "vpx" / "vpx_decoder.h",
        msys_dir / "clang64" / "include" / "vma" / "vk_mem_alloc.h",
    ]
    return all(p.is_file() for p in required)


def find_msys2_installer(root_dir: Path) -> Path:
    """Return the path to the global MSYS2 PowerShell installer script."""
    return root_dir / "tools" / "install_msys2.ps1"
