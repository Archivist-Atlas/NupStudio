"""Lightweight JSON i18n manager for NUP Studio."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("fr", "Français"),
    ("en", "English"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("es", "Español"),
    ("de", "Deutsch"),
]


def _settings_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Switch2Recomp" / "nup_studio_settings.json"
    return Path.home() / ".config" / "switch2recomp" / "nup_studio_settings.json"


def language_config_path() -> Path:
    """Shared language config in the parent folder of Switch2Recomp installations."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Switch2Recomp" / "language_config.json"
    return Path.home() / ".local" / "share" / "switch2recomp" / "language_config.json"


class I18nManager:
    """Loads translations from i18n/<lang>.json with French fallback."""

    def __init__(self, root_dir: Path) -> None:
        self.i18n_dir = root_dir / "i18n"
        self._fallback: dict[str, str] = self._load_file("fr")
        self._lang = self._load_saved_language()
        self._strings: dict[str, str] = (
            self._fallback if self._lang == "fr" else self._load_file(self._lang)
        )

    @property
    def language(self) -> str:
        return self._lang

    def _load_file(self, lang: str) -> dict[str, str]:
        path = self.i18n_dir / f"{lang}.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            pass
        return {}

    def _load_saved_language(self) -> str:
        valid = {code for code, _ in SUPPORTED_LANGUAGES}
        for p, key in ((language_config_path(), "language_default"), (_settings_path(), "language")):
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        lang = str(data.get(key) or "").lower()
                        if lang in valid:
                            return lang
                except (OSError, ValueError):
                    pass
        return "fr"

    def set_language(self, lang: str) -> None:
        valid = {code for code, _ in SUPPORTED_LANGUAGES}
        if lang not in valid:
            lang = "fr"
        self._lang = lang
        self._strings = (
            self._fallback if lang == "fr" else self._load_file(lang)
        )
        # Persist global default in Switch2Recomp/language_config.json
        cfg_p = language_config_path()
        try:
            cfg_p.parent.mkdir(parents=True, exist_ok=True)
            cfg_data: dict[str, Any] = {}
            if cfg_p.is_file():
                try:
                    loaded = json.loads(cfg_p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        cfg_data = loaded
                except (OSError, ValueError):
                    pass
            cfg_data["language_default"] = lang
            if not isinstance(cfg_data.get("games"), dict):
                cfg_data["games"] = {}
            cfg_p.write_text(
                json.dumps(cfg_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        # Keep legacy nup_studio_settings.json in sync
        p = _settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if p.is_file():
                try:
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (OSError, ValueError):
                    pass
            data["language"] = lang
            p.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def tr(self, key: str, **kwargs: Any) -> str:
        text = self._strings.get(key) or self._fallback.get(key) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text
