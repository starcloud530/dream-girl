from __future__ import annotations

import asyncio
import logging

import httpx

from packages.config import AppConfig, EdgeTTSConfig
from packages.providers.deepseek import DeepSeekProvider
from packages.providers.edge_tts import EdgeTTSProvider
from packages.providers.minimax import MiniMaxTTSProvider
from packages.providers.mock import MockLLMProvider, MockTTSProvider
from packages.providers.qwen_tts import QwenTTSProvider

logger = logging.getLogger(__name__)


async def _qwen_tts_ok(cfg: AppConfig) -> bool:
    """vLLM-Omni: GET /v1/models；兼容旧 /v1/health。"""
    base = cfg.qwen_tts.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            resp = await client.get(f"{base}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data.get("data"), list) and data["data"]:
                    return True
            resp = await client.get(f"{base}/v1/health")
            if resp.status_code == 200:
                body = resp.json()
                return body.get("status") in ("ok", "healthy", True) or bool(body)
    except Exception as exc:
        logger.info("Qwen TTS unavailable: %s", exc)
    return False


async def _minimax_ok(cfg: AppConfig) -> bool:
    if not cfg.minimax or not cfg.minimax.api_key:
        return False
    try:
        provider = MiniMaxTTSProvider(cfg.minimax)
        async with asyncio.timeout(8):
            async for _ in provider.synthesize_stream("测", sample_rate=16000):
                return True
    except Exception as exc:
        logger.info("MiniMax unavailable, fallback to Edge TTS: %s", exc)
    return False


def build_llm(cfg: AppConfig, *, use_mock: bool = False):
    if use_mock or not cfg.deepseek:
        return MockLLMProvider()
    return DeepSeekProvider(cfg.deepseek)


async def build_tts(cfg: AppConfig, *, use_mock: bool = False):
    if use_mock:
        return MockTTSProvider(), "mock"

    choice = cfg.tts_provider.lower()
    if choice == "mock":
        return MockTTSProvider(), "mock"
    if choice == "edge":
        return EdgeTTSProvider(cfg.edge_tts), "edge"
    if choice == "qwen":
        return QwenTTSProvider(cfg.qwen_tts), "qwen"
    if choice == "minimax" and cfg.minimax:
        return MiniMaxTTSProvider(cfg.minimax), "minimax"

    if choice in ("auto", "qwen") and await _qwen_tts_ok(cfg):
        return QwenTTSProvider(cfg.qwen_tts), "qwen"

    if choice in ("auto", "minimax") and cfg.minimax and await _minimax_ok(cfg):
        return MiniMaxTTSProvider(cfg.minimax), "minimax"

    return EdgeTTSProvider(cfg.edge_tts), "edge"
