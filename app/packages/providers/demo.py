from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from packages.config import load_config
from packages.interfaces import ChatMessage
from packages.providers.deepseek import DeepSeekProvider
from packages.providers.minimax import MiniMaxTTSProvider
from packages.providers.mock import MockLLMProvider, MockTTSProvider


async def _run(use_mock: bool) -> None:
    cfg = load_config()
    out = Path("out.pcm")

    if use_mock or not cfg.deepseek:
        llm = MockLLMProvider()
    else:
        llm = DeepSeekProvider(cfg.deepseek)

    if use_mock or not cfg.minimax:
        tts = MockTTSProvider()
    else:
        tts = MiniMaxTTSProvider(cfg.minimax)

    messages = [ChatMessage("user", "说一句简短的问候")]
    print("LLM stream:")
    tokens: list[str] = []
    async for t in llm.chat_stream(messages):
        print(t, end="", flush=True)
        tokens.append(t)
    print("\nTTS -> out.pcm")

    text = "".join(tokens)
    with out.open("wb") as f:
        async for chunk in tts.synthesize_stream(text):
            f.write(chunk)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.mock))


if __name__ == "__main__":
    main()
