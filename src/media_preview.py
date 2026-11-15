"""Asset preview & audio player/converter widget for NUP Explorer."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import Optional

from PIL import Image
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
import zstandard as zstd

from .audio_tools import AUDIO_EXTENSIONS, convert_audio_file, find_ffmpeg, prepare_playable_audio


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga", ".dds", ".bntx"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".bik"}
MESH_EXTENSIONS = {".obj", ".stl", ".ply", ".gltf", ".glb", ".bfres"}
TEXT_EXTENSIONS = {
    ".txt", ".json", ".xml", ".csv", ".ini", ".cfg", ".yaml", ".yml",
    ".glsl", ".vert", ".frag", ".comp", ".py", ".md", ".log",
}


def decompress_yaz0(data: bytes) -> bytes:
    """Decompress Nintendo Yaz0-compressed payload."""
    if len(data) < 16 or data[:4] != b"Yaz0":
        return data
    uncompressed_size = struct.unpack_from(">I", data, 4)[0]
    out = bytearray()
    src = 16
    n = len(data)
    while len(out) < uncompressed_size and src < n:
        code = data[src]
        src += 1
        for bit in range(7, -1, -1):
            if len(out) >= uncompressed_size or src >= n:
                break
            if (code >> bit) & 1:
                out.append(data[src])
                src += 1
            else:
                if src + 1 >= n:
                    break
                b1 = data[src]
                b2 = data[src + 1]
                src += 2
                dist = ((b1 & 0x0F) << 8) | b2
                copy_src = len(out) - (dist + 1)
                count = b1 >> 4
                if count == 0:
                    if src >= n:
                        break
                    count = data[src] + 0x12
                    src += 1
                else:
                    count += 2
                for _ in range(count):
                    out.append(out[copy_src] if 0 <= copy_src < len(out) else 0)
                    copy_src += 1
    return bytes(out)


def maybe_decompress_asset(name: str, data: bytes) -> tuple[str, bytes]:
    """Strip .zs/.szs and decompress Zstd or Yaz0 if present."""
    lower = name.lower()
    if data[:4] == b"\x28\xb5\x2f\xfd" or lower.endswith(".zs"):
        try:
            dctx = zstd.ZstdDecompressor()
            data = dctx.decompress(data, max_output_size=64 << 20)
            if lower.endswith(".zs"):
                name = name[:-3]
        except Exception:
            pass
    if data[:4] == b"Yaz0" or lower.endswith(".szs"):
        try:
            data = decompress_yaz0(data)
            if lower.endswith(".szs"):
                name = name[:-4]
        except Exception:
            pass
    return name, data


def parse_bntx_summary(data: bytes) -> tuple[str, Optional[QImage]]:
    """Extract basic info (and raw RGBA8 preview if linear/uncompressed) from a Switch BNTX."""
    if len(data) < 0x30 or data[:4] != b"BNTX":
        return "Fichier BNTX invalide.", None
    nx_off = data.find(b"NX  ")
    brti_off = data.find(b"BRTI")
    lines = ["Conteneur texture Switch (BNTX)"]
    if nx_off >= 0 and nx_off + 12 <= len(data):
        tex_count = struct.unpack_from("<I", data, nx_off + 8)[0]
        lines.append(f"Textures dans le conteneur : {tex_count}")
    if brti_off >= 0 and brti_off + 0x50 <= len(data):
        width = struct.unpack_from("<I", data, brti_off + 0x20)[0]
        height = struct.unpack_from("<I", data, brti_off + 0x24)[0]
        mips = struct.unpack_from("<I", data, brti_off + 0x1C)[0]
        fmt = struct.unpack_from("<I", data, brti_off + 0x18)[0]
        lines.append(f"1ère texture (BRTI) : {width} × {height} px, {mips} mipmap(s), format 0x{fmt:X}")
    return "\n".join(lines), None


def parse_mesh_bytes(ext: str, data: bytes) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], str]:
    """Parse OBJ, STL, PLY, or GLTF/GLB into (vertices, triangles, summary)."""
    ext = ext.lower()
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []

    if ext == ".obj":
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            if parts[0] == "v" and len(parts) >= 4:
                try:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except ValueError:
                    pass
            elif parts[0] == "f" and len(parts) >= 4:
                idxs: list[int] = []
                for tok in parts[1:]:
                    v_str = tok.split("/")[0]
                    try:
                        vi = int(v_str)
                        idxs.append(vi - 1 if vi > 0 else len(verts) + vi)
                    except ValueError:
                        pass
                for i in range(1, len(idxs) - 1):
                    tris.append((idxs[0], idxs[i], idxs[i + 1]))
        return verts, tris, f"Objet OBJ : {len(verts)} sommets, {len(tris)} triangles"

    if ext == ".stl":
        if len(data) >= 84 and not data[:5].lower().startswith(b"solid"):
            tri_cnt = struct.unpack_from("<I", data, 80)[0]
            pos = 84
            for _ in range(min(tri_cnt, 50000)):
                if pos + 50 > len(data):
                    break
                vals = struct.unpack_from("<9f", data, pos + 12)
                base = len(verts)
                verts.append((vals[0], vals[1], vals[2]))
                verts.append((vals[3], vals[4], vals[5]))
                verts.append((vals[6], vals[7], vals[8]))
                tris.append((base, base + 1, base + 2))
                pos += 50
            return verts, tris, f"Objet STL binaire : {len(tris)} triangles"
        # ASCII STL
        cur: list[tuple[float, float, float]] = []
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.strip().split()
            if len(parts) == 4 and parts[0].lower() == "vertex":
                try:
                    cur.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    if len(cur) == 3:
                        base = len(verts)
                        verts.extend(cur)
                        tris.append((base, base + 1, base + 2))
                        cur.clear()
                except ValueError:
                    pass
        return verts, tris, f"Objet STL ASCII : {len(tris)} triangles"

    if ext == ".ply":
        lines = data.decode("utf-8", "replace").splitlines()
        v_cnt = 0
        f_cnt = 0
        hdr_end = 0
        is_ascii = False
        for i, ln in enumerate(lines):
            p = ln.strip().split()
            if p[:2] == ["format", "ascii"]:
                is_ascii = True
            elif len(p) >= 3 and p[0] == "element" and p[1] == "vertex":
                v_cnt = int(p[2])
            elif len(p) >= 3 and p[0] == "element" and p[1] == "face":
                f_cnt = int(p[2])
            elif ln.strip() == "end_header":
                hdr_end = i + 1
                break
        if is_ascii and hdr_end:
            for ln in lines[hdr_end : hdr_end + v_cnt]:
                p = ln.split()
                if len(p) >= 3:
                    verts.append((float(p[0]), float(p[1]), float(p[2])))
            for ln in lines[hdr_end + v_cnt : hdr_end + v_cnt + f_cnt]:
                p = ln.split()
                if p:
                    cnt = int(p[0])
                    idxs = [int(x) for x in p[1 : 1 + cnt]]
                    for i in range(1, len(idxs) - 1):
                        tris.append((idxs[0], idxs[i], idxs[i + 1]))
        return verts, tris, f"Objet PLY : {len(verts)} sommets, {len(tris)} triangles"

    if ext == ".bfres":
        fmdl_cnt = data.count(b"FMDL")
        ftex_cnt = data.count(b"FTEX")
        fska_cnt = data.count(b"FSKA")
        return [], [], (
            f"Archive 3D Switch (BFRES)\n"
            f"Taille : {len(data):,} octets\n"
            f"Modèles (FMDL) détectés : {fmdl_cnt}\n"
            f"Textures (FTEX) détectées : {ftex_cnt}\n"
            f"Animations (FSKA) détectées : {fska_cnt}"
        )

    if ext == ".glb" and len(data) >= 20 and data[:4] == b"glTF":
        total_len = struct.unpack_from("<I", data, 8)[0]
        c0_len, c0_type = struct.unpack_from("<II", data, 12)
        if c0_type == 0x4E4F534A:
            gltf = json.loads(data[20 : 20 + c0_len].decode("utf-8", "replace"))
            return [], [], (
                f"Modèle GLB : {len(gltf.get('meshes', []))} mesh(es), "
                f"{len(gltf.get('materials', []))} matériau(x)"
            )

    return [], [], f"Fichier 3D ({ext}) : {len(data):,} octets"


class MeshPreviewWidget(QWidget):
    """Lightweight interactive 3D wireframe/shaded mesh viewer using QPainter."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._verts: list[tuple[float, float, float]] = []
        self._tris: list[tuple[int, int, int]] = []
        self._yaw = 0.6
        self._pitch = 0.35
        self._zoom = 1.0
        self._last_pos = None

    def set_mesh(
        self,
        verts: list[tuple[float, float, float]],
        tris: list[tuple[int, int, int]],
    ) -> None:
        if verts:
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            zs = [v[2] for v in verts]
            cx = (min(xs) + max(xs)) * 0.5
            cy = (min(ys) + max(ys)) * 0.5
            cz = (min(zs) + max(zs)) * 0.5
            span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
            scale = 2.0 / span
            self._verts = [((x - cx) * scale, (y - cy) * scale, (z - cz) * scale) for x, y, z in verts]
        else:
            self._verts = []
        self._tris = tris[:8000]
        self._zoom = 1.0
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._last_pos = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_pos is not None:
            dx = event.position().x() - self._last_pos.x()
            dy = event.position().y() - self._last_pos.y()
            self._yaw += dx * 0.01
            self._pitch = max(-1.5, min(1.5, self._pitch + dy * 0.01))
            self._last_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_pos = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        self._zoom = max(0.3, min(4.0, self._zoom * (1.1 if delta > 0 else 0.9)))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#090d14"))
        if not self._verts:
            p.setPen(QColor("#64748b"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Aucune géométrie 3D affichable")
            return

        cy, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        w, h = self.width(), self.height()
        scale = min(w, h) * 0.36 * self._zoom
        cx, cy_scr = w * 0.5, h * 0.5

        proj: list[tuple[float, float, float]] = []
        for x, y, z in self._verts:
            rx = x * cy - z * sy
            rz = x * sy + z * cy
            ry = y * cp - rz * sp
            rz2 = y * sp + rz * cp
            dist = 4.0 + rz2
            f = scale / max(0.5, dist) * 2.2
            proj.append((cx + rx * f, cy_scr - ry * f, rz2))

        if self._tris:
            draw_tris = []
            n_v = len(proj)
            for a, b, c in self._tris:
                if a < n_v and b < n_v and c < n_v:
                    z_avg = (proj[a][2] + proj[b][2] + proj[c][2]) / 3.0
                    draw_tris.append((z_avg, a, b, c))
            draw_tris.sort(reverse=True)
            p.setPen(QPen(QColor("#38bdf8"), 0.8))
            for _z, a, b, c in draw_tris:
                poly = QPolygonF()
                poly.append(Qt.QPointF(proj[a][0], proj[a][1]) if hasattr(Qt, "QPointF") else __import__("PySide6.QtCore").QtCore.QPointF(proj[a][0], proj[a][1]))
                from PySide6.QtCore import QPointF
                p.setBrush(QColor(30, 41, 59, 180))
                p.drawPolygon(QPolygonF([
                    QPointF(proj[a][0], proj[a][1]),
                    QPointF(proj[b][0], proj[b][1]),
                    QPointF(proj[c][0], proj[c][1]),
                ]))
        else:
            p.setPen(QPen(QColor("#38bdf8"), 2))
            for sx, sy_p, _ in proj[::max(1, len(proj) // 4000)]:
                p.drawPoint(int(sx), int(sy_p))


class AssetPreviewPanel(QFrame):
    """Right-hand previewer for textures, 3D models, video, audio (+ conversion), and text/hex."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="nup_preview_")
        self._current_name: str = ""
        self._current_bytes: bytes = b""
        self._playable_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.lbl_header = QLabel("APERÇU DU FICHIER")
        self.lbl_header.setObjectName("SectionTitle")
        layout.addWidget(self.lbl_header)

        self.lbl_meta = QLabel("Sélectionnez un fichier dans l'arborescence pour le prévisualiser.")
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self.lbl_meta)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        # Page 0: Empty / Info
        self.page_empty = QLabel("Aucun fichier sélectionné.")
        self.page_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_empty.setStyleSheet("color: #64748b;")
        self.stack.addWidget(self.page_empty)

        # Page 1: Image / Texture
        self.page_image = QLabel()
        self.page_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_image.setStyleSheet("background-color: #090d14; border-radius: 8px;")
        self.stack.addWidget(self.page_image)

        # Page 2: Text / Hex
        self.page_text = QPlainTextEdit()
        self.page_text.setReadOnly(True)
        self.page_text.setFont(QFont("Consolas", 10))
        self.stack.addWidget(self.page_text)

        # Page 3: 3D Mesh
        mesh_wrap = QWidget()
        mw_layout = QVBoxLayout(mesh_wrap)
        mw_layout.setContentsMargins(0, 0, 0, 0)
        self.mesh_widget = MeshPreviewWidget()
        self.lbl_mesh_hint = QLabel("Glisser pour pivoter • Molette pour zoomer")
        self.lbl_mesh_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        mw_layout.addWidget(self.mesh_widget, stretch=1)
        mw_layout.addWidget(self.lbl_mesh_hint)
        self.stack.addWidget(mesh_wrap)

        # Page 4: Video + Audio Player
        media_wrap = QWidget()
        med_layout = QVBoxLayout(media_wrap)
        med_layout.setContentsMargins(0, 0, 0, 0)
        med_layout.setSpacing(8)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        med_layout.addWidget(self.video_widget, stretch=1)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.8)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_widget)

        controls = QHBoxLayout()
        self.btn_play = QPushButton("Lire")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.player.stop)
        self.pos_slider = QSlider(Qt.Orientation.Horizontal)
        self.pos_slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self._on_pos_changed)
        self.player.durationChanged.connect(self._on_dur_changed)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.pos_slider, stretch=1)
        med_layout.addLayout(controls)

        # Audio conversion bar
        self.audio_conv_bar = QFrame()
        conv_row = QHBoxLayout(self.audio_conv_bar)
        conv_row.setContentsMargins(0, 4, 0, 0)
        conv_row.setSpacing(8)
        self.btn_export_wav = QPushButton("Exporter .WAV")
        self.btn_export_wav.clicked.connect(lambda: self._export_current_audio("wav"))
        self.btn_export_mp3 = QPushButton("Exporter .MP3")
        self.btn_export_mp3.clicked.connect(lambda: self._export_current_audio("mp3"))
        conv_row.addWidget(self.btn_export_wav)
        conv_row.addWidget(self.btn_export_mp3)
        conv_row.addStretch(1)
        med_layout.addWidget(self.audio_conv_bar)

        self.stack.addWidget(media_wrap)

        # Standalone PC <-> Switch audio converter bar at bottom of previewer
        pc_to_sw = QHBoxLayout()
        pc_to_sw.setSpacing(8)
        self.combo_switch_fmt = QComboBox()
        self.combo_switch_fmt.addItem("Switch .bfwav", "bfwav")
        self.combo_switch_fmt.addItem("Switch .bwav", "bwav")
        self.combo_switch_fmt.addItem("Switch .opus", "opus")
        self.combo_switch_fmt.addItem("PC .wav", "wav")
        self.combo_switch_fmt.addItem("PC .mp3", "mp3")
        btn_conv_file = QPushButton("Convertir un fichier audio (.mp3/.wav ↔ Switch)…")
        btn_conv_file.clicked.connect(self._convert_external_audio)
        pc_to_sw.addWidget(btn_conv_file, stretch=1)
        pc_to_sw.addWidget(self.combo_switch_fmt)
        layout.addLayout(pc_to_sw)

    def clear(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self._current_name = ""
        self._current_bytes = b""
        self.lbl_meta.setText("Sélectionnez un fichier dans l'arborescence pour le prévisualiser.")
        self.stack.setCurrentIndex(0)

    def preview_bytes(self, name: str, raw_data: bytes) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self._current_name = name
        eff_name, data = maybe_decompress_asset(name, raw_data)
        self._current_bytes = data
        ext = Path(eff_name).suffix.lower()

        self.lbl_meta.setText(f"{name} — {len(raw_data):,} octets (décompressé : {len(data):,} o)")

        # 1. Images / Textures
        if ext in IMAGE_EXTENSIONS:
            if ext == ".bntx" or data[:4] == b"BNTX":
                summary, _ = parse_bntx_summary(data)
                self.page_text.setPlainText(summary)
                self.stack.setCurrentIndex(2)
                return
            try:
                pil_img = Image.open(io.BytesIO(data)).convert("RGBA")
                qimg = QImage(
                    pil_img.tobytes("raw", "RGBA"),
                    pil_img.width,
                    pil_img.height,
                    pil_img.width * 4,
                    QImage.Format.Format_RGBA8888,
                ).copy()
                pix = QPixmap.fromImage(qimg).scaled(
                    420, 320, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.page_image.setPixmap(pix)
                self.lbl_meta.setText(f"{name} — {pil_img.width}×{pil_img.height} px")
                self.stack.setCurrentIndex(1)
                return
            except Exception:
                pass

        # 2. 3D Models
        if ext in MESH_EXTENSIONS or data[:4] == b"FRES":
            verts, tris, summary = parse_mesh_bytes(ext if ext in MESH_EXTENSIONS else ".bfres", data)
            self.lbl_meta.setText(summary)
            if verts:
                self.mesh_widget.set_mesh(verts, tris)
                self.stack.setCurrentIndex(3)
            else:
                self.page_text.setPlainText(summary)
                self.stack.setCurrentIndex(2)
            return

        # 3. Video
        if ext in VIDEO_EXTENSIONS:
            vid_path = Path(self._tmp_dir.name) / f"video{ext}"
            vid_path.write_bytes(data)
            if ext == ".bik" and find_ffmpeg():
                mp4 = Path(self._tmp_dir.name) / "video_preview.mp4"
                subprocess.run(
                    [find_ffmpeg(), "-y", "-i", str(vid_path), "-c:v", "libx264", "-preset", "ultrafast", str(mp4)],
                    capture_output=True,
                )
                if mp4.is_file():
                    vid_path = mp4
            self.video_widget.setVisible(True)
            self.audio_conv_bar.setVisible(False)
            self.player.setSource(QUrl.fromLocalFile(str(vid_path)))
            self.stack.setCurrentIndex(4)
            return

        # 4. Audio
        if ext in AUDIO_EXTENSIONS or data[:4] in (b"FWAV", b"FSTM", b"BWAV", b"RIFF", b"OggS"):
            eff_ext = ext if ext in AUDIO_EXTENSIONS else ".wav"
            if data[:4] == b"FWAV":
                eff_ext = ".bfwav"
            elif data[:4] == b"FSTM":
                eff_ext = ".bfstm"
            elif data[:4] == b"BWAV":
                eff_ext = ".bwav"
            playable = prepare_playable_audio(data, eff_ext, Path(self._tmp_dir.name))
            if playable is not None:
                self._playable_path = playable
                self.video_widget.setVisible(False)
                self.audio_conv_bar.setVisible(True)
                self.player.setSource(QUrl.fromLocalFile(str(playable)))
                self.stack.setCurrentIndex(4)
                return

        # 5. Text / JSON
        if ext in TEXT_EXTENSIONS or (len(data) < 262144 and b"\x00" not in data[:512]):
            try:
                txt = data[:200000].decode("utf-8")
                self.page_text.setPlainText(txt)
                self.stack.setCurrentIndex(2)
                return
            except UnicodeDecodeError:
                pass

        # 6. Hex fallback
        self.page_text.setPlainText(self._format_hex(data[:8192]))
        self.stack.setCurrentIndex(2)

    @staticmethod
    def _format_hex(data: bytes) -> str:
        lines: list[str] = []
        for off in range(0, len(data), 16):
            chunk = data[off : off + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{off:08X}  {hex_part:<47}  {asc_part}")
        if len(data) == 8192:
            lines.append("… (aperçu limité aux 8 premiers Ko)")
        return "\n".join(lines) if lines else "(fichier vide)"

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("Lire")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def _on_pos_changed(self, pos: int) -> None:
        if not self.pos_slider.isSliderDown():
            self.pos_slider.setValue(pos)

    def _on_dur_changed(self, dur: int) -> None:
        self.pos_slider.setRange(0, max(0, dur))

    def _export_current_audio(self, fmt: str) -> None:
        if not self._current_bytes:
            return
        stem = Path(self._current_name or "audio").stem
        out, _ = QFileDialog.getSaveFileName(
            self,
            f"Exporter en .{fmt}",
            f"{stem}.{fmt}",
            f"Audio (*.{fmt});;Tous les fichiers (*)",
        )
        if not out:
            return
        try:
            src_tmp = Path(self._tmp_dir.name) / (self._current_name or "audio.bin")
            src_tmp.write_bytes(self._current_bytes)
            convert_audio_file(src_tmp, Path(out), fmt)
            QMessageBox.information(self, "Conversion réussie", f"Fichier exporté :\n{out}")
        except Exception as exc:
            QMessageBox.warning(self, "Erreur de conversion", str(exc))

    def _convert_external_audio(self) -> None:
        src, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un fichier audio (.mp3, .wav, .bfwav, .bwav, .opus)",
            "",
            "Audio (*.mp3 *.wav *.ogg *.flac *.opus *.bfwav *.bfstm *.bwav);;Tous (*)",
        )
        if not src:
            return
        fmt = str(self.combo_switch_fmt.currentData())
        src_p = Path(src)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            f"Enregistrer en .{fmt}",
            str(src_p.with_suffix(f".{fmt}")),
            f"Audio (*.{fmt});;Tous (*)",
        )
        if not dest:
            return
        try:
            convert_audio_file(src_p, Path(dest), fmt)
            QMessageBox.information(self, "Conversion réussie", f"Fichier converti :\n{dest}")
        except Exception as exc:
            QMessageBox.warning(self, "Erreur de conversion", str(exc))

