from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from packages.config import DeepSeekConfig
from packages.interfaces import ChatMessage

logger = logging.getLogger(__name__)


class DeepSeekProvider:
    """DeepSeek LLM：优先 Responses API（flash），稳定 instructions 吃磁盘前缀缓存。

    注意：官方 Responses **不支持** previous_response_id / conversation（无状态）。
    多轮必须自带历史；缓存靠「相同前缀完整命中」，不是靠 resp_id 串联。
    """

    def __init__(self, cfg: DeepSeekConfig) -> None:
        self._cfg = cfg
        self._use_responses = self._want_responses(cfg)

    @staticmethod
    def _want_responses(cfg: DeepSeekConfig) -> bool:
        mid = (cfg.model_id or "").lower()
        # Responses 目前仅 deepseek-v4-flash
        return "v4-flash" in mid or mid in ("deepseek-chat", "deepseek-reasoner")

    def _api_root(self) -> str:
        # base_url 常带 /v1；Responses 在 https://api.deepseek.com/responses
        root = self._cfg.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        if self._use_responses:
            async for delta in self._stream_responses(messages, cancel_event=cancel_event):
                yield delta
        else:
            async for delta in self._stream_chat_completions(
                messages, cancel_event=cancel_event
            ):
                yield delta

    def _split_system(self, messages: list[ChatMessage]) -> tuple[str, list[ChatMessage]]:
        instructions_parts: list[str] = []
        rest: list[ChatMessage] = []
        for m in messages:
            if m.role == "system" and not rest:
                instructions_parts.append(m.content)
            else:
                rest.append(m)
        return "\n\n".join(instructions_parts).strip(), rest

    async def _stream_responses(
        self,
        messages: list[ChatMessage],
        *,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[str]:
        instructions, rest = self._split_system(messages)
        # input：字符串（仅一轮）或 item list（多轮）。system 进 instructions 以稳定前缀。
        if len(rest) == 1 and rest[0].role == "user":
            inp: Any = rest[0].content
        else:
            inp = [
                {
                    "type": "message",
                    "role": m.role if m.role in ("user", "assistant", "system") else "user",
                    "content": m.content,
                }
                for m in rest
            ]
        payload: dict[str, Any] = {
            "model": self._cfg.model_id
            if "flash" in (self._cfg.model_id or "").lower()
            else "deepseek-v4-flash",
            "input": inp,
            "stream": True,
            "temperature": self._cfg.temperature,
            "max_output_tokens": self._cfg.max_tokens,
        }
        if instructions:
            payload["instructions"] = instructions

        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._api_root()}/responses"

        async with httpx.AsyncClient(timeout=self._cfg.timeout, trust_env=False) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    logger.warning(
                        "responses HTTP %s, fallback chat/completions: %s",
                        resp.status_code,
                        body,
                    )
                    async for d in self._stream_chat_completions(
                        messages, cancel_event=cancel_event
                    ):
                        yield d
                    return
                async for line in resp.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    typ = obj.get("type") or ""
                    if typ == "response.output_text.delta":
                        delta = obj.get("delta") or ""
                        if delta:
                            yield delta
                    elif typ in (
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                    ):
                        usage = (obj.get("response") or {}).get("usage") or obj.get(
                            "usage"
                        )
                        if usage:
                            cached = (
                                (usage.get("input_tokens_details") or {}).get(
                                    "cached_tokens"
                                )
                                or usage.get("prompt_cache_hit_tokens")
                                or 0
                            )
                            logger.info(
                                "deepseek responses done type=%s in=%s cached=%s out=%s",
                                typ,
                                usage.get("input_tokens")
                                or usage.get("prompt_tokens"),
                                cached,
                                usage.get("output_tokens")
                                or usage.get("completion_tokens"),
                            )

    async def _stream_chat_completions(
        self,
        messages: list[ChatMessage],
        *,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._cfg.model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._cfg.base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=self._cfg.timeout, trust_env=False) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"].get("content") or ""
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
