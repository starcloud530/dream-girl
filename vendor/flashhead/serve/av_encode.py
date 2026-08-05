"""短 MP4 / 预览 JPEG 编码（Engine 与 Gateway 共用）。"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image


def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ffmpeg not found (system or imageio-ffmpeg)") from exc


def preview_jpeg(frames: np.ndarray, quality: int = 52) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(frames[-1]).save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def frames_pcm_to_mp4(
    frames: np.ndarray, pcm: bytes, *, fps: int, sample_rate: int
) -> bytes:
    """短 MP4 段：偏小体积（更高 CRF + 低码率 AAC），适合公网分段拉取。"""
    import os

    import imageio.v2 as imageio

    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(f"bad frames shape {frames.shape}")
    h, w = int(frames.shape[1]), int(frames.shape[2])
    if (h % 2) or (w % 2):
        nh, nw = h - (h % 2), w - (w % 2)
        frames = frames[:, :nh, :nw, :]

    # 512@~1.4s：回到原先约 ~100KB/段（CRF28 + 64k AAC）
    crf = str(os.environ.get("FLASHHEAD_MP4_CRF", "28"))
    aac_br = os.environ.get("FLASHHEAD_AAC_BITRATE", "64k")

    ff = ffmpeg_bin()
    with tempfile.TemporaryDirectory(prefix="fh_av_") as td:
        td_path = Path(td)
        raw_v = td_path / "v.mp4"
        wav_p = td_path / "a.wav"
        out_p = td_path / "out.mp4"
        with wave.open(str(wav_p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm)
        writer = imageio.get_writer(
            str(raw_v),
            fps=int(fps),
            codec="libx264",
            ffmpeg_params=[
                "-preset",
                "ultrafast",
                "-profile:v",
                "baseline",
                "-level",
                "3.1",
                "-crf",
                crf,
                "-pix_fmt",
                "yuv420p",
                "-bf",
                "0",
                "-g",
                str(max(1, int(fps))),
            ],
        )
        try:
            for i in range(frames.shape[0]):
                writer.append_data(frames[i])
        finally:
            writer.close()
        subprocess.run(
            [
                ff,
                "-y",
                "-i",
                str(raw_v),
                "-i",
                str(wav_p),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                aac_br,
                "-ac",
                "1",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out_p),
            ],
            check=True,
            capture_output=True,
        )
        return out_p.read_bytes()


def mp4_to_fmp4(mp4: bytes) -> bytes:
    """常规 MP4 → MSE 可用的 fragmented MP4（每段自带 moov）。"""
    if not mp4:
        return b""
    ff = ffmpeg_bin()
    with tempfile.TemporaryDirectory(prefix="fh_fmp4_") as td:
        td_path = Path(td)
        inn = td_path / "in.mp4"
        out = td_path / "out.mp4"
        inn.write_bytes(mp4)
        subprocess.run(
            [
                ff,
                "-y",
                "-i",
                str(inn),
                "-c",
                "copy",
                "-f",
                "mp4",
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        data = out.read_bytes()
    return _strip_top_level_boxes(data, drop={"mfra", "tfra", "mfro"})


def mp4_to_mpegts(mp4: bytes) -> bytes:
    """短 MP4 → MPEG-TS，Chrome MSE 按段追加更像「看视频」（欠载=暂停等缓冲）。"""
    if not mp4:
        return b""
    ff = ffmpeg_bin()
    with tempfile.TemporaryDirectory(prefix="fh_ts_") as td:
        td_path = Path(td)
        inn = td_path / "in.mp4"
        out = td_path / "out.ts"
        inn.write_bytes(mp4)
        subprocess.run(
            [
                ff,
                "-y",
                "-i",
                str(inn),
                "-c",
                "copy",
                "-bsf:v",
                "h264_mp4toannexb",
                "-f",
                "mpegts",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return out.read_bytes()


def _strip_top_level_boxes(data: bytes, *, drop: set[str]) -> bytes:
    import struct

    out = bytearray()
    off = 0
    n = len(data)
    while off + 8 <= n:
        size = struct.unpack_from(">I", data, off)[0]
        typ = data[off + 4 : off + 8].decode("latin1", errors="ignore")
        header = 8
        if size == 1:
            if off + 16 > n:
                break
            size = struct.unpack_from(">Q", data, off + 8)[0]
            header = 16
        elif size == 0:
            size = n - off
        if size < 8 or off + size > n:
            break
        if typ not in drop:
            out += data[off : off + size]
        off += size
    return bytes(out) if out else data


def concat_mp4s(parts: list[bytes]) -> bytes:
    """把多段同编码短 MP4 拼成一段（优先 stream copy）。"""
    parts = [p for p in parts if p]
    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]
    ff = ffmpeg_bin()
    with tempfile.TemporaryDirectory(prefix="fh_concat_") as td:
        td_path = Path(td)
        list_p = td_path / "list.txt"
        out_p = td_path / "out.mp4"
        lines: list[str] = []
        for i, blob in enumerate(parts):
            p = td_path / f"p{i:03d}.mp4"
            p.write_bytes(blob)
            lines.append(f"file '{p.name}'")
        list_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            subprocess.run(
                [
                    ff,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_p),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(out_p),
                ],
                check=True,
                capture_output=True,
                cwd=str(td_path),
            )
        except subprocess.CalledProcessError:
            # copy 失败时重编码兜底
            subprocess.run(
                [
                    ff,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_p),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-movflags",
                    "+faststart",
                    str(out_p),
                ],
                check=True,
                capture_output=True,
                cwd=str(td_path),
            )
        return out_p.read_bytes()
