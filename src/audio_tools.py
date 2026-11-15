"""Switch & PC audio decoding, playback preparation, and format conversion."""

from __future__ import annotations

import io
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import wave


AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
    ".opus",
    ".bfwav",
    ".bfstm",
    ".bwav",
    ".brstm",
    ".bcstm",
}


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _clamp16(v: int) -> int:
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def _decode_dsp_adpcm(
    data: bytes, sample_count: int, coefs: tuple[int, ...], init_yn1: int = 0, init_yn2: int = 0
) -> list[int]:
    """Decode Nintendo 4-bit DSP-ADPCM stream into 16-bit signed PCM samples."""
    out: list[int] = []
    yn1 = init_yn1
    yn2 = init_yn2
    pos = 0
    n_bytes = len(data)
    while len(out) < sample_count and pos < n_bytes:
        header = data[pos]
        pos += 1
        scale = 1 << (header & 0x0F)
        pred = (header >> 4) & 0x0F
        if pred * 2 + 1 >= len(coefs):
            c1, c2 = 0, 0
        else:
            c1 = coefs[pred * 2]
            c2 = coefs[pred * 2 + 1]
        for _ in range(7):
            if pos >= n_bytes or len(out) >= sample_count:
                break
            byte = data[pos]
            pos += 1
            for shift in (4, 0):
                if len(out) >= sample_count:
                    break
                nib = (byte >> shift) & 0x0F
                if nib >= 8:
                    nib -= 16
                sample = ((nib * scale) << 11) + 1024 + (c1 * yn1 + c2 * yn2)
                sample = _clamp16(sample >> 11)
                out.append(sample)
                yn2 = yn1
                yn1 = sample
    return out


def _interleave_wav(channels: list[list[int]], sample_rate: int, dest_wav: Path) -> None:
    n_ch = len(channels)
    n_frames = min(len(ch) for ch in channels) if channels else 0
    pcm = bytearray(n_frames * n_ch * 2)
    idx = 0
    for i in range(n_frames):
        for ch in range(n_ch):
            struct.pack_into("<h", pcm, idx, channels[ch][i])
            idx += 2
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest_wav), "wb") as wf:
        wf.setnchannels(max(1, n_ch))
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))


def decode_switch_audio_to_wav(data: bytes, dest_wav: Path) -> bool:
    """Decode BFWAV (FWAV), BFSTM (FSTM), or BWAV bytes into a standard PCM16 WAV file."""
    if len(data) < 0x40:
        return False
    magic = data[:4]
    if magic in (b"FWAV", b"FSTM"):
        return _decode_bfwav_or_bfstm(data, dest_wav)
    if magic == b"BWAV":
        return _decode_bwav(data, dest_wav)
    return False


