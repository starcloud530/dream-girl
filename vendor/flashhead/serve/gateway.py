"""Avatar Gateway compatible with demo OpenAPI — backed by FlashHeadRuntime."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _ROOT / "configs" / "t_v2_face256.yaml"


@dataclass
class AvBundle:
    """一段推理产出的音画绑定包。

    主路径对齐 Soul Gradio：短 MP4（H264+AAC）；JPEG 仅供 frames_ws 预览。
    """

    pcm: bytes
    fps: int
    sample_rate: int
    chunk_index: int = 0
    n_frames: int = 0
    mp4: bytes = b""
    jpegs: list[bytes] = field(default_factory=list)
    frames: np.ndarray | None = field(default=None, repr=False)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# 默认：攒 ~1.4s PCM 后开脸（降首动画）；FACE_WAIT_TTS_END=1 则整句后再开脸
_FACE_WAIT_TTS_END = _env_bool("FACE_WAIT_TTS_END", False)
_FACE_PREROLL_MS = int(os.environ.get("FACE_PREROLL_MS", "1400"))
_FACE_FLUSH_MS = int(os.environ.get("FACE_FLUSH_MS", "250"))  # 开脸后的攒包粒度
# 投喂推理队列的片大小（模型内部仍约 1.4s/片）
_FACE_JOB_MS = int(os.environ.get("FACE_JOB_MS", "1400"))
# 首段/后续均按 ~1.4s 模型片直出（降首动画）
_FACE_OUT_MS = int(os.environ.get("FACE_OUT_MS", "1400"))
_FACE_OUT_FOLLOW_MS = int(os.environ.get("FACE_OUT_FOLLOW_MS", "1400"))


@dataclass
class AvatarSession:
    session_id: str
    avatar_id: str
    pcm_buffer: bytearray = field(default_factory=bytearray)
    frame_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    av_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    latest_jpeg: bytes | None = None
    closed: bool = False
    # HTTP/SSE 路径：按 chunk 存短 MP4，SSE 只推元数据
    mp4_by_chunk: dict[int, bytes] = field(default_factory=dict)
    sse_queues: list = field(default_factory=list)
    # PCM 异步推理：HTTP 立即 204，后台按序出片（避免卡住 TTS）
    pcm_job_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=16)
    )
    pcm_worker: asyncio.Task | None = None
    # False：还在等 TTS/preroll；True：已开始往推理队列送 PCM
    face_infer_started: bool = False
    pcm_feed_task: asyncio.Task | None = None
    # 拼段缓冲：模型片 ~1.4s，攒到 FACE_OUT_MS 再 SSE
    mux_mp4s: list[bytes] = field(default_factory=list)
    mux_pcms: list[bytes] = field(default_factory=list)
    mux_jpegs: list[bytes] = field(default_factory=list)
    mux_fps: int = 20
    mux_sample_rate: int = 16000
    mux_pcm_bytes: int = 0
    out_chunk_index: int = 0
    # False：还在攒首段；True：已下发过首段，后续按 FOLLOW 粒度
    first_out_done: bool = False


class CreateSessionRequest(BaseModel):
    avatar_id: str = "xiaoya_v1"


def _cfg_shim_from_engine_meta(meta: dict[str, Any]) -> Any:
    """Engine 模式下用 health 元数据冒充 cfg，供 Gateway 读 fps/sr 等。"""

    class _IP:
        sample_rate = int(meta.get("sample_rate") or 16000)
        tgt_fps = int(meta.get("tgt_fps") or 20)
        height = meta.get("height")
        width = meta.get("width")

    class _RT:
        display_size = meta.get("display_size")

    class _Comp:
        enabled = bool(meta.get("composite"))
        canvas_width = meta.get("canvas_width")
        canvas_height = meta.get("canvas_height")

    class _Cfg:
        infer_params = _IP()
        runtime = _RT()
        composite = _Comp()
        tier = meta.get("tier")

    return _Cfg()


class GatewayState:
    def __init__(self) -> None:
        self.stack = None  # ModelStack | None — 单体模式
        self.engine = None  # EngineClient | None — 双进程模式
        self.engine_url: str | None = None
        self.runtime = None  # 兼容旧字段：单体时指向 stack.runtime
        self.cfg = None
        self.sessions: dict[str, AvatarSession] = {}
        self._infer_lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.preset = None
        self.preset_pool = None
        self.composite_enabled: bool = False
        self.feather_px: int = 14
        self.random_clip: bool = True
        self.upscaler = None

    @property
    def ready(self) -> bool:
        if self.engine is not None:
            return bool(self.engine.ready)
        return bool(self.stack and self.stack.ready)

    def load(self, config_path: Path, avatar_image: Path | None) -> None:
        """单体模式：Gateway 进程内加载模型（兼容旧启动方式）。"""
        from serve.model_stack import ModelStack

        self.engine = None
        self.engine_url = None
        self.stack = ModelStack()
        self.stack.load(config_path, avatar_image)
        self._sync_from_stack()
        logger.info("FlashHead gateway mode=embedded (model in-process)")

    def attach_engine(self, engine_url: str, *, wait_s: int = 600) -> None:
        """双进程模式：只连常驻 Engine，本进程不占 GPU 权重。"""
        from serve.engine_client import EngineClient

        self.stack = None
        self.runtime = None
        self.upscaler = None
        self.preset = None
        self.preset_pool = None
        self.engine_url = engine_url.rstrip("/")
        self.engine = EngineClient(self.engine_url)
        deadline = time.time() + max(int(wait_s), 1)
        last_err = None
        while time.time() < deadline:
            try:
                meta = self.engine.refresh_health()
                if meta.get("ready") or meta.get("status") == "ok":
                    self.cfg = _cfg_shim_from_engine_meta(meta)
                    self.composite_enabled = bool(meta.get("composite"))
                    logger.info(
                        "FlashHead gateway mode=engine url=%s infer=%sx%s display=%s",
                        self.engine_url,
                        meta.get("width"),
                        meta.get("height"),
                        meta.get("display_size"),
                    )
                    return
            except Exception as exc:
                last_err = exc
            time.sleep(1.0)
        raise RuntimeError(
            f"engine not ready at {self.engine_url}: {last_err}"
        )

    def _sync_from_stack(self) -> None:
        assert self.stack is not None
        self.cfg = self.stack.cfg
        self.runtime = self.stack.runtime
        self.preset = self.stack.preset
        self.preset_pool = self.stack.preset_pool
        self.composite_enabled = self.stack.composite_enabled
        self.feather_px = self.stack.feather_px
        self.random_clip = self.stack.random_clip
        self.upscaler = self.stack.upscaler

    def pick_preset_for_session(self) -> None:
        if self.engine is not None:
            return  # Engine /v1/session/reset 内处理
        if self.stack is not None:
            self.stack.pick_preset_for_session()
            self.preset = self.stack.preset
            self.preset_pool = self.stack.preset_pool

    def reset_session(self) -> None:
        if self.engine is not None:
            self.engine.reset_session()
            try:
                meta = self.engine.refresh_health()
                self.cfg = _cfg_shim_from_engine_meta(meta)
            except Exception:
                pass
            return
        if self.stack is not None:
            self.stack.reset_session()
            self._sync_from_stack()


STATE = GatewayState()
app = FastAPI(title="FlashHead Avatar Gateway", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mount_assets(assets_dir: Path | None = None) -> Path | None:
    """Mount demo character assets at /assets (idempotent)."""
    if any(getattr(r, "path", None) == "/assets" for r in app.routes):
        return assets_dir
    cand = assets_dir
    if cand is None:
        root = os.environ.get("DREAM_GIRL_ROOT") or os.environ.get(
            "CYBER_GF_DATA_ROOT", "/root/autodl-tmp/dream-girl"
        )
        cand = Path(root) / "assets"
        if not cand.exists():
            cand = Path(root) / "app" / "assets"
    if cand and cand.exists():
        app.mount("/assets", StaticFiles(directory=str(cand)), name="assets")
        logger.info("mounted /assets -> %s", cand)
        return cand
    logger.warning("assets dir missing, /assets not mounted: %s", cand)
    return None


# Eager mount so alternate entrypoints (not only main()) still serve portraits
_mount_assets()


def _maybe_upscale_frames(frames: np.ndarray) -> np.ndarray:
    """按 display_size 放大；优先 GPU RealESRGAN，否则 PIL 双线性。"""
    display = 0
    if STATE.cfg is not None:
        display = int(getattr(STATE.cfg.runtime, "display_size", 0) or 0)
    if display <= 0 or int(frames.shape[2]) == display:
        return frames
    if STATE.upscaler is not None:
        try:
            return STATE.upscaler.upscale_frames(frames, display)
        except Exception as exc:
            logger.warning("GPU upscale failed, fallback PIL: %s", exc)
    from lightning.image_io import upscale_frame

    out = []
    for i in range(frames.shape[0]):
        img = upscale_frame(frames[i], out_size=display)
        out.append(np.asarray(img, dtype=np.uint8))
    return np.stack(out, axis=0)


def _preview_jpeg(frames: np.ndarray, quality: int = 70) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(frames[-1]).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _ffmpeg_bin() -> str:
    """Prefer system ffmpeg; fall back to imageio-ffmpeg wheel binary."""
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ffmpeg not found (system or imageio-ffmpeg)") from exc


def _frames_pcm_to_mp4(frames: np.ndarray, pcm: bytes, *, fps: int, sample_rate: int) -> bytes:
    """Soul 官方 streaming demo 同思路：短 MP4 段（ultrafast H264 + AAC）。"""
    import subprocess
    import tempfile
    import wave
    from pathlib import Path

    import imageio.v2 as imageio

    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(f"bad frames shape {frames.shape}")
    # libx264 yuv420p 需要偶数边
    h, w = int(frames.shape[1]), int(frames.shape[2])
    if (h % 2) or (w % 2):
        nh, nw = h - (h % 2), w - (w % 2)
        frames = frames[:, :nh, :nw, :]

    ff = _ffmpeg_bin()
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
                "-crf",
                "28",
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
                "64k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out_p),
            ],
            check=True,
            capture_output=True,
        )
        return out_p.read_bytes()


def _composite_face_chunk(face_frames: np.ndarray) -> np.ndarray:
    """Paste FlashHead face frames onto cycling preset body (V2)."""
    from lightning.composite import composite_chunk

    preset = STATE.preset
    if preset is None and STATE.preset_pool is not None:
        preset = STATE.preset_pool.active
        STATE.preset = preset
    assert preset is not None
    n = int(face_frames.shape[0])
    bodies, boxes = preset.next_n(n)
    return composite_chunk(
        face_frames, bodies, boxes, feather_px=int(STATE.feather_px)
    )


def _process_pcm_sync(session: AvatarSession, pcm: bytes, *, end: bool) -> list[AvBundle]:
    assert STATE.cfg is not None
    sr = int(STATE.cfg.infer_params.sample_rate)
    raw: list[AvBundle] = []

    # 双进程：Engine 已出 MP4（FH02）；单体：本进程编码
    if STATE.engine is not None:
        for enc in STATE.engine.infer_pcm(pcm, sample_rate=sr, end=end):
            meta = enc.meta
            raw.append(
                AvBundle(
                    pcm=enc.pcm,
                    fps=int(meta.get("fps") or STATE.cfg.infer_params.tgt_fps),
                    sample_rate=int(meta.get("sample_rate") or sr),
                    chunk_index=int(meta["chunk_index"]),
                    n_frames=int(meta.get("n_frames") or 0),
                    mp4=enc.mp4,
                    jpegs=[enc.jpeg] if enc.jpeg else [],
                )
            )
        return raw

    if STATE.stack is None:
        raise RuntimeError("no engine/stack")
    for enc in STATE.stack.infer_pcm_encoded(pcm, sample_rate=sr, end=end):
        meta = enc.meta
        raw.append(
            AvBundle(
                pcm=enc.pcm,
                fps=int(meta.get("fps") or STATE.cfg.infer_params.tgt_fps),
                sample_rate=int(meta.get("sample_rate") or sr),
                chunk_index=int(meta["chunk_index"]),
                n_frames=int(meta.get("n_frames") or 0),
                mp4=enc.mp4,
                jpegs=[enc.jpeg] if enc.jpeg else [],
            )
        )
    return raw


async def _enqueue_jpegs(session: AvatarSession, jpegs: list[bytes]) -> None:
    for jpeg in jpegs:
        session.latest_jpeg = jpeg
        try:
            session.frame_queue.put_nowait(jpeg)
        except asyncio.QueueFull:
            try:
                _ = session.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                session.frame_queue.put_nowait(jpeg)
            except asyncio.QueueFull:
                pass


def _get_or_create_session(session_id: str, *, avatar_id: str = "xiaoya_v1") -> AvatarSession:
    st = STATE.sessions.get(session_id)
    if st is None:
        st = AvatarSession(session_id=session_id, avatar_id=avatar_id)
        STATE.sessions[session_id] = st
        logger.warning("recreate missing session=%s", session_id[:8])
    return st


def _mux_buffered_ms(session: AvatarSession) -> int:
    sr = max(int(session.mux_sample_rate) or 16000, 1)
    return int(session.mux_pcm_bytes * 1000 / (sr * 2))


def _mux_target_ms(session: AvatarSession) -> int:
    """首段用 FACE_OUT_MS，之后用 FACE_OUT_FOLLOW_MS；<=0 表示不拼、片到即发。"""
    if not session.first_out_done:
        return int(_FACE_OUT_MS)
    return int(_FACE_OUT_FOLLOW_MS)


async def _emit_av_mp4(session: AvatarSession, bundle: AvBundle) -> None:
    """写入媒体并推 SSE。默认 fMP4（MSE 连续时间轴）；FACE_MSE_FORMAT=mp4|mpegts 可改。"""
    await _enqueue_jpegs(session, bundle.jpegs)
    fmt = "-"
    if bundle.mp4:
        loop = asyncio.get_running_loop()
        raw = bundle.mp4
        fmt = "mp4"
        mse_fmt = (os.environ.get("FACE_MSE_FORMAT") or "fmp4").strip().lower()
        if mse_fmt in ("", "fmp4", "fragmented", "mse"):
            try:
                from serve.av_encode import mp4_to_fmp4

                fmp4 = await loop.run_in_executor(None, mp4_to_fmp4, raw)
                if fmp4:
                    bundle.mp4 = fmp4
                    fmt = "fmp4"
            except Exception:
                logger.exception(
                    "fmp4 remux fail session=%s chunk=%s",
                    session.session_id[:8],
                    bundle.chunk_index,
                )
        elif mse_fmt in ("mpegts", "ts"):
            try:
                from serve.av_encode import mp4_to_mpegts

                ts = await loop.run_in_executor(None, mp4_to_mpegts, raw)
                if ts:
                    bundle.mp4 = ts
                    fmt = "mpegts"
            except Exception:
                logger.exception(
                    "mpegts remux fail session=%s chunk=%s",
                    session.session_id[:8],
                    bundle.chunk_index,
                )
        # mse_fmt=mp4 → 保持 progressive，仅调试用
        duration_ms = int(
            len(bundle.pcm) * 1000 / max(int(bundle.sample_rate) * 2, 1)
        )
        session.mp4_by_chunk[int(bundle.chunk_index)] = bundle.mp4
        if len(session.mp4_by_chunk) > 24:
            for k in sorted(session.mp4_by_chunk.keys())[:-24]:
                session.mp4_by_chunk.pop(k, None)
        evt = {
            "type": "av_mp4",
            "chunk": int(bundle.chunk_index),
            "bytes": len(bundle.mp4),
            "fps": int(bundle.fps),
            "duration_ms": duration_ms,
            "format": fmt,
            "url": f"/v1/avatar/{session.session_id}/mp4/{int(bundle.chunk_index)}",
        }
        dead: list = []
        for q in list(session.sse_queues):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(evt)
                except asyncio.QueueFull:
                    dead.append(q)
        for q in dead:
            if q in session.sse_queues:
                session.sse_queues.remove(q)
    try:
        session.av_queue.put_nowait(bundle)
    except asyncio.QueueFull:
        try:
            _ = session.av_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            session.av_queue.put_nowait(bundle)
        except asyncio.QueueFull:
            pass
    logger.info(
        "av_bundle session=%s chunk=%s mp4=%s pcm=%s sse=%s out_ms≈%s format=%s",
        session.session_id[:8],
        bundle.chunk_index,
        len(bundle.mp4 or b""),
        len(bundle.pcm),
        len(session.sse_queues),
        int(len(bundle.pcm) * 1000 / max(bundle.sample_rate * 2, 1)) if bundle.pcm else 0,
        fmt,
    )


async def _flush_mux(session: AvatarSession, *, force: bool = False) -> None:
    """把缓冲的模型片拼成一段下发。"""
    if not session.mux_mp4s:
        return
    target = _mux_target_ms(session)
    if not force and target > 0 and _mux_buffered_ms(session) < target:
        return
    from serve.av_encode import concat_mp4s

    mp4s = list(session.mux_mp4s)
    pcms = list(session.mux_pcms)
    jpegs = list(session.mux_jpegs)
    fps = int(session.mux_fps or 20)
    sr = int(session.mux_sample_rate or 16000)
    session.mux_mp4s.clear()
    session.mux_pcms.clear()
    session.mux_jpegs.clear()
    session.mux_pcm_bytes = 0

    loop = asyncio.get_running_loop()
    # 单片无需 concat
    if len(mp4s) == 1:
        mp4 = mp4s[0]
    else:
        try:
            mp4 = await loop.run_in_executor(None, concat_mp4s, mp4s)
        except Exception:
            logger.exception(
                "mp4 concat fail session=%s parts=%s; emit last only",
                session.session_id[:8],
                len(mp4s),
            )
            mp4 = mp4s[-1]
    pcm = b"".join(pcms)
    session.out_chunk_index += 1
    session.first_out_done = True
    await _emit_av_mp4(
        session,
        AvBundle(
            pcm=pcm,
            fps=fps,
            sample_rate=sr,
            chunk_index=session.out_chunk_index,
            n_frames=0,
            mp4=mp4,
            jpegs=jpegs[-1:] if jpegs else [],
        ),
    )


async def _enqueue_bundles(session: AvatarSession, bundles: list[AvBundle]) -> None:
    # 首段/后续目标都 <=0：不拼段，模型片原样下发
    if _FACE_OUT_MS <= 0 and _FACE_OUT_FOLLOW_MS <= 0:
        for b in bundles:
            if b.mp4 or b.jpegs:
                if b.chunk_index > session.out_chunk_index:
                    session.out_chunk_index = int(b.chunk_index)
                session.first_out_done = True
                await _emit_av_mp4(session, b)
        return

    for b in bundles:
        if b.jpegs:
            await _enqueue_jpegs(session, b.jpegs)
        if not b.mp4 and not b.pcm:
            continue
        session.mux_fps = int(b.fps or session.mux_fps or 20)
        session.mux_sample_rate = int(b.sample_rate or session.mux_sample_rate or 16000)
        if b.mp4:
            session.mux_mp4s.append(b.mp4)
        if b.pcm:
            session.mux_pcms.append(b.pcm)
            session.mux_pcm_bytes += len(b.pcm)
        if b.jpegs:
            session.mux_jpegs.extend(b.jpegs)
        target = _mux_target_ms(session)
        # target<=0：有片就发；否则攒到目标时长
        if target <= 0 or _mux_buffered_ms(session) >= target:
            await _flush_mux(session, force=True)


async def _pcm_worker_loop(session: AvatarSession) -> None:
    """按序消费 PCM 作业；推理/编码不阻塞 HTTP 推流。"""
    loop = asyncio.get_running_loop()
    while not session.closed:
        try:
            item = await asyncio.wait_for(session.pcm_job_queue.get(), timeout=60.0)
        except asyncio.TimeoutError:
            continue
        if item is None:
            break
        data, end = item
        try:
            bundles = await loop.run_in_executor(
                None, lambda d=data, e=end: _process_pcm_sync(session, d, end=e)
            )
            await _enqueue_bundles(session, bundles)
            if end:
                await _flush_mux(session, force=True)
        except Exception:
            logger.exception(
                "pcm_worker fail session=%s end=%s bytes=%s",
                session.session_id[:8],
                end,
                len(data),
            )


def _ensure_pcm_worker(session: AvatarSession) -> None:
    if session.pcm_worker is not None and not session.pcm_worker.done():
        return
    session.pcm_worker = asyncio.create_task(
        _pcm_worker_loop(session), name=f"pcm-{session.session_id[:8]}"
    )


def _pcm_bytes_per_ms(sample_rate: int = 16000) -> int:
    return max(sample_rate * 2 // 1000, 1)


def _pcm_flush_threshold(session: AvatarSession, *, sample_rate: int = 16000) -> int:
    """非 wait-tts 模式：首段 preroll，之后 flush 粒度。"""
    bpm = _pcm_bytes_per_ms(sample_rate)
    if not session.face_infer_started:
        return max(_FACE_PREROLL_MS * bpm, bpm * 100)
    return max(_FACE_FLUSH_MS * bpm, 8000)


async def _enqueue_pcm_job(
    session: AvatarSession, data: bytes, *, end: bool
) -> None:
    try:
        session.pcm_job_queue.put_nowait((data, end))
    except asyncio.QueueFull:
        await session.pcm_job_queue.put((data, end))


async def _feed_pcm_after_tts(session: AvatarSession, data: bytes) -> None:
    """TTS 已结束后台按片投喂，HTTP/end 不阻塞。"""
    try:
        bpm = _pcm_bytes_per_ms()
        job_bytes = max(_FACE_JOB_MS * bpm, 8000)
        if not data:
            await _enqueue_pcm_job(session, b"", end=True)
            return
        offset = 0
        total = len(data)
        while offset < total and not session.closed:
            piece = data[offset : offset + job_bytes]
            offset += len(piece)
            await _enqueue_pcm_job(session, piece, end=(offset >= total))
    except Exception:
        logger.exception(
            "pcm_feed fail session=%s bytes=%s", session.session_id[:8], len(data)
        )


async def _forward_pcm(session: AvatarSession, chunk: bytes, *, end: bool = False) -> None:
    if chunk:
        session.pcm_buffer.extend(chunk)

    # 同卡优化：TTS 推流期间只攒 PCM，end=1 后再开脸（错开 GPU）
    if _FACE_WAIT_TTS_END and not end:
        return

    if _FACE_WAIT_TTS_END and end:
        data = bytes(session.pcm_buffer)
        session.pcm_buffer.clear()
        if not session.face_infer_started:
            session.face_infer_started = True
            logger.info(
                "face_start_after_tts session=%s bytes=%s job_ms=%s",
                session.session_id[:8],
                len(data),
                _FACE_JOB_MS,
            )
        _ensure_pcm_worker(session)
        # 后台投喂，避免 end_pcm 被队列背压拖死编排器
        if session.pcm_feed_task is not None and not session.pcm_feed_task.done():
            logger.warning(
                "pcm_feed overlap session=%s; chaining", session.session_id[:8]
            )
        session.pcm_feed_task = asyncio.create_task(
            _feed_pcm_after_tts(session, data),
            name=f"pcm-feed-{session.session_id[:8]}",
        )
        return

    min_bytes = _pcm_flush_threshold(session)
    if not end and len(session.pcm_buffer) < min_bytes:
        return
    data = bytes(session.pcm_buffer)
    session.pcm_buffer.clear()
    if not data and not end:
        return
    if not session.face_infer_started:
        session.face_infer_started = True
        logger.info(
            "face_preroll done session=%s bytes=%s preroll_ms=%s",
            session.session_id[:8],
            len(data),
            _FACE_PREROLL_MS,
        )
    _ensure_pcm_worker(session)
    await _enqueue_pcm_job(session, data, end=end)


@app.on_event("startup")
async def _startup() -> None:
    STATE.loop = asyncio.get_running_loop()


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    ok = STATE.ready
    ip = STATE.cfg.infer_params if STATE.cfg else None
    comp = STATE.cfg.composite if STATE.cfg else None
    eng_meta = STATE.engine.meta if STATE.engine is not None else {}
    return {
        "status": "ok" if ok else "starting",
        "backend": "flashhead",
        "mode": "engine" if STATE.engine is not None else "embedded",
        "engine_url": STATE.engine_url,
        "livetalking_ready": ok,  # demo 兼容字段
        "avatar_id": "xiaoya_v1",
        "gpu": "autodl",
        "pcm_ws": True,
        "av_mux": True,
        "av_mode": "mse_fmp4",
        "face_mse_format": (os.environ.get("FACE_MSE_FORMAT") or "fmp4").strip().lower()
        or "fmp4",
        "pcm_http": True,
        "face_wait_tts_end": _FACE_WAIT_TTS_END,
        "face_preroll_ms": _FACE_PREROLL_MS,
        "face_flush_ms": _FACE_FLUSH_MS,
        "face_job_ms": _FACE_JOB_MS,
        "face_out_ms": _FACE_OUT_MS,
        "face_out_follow_ms": _FACE_OUT_FOLLOW_MS,
        "height": getattr(ip, "height", None) or eng_meta.get("height"),
        "width": getattr(ip, "width", None) or eng_meta.get("width"),
        "tgt_fps": getattr(ip, "tgt_fps", None) or eng_meta.get("tgt_fps"),
        "display_size": (
            getattr(STATE.cfg.runtime, "display_size", None) if STATE.cfg else None
        )
        or eng_meta.get("display_size"),
        "upscale": bool(STATE.upscaler) or bool(eng_meta.get("upscale")),
        "upscale_model": (
            getattr(STATE.upscaler, "model_name", None) if STATE.upscaler else None
        )
        or eng_meta.get("upscale_model"),
        "composite": bool(STATE.composite_enabled),
        "canvas_width": getattr(comp, "canvas_width", None) if comp else None,
        "canvas_height": getattr(comp, "canvas_height", None) if comp else None,
        "aspect_ratio": "2:3" if STATE.composite_enabled else "1:1",
        "layout": "backend_composite" if STATE.composite_enabled else "frontend_stack",
        "tier": getattr(STATE.cfg, "tier", None) if STATE.cfg else eng_meta.get("tier"),
    }


@app.post("/v1/avatar/session")
async def create_session(body: CreateSessionRequest | None = None) -> dict:
    if not STATE.ready:
        raise HTTPException(503, "FLASHHEAD_NOT_READY")
    sid = uuid.uuid4().hex
    avatar_id = (body.avatar_id if body else None) or "xiaoya_v1"
    STATE.sessions[sid] = AvatarSession(session_id=sid, avatar_id=avatar_id)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, STATE.reset_session)
    return {
        "session_id": sid,
        "livetalking_session_id": sid,
        "webrtc_url": "",
        "livetalking_base_url": "",
        "audio_ws_path": f"/v1/avatar/{sid}/audio/ws",
        "frames_ws_path": f"/v1/avatar/{sid}/frames/ws",
        "av_ws_path": f"/v1/avatar/{sid}/av/ws",
        "av_sse_path": f"/v1/avatar/{sid}/av/sse",
        "backend": "flashhead",
        "av_mux": True,
        "av_mode": "mse_fmp4",
        "face_mse_format": (os.environ.get("FACE_MSE_FORMAT") or "fmp4").strip().lower()
        or "fmp4",
        "composite": bool(STATE.composite_enabled),
        "aspect_ratio": "2:3" if STATE.composite_enabled else "1:1",
        "layout": "backend_composite" if STATE.composite_enabled else "frontend_stack",
        "preset_clip_id": getattr(STATE.preset_pool, "active_id", None),
    }


@app.websocket("/v1/avatar/{session_id}/audio/ws")
async def audio_ws(websocket: WebSocket, session_id: str) -> None:
    st = STATE.sessions.get(session_id)
    if not st:
        st = AvatarSession(session_id=session_id, avatar_id="xiaoya_v1")
        STATE.sessions[session_id] = st
        logger.warning("audio_ws recreate missing session=%s", session_id[:8])
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await _forward_pcm(st, msg["bytes"], end=False)
            elif msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if data.get("end") or data.get("type") == "end":
                    await _forward_pcm(st, b"", end=True)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await _forward_pcm(st, b"", end=True)
        except Exception as exc:
            logger.debug("audio end flush: %s", exc)


@app.websocket("/v1/avatar/{session_id}/av/ws")
async def av_ws(websocket: WebSocket, session_id: str) -> None:
    """音画同流：JSON av_mp4 元数据 + 1 个 MP4 binary（对齐 Soul 官方 streaming）。

    比 28×JPEG 省带宽、浏览器硬解更稳；客户端预缓冲 2 段再播。
    """
    from starlette.websockets import WebSocketState

    st = STATE.sessions.get(session_id)
    if not st:
        # Gateway 重启后内存 session 清空；为避免前端黑屏，按 id 软重建
        st = AvatarSession(session_id=session_id, avatar_id="xiaoya_v1")
        STATE.sessions[session_id] = st
        logger.warning("av_ws recreate missing session=%s", session_id[:8])
    await websocket.accept()
    fps = 20
    if STATE.cfg:
        fps = int(STATE.cfg.infer_params.tgt_fps)
    await websocket.send_json(
        {
            "type": "status",
            "event": "open",
            "mode": "av_mp4",
            "fps": fps,
            "sample_rate": 16000,
            "backend": "flashhead",
            "preroll_hint": 1,
        }
    )

    async def _watch() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            return
        except Exception:
            return

    watcher = asyncio.create_task(_watch())
    sent_chunks = 0
    try:
        while not watcher.done() and websocket.client_state == WebSocketState.CONNECTED:
            try:
                bundle: AvBundle = await asyncio.wait_for(st.av_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            mp4 = bundle.mp4
            if not mp4:
                continue
            await websocket.send_json(
                {
                    "type": "av_mp4",
                    "chunk": bundle.chunk_index,
                    "fps": bundle.fps,
                    "sample_rate": bundle.sample_rate,
                    "bytes": len(mp4),
                }
            )
            await websocket.send_bytes(mp4)
            sent_chunks += 1
            logger.info(
                "av_ws SEND session=%s chunk=%s mp4=%s sent_total=%s",
                session_id[:8],
                bundle.chunk_index,
                len(mp4),
                sent_chunks,
            )
    except WebSocketDisconnect:
        pass
    finally:
        if not watcher.done():
            watcher.cancel()
        qsize = st.av_queue.qsize() if st else -1
        logger.info(
            "av_ws CLOSE session=%s chunks_sent=%s queue_left=%s",
            session_id[:8],
            sent_chunks,
            qsize,
        )


@app.websocket("/v1/avatar/{session_id}/frames/ws")
async def frames_ws(websocket: WebSocket, session_id: str) -> None:
    from starlette.websockets import WebSocketState

    st = STATE.sessions.get(session_id)
    if not st:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    fps = 15
    try:
        q = websocket.query_params.get("fps")
        if q:
            fps = max(5, min(25, int(q)))
    except Exception:
        pass
    interval = 1.0 / fps
    sent = 0
    await websocket.send_json({"type": "status", "event": "open", "fps": fps, "backend": "flashhead"})

    async def _watch() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            return
        except Exception:
            return

    watcher = asyncio.create_task(_watch())
    try:
        while not watcher.done() and websocket.client_state == WebSocketState.CONNECTED:
            jpeg = None
            try:
                jpeg = await asyncio.wait_for(st.frame_queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                jpeg = st.latest_jpeg
            if jpeg and websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_bytes(jpeg)
                sent += 1
    except WebSocketDisconnect:
        pass
    finally:
        if not watcher.done():
            watcher.cancel()
        logger.info("frames_ws CLOSE session=%s sent=%s", session_id[:8], sent)


@app.post("/v1/avatar/{session_id}/audio", status_code=204)
async def push_audio(session_id: str, request: Request) -> None:
    """HTTP 推 PCM（推荐路径，无需 WebSocket / 隧道）。?end=1 表示收尾 flush。"""
    st = _get_or_create_session(session_id)
    body = await request.body()
    end = request.query_params.get("end") in ("1", "true", "yes")
    await _forward_pcm(st, body, end=end)


@app.get("/v1/avatar/{session_id}/av/sse")
async def av_sse(session_id: str):
    """SSE：推送 av_mp4 元数据；客户端再 HTTP GET /mp4/{chunk} 拉二进制。"""
    from fastapi.responses import StreamingResponse

    st = _get_or_create_session(session_id)
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    st.sse_queues.append(q)

    async def event_gen():
        try:
            open_evt = {
                "type": "status",
                "event": "open",
                "mode": "av_sse",
                "preroll_hint": 1,
                "backend": "flashhead",
            }
            yield f"data: {json.dumps(open_evt, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in st.sse_queues:
                st.sse_queues.remove(q)
            logger.info("av_sse CLOSE session=%s", session_id[:8])

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/v1/avatar/{session_id}/mp4/{chunk_index}")
async def get_mp4_chunk(session_id: str, chunk_index: int):
    from fastapi.responses import Response

    st = STATE.sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    data = st.mp4_by_chunk.get(int(chunk_index))
    if not data:
        raise HTTPException(404, "CHUNK_NOT_FOUND")
    return Response(
        content=data,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store", "Content-Length": str(len(data))},
    )


@app.post("/v1/avatar/{session_id}/interrupt", status_code=204)
async def interrupt(session_id: str) -> None:
    st = _get_or_create_session(session_id)
    st.pcm_buffer.clear()
    st.mp4_by_chunk.clear()
    st.mux_mp4s.clear()
    st.mux_pcms.clear()
    st.mux_jpegs.clear()
    st.mux_pcm_bytes = 0
    st.out_chunk_index = 0
    st.first_out_done = False
    st.face_infer_started = False
    if st.pcm_feed_task is not None and not st.pcm_feed_task.done():
        st.pcm_feed_task.cancel()
        st.pcm_feed_task = None
    while not st.pcm_job_queue.empty():
        try:
            st.pcm_job_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    while not st.frame_queue.empty():
        try:
            st.frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    while not st.av_queue.empty():
        try:
            st.av_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    # 通知前端：本轮清空，重建 MSE 时间轴
    evt = {"type": "status", "event": "turn_reset"}
    for q in list(st.sse_queues):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            pass
    if STATE.ready:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, STATE.reset_session)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("FLASHHEAD_CONFIG", _DEFAULT_CONFIG)))
    parser.add_argument("--avatar-image", type=Path, default=None)
    parser.add_argument("--host", default=os.environ.get("FLASHHEAD_GATEWAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FLASHHEAD_GATEWAY_PORT", "6008")))
    parser.add_argument("--assets-dir", type=Path, default=None)
    parser.add_argument(
        "--engine-url",
        default=os.environ.get("FLASHHEAD_ENGINE_URL", ""),
        help="若设置则双进程模式（不加载模型），例如 http://127.0.0.1:6009",
    )
    args = parser.parse_args()

    # Ensure repo on path
    import sys

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    if args.engine_url:
        STATE.attach_engine(args.engine_url)
    else:
        STATE.load(args.config, args.avatar_image)
    _mount_assets(args.assets_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
