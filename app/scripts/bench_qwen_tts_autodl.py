#!/usr/bin/env python3
"""Qwen3-TTS bench on AutoDL — official dtype pattern (no autocast during generate)."""

from __future__ import annotations

import argparse
import time
import torch
from qwen_tts import Qwen3TTSModel

DEFAULT_MODELS = {
    "06b-custom": "/root/autodl-fs/models/qwen-tts/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "06b-base": "/root/autodl-fs/models/qwen-tts/Qwen3-TTS-12Hz-0.6B-Base",
    "17b-base": "/root/autodl-fs/models/qwen-tts/Qwen3-TTS-12Hz-1.7B-Base",
}

REF_AUDIO = "/root/autodl-tmp/clone_ref.wav"
REF_TEXT = (
    "Okay. Yeah. I resent you. I love you. I respect you. "
    "But you know what? You blew it! And thanks to you."
)

TEXT_SHORT = "你好呀。"
TEXT_LONG = "哥哥你回来啦，人家等你很久了，今天过得怎么样呀？有没有想我？"

GEN_KWARGS = {
    "max_new_tokens": 2048,
    "do_sample": True,
    "top_k": 50,
    "top_p": 1.0,
    "temperature": 0.9,
    "repetition_penalty": 1.05,
    "subtalker_dosample": True,
    "subtalker_top_k": 50,
    "subtalker_top_p": 1.0,
    "subtalker_temperature": 0.9,
}


def vram_gb() -> float:
    return torch.cuda.memory_allocated() / 1e9


def bench_generate(fn, label: str) -> tuple[float, float, float]:
    print(f">> {label}", flush=True)
    torch.cuda.synchronize()
    t0 = time.time()
    with torch.inference_mode():
        wavs, sr = fn()
    torch.cuda.synchronize()
    wall = time.time() - t0
    audio_sec = len(wavs[0]) / sr
    rtf = wall / audio_sec if audio_sec > 0 else 0.0
    print(
        f"[{label}] audio={audio_sec:.2f}s wall={wall:.2f}s "
        f"RTF={rtf:.2f} VRAM={vram_gb():.1f}GB",
        flush=True,
    )
    return wall, audio_sec, rtf


def load_model(path: str, attn: str) -> Qwen3TTSModel:
    # Official: bfloat16 at load only — do NOT wrap generate() in autocast.
    kwargs: dict = {
        "device_map": "cuda:0",
        "dtype": torch.bfloat16,
    }
    if attn == "flash":
        kwargs["attn_implementation"] = "flash_attention_2"
    elif attn == "sdpa":
        kwargs["attn_implementation"] = "sdpa"
    # attn=eager: omit attn_implementation

    print(f"=== load {path} attn={attn} dtype=bfloat16 ===", flush=True)
    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(path, **kwargs)
    print(f"[load] {time.time() - t0:.1f}s VRAM={vram_gb():.1f}GB", flush=True)
    return model


def run_custom_voice(model: Qwen3TTSModel, text: str, runs: int) -> None:
    results = []
    for i in range(1, runs + 1):
        label = f"custom-run{i}-len{len(text)}"
        results.append(
            bench_generate(
                lambda: model.generate_custom_voice(
                    text=text,
                    language="Chinese",
                    speaker="Serena",
                    instruct="温柔亲切，适合日常对话",
                    **GEN_KWARGS,
                ),
                label,
            )
        )
    avg = sum(r[2] for r in results) / len(results)
    print(f"custom_voice avg RTF = {avg:.2f}", flush=True)


def run_voice_clone(model: Qwen3TTSModel, text: str, runs: int) -> None:
    print("=== create_voice_clone_prompt ===", flush=True)
    t0 = time.time()
    prompt = model.create_voice_clone_prompt(
        ref_audio=REF_AUDIO,
        ref_text=REF_TEXT,
        x_vector_only_mode=False,
    )
    print(f"[prompt] {time.time() - t0:.1f}s VRAM={vram_gb():.1f}GB", flush=True)

    results = []
    for i in range(1, runs + 1):
        label = f"clone-run{i}-len{len(text)}"
        results.append(
            bench_generate(
                lambda: model.generate_voice_clone(
                    text=text,
                    language="Chinese",
                    voice_clone_prompt=prompt,
                    **GEN_KWARGS,
                ),
                label,
            )
        )
    avg = sum(r[2] for r in results) / len(results)
    print(f"voice_clone avg RTF = {avg:.2f}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=list(DEFAULT_MODELS.keys()),
        default="06b-custom",
    )
    parser.add_argument("--model-path", default=None, help="override local model dir")
    parser.add_argument(
        "--mode",
        choices=["custom", "clone"],
        default="custom",
    )
    parser.add_argument("--text", choices=["short", "long", "both"], default="both")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--attn",
        choices=["flash", "sdpa", "eager"],
        default="flash",
        help="flash=speed; use sdpa/eager if flash_attn dtype errors",
    )
    args = parser.parse_args()

    path = args.model_path or DEFAULT_MODELS[args.model]
    model = load_model(path, args.attn)

    texts: list[str] = []
    if args.text in ("short", "both"):
        texts.append(TEXT_SHORT)
    if args.text in ("long", "both"):
        texts.append(TEXT_LONG)

    for text in texts:
        print(f"\n=== text len={len(text)} ===", flush=True)
        if args.mode == "custom":
            run_custom_voice(model, text, args.runs)
        else:
            run_voice_clone(model, text, args.runs)


if __name__ == "__main__":
    main()