def _decode_bfwav_or_bfstm(data: bytes, dest_wav: Path) -> bool:
    bom = struct.unpack_from("<H", data, 4)[0]
    endian = "<" if bom == 0xFEFF else ">"
    magic = data[:4]
    header_size = struct.unpack_from(f"{endian}H", data, 6)[0]
    block_count = struct.unpack_from(f"{endian}H", data, 0x10)[0]

    info_off = -1
    data_off = -1
    pos = 0x14
    for _ in range(block_count):
        if pos + 12 > len(data):
            break
        b_type, _pad, b_off, _b_sz = struct.unpack_from(f"{endian}HHII", data, pos)
        pos += 12
        if b_type == 0x7000 or b_type == 0x4000:
            info_off = b_off
        elif b_type == 0x7001 or b_type == 0x4002:
            data_off = b_off

    if info_off < 0 or data_off < 0 or info_off + 0x20 > len(data):
        return False

    # INFO block
    info_magic = data[info_off : info_off + 4]
    if info_magic != b"INFO":
        return False
    encoding = data[info_off + 8]  # 0=PCM8, 1=PCM16, 2=DSP-ADPCM
    sample_rate = struct.unpack_from(f"{endian}I", data, info_off + 0x0C)[0]
    if sample_rate == 0 or sample_rate > 192000:
        sample_rate = 48000

    if magic == b"FWAV":
        sample_count = struct.unpack_from(f"{endian}I", data, info_off + 0x14)[0]
        ch_count = struct.unpack_from(f"{endian}I", data, info_off + 0x1C)[0]
        ch_table_off = info_off + 0x20
        ch_entry_stride = 8
        stream_info_size = 0
    else:  # FSTM
        sample_count = struct.unpack_from(f"{endian}I", data, info_off + 0x14)[0]
        # FSTM INFO has stream info then track ref table then channel ref table
        ch_count = data[info_off + 0x22] if info_off + 0x23 <= len(data) else 1
        stream_info_size = 0
        ch_table_off = info_off + 0x38
        ch_entry_stride = 8

    if ch_count <= 0 or ch_count > 8:
        return False

    data_payload_base = data_off + 8
    channels: list[list[int]] = []

    for ch in range(ch_count):
        if magic == b"FWAV":
            ref_pos = ch_table_off + ch * ch_entry_stride
            if ref_pos + 8 > len(data):
                break
            _type, _pad, ch_info_rel = struct.unpack_from(f"{endian}HHI", data, ref_pos)
            ch_info_abs = info_off + 8 + ch_info_rel
            if ch_info_abs + 16 > len(data):
                break
            _smp_type, _p, smp_rel = struct.unpack_from(f"{endian}HHI", data, ch_info_abs)
            adpcm_rel = struct.unpack_from(f"{endian}I", data, ch_info_abs + 12)[0]
            smp_abs = data_payload_base + smp_rel
            adpcm_abs = ch_info_abs + adpcm_rel
        else:
            # Best-effort sequential FSTM channel offset
            smp_abs = data_payload_base + ch * ((sample_count * 2) if encoding == 1 else (sample_count // 14 * 8 + 8))
            adpcm_abs = -1

        if encoding == 1:  # PCM16
            raw = data[smp_abs : smp_abs + sample_count * 2]
            cnt = len(raw) // 2
            samples = list(struct.unpack_from(f"{endian}{cnt}h", raw))
            channels.append(samples)
        elif encoding == 0:  # PCM8
            raw = data[smp_abs : smp_abs + sample_count]
            channels.append([((b - 128) << 8) for b in raw])
        elif encoding == 2 and adpcm_abs > 0 and adpcm_abs + 0x24 <= len(data):  # DSP-ADPCM
            coefs = struct.unpack_from(f"{endian}16h", data, adpcm_abs)
            _pred_scale, _yn, yn1, yn2 = struct.unpack_from(
                f"{endian}Hhhh", data, adpcm_abs + 0x20
            )
            channels.append(
                _decode_dsp_adpcm(data[smp_abs:], sample_count, coefs, yn1, yn2)
            )
        else:
            return False

    if not channels:
        return False
    _interleave_wav(channels, sample_rate, dest_wav)
    return True


def _decode_bwav(data: bytes, dest_wav: Path) -> bool:
    if len(data) < 0x10:
        return False
    bom = struct.unpack_from("<H", data, 4)[0]
    endian = "<" if bom == 0xFEFF else ">"
    ch_count = struct.unpack_from(f"{endian}H", data, 0x0E)[0]
    if ch_count <= 0 or ch_count > 8:
        return False
    channels: list[list[int]] = []
    sample_rate = 48000
    pos = 0x10
    for _ in range(ch_count):
        if pos + 0x4C > len(data):
            break
        codec, _pan, sample_rate, n_samples_nonloop, n_samples = struct.unpack_from(
            f"{endian}HHIII", data, pos
        )
        coefs = struct.unpack_from(f"{endian}16h", data, pos + 0x10)
        start_off_nonloop, start_off = struct.unpack_from(f"{endian}II", data, pos + 0x30)
        pos += 0x4C
        count = n_samples or n_samples_nonloop
        off = start_off or start_off_nonloop
        if off >= len(data):
            return False
        if codec == 0:  # PCM16
            raw = data[off : off + count * 2]
            cnt = len(raw) // 2
            channels.append(list(struct.unpack_from(f"{endian}{cnt}h", raw)))
        elif codec == 1:  # DSP-ADPCM
            channels.append(_decode_dsp_adpcm(data[off:], count, coefs))
        else:
            return False
    if not channels:
        return False
    _interleave_wav(channels, sample_rate or 48000, dest_wav)
    return True


def prepare_playable_audio(data: bytes, ext: str, work_dir: Path) -> Path | None:
    """Write or decode audio bytes into a file playable by QtMultimedia."""
    ext = ext.lower()
    work_dir.mkdir(parents=True, exist_ok=True)
    if ext in (".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"):
        out = work_dir / f"preview{ext}"
        out.write_bytes(data)
        return out
    if ext in (".bfwav", ".bfstm", ".bwav"):
        out = work_dir / "preview.wav"
        if decode_switch_audio_to_wav(data, out):
            return out
    # Fallback via ffmpeg (e.g. .opus)
    ff = find_ffmpeg()
    if ff:
        src = work_dir / f"source{ext or '.bin'}"
        src.write_bytes(data)
        out = work_dir / "preview.wav"
        res = subprocess.run(
            [ff, "-y", "-i", str(src), "-acodec", "pcm_s16le", str(out)],
            capture_output=True,
        )
        if res.returncode == 0 and out.is_file():
            return out
    return None


def encode_pcm16_to_bfwav(wav_path: Path, dest_bfwav: Path) -> None:
    """Encode a PCM16 WAV file into a Switch little-endian BFWAV (FWAV) container."""
    with wave.open(str(wav_path), "rb") as wf:
        n_ch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError("Le WAV intermédiaire doit être en PCM 16-bit.")

    # De-interleave channels
    all_samples = struct.unpack(f"<{n_frames * n_ch}h", raw)
    ch_bytes = [
        struct.pack(f"<{n_frames}h", *all_samples[ch::n_ch])
        for ch in range(n_ch)
    ]

    # Build INFO block
    # INFO header (8) + fields (0x18) + ch refs (8 * n_ch) + channel infos (0x14 * n_ch)
    ch_refs_size = 8 * n_ch
    ch_info_size = 0x14 * n_ch
    info_body_size = 0x18 + ch_refs_size + ch_info_size
    info_pad = (-info_body_size) & 0x1F
    info_size = 8 + info_body_size + info_pad

    info_buf = bytearray(info_size)
    struct.pack_into("<4sI", info_buf, 0, b"INFO", info_size)
    struct.pack_into("<BBHIII", info_buf, 8, 1, 0, 0, rate, 0, n_frames)
    struct.pack_into("<II", info_buf, 0x18, 0, n_ch)

    pcm_offset_rel = 0
    for ch in range(n_ch):
        ch_info_rel = 0x18 + ch_refs_size + ch * 0x14
        struct.pack_into("<HHI", info_buf, 0x20 + ch * 8, 0x7100, 0, ch_info_rel)
        # Channel info: sample ref (0x1F00, pad, offset), adpcm ref (0, 0, 0xFFFFFFFF), reserved
        struct.pack_into(
            "<HHIIII",
            info_buf,
            8 + ch_info_rel,
            0x1F00,
            0,
            pcm_offset_rel,
            0,
            0,
            0xFFFFFFFF,
        )
        pcm_offset_rel += len(ch_bytes[ch])

    # Build DATA block
    data_payload = b"".join(ch_bytes)
    data_pad = (-len(data_payload)) & 0x1F
    data_size = 8 + len(data_payload) + data_pad
    data_buf = bytearray(data_size)
    struct.pack_into("<4sI", data_buf, 0, b"DATA", data_size)
    data_buf[8 : 8 + len(data_payload)] = data_payload

    header_size = 0x40
    total_size = header_size + info_size + data_size
    header = bytearray(header_size)
    struct.pack_into(
        "<4sHHIIHH",
        header,
        0,
        b"FWAV",
        0xFEFF,
        header_size,
        0x00010200,
        total_size,
        2,
        0,
    )
    struct.pack_into("<HHII", header, 0x14, 0x7000, 0, header_size, info_size)
    struct.pack_into("<HHII", header, 0x20, 0x7001, 0, header_size + info_size, data_size)

    dest_bfwav.parent.mkdir(parents=True, exist_ok=True)
    dest_bfwav.write_bytes(bytes(header) + bytes(info_buf) + bytes(data_buf))


def encode_pcm16_to_bwav(wav_path: Path, dest_bwav: Path) -> None:
    """Encode a PCM16 WAV file into a Switch little-endian BWAV container."""
    with wave.open(str(wav_path), "rb") as wf:
        n_ch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError("Le WAV intermédiaire doit être en PCM 16-bit.")

    all_samples = struct.unpack(f"<{n_frames * n_ch}h", raw)
    ch_bytes = [
        struct.pack(f"<{n_frames}h", *all_samples[ch::n_ch])
        for ch in range(n_ch)
    ]

    header_size = 0x10 + 0x4C * n_ch
    data_start = (header_size + 0x3F) & ~0x3F
    buf = bytearray(data_start)
    struct.pack_into("<4sHHIHH", buf, 0, b"BWAV", 0xFEFF, 0x0001, 0, 0, n_ch)

    cursor = data_start
    payload_parts: list[bytes] = []
    for ch in range(n_ch):
        pos = 0x10 + ch * 0x4C
        pan = 0 if n_ch == 1 else (0 if ch == 0 else 1)
        struct.pack_into(
            "<HHIII",
            buf,
            pos,
            0,  # PCM16
            pan,
            rate,
            n_frames,
            n_frames,
        )
        struct.pack_into("<II", buf, pos + 0x30, cursor, cursor)
        struct.pack_into("<II", buf, pos + 0x38, 0, 0xFFFFFFFF)
        payload_parts.append(ch_bytes[ch])
        cursor += len(ch_bytes[ch])

    dest_bwav.parent.mkdir(parents=True, exist_ok=True)
    dest_bwav.write_bytes(bytes(buf) + b"".join(payload_parts))


def convert_audio_file(src_path: Path, dest_path: Path, target_fmt: str) -> None:
    """Convert between PC formats (.mp3, .wav) and Switch formats (.bfwav, .bwav, .opus)."""
    target_fmt = target_fmt.lower().lstrip(".")
    src_ext = src_path.suffix.lower()

    with tempfile.TemporaryDirectory(prefix="nup_audio_") as tmp:
        tmp_dir = Path(tmp)
        wav_src = src_path
        if src_ext in (".bfwav", ".bfstm", ".bwav"):
            wav_src = tmp_dir / "decoded.wav"
            if not decode_switch_audio_to_wav(src_path.read_bytes(), wav_src):
                raise RuntimeError(f"Impossible de décoder le format Switch : {src_path.name}")
        elif src_ext != ".wav":
            ff = find_ffmpeg()
            if not ff:
                raise RuntimeError("ffmpeg est requis pour convertir ce format audio.")
            wav_src = tmp_dir / "decoded.wav"
            res = subprocess.run(
                [ff, "-y", "-i", str(src_path), "-ar", "48000", "-acodec", "pcm_s16le", str(wav_src)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Erreur ffmpeg : {res.stderr.strip()}")

        if target_fmt == "wav":
            shutil.copy2(wav_src, dest_path)
        elif target_fmt == "mp3":
            ff = find_ffmpeg()
            if not ff:
                raise RuntimeError("ffmpeg est requis pour exporter en .mp3.")
            res = subprocess.run(
                [ff, "-y", "-i", str(wav_src), "-codec:a", "libmp3lame", "-qscale:a", "2", str(dest_path)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Erreur ffmpeg : {res.stderr.strip()}")
        elif target_fmt == "opus":
            ff = find_ffmpeg()
            if not ff:
                raise RuntimeError("ffmpeg est requis pour exporter en .opus.")
            res = subprocess.run(
                [ff, "-y", "-i", str(wav_src), "-c:a", "libopus", "-b:a", "128k", str(dest_path)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Erreur ffmpeg : {res.stderr.strip()}")
        elif target_fmt == "bfwav":
            encode_pcm16_to_bfwav(wav_src, dest_path)
        elif target_fmt == "bwav":
            encode_pcm16_to_bwav(wav_src, dest_path)
        else:
            raise ValueError(f"Format cible non supporté : {target_fmt}")
