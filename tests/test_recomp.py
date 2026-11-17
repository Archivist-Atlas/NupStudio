"""Tests for Switch2Recomp NUP parser, recompiler discovery, and GUI."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.nup_parser import NupParseError, parse_nup
from src.recompiler_manager import RecompilerManager


def _build_synthetic_nup(
    path: Path,
    title_id: int = 0x0400486024DEC000,
    version: int = 131072,
    exefs_names: tuple[str, ...] = ("main", "sdk", "subsdk0"),
) -> None:
    """Build a minimal valid NUP\\x01 file matching tools/extract.cpp layout."""
    packages_count = 1
    dirs_count = 1
    files_count = len(exefs_names)

    pkg_table_off = 0x30
    dir_table_off = pkg_table_off + packages_count * 0x40
    file_table_off = dir_table_off + dirs_count * 0x18
    children_off = file_table_off + files_count * 0x50
    pool_off = children_off + files_count * 4

    pool_bytes = bytearray()
    name_entries: list[tuple[int, int]] = []
    for name in exefs_names:
        encoded = name.encode("utf-8")
        name_entries.append((len(pool_bytes), len(encoded)))
        pool_bytes.extend(encoded)

    toc = bytearray(pool_off + len(pool_bytes))
    struct.pack_into("<I", toc, 4, packages_count)
    struct.pack_into("<Q", toc, 8, dirs_count)
    struct.pack_into("<Q", toc, 16, files_count)
    struct.pack_into("<Q", toc, 24, pool_off)

    # Package 0
    struct.pack_into("<Q", toc, pkg_table_off, title_id)
    struct.pack_into("<I", toc, pkg_table_off + 0x0C, 0)  # exefs dir index 0
    struct.pack_into("<I", toc, pkg_table_off + 0x14, version)
    struct.pack_into("<B", toc, pkg_table_off + 0x18, 0)  # kind 0

    # Dir 0
    struct.pack_into("<II", toc, dir_table_off + 8, 0, files_count)

    # Files + children
    for idx, (n_off, n_len) in enumerate(name_entries):
        f_off = file_table_off + idx * 0x50
        struct.pack_into("<II", toc, f_off, n_off, n_len)
        struct.pack_into("<I", toc, children_off + idx * 4, dirs_count + idx)

    toc[pool_off:] = pool_bytes

    super_block = bytearray(0x80)
    super_block[:4] = b"NUP\x01"
    toc_off = 0x80
    struct.pack_into("<Q", super_block, 8, toc_off)
    struct.pack_into("<Q", super_block, 16, len(toc))

    path.write_bytes(bytes(super_block) + bytes(toc))


def test_parse_valid_nup(tmp_path: Path) -> None:
    rom = tmp_path / "oot.nup"
    _build_synthetic_nup(rom)
    info = parse_nup(rom)
    assert info.title_id == "0400486024DEC000"
    assert info.version == 131072
    assert info.exefs_files == ["main", "sdk", "subsdk0"]


def test_parse_invalid_nup(tmp_path: Path) -> None:
    bad = tmp_path / "bad.nup"
    bad.write_bytes(b"NOT_A_NUP_FILE" + b"\x00" * 200)
    with pytest.raises(NupParseError):
        parse_nup(bad)


def test_recompiler_manager_matches_zelda(tmp_path: Path) -> None:
    mgr = RecompilerManager(ROOT)
    assert any(r.id == "zelda-ocarina-of-time" for r in mgr.recompilers)
    assert any(r.id == "super-mario-bros-wonder" for r in mgr.recompilers)

    rom = tmp_path / "oot.nup"
    _build_synthetic_nup(rom, title_id=0x0400486024DEC000, version=131072)
    match = mgr.match_rom(parse_nup(rom))
    assert match.recompiler is not None
    assert match.recompiler.id == "zelda-ocarina-of-time"
    assert match.recompiler.shortcut_name == "The Legend of Zelda - Ocarina of Time"
    assert match.recompiler.resolve_icon_path() is not None
    assert match.version_supported is True

    wonder_rom = tmp_path / "wonder.nup"
    _build_synthetic_nup(
        wonder_rom,
        title_id=0x010015100B514000,
        version=327680,
        exefs_names=("main", "sdk", "rtld"),
    )
    wonder_match = mgr.match_rom(parse_nup(wonder_rom))
    assert wonder_match.recompiler is not None
    assert wonder_match.recompiler.id == "super-mario-bros-wonder"
    assert wonder_match.recompiler.shortcut_name == "Super Mario Bros. Wonder"
    assert wonder_match.recompiler.resolve_icon_path() is not None
    assert wonder_match.version_supported is True

    other_rom = tmp_path / "unknown.nup"
    _build_synthetic_nup(other_rom, title_id=0x0400999999999999, version=65536)
    other_match = mgr.match_rom(parse_nup(other_rom))
    assert other_match.recompiler is None
    assert other_match.version_supported is False


def test_gui_window_initializes_offscreen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow(ROOT)
    assert win.tabs.count() == 5
    rom = tmp_path / "oot.nup"
    _build_synthetic_nup(rom)
    win._load_rom(rom)
    assert win.btn_compile.isEnabled() is True
    assert win.status_badge.text() == "Recompilation disponible"
    assert "TOoT:R" in win.lbl_status_detail.text()
    assert win._format_elapsed(75) == "01:15"
    assert win._format_elapsed(3665) == "01:01:05"
    win.progress.setValue(0)
    win._update_compile_progress("progress 0.460 Recompiling main")
    assert win.progress.value() == 46
    win._update_compile_progress("[50/100] Building CXX object foo.cpp.obj")
    assert win.progress.value() == 75
    win.close()
    app.processEvents()


def test_detect_prompt_choices() -> None:
    from src.runner import detect_prompt_choices

    assert detect_prompt_choices(":: Procéder à l’installation ? [O/n] ") == ["O", "n"]
    assert detect_prompt_choices("Continue? [Y/n] ") == ["Y", "n"]
    assert detect_prompt_choices("Overwrite? (y/N)") == ["y", "N"]
    assert detect_prompt_choices("Appuyez sur Entrée pour continuer...") == [""]
    assert detect_prompt_choices("Downloading packages...") is None


def test_game_launcher_window_initializes_offscreen(tmp_path: Path, monkeypatch) -> None:
    import json
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.game_launcher import GameLauncherWindow

    tpl_path = ROOT / "tools" / "recompilers" / "zelda-ocarina-of-time" / "launch_options_template.json"
    cfg = json.loads(tpl_path.read_text(encoding="utf-8"))
    cfg["command"] = ["echo"]
    cfg["working_dir"] = str(tmp_path)
    cfg_path = tmp_path / "launch_options.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    win = GameLauncherWindow(cfg_path, [])
    assert len(win.toggles) == len(cfg["options"])
    assert win.toggles["local_zero"][0].isChecked() is True
    assert win.toggles["mesh_nocull"][0].isChecked() is False
    win._save_settings()
    assert (tmp_path / "launch_settings.json").is_file()
    win.close()
    app.processEvents()


def test_game_launcher_prefers_png_header_icon(tmp_path: Path, monkeypatch) -> None:
    import json
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.game_launcher import GameLauncherWindow

    (tmp_path / "game_icon.ico").write_bytes(b"ico")
    cfg = {
        "game_name": "Test Game",
        "icon": "game_icon.ico",
        "command": ["echo"],
        "working_dir": str(tmp_path),
        "options": [],
    }
    cfg_path = tmp_path / "launch_options.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    win1 = GameLauncherWindow(cfg_path, [])
    assert win1._resolve_header_icon() == tmp_path / "game_icon.ico"
    win1.close()

    (tmp_path / "game_icon.png").write_bytes(b"png")
    win2 = GameLauncherWindow(cfg_path, [])
    assert win2._resolve_header_icon() == tmp_path / "game_icon.png"
    win2.close()
    app.processEvents()


def test_nup_pack_and_extract_roundtrip(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    src_dir = tmp_path / "sample_game"
    (src_dir / "exefs").mkdir(parents=True)
    (src_dir / "romfs").mkdir(parents=True)
    (src_dir / "exefs" / "main").write_bytes(b"MAIN_BIN")
    (src_dir / "romfs" / "hello.txt").write_text("hello switch 2", encoding="utf-8")

    nup_file = tmp_path / "out.nup"
    pack_env = dict(os.environ, PYTHONPATH=str(ROOT / "tools" / "packagers"))
    res_pack = subprocess.run(
        [
            sys.executable,
            "-m",
            "nup.cli",
            "pack",
            str(src_dir),
            str(nup_file),
            "--title-id",
            "010015100B514000",
            "--name",
            "TestGame",
            "-q",
        ],
        cwd=str(ROOT / "tools" / "packagers"),
        env=pack_env,
        capture_output=True,
        text=True,
    )
    assert res_pack.returncode == 0, res_pack.stderr
    assert nup_file.is_file()

    ext_dir = tmp_path / "extracted"
    ext_env = dict(os.environ, PYTHONPATH=str(ROOT / "tools" / "extractors"))
    res_ext = subprocess.run(
        [sys.executable, "-m", "nup.cli", "extract", str(nup_file), str(ext_dir)],
        cwd=str(ROOT / "tools" / "extractors"),
        env=ext_env,
        capture_output=True,
        text=True,
    )
    assert res_ext.returncode == 0, res_ext.stderr
    assert (ext_dir / "romfs" / "hello.txt").read_text(encoding="utf-8") == "hello switch 2"

    # Multi-package roundtrip (base + updates) with --package all
    multi_src = tmp_path / "multi_src"
    (multi_src / "base" / "exefs").mkdir(parents=True)
    (multi_src / "base" / "romfs").mkdir(parents=True)
    (multi_src / "base" / "exefs" / "main").write_bytes(b"BASE_MAIN")
    (multi_src / "base" / "romfs" / "base.txt").write_text("base", encoding="utf-8")
    (multi_src / "updates" / "65536" / "romfs").mkdir(parents=True)
    (multi_src / "updates" / "65536" / "romfs" / "upd.txt").write_text("upd", encoding="utf-8")

    multi_nup = tmp_path / "multi.nup"
    res_mp = subprocess.run(
        [sys.executable, "-m", "nup.cli", "pack", str(multi_src), str(multi_nup), "--title-id", "010015100B514000", "-q"],
        cwd=str(ROOT / "tools" / "packagers"),
        env=pack_env,
        capture_output=True,
        text=True,
    )
    assert res_mp.returncode == 0, res_mp.stderr

    multi_ext = tmp_path / "multi_ext"
    res_me = subprocess.run(
        [sys.executable, "-m", "nup.cli", "extract", str(multi_nup), str(multi_ext), "--package", "all"],
        cwd=str(ROOT / "tools" / "extractors"),
        env=ext_env,
        capture_output=True,
        text=True,
    )
    assert res_me.returncode == 0, res_me.stderr
    assert (multi_ext / "base" / "romfs" / "base.txt").read_text(encoding="utf-8") == "base"
    assert (multi_ext / "updates" / "65536" / "romfs" / "upd.txt").read_text(encoding="utf-8") == "upd"


def test_game_launcher_rom_selector(tmp_path: Path, monkeypatch) -> None:
    import json
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.game_launcher import GameLauncherWindow

    (tmp_path / "config.json").write_text(
        json.dumps({"nup": str(tmp_path / "missing.nup")}), encoding="utf-8"
    )
    cfg = {
        "game_name": "Zelda Test",
        "rom_env_var": "OOT_RECOMP_NUP",
        "command": ["echo"],
        "working_dir": str(tmp_path),
        "options": [],
    }
    cfg_path = tmp_path / "launch_options.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    win_missing = GameLauncherWindow(cfg_path, [])
    assert win_missing.rom_input is not None
    assert win_missing.rom_input.text() == ""
    win_missing.close()

    real_nup = tmp_path / "game.nup"
    real_nup.write_bytes(b"NUP")
    (tmp_path / "config.json").write_text(
        json.dumps({"nup": str(real_nup)}), encoding="utf-8"
    )
    win_found = GameLauncherWindow(cfg_path, [])
    assert win_found.rom_input is not None
    assert win_found.rom_input.text() == str(real_nup)
    win_found.close()
    app.processEvents()


def test_nup_explorer_mutations_and_mod_update(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    from src.nup_explorer import NupMutation, load_nup_tree, mutate_nup, read_nup_file_bytes

    src_dir = tmp_path / "game"
    (src_dir / "exefs").mkdir(parents=True)
    (src_dir / "romfs" / "textures").mkdir(parents=True)
    (src_dir / "exefs" / "main").write_bytes(b"MAIN_BIN")
    (src_dir / "romfs" / "textures" / "hero.txt").write_bytes(b"ORIGINAL_HERO")

    nup_path = tmp_path / "game.nup"
    pack_env = dict(os.environ, PYTHONPATH=str(ROOT / "tools" / "packagers"))
    res = subprocess.run(
        [sys.executable, "-m", "nup.cli", "pack", str(src_dir), str(nup_path), "--title-id", "010015100B514000", "-q"],
        cwd=str(ROOT / "tools" / "packagers"),
        env=pack_env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr

    mod_file = tmp_path / "mod_hero.txt"
    mod_file.write_bytes(b"MODDED_HERO_V2")

    # 1. Non-destructive mod_import creates an update package without touching base
    mutate_nup(
        ROOT,
        nup_path,
        [NupMutation("mod_import", 0, ("romfs", "textures"), new_name="hero.txt", local_path=mod_file)],
    )
    pkgs, entries = load_nup_tree(ROOT, nup_path)
    assert len(pkgs) == 2
    assert pkgs[0].kind_name == "base"
    assert pkgs[1].kind_name == "update"
    assert read_nup_file_bytes(ROOT, nup_path, 0, ("romfs", "textures", "hero.txt")) == b"ORIGINAL_HERO"
    assert read_nup_file_bytes(ROOT, nup_path, 1, ("romfs", "textures", "hero.txt")) == b"MODDED_HERO_V2"

    # 2. Copy, rename, and delete inside the update package
    mutate_nup(
        ROOT,
        nup_path,
        [
            NupMutation("copy", 1, ("romfs", "textures", "hero.txt"), new_name="hero_copy.txt", dest_pkg_index=1, dest_dir_parts=("romfs",)),
            NupMutation("rename", 1, ("romfs", "hero_copy.txt"), new_name="hero_renamed.txt"),
            NupMutation("delete", 1, ("romfs", "textures", "hero.txt")),
        ],
    )
    assert read_nup_file_bytes(ROOT, nup_path, 1, ("romfs", "hero_renamed.txt")) == b"MODDED_HERO_V2"

    # 3. Verify full NUP integrity (superblock + TOC + SHA-256)
    if str(ROOT / "tools" / "packagers") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools" / "packagers"))
    from nup.verify import verify

    ok, problems = verify(str(nup_path), deep=True)
    assert ok is True, problems


def test_audio_switch_roundtrip(tmp_path: Path) -> None:
    import struct
    import wave
    from src.audio_tools import convert_audio_file, decode_switch_audio_to_wav

    wav_in = tmp_path / "tone.wav"
    with wave.open(str(wav_in), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(struct.pack("<100h", *([1000, -1000] * 50)))

    bfwav_out = tmp_path / "tone.bfwav"
    bwav_out = tmp_path / "tone.bwav"
    convert_audio_file(wav_in, bfwav_out, "bfwav")
    convert_audio_file(wav_in, bwav_out, "bwav")
    assert bfwav_out.read_bytes()[:4] == b"FWAV"
    assert bwav_out.read_bytes()[:4] == b"BWAV"

    dec1 = tmp_path / "dec1.wav"
    dec2 = tmp_path / "dec2.wav"
    assert decode_switch_audio_to_wav(bfwav_out.read_bytes(), dec1) is True
    assert decode_switch_audio_to_wav(bwav_out.read_bytes(), dec2) is True
    assert dec1.is_file() and dec2.is_file()


def test_diagnose_game_crash() -> None:
    from src.game_launcher import diagnose_game_crash

    title, msg = diagnose_game_crash(
        "[rt] GPU fault:\n[rt] FATAL: vkQueueSubmit(vk.queue, 1, &si, f.fence) failed: -4",
        -1073740791,
    )
    assert "VK_ERROR_DEVICE_LOST" in title
    assert "OOT_MESH_NOCULL" in msg

    title2, msg2 = diagnose_game_crash(
        "[rt] FATAL: missing C:\\Users\\test\\AppData\\Local\\tootr/main.img",
        -1073740791,
    )
    assert "main.img" in title2
    assert "main.img" in msg2


def test_recompiler_manager_loads_catalog_when_not_installed(tmp_path: Path) -> None:
    import json

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True)
    catalog = [
        {
            "id": "mario-kart-world",
            "name": "Mario Kart World",
            "recompiler_name": "mkw-recomp",
            "title_ids": ["0400C3F00006E000"],
            "supported_versions": [524288],
            "default_output_subdir": "Mario Kart World",
            "repo_url": "https://github.com/Archivist-Atlas/recompiler-mario-kart-world.git",
        }
    ]
    (tools_dir / "recompilers_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")

    mgr = RecompilerManager(tmp_path)
    assert len(mgr.recompilers) == 1
    rec = mgr.recompilers[0]
    assert rec.id == "mario-kart-world"
    assert rec.is_installed is False
    assert rec.repo_url.endswith("recompiler-mario-kart-world.git")


def test_recompiler_downloader_extracts_and_resolves_lfs(tmp_path: Path, monkeypatch) -> None:
    import io
    import json
    import zipfile
    from src import recompiler_downloader as dl

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("recompiler-demo-main/install.ps1", "Write-Host 'ok'\n")
        zf.writestr(
            "recompiler-demo-main/bin/tool.exe",
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid sha256:{'a' * 64}\n"
            "size 12\n",
        )
    zip_bytes = zip_buf.getvalue()

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._bio = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}

        def read(self, n: int = -1) -> bytes:
            return self._bio.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=60):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/archive/refs/heads/main.zip"):
            return _Resp(zip_bytes)
        if url.endswith("/info/lfs/objects/batch"):
            body = json.dumps(
                {
                    "objects": [
                        {
                            "oid": "a" * 64,
                            "size": 12,
                            "actions": {"download": {"href": "https://cdn.example/tool.exe"}},
                        }
                    ]
                }
            ).encode("utf-8")
            return _Resp(body)
        if url == "https://cdn.example/tool.exe":
            return _Resp(b"MZ_FAKE_BIN!")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "tools" / "recompilers" / "demo"
    dl.download_recompiler("https://github.com/org/recompiler-demo.git", dest)

    assert (dest / "install.ps1").is_file()
    assert (dest / "bin" / "tool.exe").read_bytes() == b"MZ_FAKE_BIN!"


def test_msys2_helper_detects_missing_and_ready(tmp_path: Path) -> None:
    from src.msys2_helper import find_msys2_installer, is_msys2_ready

    assert is_msys2_ready(tmp_path / "msys64") is False
    for rel in (
        "usr/bin/bash.exe",
        "clang64/bin/clang.exe",
        "clang64/bin/clang++.exe",
        "clang64/bin/lld.exe",
        "clang64/bin/cmake.exe",
        "clang64/bin/ninja.exe",
        "clang64/bin/python.exe",
        "clang64/include/SDL3/SDL.h",
    ):
        p = tmp_path / "msys64" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ok")

    assert is_msys2_ready(tmp_path / "msys64") is True
    assert find_msys2_installer(ROOT).is_file()




