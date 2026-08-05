#!/usr/bin/env python3
"""单独拉 Avatar Gateway frames_ws，录一段 JPEG 帧并合成 MP4。

用法（在 demo/ 下）:
  .venv/bin/python scripts/capture_frames_mp4.py
  .venv/bin/python scripts/capture_frames_mp4.py --seconds 8 --fps 12
"""
from __future__ import annotations

import argparse
import asyncio
import math
import struct
import sys
import time
import wave
from pathlib import Path

import httpx
import websockets

DEFAULT_GW = "http://127.0.0.1:6008"


def _pcm_tone(seconds: float, sr: int = 16000, hz: float = 220.0) -> bytes:
    """简单正弦 PCM，促使 LiveTalking 出动态嘴型帧。"""
    n = int(sr * seconds)
    out = bytearray()
    for i in range(n):
        # 轻量包络，避免咔哒
        t = i / sr
        env = min(1.0, t * 8, max(0.0, (seconds - t) * 8))
        sample = int(8000 * env * math.sin(2 * math.pi * hz * t))
        out += struct.pack("<h", max(-32767, min(32767, sample)))
    return bytes(out)


def _write_wav(path: Path, pcm: bytes, sr: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Capture frames_ws → MP4")
    ap.add_argument("--gateway", default=DEFAULT_GW)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-audio", action="store_true", help="不推 PCM，只录静态帧")
    args = ap.parse_args()

    gw = args.gateway.rstrip("/")
    out_dir = Path(__file__).resolve().parents[1] / "tmp" / "frame_capture"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    frames_dir = out_dir / f"frames_{stamp}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = Path(args.out) if args.out else out_dir / f"avatar_{stamp}.mp4"

    print(f"gateway: {gw}")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(f"{gw}/v1/health")
        r.raise_for_status()
        print("health:", r.json())
        r = await client.post(f"{gw}/v1/avatar/session", json={})
        r.raise_for_status()
        sess = r.json()
    sid = sess["session_id"]
    print("session:", sid)
    print("lt_session:", sess.get("livetalking_session_id"))
    print("frames_ws:", sess.get("frames_ws_path"))

    if gw.startswith("https://"):
        wss = "wss://" + gw[len("https://") :]
    else:
        wss = "ws://" + gw[len("http://") :]
    frames_url = f"{wss}/v1/avatar/{sid}/frames/ws?fps={args.fps}"
    audio_url = f"{wss}/v1/avatar/{sid}/audio/ws"

    saved = 0
    bytes_total = 0
    stop_at = time.monotonic() + args.seconds

    async def push_audio() -> None:
        if args.no_audio:
            return
        pcm = _pcm_tone(max(1.5, args.seconds - 0.5))
        wav_path = out_dir / f"tone_{stamp}.wav"
        _write_wav(wav_path, pcm)
        print(f"push pcm → {audio_url} ({len(pcm)} bytes, wav={wav_path.name})")
        # 等帧流先连上
        await asyncio.sleep(0.4)
        async with websockets.connect(
            audio_url, proxy=None, max_size=8 * 1024 * 1024, open_timeout=20
        ) as aws:
            # 按 ~100ms 切片推送
            step = 3200  # 100ms @16k mono s16le
            for i in range(0, len(pcm), step):
                await aws.send(pcm[i : i + step])
                await asyncio.sleep(0.08)
            await aws.send('{"end": true}')
        print("audio push done")

    async def pull_frames() -> None:
        nonlocal saved, bytes_total
        print(f"connect frames → {frames_url}")
        async with websockets.connect(
            frames_url, proxy=None, max_size=8 * 1024 * 1024, open_timeout=20
        ) as ws:
            while time.monotonic() < stop_at:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    print("… waiting frame")
                    continue
                if isinstance(msg, str):
                    print("status:", msg[:200])
                    continue
                if not isinstance(msg, (bytes, bytearray)) or len(msg) < 100:
                    continue
                saved += 1
                bytes_total += len(msg)
                path = frames_dir / f"frame_{saved:05d}.jpg"
                path.write_bytes(msg)
                if saved == 1 or saved % 15 == 0:
                    print(f"  saved #{saved} ({len(msg)} bytes)")

    await asyncio.gather(pull_frames(), push_audio())
    print(f"saved {saved} jpeg frames → {frames_dir} ({bytes_total} bytes)")

    if saved < 2:
        print("ERROR: too few frames, skip ffmpeg", file=sys.stderr)
        return 2

    # ffmpeg: image2 sequence → H.264 mp4
    import subprocess

    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(args.fps),
        "-i",
        str(frames_dir / "frame_%05d.jpg"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    print("ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"OK mp4: {mp4_path} ({mp4_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
