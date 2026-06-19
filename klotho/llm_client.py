"""Async OpenAI-compatible LLM client (works against ollama proxy or shim)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class LLMResult:
    text: str
    elapsed_ms: int
    raw: dict


class LLMClient:
    """Thin async wrapper around OpenAI-style /chat/completions."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        *,
        timeout: float = 180.0,
        api_key: str = "ollama",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            elapsed = int((time.perf_counter() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return LLMResult(text=text, elapsed_ms=elapsed, raw=data)

    async def chat_json(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Best-effort JSON response. Falls back to extracting {...} from text."""
        try:
            result = await self.chat(
                model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            result = await self.chat(
                model, messages, temperature=temperature, max_tokens=max_tokens
            )
        return _parse_json(result.text)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        # strip markdown code fences
        text = text.split("```", 2)[1] if "```" in text else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise