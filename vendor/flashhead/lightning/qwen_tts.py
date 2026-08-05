"""Qwen3-TTS CustomVoice 本地推理封装（内置 speaker）。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterator

import librosa
import numpy as np

from lightning.qwen_tts_config import QwenTTSConfig

logger = logging.getLogger(__name__)

# CustomVoice 内置 speaker（中文女声默认 Serena）
DEFAULT_SPEAKERS = (
    "Serena",
    "Vivian",
    "Uncle_Fu",
    "Dylan",
    "Eric",
    "Ryan",
    "Aiden",
    "Ono_Anna",
    "Sohee",
)


class Qwen3TTSRuntime:
    def __init__(self, cfg: QwenTTSConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._lock = threading.Lock()
        self._loaded = False
        self._speakers: list[str] = list(DEFAULT_SPEAKERS)

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        if self._loaded:
            return
        import torch
        from qwen_tts import Qwen3TTSModel

        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }
        dtype = dtype_map.get(self.cfg.dtype, torch.bfloat16)
        model_path = str(self.cfg.model_dir or self.cfg.model_id)

        attn = self.cfg.attn_implementation
        try:
            self._model = Qwen3TTSModel.from_pretrained(
                model_path,
                device_map=self.cfg.device,
                dtype=dtype,
                attn_implementation=attn,
            )
        except Exception as exc:
            if attn != "sdpa":
                logger.warning("attn=%s failed (%s), fallback sdpa", attn, exc)
                self._model = Qwen3TTSModel.from_pretrained(
                    model_path,
                    device_map=self.cfg.device,
                    dtype=dtype,
                    attn_implementation="sdpa",
                )
            else:
                raise

        try:
            speakers = self._model.get_supported_speakers()
            if speakers:
                self._speakers = [str(s) for s in speakers]
        except Exception:
            pass

        self._loaded = True
        logger.info(
            "Qwen3-TTS loaded path=%s speakers=%s device=%s",
            model_path,
            self._speakers[:4],
            self.cfg.device,
        )

    def speakers(self) -> list[str]:
        return list(self._speakers)

    def synthesize_pcm(
        self,
        text: str,
        *,
        speaker: str | None = None,
        language: str | None = None,
        instruct: str | None = None,
        sample_rate: int | None = None,
    ) -> tuple[bytes, dict]:
        """合成一段文本 → s16le mono PCM。"""
        if not text.strip():
            return b"", {"audio_ms": 0, "elapsed_ms": 0}

        self.load()
        tgt_sr = int(sample_rate or self.cfg.sample_rate)
        spk = speaker or self.cfg.speaker
        lang = language or self.cfg.language
        ins = instruct if instruct is not None else self.cfg.instruct

        t0 = time.perf_counter()
        with self._lock:
            wavs, sr = self._model.generate_custom_voice(
                text=text.strip(),
                language=lang,
                speaker=spk,
                instruct=ins or None,
                non_streaming_mode=self.cfg.non_streaming_mode,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        wav = np.asarray(wavs[0], dtype=np.float32)
        pcm = _to_pcm_s16le(wav, int(sr), tgt_sr)
        audio_ms = int(len(pcm) / 2 / tgt_sr * 1000) if tgt_sr else 0
        rtf = elapsed_ms / audio_ms if audio_ms > 0 else 0.0
        return pcm, {
            "audio_ms": audio_ms,
            "elapsed_ms": elapsed_ms,
            "rtf": round(rtf, 3),
            "sample_rate": tgt_sr,
            "speaker": spk,
        }

    def synthesize_pcm_chunks(
        self,
        text: str,
        *,
        chunk_ms: int = 200,
        **kwargs,
    ) -> Iterator[tuple[bytes, dict]]:
        """整句合成后按固定毫秒切块吐出（便于流式协议）。"""
        pcm, meta = self.synthesize_pcm(text, **kwargs)
        if not pcm:
            return
        sr = int(meta["sample_rate"])
        frame_bytes = max(2, sr * 2 * chunk_ms // 1000)
        frame_bytes -= frame_bytes % 2
        for i in range(0, len(pcm), frame_bytes):
            yield pcm[i : i + frame_bytes], meta


def _to_pcm_s16le(wav_f32: np.ndarray, orig_sr: int, target_sr: int) -> bytes:
    x = np.asarray(wav_f32, dtype=np.float32).reshape(-1)
    if orig_sr != target_sr and x.size:
        x = librosa.resample(x, orig_sr=orig_sr, target_sr=target_sr)
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm.tobytes()
